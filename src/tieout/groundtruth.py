"""Ground Truth: check TieOut against every institution's public Call Report.

A Call Report must pass every validity edit before it is filed. So the
public Call Reports of all filers for a quarter form a natural test set for
TieOut: run the compiled validity edits on them and count how often TieOut
agrees with the reports the agencies accepted. Every disagreement is a lead
to investigate: a gap in TieOut, or a difference between the report as filed
and the public bulk data, such as an item that is not published.

The data comes from the FFIEC's bulk download (see :mod:`tieout.cdr`), and
the edits from the FFIEC taxonomy for the same report date (see
:mod:`tieout.taxonomy`).
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

from ._version import __version__
from .cdr import BULK_ITEMS_TABLE, BulkData
from .compiler import ITEMS_RELATION, PERIODS_RELATION, SkippedEdit, compile_edits
from .edits import Edit, filter_edits
from .mdrm import MdrmDictionary
from .runner import Failure, load_items

__all__ = ["EditOutcome", "GroundTruthReport", "read_rssd_list", "run_ground_truth"]

OUTCOME_COLUMNS = (
    "edit_id",
    "edit_type",
    "evaluated",
    "passed",
    "failed",
    "errors",
    "not_run",
    "expression",
)


@dataclass(frozen=True)
class EditOutcome:
    """How one edit fared across all institutions."""

    edit: Edit
    evaluated: int
    """Institutions where the edit ran: every item it uses was reported."""
    passed: int
    failed: int
    errors: int
    not_run: int
    """Institutions where an item the edit uses was not reported or is not public."""
    failures: tuple[Failure, ...] = ()


@dataclass
class GroundTruthReport:
    """The result of running edits on every institution's public Call Report."""

    report_date: date
    institutions: int
    outcomes: list[EditOutcome] = field(default_factory=list)
    skipped: list[SkippedEdit] = field(default_factory=list)
    execution_errors: list[tuple[str, str]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    bulk: BulkData | None = None

    @property
    def evaluations(self) -> int:
        return sum(o.evaluated for o in self.outcomes)

    @property
    def passes(self) -> int:
        return sum(o.passed for o in self.outcomes)

    @property
    def failures(self) -> int:
        return sum(o.failed for o in self.outcomes)

    @property
    def errors(self) -> int:
        return sum(o.errors for o in self.outcomes)

    @property
    def not_run(self) -> int:
        return sum(o.not_run for o in self.outcomes)

    @property
    def agreement(self) -> float | None:
        """Share of evaluations that passed, or ``None`` when nothing ran."""
        return self.passes / self.evaluations if self.evaluations else None

    def edits_with_failures(self) -> list[EditOutcome]:
        """Edits that failed or could not be evaluated somewhere, most failures first."""
        found = [o for o in self.outcomes if o.failed or o.errors]
        return sorted(found, key=lambda o: (-(o.failed + o.errors), o.edit.edit_id))

    def edits_never_run(self) -> list[EditOutcome]:
        """Edits that ran nowhere, because an item they use was never reported."""
        return [o for o in self.outcomes if o.evaluated == 0]

    def all_failures(self) -> list[Failure]:
        return [failure for outcome in self.outcomes for failure in outcome.failures]

    def write_csv(self, target: str | Path | TextIO) -> None:
        """Write one row per edit with its counts."""
        if isinstance(target, (str, Path)):
            with Path(target).open("w", encoding="utf-8", newline="") as handle:
                self.write_csv(handle)
            return
        writer = csv.writer(target, lineterminator="\n")
        writer.writerow(OUTCOME_COLUMNS)
        for o in self.outcomes:
            writer.writerow(
                [
                    o.edit.edit_id,
                    o.edit.edit_type,
                    o.evaluated,
                    o.passed,
                    o.failed,
                    o.errors,
                    o.not_run,
                    o.edit.expression,
                ]
            )

    def summary(self) -> str:
        """A few lines for the terminal."""
        rate = f"{self.agreement:.2%}" if self.agreement is not None else "n/a"
        return "\n".join(
            [
                f"Report date: {self.report_date.isoformat()}",
                f"Institutions checked: {self.institutions:,}",
                f"Edits run: {len(self.outcomes):,} ({len(self.skipped):,} not compiled)",
                f"Evaluations: {self.evaluations:,}; passed {self.passes:,} ({rate}); "
                f"failed {self.failures:,}; could not be evaluated {self.errors:,}",
                f"Edits with failures: {len(self.edits_with_failures()):,}; "
                f"edits that never ran: {len(self.edits_never_run()):,}",
            ]
        )

    def to_markdown(self, *, edits_source: str = "", examples: int = 3) -> str:
        """The report as a Markdown document."""
        when = self.report_date.isoformat()
        rate = f"{self.agreement:.2%}" if self.agreement is not None else "not measurable"
        lines = [
            f"# TieOut Ground Truth: public Call Reports for {when}",
            "",
            "A Call Report must pass every validity edit before it is filed. TieOut ran "
            f"{len(self.outcomes):,} compiled edits on the public Call Reports of "
            f"{self.institutions:,} institutions for {when}. Of {self.evaluations:,} "
            f"evaluations, {rate} agreed with the filed reports.",
            "",
            "## Results",
            "",
            "| Measure | Value |",
            "|---|---|",
            f"| Report date | {when} |",
            f"| Institutions checked | {self.institutions:,} |",
            f"| Edits run | {len(self.outcomes):,} |",
            f"| Edits not compiled yet | {len(self.skipped):,} |",
            f"| Evaluations (edit and institution, every item reported) | {self.evaluations:,} |",
            f"| Passed | {self.passes:,} ({rate}) |",
            f"| Failed | {self.failures:,} |",
            f"| Could not be evaluated | {self.errors:,} |",
            f"| Not run: an item was not reported or is not public | {self.not_run:,} |",
            "",
        ]
        failing = self.edits_with_failures()
        lines += ["## Edits that disagreed with filed reports", ""]
        if not failing:
            lines += ["None. Every edit that ran agreed with every filed report.", ""]
        else:
            lines += [
                "Each row is a lead to investigate: a gap in TieOut, or a difference between "
                "the filed report and the public data.",
                "",
                "| Edit | Type | Failed | Errors | Evaluated | Example |",
                "|---|---|---|---|---|---|",
            ]
            for o in failing:
                example = ""
                if o.failures:
                    first = o.failures[0]
                    name = self.bulk.institution_name(first.rssd_id) if self.bulk else ""
                    who = f"RSSD {first.rssd_id}" + (f" ({name})" if name else "")
                    example = f"{who}: {first.item_values}"
                lines.append(
                    f"| {_cell(o.edit.edit_id)} | {_cell(o.edit.edit_type)} | {o.failed:,} | "
                    f"{o.errors:,} | {o.evaluated:,} | {_cell(example)} |"
                )
            lines.append("")
            lines += ["### Formulas of these edits", ""]
            for o in failing:
                lines.append(f"- **{_cell(o.edit.edit_id)}**: `{_code(o.edit.expression)}`")
            lines.append("")
        never = self.edits_never_run()
        if never:
            shown = ", ".join(_cell(o.edit.edit_id) for o in never[:30])
            more = f" and {len(never) - 30:,} more" if len(never) > 30 else ""
            lines += [
                "## Edits that never ran",
                "",
                f"{len(never):,} edits never ran because an item they use was not reported "
                f"by any institution, or is not public: {shown}{more}.",
                "",
            ]
        if self.skipped:
            reasons = Counter(s.reason for s in self.skipped)
            lines += ["## Edits not compiled yet", "", "| Reason | Edits |", "|---|---|"]
            for reason, count in reasons.most_common():
                lines.append(f"| {_cell(reason)} | {count:,} |")
            lines.append("")
        if self.execution_errors:
            lines += ["## Checks that failed to run", ""]
            for edit_id, message in self.execution_errors:
                lines.append(f"- {_cell(edit_id)}: {_cell(message)}")
            lines.append("")
        lines += ["## Data", ""]
        if edits_source:
            lines.append(f"- Edits: {_cell(edits_source)}")
        if self.bulk is not None:
            for line in self.bulk.summary().splitlines():
                lines.append(f"- {_cell(line)}")
        for warning in self.warnings[:20]:
            lines.append(f"- {_cell(warning)}")
        lines += [
            "",
            "## How to read this",
            "",
            "- An edit runs for an institution only when every item it uses is in the public "
            "data, as the FFIEC CDR Interchange Specification requires. Confidential items are "
            "not published, so edits that use them do not run.",
            "- Edits written for one form (FFIEC 031, 041 or 051) can disagree with reports filed "
            "on another form. To check one form, list its filers in a file and pass it with "
            "`--only-rssd`.",
            "- Use the taxonomy for the same report date as the data.",
            "",
            f"Produced by TieOut {__version__}.",
            "",
        ]
        return "\n".join(lines)


def _cell(text: object) -> str:
    return " ".join(str(text).split()).replace("|", "\\|")


def _code(text: str) -> str:
    return " ".join(text.split()).replace("`", "'")


def read_rssd_list(path: str | Path) -> set[str]:
    """Read RSSD IDs from a file: the first column of each line; other lines are ignored."""
    ids: set[str] = set()
    for line in Path(path).read_text(encoding="utf-8-sig").splitlines():
        first = line.replace("\t", ",").split(",")[0].strip().strip('"')
        if first.isdigit():
            ids.add(first)
    return ids


def run_ground_truth(
    connection: duckdb.DuckDBPyConnection,
    edits: Iterable[Edit],
    *,
    table: str = BULK_ITEMS_TABLE,
    report_date: date | None = None,
    types: Sequence[str] | None = ("validity",),
    dictionary: MdrmDictionary | None = None,
    only_rssd: set[str] | None = None,
    bulk: BulkData | None = None,
) -> GroundTruthReport:
    """Run edits on every institution in ``table`` and count the outcomes.

    ``table`` holds the long-format items, by default the table that
    :func:`tieout.cdr.load_bulk` creates. ``types`` selects the edit types to
    run (validity edits by default). ``only_rssd`` limits the run to the
    given institutions.
    """
    when = report_date or (bulk.report_date if bulk else None)
    compiled = compile_edits(filter_edits(edits, types), dictionary=dictionary, as_of=when)
    institutions = load_items(connection, table=table, report_date=when)
    if only_rssd is not None:
        connection.execute("CREATE OR REPLACE TEMP TABLE tieout_only_rssd (rssd_id VARCHAR)")
        connection.executemany(
            "INSERT INTO tieout_only_rssd VALUES (?)", [(i,) for i in sorted(only_rssd)]
        )
        connection.execute(
            f"DELETE FROM {PERIODS_RELATION} WHERE CAST(rssd_id AS VARCHAR) NOT IN "
            "(SELECT rssd_id FROM tieout_only_rssd)"
        )
        row = connection.execute(f"SELECT count(*) FROM {PERIODS_RELATION}").fetchone()
        institutions = int(row[0]) if row else 0
    if when is None:
        row = connection.execute(f"SELECT max(report_date) FROM {ITEMS_RELATION}").fetchone()
        when = row[0] if row and row[0] else date.today()

    report = GroundTruthReport(
        report_date=when,
        institutions=institutions,
        skipped=compiled.skipped,
        warnings=compiled.warnings,
        bulk=bulk,
    )
    for check in compiled.checks:
        try:
            row = connection.execute(check.summary_sql).fetchone()
            _, _, evaluated, passed, failed, errors = row or ("", "", 0, 0, 0, 0)
            failures: tuple[Failure, ...] = ()
            if failed or errors:
                failures = tuple(
                    Failure(
                        edit_id=r[0],
                        edit_type=r[1],
                        rssd_id=r[2],
                        report_date=r[3],
                        status=r[4],
                        item_values=r[5],
                        message=check.edit.message,
                    )
                    for r in connection.execute(check.sql).fetchall()
                )
        except duckdb.Error as exc:
            report.execution_errors.append((check.edit.edit_id, str(exc)))
            continue
        report.outcomes.append(
            EditOutcome(
                edit=check.edit,
                evaluated=int(evaluated),
                passed=int(passed),
                failed=int(failed),
                errors=int(errors),
                not_run=institutions - int(evaluated),
                failures=failures,
            )
        )
    return report
