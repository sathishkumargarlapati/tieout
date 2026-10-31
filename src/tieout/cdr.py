"""Read the FFIEC's public bulk Call Report data.

Every quarter the FFIEC Central Data Repository (CDR) publishes the Call
Reports of every filer as one zip file, ``FFIEC CDR Call Bulk All Schedules
MMDDYYYY.zip`` (product "Call Reports -- Single Period", tab-delimited
format), at https://cdr.ffiec.gov/public/PWS/DownloadBulkData.aspx.

The zip holds a Panel of Reporters (POR) file that lists the institutions,
and one tab-delimited file per schedule; large schedules are split into
parts such as ``(1 of 2)``. Each schedule file has a header row of MDRM
identifiers starting with ``IDRSSD``, a second row of item descriptions, then
one row per institution.

Researchers who process these files document a few quirks (see the ffiec.pq
project, https://iangow.github.io/ffiec.pq/), and this module handles them:

- values are not quoted and every line ends with a tab, so a line break that
  is not preceded by a tab is a line break inside a text value; the two
  pieces are joined back together;
- a tab inside a text value adds a field; extra fields are folded into the
  last column;
- ``CONF`` marks a confidential value that is not published;
- an empty field means the item was not reported.

The result is TieOut's long format: one row per institution, report date,
MDRM item and value, ready for :func:`tieout.check` and
:func:`tieout.groundtruth.run_ground_truth`.
"""

from __future__ import annotations

import csv
import re
import tempfile
import zipfile
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import duckdb

__all__ = [
    "BULK_ITEMS_TABLE",
    "INSTITUTIONS_TABLE",
    "BulkData",
    "BulkFormatError",
    "load_bulk",
]

BULK_ITEMS_TABLE = "tieout_bulk_items"
INSTITUTIONS_TABLE = "tieout_institutions"

_MDRM_RE = re.compile(r"^[A-Z]{4}[A-Z0-9]{4}$")
_DATE_RE = re.compile(r"(?<!\d)(\d{2})(\d{2})(\d{4})(?!\d)")
_RAW_TABLE = BULK_ITEMS_TABLE + "_raw"


class BulkFormatError(ValueError):
    """Raised when files do not look like the FFIEC bulk Call Report data."""


@dataclass
class BulkData:
    """What was read from a bulk Call Report download."""

    report_date: date
    files_read: list[str] = field(default_factory=list)
    files_skipped: list[str] = field(default_factory=list)
    schedules: list[str] = field(default_factory=list)
    institutions: dict[str, dict[str, str]] = field(default_factory=dict)
    """Panel of Reporters records, keyed by RSSD ID."""
    filers: int = 0
    """Institutions with at least one reported item."""
    values: int = 0
    confidential: int = 0
    joined_lines: int = 0
    folded_tabs: int = 0
    skipped_rows: int = 0
    """Data rows without a numeric IDRSSD (the description rows are not counted)."""
    conflicts: int = 0
    """Items whose value differs between schedules; they are left out."""
    skipped_columns: list[str] = field(default_factory=list)

    def institution_name(self, rssd_id: object) -> str:
        """The institution's name from the Panel of Reporters, if listed."""
        record = self.institutions.get(str(rssd_id), {})
        for key, value in record.items():
            if re.sub(r"[^a-z]", "", key.lower()) == "financialinstitutionname":
                return value
        return ""

    def summary(self) -> str:
        """A short human-readable account of what was read."""
        lines = [
            f"Report date: {self.report_date.isoformat()}",
            f"Files read: {len(self.files_read)} ({len(self.schedules)} schedules)",
            f"Institutions in the Panel of Reporters: {len(self.institutions)}",
            f"Institutions with reported items: {self.filers}",
            f"Reported values: {self.values}",
            f"Confidential values withheld from the public data: {self.confidential}",
        ]
        if self.joined_lines or self.folded_tabs:
            lines.append(
                f"Repairs: {self.joined_lines} line break(s) inside values joined, "
                f"{self.folded_tabs} row(s) with extra tabs folded"
            )
        if self.skipped_rows:
            lines.append(f"Rows skipped (no numeric IDRSSD): {self.skipped_rows}")
        if self.conflicts:
            lines.append(f"Items left out because schedules disagree: {self.conflicts}")
        if self.skipped_columns:
            lines.append(f"Columns that are not MDRM items: {', '.join(self.skipped_columns)}")
        if self.files_skipped:
            lines.append(f"Files skipped: {', '.join(self.files_skipped)}")
        return "\n".join(lines)


