"""Run compiled checks against Call Report data with DuckDB.

The data to check is a long table with one row per institution, report date
and MDRM item::

    rssd_id   report_date  mdrm      value
    9000001   2026-06-30   RCON2170  1250000
    9000001   2026-06-30   RCON2160  3400

The column names can differ; pass :class:`Columns` to map them. Include the
earlier quarters an edit compares against, and pass ``report_date`` to check
only the latest one.
"""

from __future__ import annotations

import csv
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import TextIO

import duckdb

from .compiler import (
    ITEMS_RELATION,
    PERIODS_RELATION,
    CompiledCheck,
    SkippedEdit,
    compile_edits,
)
from .edits import DEFAULT_TYPES, Edit, filter_edits
from .mdrm import MdrmDictionary

__all__ = [
    "FAILURE_COLUMNS",
    "CheckReport",
    "Columns",
    "DataError",
    "Failure",
    "check",
    "load_items",
    "run_checks",
]

SOURCE_RELATION = "tieout_source"

FAILURE_COLUMNS = (
    "edit_id",
    "edit_type",
    "rssd_id",
    "report_date",
    "status",
    "item_values",
    "message",
)


class DataError(ValueError):
    """Raised when the data to check does not meet TieOut's data contract."""


@dataclass(frozen=True)
class Columns:
    """Names of the columns in your data. The defaults are TieOut's own names."""

    entity: str = "rssd_id"
    report_date: str = "report_date"
    item: str = "mdrm"
    value: str = "value"


DEFAULT_COLUMNS = Columns()


@dataclass(frozen=True)
class Failure:
    """One edit that did not pass for one institution and report date."""

    edit_id: str
    edit_type: str
    rssd_id: object
    report_date: date
    status: str
    """``fail`` when the formula is false, ``error`` when it could not be evaluated."""
    item_values: str
    """The values the edit read, for example ``RCON2170@P0=1250000; RCON2170@-P1Q=1250000``."""
    message: str = ""


@dataclass
class CheckReport:
    """The result of checking data against a set of edits."""

    failures: list[Failure] = field(default_factory=list)
    checks_run: int = 0
    periods_checked: int = 0
    skipped: list[SkippedEdit] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    execution_errors: list[tuple[str, str]] = field(default_factory=list)

    def count(self, edit_type: str | None = None, status: str | None = None) -> int:
        """Number of failures, optionally of one edit type and status."""
        return sum(
            1
            for failure in self.failures
            if (edit_type is None or failure.edit_type == edit_type)
            and (status is None or failure.status == status)
        )

    def by_type(self) -> Counter[str]:
        """Failures counted by edit type."""
        return Counter(failure.edit_type for failure in self.failures)

    def write_csv(self, target: str | Path | TextIO) -> None:
        """Write the failures as CSV, one row per failure."""
        if isinstance(target, (str, Path)):
            with Path(target).open("w", encoding="utf-8", newline="") as handle:
                self.write_csv(handle)
            return
        writer = csv.writer(target, lineterminator="\n")
        writer.writerow(FAILURE_COLUMNS)
        for failure in self.failures:
            writer.writerow(
                [
                    failure.edit_id,
                    failure.edit_type,
                    failure.rssd_id,
                    failure.report_date.isoformat()
                    if isinstance(failure.report_date, date)
                    else failure.report_date,
                    failure.status,
                    failure.item_values,
                    failure.message,
                ]
            )

    def summary(self) -> str:
        """A short human-readable summary."""
        lines = [
            f"Checked {self.periods_checked} institution report date(s) "
            f"against {self.checks_run} edit(s).",
        ]
        if not self.failures:
            lines.append("Every edit that ran passed.")
        else:
            counts = ", ".join(f"{t} {n}" for t, n in sorted(self.by_type().items()))
            lines.append(f"Failures: {len(self.failures)} ({counts}).")
            errors = self.count(status="error")
            if errors:
                lines.append(
                    f"{errors} of them could not be evaluated (status 'error'), "
                    "usually because a value that must be a number is not one."
                )
        if self.skipped:
            lines.append(f"{len(self.skipped)} edit(s) were not compiled and did not run.")
        if self.execution_errors:
            lines.append(f"{len(self.execution_errors)} check(s) failed to run.")
        return "\n".join(lines)


def _quote_ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _quote_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _quote_table(name: str) -> str:
    return ".".join(_quote_ident(part) for part in name.split("."))


def _file_sql(path: Path) -> str:
    if not path.exists():
        raise DataError(f"{path} does not exist")
    name = path.name.lower()
    literal = _quote_literal(str(path))
    if name.endswith((".parquet", ".pq")):
        return f"read_parquet({literal})"
    if name.endswith((".csv", ".csv.gz", ".tsv", ".txt")):
        return f"read_csv({literal}, header = true, all_varchar = true)"
    raise DataError(f"{path}: use a CSV or Parquet file")


