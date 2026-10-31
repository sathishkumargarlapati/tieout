"""The ``tieout`` command line.

::

    tieout extract       TAXONOMY            read the edits from the FFIEC taxonomy
    tieout compile       EDITS               turn edits into readable SQL checks
    tieout check         --data FILE ...     run the checks against your data
    tieout mdrm          MDRM_FILE ITEM ...  look items up in the MDRM dictionary
    tieout bulk          BULK_ZIP            convert the FFIEC bulk Call Report data
    tieout ground-truth  BULK_ZIP ...        run the edits on every public Call Report

Run ``tieout <command> --help`` for the options of each command.
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from collections.abc import Sequence
from datetime import date
from pathlib import Path

import duckdb

from ._version import __version__
from .cdr import BULK_ITEMS_TABLE, BulkFormatError, load_bulk
from .compiler import compile_edits
from .edits import DEFAULT_TYPES, Edit, EditFileError, filter_edits, read_edits_csv, write_edits_csv
from .groundtruth import read_rssd_list, run_ground_truth
from .mdrm import MdrmDictionary, MdrmFormatError
from .runner import CheckReport, Columns, DataError, check
from .taxonomy import TaxonomyError, read_taxonomy

__all__ = ["main"]

_EXIT_OK, _EXIT_FAILURES, _EXIT_ERROR = 0, 1, 2


def _say(message: str = "") -> None:
    print(message, file=sys.stderr)


def _iso_date(text: str) -> date:
    try:
        return date.fromisoformat(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"not a date in YYYY-MM-DD form: {text!r}") from None


def _types(text: str) -> tuple[str, ...] | None:
    values = tuple(part.strip().lower() for part in text.split(",") if part.strip())
    return None if values == ("all",) else values


def _load_edits(path: str, source_pattern: str | None) -> list[Edit]:
    location = Path(path)
    if location.suffix.lower() == ".csv":
        return read_edits_csv(location)
    found = read_taxonomy(location, source_pattern=source_pattern)
    _say(found.summary())
    return found.edits


def _load_dictionary(path: str | None) -> MdrmDictionary | None:
    if not path:
        return None
    dictionary = MdrmDictionary.load(path)
    _say(f"MDRM dictionary: {len(dictionary)} rows")
    return dictionary


def _write_text(text: str, out: str | None) -> None:
    if out:
        Path(out).write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text)


# -- commands -------------------------------------------------------------------


def _cmd_extract(args: argparse.Namespace) -> int:
    found = read_taxonomy(args.taxonomy, source_pattern=args.source)
    _say(found.summary())
    edits = filter_edits(found.edits, args.types)
    if not found.edits:
        _say("No edits were found. Is this the Call Report taxonomy from the FFIEC CDR?")
        return _EXIT_FAILURES
    if args.out:
        write_edits_csv(edits, args.out)
        _say(f"Wrote {len(edits)} edits to {args.out}")
    else:
        write_edits_csv(edits, sys.stdout)
    return _EXIT_OK


def _cmd_compile(args: argparse.Namespace) -> int:
    edits = filter_edits(_load_edits(args.edits, args.source), args.types)
    dictionary = _load_dictionary(args.mdrm)
    result = compile_edits(edits, dictionary=dictionary, as_of=args.as_of)
    _write_text(result.to_sql_script(generated_from=Path(args.edits).name), args.out)
    _say(f"Compiled {len(result.checks)} check(s); {len(result.skipped)} edit(s) not compiled.")
    _report_skipped(result.skipped)
    _report_warnings(result.warnings)
    if args.out:
        _say(f"Wrote {args.out}")
    return _EXIT_OK


def _cmd_check(args: argparse.Namespace) -> int:
    if (args.data is None) == (args.table is None):
        raise DataError("Give --data FILE, or --database FILE with --table NAME")
    if args.table is not None and args.database is None:
        raise DataError("--table needs --database")
    edits = _load_edits(args.edits, args.source)
    dictionary = _load_dictionary(args.mdrm)
    connection = duckdb.connect(args.database, read_only=True) if args.database else None
    report = check(
        args.data,
        edits,
        table=args.table,
        connection=connection,
        columns=Columns(
            entity=args.entity_column,
            report_date=args.date_column,
            item=args.item_column,
            value=args.value_column,
        ),
        report_date=args.report_date,
        types=args.types,
        dictionary=dictionary,
    )
    if args.out:
        report.write_csv(args.out)
    _print_failures(report, limit=0 if args.out else args.show)
    _say(report.summary())
    _report_skipped(report.skipped)
    _report_warnings(report.warnings)
    for edit_id, message in report.execution_errors:
        _say(f"  {edit_id}: {message}")
    if args.out:
        _say(f"Wrote {len(report.failures)} failure(s) to {args.out}")
    if report.periods_checked == 0:
        _say("No institution report dates matched; nothing was checked.")
    if report.execution_errors:
        return _EXIT_ERROR
    if args.fail_on == "any" and report.failures:
        return _EXIT_FAILURES
    if args.fail_on == "validity" and report.count(edit_type="validity"):
        return _EXIT_FAILURES
    return _EXIT_OK


def _cmd_mdrm(args: argparse.Namespace) -> int:
    dictionary = MdrmDictionary.load(args.mdrm_file)
    missing = 0
    for wanted in args.items:
        rows = dictionary.get(wanted, args.as_of)
        if not rows:
            missing += 1
            when = f" on {args.as_of}" if args.as_of else ""
            print(f"{wanted.upper()}: not found{when}")
            continue
        for row in sorted(rows, key=lambda r: r.start_date or date.min):
            start = row.start_date.isoformat() if row.start_date else "?"
            end = (
                "open"
                if (row.end_date is None or row.end_date.year == 9999)
                else (row.end_date.isoformat())
            )
            confidential = {True: "confidential", False: "public"}.get(row.confidential, "?")
            forms = ", ".join(row.reporting_forms) or "-"
            print(f"{row.mdrm}  {row.item_name}")
            print(
                f"    type {row.item_type or '?'} ({row.item_type_label or 'unknown'}); "
                f"{confidential}; in effect {start} to {end}; forms: {forms}"
            )
    return _EXIT_FAILURES if missing else _EXIT_OK


def _cmd_bulk(args: argparse.Namespace) -> int:
    connection = duckdb.connect()
    bulk = load_bulk(connection, args.bulk, report_date=args.report_date)
    _say(bulk.summary())
    out = Path(args.out)
    target = "'" + str(out).replace("'", "''") + "'"
    if out.suffix.lower() == ".csv":
        options = "FORMAT csv, HEADER true"
    elif out.suffix.lower() in (".parquet", ".pq"):
        options = "FORMAT parquet"
    else:
        raise DataError(f"{out}: write a .parquet or .csv file")
    connection.execute(
        f"COPY (SELECT * FROM {BULK_ITEMS_TABLE} ORDER BY rssd_id, mdrm) TO {target} ({options})"
    )
    _say(f"Wrote {bulk.values:,} values for {bulk.filers:,} institutions to {out}")
    return _EXIT_OK


def _cmd_ground_truth(args: argparse.Namespace) -> int:
    edits = _load_edits(args.edits, args.source)
    dictionary = _load_dictionary(args.mdrm)
    connection = duckdb.connect()
    bulk = load_bulk(connection, args.bulk, report_date=args.report_date)
    _say(bulk.summary())
    only = read_rssd_list(args.only_rssd) if args.only_rssd else None
    report = run_ground_truth(
        connection,
        edits,
        report_date=bulk.report_date,
        types=args.types,
        dictionary=dictionary,
        only_rssd=only,
        bulk=bulk,
    )
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "ground-truth.md").write_text(
        report.to_markdown(edits_source=Path(args.edits).name), encoding="utf-8"
    )
    report.write_csv(out_dir / "ground-truth.csv")
    failures = CheckReport(failures=report.all_failures())
    failures.write_csv(out_dir / "failures.csv")
    _say(report.summary())
    _report_skipped(report.skipped)
    for edit_id, message in report.execution_errors:
        _say(f"  {edit_id}: {message}")
    _say(f"Wrote ground-truth.md, ground-truth.csv and failures.csv to {out_dir}")
    return _EXIT_ERROR if report.execution_errors else _EXIT_OK


# -- output helpers ---------------------------------------------------------------


def _report_skipped(skipped: Sequence) -> None:
    if not skipped:
        return
    reasons = Counter(item.reason for item in skipped)
    _say("Edits not compiled, by reason:")
    for reason, count in reasons.most_common(10):
        _say(f"  {count:>5}  {reason}")


def _report_warnings(warnings: Sequence[str]) -> None:
    if not warnings:
        return
    _say(f"Warnings ({len(warnings)}):")
    for warning in warnings[:20]:
        _say(f"  {warning}")
    if len(warnings) > 20:
        _say(f"  ... and {len(warnings) - 20} more")


def _print_failures(report: CheckReport, limit: int) -> None:
    if limit <= 0 or not report.failures:
        return
    header = ("edit_id", "edit_type", "rssd_id", "report_date", "status", "item_values")
    rows = [
        (
            f.edit_id,
            f.edit_type,
            str(f.rssd_id),
            str(f.report_date),
            f.status,
            f.item_values if len(f.item_values) <= 90 else f.item_values[:87] + "...",
        )
        for f in report.failures[:limit]
    ]
    widths = [max(len(header[i]), *(len(r[i]) for r in rows)) for i in range(len(header) - 1)]
    for line in (header, *rows):
        cells = [line[i].ljust(widths[i]) for i in range(len(widths))] + [line[-1]]
        print("  ".join(cells).rstrip())
    if len(report.failures) > limit:
        print(f"... {len(report.failures) - limit} more; use --out FILE to save every failure.")


# -- parser -------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tieout",
        description="Run the FFIEC Call Report edits as plain SQL, before filing day.",
    )
    parser.add_argument("--version", action="version", version=f"tieout {__version__}")
    commands = parser.add_subparsers(dest="command", required=True, metavar="COMMAND")

    types_help = (
        "comma-separated edit types to keep, or 'all' (default: " + ",".join(DEFAULT_TYPES) + ")"
    )
    source_help = "only read taxonomy files whose path matches this pattern, e.g. '*041*'"

    extract = commands.add_parser(
        "extract",
        help="read the edits from the FFIEC Call Report taxonomy into a CSV file",
        description="Read the edits from the FFIEC Call Report taxonomy (zip file or folder) "
        "into a reviewable CSV file.",
    )
    extract.add_argument("taxonomy", help="the taxonomy zip file or folder from the FFIEC CDR")
    extract.add_argument("--out", help="CSV file to write (default: standard output)")
    extract.add_argument(
        "--types",
        type=_types,
        default=None,
        help=types_help.replace("(default: " + ",".join(DEFAULT_TYPES) + ")", "(default: all)"),
    )
    extract.add_argument("--source", help=source_help)
    extract.set_defaults(handler=_cmd_extract)

    compile_ = commands.add_parser(
        "compile",
        help="turn edits into readable SQL checks",
        description="Compile edits (a CSV file from 'tieout extract', or the taxonomy itself) "
        "into a SQL script with one readable check per edit.",
    )
    compile_.add_argument("edits", help="an edits CSV file, or the taxonomy zip file or folder")
    compile_.add_argument("--out", help="SQL file to write (default: standard output)")
    compile_.add_argument("--types", type=_types, default=DEFAULT_TYPES, help=types_help)
    compile_.add_argument("--source", help=source_help)
    compile_.add_argument("--mdrm", help="MDRM.zip or MDRM_CSV.csv, to name the items")
    compile_.add_argument("--as-of", type=_iso_date, help="report date for MDRM lookups")
    compile_.set_defaults(handler=_cmd_compile)

    check_ = commands.add_parser(
        "check",
        help="run the checks against your data",
        description="Run the edits against Call Report data: a long table with one row per "
        "institution, report date and MDRM item.",
    )
    check_.add_argument("--data", help="CSV or Parquet file with the data to check")
    check_.add_argument("--database", help="DuckDB database file holding --table")
    check_.add_argument("--table", help="table or view in --database holding the data")
    check_.add_argument(
        "--edits", required=True, help="an edits CSV file, or the taxonomy zip file or folder"
    )
    check_.add_argument("--types", type=_types, default=DEFAULT_TYPES, help=types_help)
    check_.add_argument("--source", help=source_help)
    check_.add_argument("--report-date", type=_iso_date, help="check only this report date")
    check_.add_argument("--mdrm", help="MDRM.zip or MDRM_CSV.csv, to flag unknown items")
    check_.add_argument("--out", help="CSV file for every failure")
    check_.add_argument(
        "--fail-on",
        choices=("validity", "any", "never"),
        default="validity",
        help="exit with status 1 when these edits fail (default: validity)",
    )
    check_.add_argument("--show", type=int, default=50, help="failures to print (default: 50)")
    check_.add_argument("--entity-column", default="rssd_id", help="default: rssd_id")
    check_.add_argument("--date-column", default="report_date", help="default: report_date")
    check_.add_argument("--item-column", default="mdrm", help="default: mdrm")
    check_.add_argument("--value-column", default="value", help="default: value")
    check_.set_defaults(handler=_cmd_check)

    mdrm = commands.add_parser(
        "mdrm",
        help="look items up in the Federal Reserve's MDRM dictionary",
        description="Look items up in the MDRM dictionary (MDRM.zip from the Federal Reserve).",
    )
    mdrm.add_argument("mdrm_file", help="MDRM.zip or MDRM_CSV.csv")
    mdrm.add_argument("items", nargs="+", help="MDRM identifiers, such as RCON2170")
    mdrm.add_argument("--as-of", type=_iso_date, help="show only rows in effect on this date")
    mdrm.set_defaults(handler=_cmd_mdrm)

    bulk = commands.add_parser(
        "bulk",
        help="convert the FFIEC bulk Call Report data to TieOut's format",
        description="Convert the FFIEC CDR bulk download 'Call Reports -- Single Period' "
        "(tab-delimited zip file or its folder) into one long table: rssd_id, report_date, "
        "mdrm and value.",
    )
    bulk.add_argument("bulk", help="the bulk zip file from the FFIEC CDR, or its folder")
    bulk.add_argument("--out", required=True, help="a .parquet or .csv file to write")
    bulk.add_argument("--report-date", type=_iso_date, help="default: read from the file names")
    bulk.set_defaults(handler=_cmd_bulk)

    ground = commands.add_parser(
        "ground-truth",
        help="run the edits on every institution's public Call Report",
        description="Run the edits on every institution's public Call Report for one quarter "
        "and report how often TieOut agrees with the filed reports.",
    )
    ground.add_argument("bulk", help="the bulk zip file from the FFIEC CDR, or its folder")
    ground.add_argument(
        "--edits", required=True, help="an edits CSV file, or the taxonomy zip file or folder"
    )
    ground.add_argument(
        "--types", type=_types, default=("validity",), help="edit types to run (default: validity)"
    )
    ground.add_argument("--source", help=source_help)
    ground.add_argument("--report-date", type=_iso_date, help="default: read from the file names")
    ground.add_argument("--mdrm", help="MDRM.zip or MDRM_CSV.csv, to flag unknown items")
    ground.add_argument(
        "--only-rssd", help="a file listing the RSSD IDs to check, one per line (first column)"
    )
    ground.add_argument("--out-dir", default="ground-truth", help="default: ./ground-truth")
    ground.set_defaults(handler=_cmd_ground_truth)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the command line and return its exit status."""
    args = build_parser().parse_args(argv)
    try:
        return int(args.handler(args))
    except (
        BulkFormatError,
        DataError,
        EditFileError,
        MdrmFormatError,
        TaxonomyError,
        FileNotFoundError,
        duckdb.Error,
    ) as exc:
        _say(f"tieout: error: {exc}")
        return _EXIT_ERROR


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