def load_bulk(
    connection: duckdb.DuckDBPyConnection,
    path: str | Path,
    *,
    report_date: date | None = None,
) -> BulkData:
    """Load a bulk Call Report download into ``connection``.

    ``path`` is the zip file as downloaded, or a folder with its files. The
    items land in the temporary table ``tieout_bulk_items`` (``rssd_id``,
    ``report_date``, ``mdrm``, ``value``) and the institutions in
    ``tieout_institutions`` (``rssd_id``, ``name``). Nothing else in the
    database changes. The report date is read from the file names unless
    ``report_date`` is given.
    """
    source = Path(path)
    if not source.exists():
        raise BulkFormatError(f"{source} does not exist")
    files = list(_iter_text_files(source))
    if not files:
        raise BulkFormatError(f"{source}: no .txt files found")

    when = report_date or _report_date([name for name, _ in files])
    result = BulkData(report_date=when)
    connection.execute(
        f"CREATE OR REPLACE TEMP TABLE {_RAW_TABLE} (rssd_id BIGINT, mdrm VARCHAR, value VARCHAR)"
    )
    skipped_columns: set[str] = set()

    with tempfile.TemporaryDirectory(prefix="tieout-bulk-") as workdir:
        for name, data in files:
            base = Path(name).name
            lowered = base.lower()
            if "readme" in lowered:
                continue
            is_por = re.search(r"\bpor\b", lowered) is not None
            if not is_por and "schedule" not in lowered:
                result.files_skipped.append(name)
                continue
            parsed = _parse(data)
            if "IDRSSD" not in parsed.header:
                result.files_skipped.append(name)
                continue
            result.files_read.append(name)
            result.joined_lines += parsed.joined
            result.folded_tabs += parsed.folded
            result.skipped_rows += parsed.skipped
            if is_por:
                _read_por(parsed, result)
                continue
            cleaned = Path(workdir) / f"part-{len(result.files_read)}.csv"
            confidential, extra = _write_wide(parsed, cleaned)
            result.confidential += confidential
            skipped_columns.update(extra)
            _insert_long(connection, cleaned)
            schedule = _schedule_name(base)
            if schedule and schedule not in result.schedules:
                result.schedules.append(schedule)

    if not result.schedules:
        raise BulkFormatError(f"{source}: no schedule files with an IDRSSD column were found")

    connection.execute(
        f"""CREATE OR REPLACE TEMP TABLE {BULK_ITEMS_TABLE} AS
        SELECT rssd_id, DATE '{when.isoformat()}' AS report_date, mdrm, any_value(value) AS value
        FROM {_RAW_TABLE}
        GROUP BY rssd_id, mdrm
        HAVING count(DISTINCT value) = 1"""
    )
    conflicts = connection.execute(
        f"""SELECT count(*) FROM (
            SELECT rssd_id, mdrm FROM {_RAW_TABLE}
            GROUP BY rssd_id, mdrm HAVING count(DISTINCT value) > 1)"""
    ).fetchone()
    result.conflicts = int(conflicts[0]) if conflicts else 0
    connection.execute(f"DROP TABLE {_RAW_TABLE}")
    counts = connection.execute(
        f"SELECT count(*), count(DISTINCT rssd_id) FROM {BULK_ITEMS_TABLE}"
    ).fetchone() or (0, 0)
    result.values, result.filers = int(counts[0]), int(counts[1])
    result.skipped_columns = sorted(skipped_columns)

    connection.execute(
        f"CREATE OR REPLACE TEMP TABLE {INSTITUTIONS_TABLE} (rssd_id BIGINT, name VARCHAR)"
    )
    names = [(int(rssd), result.institution_name(rssd)) for rssd in result.institutions]
    if names:
        connection.executemany(f"INSERT INTO {INSTITUTIONS_TABLE} VALUES (?, ?)", names)
    return result


# --------------------------------------------------------------------------
# Files
# --------------------------------------------------------------------------


@dataclass
class _Parsed:
    header: list[str]
    rows: list[list[str]]
    joined: int = 0
    folded: int = 0
    skipped: int = 0


def _iter_text_files(source: Path) -> Iterator[tuple[str, bytes]]:
    if source.is_dir():
        for path in sorted(source.rglob("*")):
            if path.is_file() and path.suffix.lower() == ".txt":
                yield path.relative_to(source).as_posix(), path.read_bytes()
    elif zipfile.is_zipfile(source):
        with zipfile.ZipFile(source) as archive:
            for info in sorted(archive.infolist(), key=lambda i: i.filename):
                if not info.is_dir() and info.filename.lower().endswith(".txt"):
                    yield info.filename, archive.read(info)
    elif source.suffix.lower() == ".txt":
        yield source.name, source.read_bytes()