def load_items(
    connection: duckdb.DuckDBPyConnection,
    data: str | Path | None = None,
    *,
    table: str | None = None,
    columns: Columns = DEFAULT_COLUMNS,
    report_date: date | None = None,
) -> int:
    """Expose data to the checks as ``tieout_items`` and ``tieout_periods``.

    ``data`` is a CSV or Parquet file; ``table`` names a table or view already
    in ``connection``. The data is read once into temporary tables, so your
    own tables are never changed. Returns the number of institution report
    dates to check.

    Raises :class:`DataError` when columns are missing, report dates are not
    valid dates, or an item has two different values for the same
    institution and date.
    """
    if (data is None) == (table is None):
        raise DataError("Give either a data file or a table name")
    source = _file_sql(Path(data)) if data is not None else _quote_table(table or "")
    connection.execute(f"CREATE OR REPLACE TEMP VIEW {SOURCE_RELATION} AS SELECT * FROM {source}")

    found = [row[0] for row in connection.execute(f"DESCRIBE {SOURCE_RELATION}").fetchall()]
    by_lower = {name.lower(): name for name in found}
    wanted = {
        "entity": columns.entity,
        "report date": columns.report_date,
        "item": columns.item,
        "value": columns.value,
    }
    missing = [
        f"{role} column '{name}'" for role, name in wanted.items() if name.lower() not in by_lower
    ]
    if missing:
        raise DataError(
            "The data has no " + ", no ".join(missing) + ". Columns found: " + ", ".join(found)
        )
    entity = _quote_ident(by_lower[columns.entity.lower()])
    when = _quote_ident(by_lower[columns.report_date.lower()])
    item = _quote_ident(by_lower[columns.item.lower()])
    value = _quote_ident(by_lower[columns.value.lower()])
    as_text = f"CAST({when} AS VARCHAR)"
    parsed_date = (
        f"COALESCE(TRY_CAST({when} AS DATE), "
        f"CAST(TRY_STRPTIME({as_text}, '%m/%d/%Y') AS DATE), "
        f"CAST(TRY_STRPTIME({as_text}, '%Y%m%d') AS DATE))"
    )
    connection.execute(
        f"""CREATE OR REPLACE TEMP TABLE {ITEMS_RELATION} AS
        SELECT
            {entity} AS rssd_id,
            {parsed_date} AS report_date,
            upper(trim(CAST({item} AS VARCHAR))) AS mdrm,
            {value} AS value
        FROM {SOURCE_RELATION}"""
    )

    bad_dates, empty_keys = connection.execute(
        f"""SELECT
            count(*) FILTER (WHERE report_date IS NULL),
            count(*) FILTER (WHERE rssd_id IS NULL OR mdrm IS NULL OR mdrm = '')
        FROM {ITEMS_RELATION}"""
    ).fetchone() or (0, 0)
    if bad_dates:
        raise DataError(
            f"{bad_dates} row(s) have a report date that is missing or not a date (use YYYY-MM-DD)"
        )
    if empty_keys:
        raise DataError(f"{empty_keys} row(s) have no institution identifier or no MDRM item")

    conflicts = connection.execute(
        f"""SELECT rssd_id, report_date, mdrm
        FROM {ITEMS_RELATION}
        GROUP BY rssd_id, report_date, mdrm
        HAVING count(DISTINCT CAST(value AS VARCHAR)) > 1
        ORDER BY rssd_id, report_date, mdrm
        LIMIT 5"""
    ).fetchall()
    if conflicts:
        shown = "; ".join(f"{r[0]} {r[1]} {r[2]}" for r in conflicts)
        raise DataError(
            "Some items have more than one value for the same institution and report date: " + shown
        )

    where = f"WHERE report_date = DATE '{report_date.isoformat()}'" if report_date else ""
    connection.execute(
        f"""CREATE OR REPLACE TEMP TABLE {PERIODS_RELATION} AS
        SELECT DISTINCT rssd_id, report_date FROM {ITEMS_RELATION} {where}"""
    )
    row = connection.execute(f"SELECT count(*) FROM {PERIODS_RELATION}").fetchone()
    return int(row[0]) if row else 0


def run_checks(
    connection: duckdb.DuckDBPyConnection,
    checks: Iterable[CompiledCheck],
) -> tuple[list[Failure], list[tuple[str, str]]]:
    """Run compiled checks; return the failures and any checks that could not run."""
    failures: list[Failure] = []
    errors: list[tuple[str, str]] = []
    for compiled in checks:
        try:
            rows = connection.execute(compiled.sql).fetchall()
        except duckdb.Error as exc:
            errors.append((compiled.edit.edit_id, str(exc)))
            continue
        for edit_id, edit_type, rssd_id, report_date, status, item_values in rows:
            failures.append(
                Failure(
                    edit_id=edit_id,
                    edit_type=edit_type,
                    rssd_id=rssd_id,
                    report_date=report_date,
                    status=status,
                    item_values=item_values,
                    message=compiled.edit.message,
                )
            )
    return failures, errors


def check(
    data: str | Path | None = None,
    edits: Iterable[Edit] = (),
    *,
    table: str | None = None,
    connection: duckdb.DuckDBPyConnection | None = None,
    columns: Columns = DEFAULT_COLUMNS,
    report_date: date | None = None,
    types: Sequence[str] | None = DEFAULT_TYPES,
    dictionary: MdrmDictionary | None = None,
) -> CheckReport:
    """Check Call Report data against edits and return a :class:`CheckReport`.

    ``data`` is a CSV or Parquet file, or pass ``table`` and ``connection`` to
    check a table already in DuckDB. ``types`` selects the edit types to run
    (``None`` runs every type). With an MDRM ``dictionary``, items that are
    not in it are listed in the report's warnings.
    """
    selected = filter_edits(edits, types)
    compiled = compile_edits(selected, dictionary=dictionary, as_of=report_date)
    con = connection if connection is not None else duckdb.connect()
    periods = load_items(con, data, table=table, columns=columns, report_date=report_date)
    failures, errors = run_checks(con, compiled.checks)
    return CheckReport(
        failures=failures,
        checks_run=len(compiled.checks),
        periods_checked=periods,
        skipped=compiled.skipped,
        warnings=compiled.warnings,
        execution_errors=errors,
    )