def _report_date(names: list[str]) -> date:
    found: set[date] = set()
    for name in names:
        for month, day, year in _DATE_RE.findall(Path(name).name):
            try:
                found.add(date(int(year), int(month), int(day)))
            except ValueError:
                continue
    if len(found) != 1:
        shown = ", ".join(sorted(d.isoformat() for d in found)) or "none"
        raise BulkFormatError(
            f"Could not tell the report date from the file names (found: {shown}); "
            "give the report date explicitly"
        )
    return found.pop()


def _schedule_name(base: str) -> str:
    match = re.search(r"Schedule\s+([^\s(]+)", base, re.IGNORECASE)
    return match.group(1) if match else ""


def _decode(data: bytes) -> str:
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return data.decode("cp1252", errors="replace")


def _clean_name(name: str) -> str:
    return name.strip().strip('"').strip()


def _records(text: str) -> tuple[list[str], bool, int]:
    """Split the text into records, joining line breaks found inside values."""
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    while lines and lines[-1] == "":
        lines.pop()
    if not lines or not lines[0].endswith("\t"):
        return lines, False, 0
    # Every line ends with a tab, so a line that does not was cut short by a
    # line break inside a value.
    records: list[str] = []
    pending: str | None = None
    joined = 0
    for line in lines:
        if pending is None:
            pending = line
        else:
            pending = f"{pending} {line}"
            joined += 1
        if line.endswith("\t"):
            records.append(pending)
            pending = None
    if pending is not None:
        records.append(pending)
    return records, True, joined


def _parse(data: bytes) -> _Parsed:
    """Split a tab-delimited bulk file into its header and data rows."""
    records, tab_terminated, joined = _records(_decode(data))
    if not records:
        return _Parsed([], [])
    header = [_clean_name(name) for name in records[0].split("\t")]
    while header and header[-1] == "":
        header.pop()
    header = [
        name.upper() if name.upper() in ("IDRSSD",) or _MDRM_RE.match(name.upper()) else name
        for name in header
    ]
    parsed = _Parsed(header, [], joined=joined)
    if "IDRSSD" not in header:
        return parsed
    width = len(header)
    id_index = header.index("IDRSSD")
    for number, record in enumerate(records[1:]):
        fields = record.split("\t")
        if tab_terminated and fields and fields[-1] == "":
            fields.pop()
        if len(fields) > width:
            fields = [*fields[: width - 1], " ".join(fields[width - 1 :])]
            parsed.folded += 1
        elif len(fields) < width:
            fields += [""] * (width - len(fields))
        rssd = _clean_name(fields[id_index])
        if not rssd.isdigit():
            if number > 0:  # the first row after the header holds item descriptions
                parsed.skipped += 1
            continue
        fields[id_index] = rssd
        parsed.rows.append(fields)
    return parsed


def _write_wide(parsed: _Parsed, target: Path) -> tuple[int, set[str]]:
    """Write the item columns as a clean CSV; return the confidential count and other columns."""
    keep: list[int] = []
    seen: set[str] = set()
    other: set[str] = set()
    for index, name in enumerate(parsed.header):
        if name == "IDRSSD":
            continue
        if not _MDRM_RE.match(name):
            other.add(name)
        elif name not in seen:
            keep.append(index)
            seen.add(name)
    id_index = parsed.header.index("IDRSSD")
    confidential = 0
    with target.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["IDRSSD", *(parsed.header[i] for i in keep)])
        for fields in parsed.rows:
            values = []
            for index in keep:
                value = fields[index].strip()
                if value.upper() == "CONF":
                    confidential += 1
                    value = ""
                values.append(value)
            writer.writerow([fields[id_index], *values])
    return confidential, other


def _insert_long(connection: duckdb.DuckDBPyConnection, cleaned: Path) -> None:
    literal = "'" + str(cleaned).replace("'", "''") + "'"
    reader = (
        f"read_csv({literal}, header = true, all_varchar = true, delim = ',', "
        "quote = '\"', escape = '\"', max_line_size = 10000000)"
    )
    if len(connection.execute(f"DESCRIBE SELECT * FROM {reader}").fetchall()) < 2:
        return
    connection.execute(
        f"""INSERT INTO {_RAW_TABLE}
        SELECT CAST(IDRSSD AS BIGINT), mdrm, value FROM (
            UNPIVOT (SELECT * FROM {reader})
            ON COLUMNS(* EXCLUDE (IDRSSD)) INTO NAME mdrm VALUE value
        )
        WHERE value IS NOT NULL AND trim(value) <> ''"""
    )


def _read_por(parsed: _Parsed, result: BulkData) -> None:
    id_index = parsed.header.index("IDRSSD")
    for fields in parsed.rows:
        result.institutions[fields[id_index]] = {
            name: fields[i].strip() for i, name in enumerate(parsed.header) if name
        }
