from __future__ import annotations

import io
from datetime import date
from pathlib import Path

import duckdb
import pytest

from tieout import CheckReport, Columns, DataError, Edit, check, load_items, run_checks
from tieout.compiler import CompiledCheck
from tieout.runner import Failure

EDITS = [
    Edit("A", "validity", "cc:RCON2160[P0] >= 0", "Must not be negative."),
    Edit("B", "quality", "cc:RCON2170[P0] <> cc:RCON2170[-P1Q]", "Explain an unchanged total."),
    Edit("C", "reportability", "ExistsNonNil(cc:RCON2160[P0])"),
    Edit("D", "validity", "Foo(cc:RCON2160[P0])"),
]


def write_csv(path: Path, header: str, rows: list[str]) -> Path:
    path.write_text("\n".join([header, *rows]) + "\n", encoding="utf-8")
    return path


@pytest.fixture
def items(tmp_path: Path) -> Path:
    return write_csv(
        tmp_path / "items.csv",
        "rssd_id,report_date,mdrm,value",
        [
            "1,2026-03-31,RCON2170,500",
            "1,2026-06-30,RCON2170,500",
            "1,2026-06-30,RCON2160,-3",
            "2,2026-06-30,RCON2160,4",
            "2,2026-06-30,rcon2170 ,700",
        ],
    )


def test_check_returns_failures_with_messages(items: Path) -> None:
    report = check(items, EDITS, report_date=date(2026, 6, 30))
    assert report.checks_run == 2
    assert report.periods_checked == 2
    assert [(s.edit.edit_id, s.reason) for s in report.skipped] == [
        ("D", "the function Foo() is not supported yet")
    ]
    assert report.failures == [
        Failure(
            "A",
            "validity",
            "1",
            date(2026, 6, 30),
            "fail",
            "RCON2160@P0=-3",
            "Must not be negative.",
        ),
        Failure(
            "B",
            "quality",
            "1",
            date(2026, 6, 30),
            "fail",
            "RCON2170@P0=500; RCON2170@-P1Q=500",
            "Explain an unchanged total.",
        ),
    ]
    assert report.count() == 2
    assert report.count(edit_type="validity") == 1
    assert report.count(status="error") == 0
    assert report.by_type() == {"validity": 1, "quality": 1}


def test_check_all_types(items: Path) -> None:
    report = check(items, EDITS, types=None)
    assert report.checks_run == 3
    # Edit C fails where RCON2160 was not reported: institution 1 in the first quarter.
    assert [(f.edit_id, f.rssd_id, f.report_date) for f in report.failures if f.edit_id == "C"] == [
        ("C", "1", date(2026, 3, 31))
    ]


def test_report_csv_and_summary(items: Path) -> None:
    report = check(items, EDITS, report_date=date(2026, 6, 30))
    out = io.StringIO()
    report.write_csv(out)
    lines = out.getvalue().splitlines()
    assert lines[0] == "edit_id,edit_type,rssd_id,report_date,status,item_values,message"
    assert lines[1] == "A,validity,1,2026-06-30,fail,RCON2160@P0=-3,Must not be negative."
    summary = report.summary()
    assert "Checked 2 institution report date(s) against 2 edit(s)." in summary
    assert "Failures: 2 (quality 1, validity 1)." in summary
    assert "1 edit(s) were not compiled" in summary


def test_clean_report_summary() -> None:
    assert "Every edit that ran passed." in CheckReport(checks_run=3, periods_checked=1).summary()


def test_parquet_and_custom_column_names(tmp_path: Path) -> None:
    path = tmp_path / "items.parquet"
    connection = duckdb.connect()
    connection.execute(
        f"""COPY (SELECT * FROM (VALUES
                (11, DATE '2026-06-30', 'RCON2160', -1.0),
                (12, DATE '2026-06-30', 'RCON2160', 1.0)
            ) AS t(bank, period_end, item, amount))
        TO '{path}' (FORMAT parquet)"""
    )
    report = check(
        path,
        EDITS[:1],
        columns=Columns(entity="bank", report_date="period_end", item="item", value="amount"),
    )
    assert [(f.rssd_id, f.item_values) for f in report.failures] == [(11, "RCON2160@P0=-1.0")]


def test_table_in_an_existing_connection() -> None:
    connection = duckdb.connect()
    connection.execute(
        "CREATE TABLE call_items AS SELECT * FROM (VALUES "
        "(1, '2026-06-30', 'RCON2160', '-1')) AS t(rssd_id, report_date, mdrm, value)"
    )
    report = check(edits=EDITS[:1], table="call_items", connection=connection)
    assert len(report.failures) == 1


def test_us_style_dates_are_accepted(tmp_path: Path) -> None:
    path = write_csv(
        tmp_path / "items.csv",
        "rssd_id,report_date,mdrm,value",
        ["1,6/30/2026,RCON2160,-1", "1,20260331,RCON2160,1"],
    )
    report = check(path, EDITS[:1])
    assert [f.report_date for f in report.failures] == [date(2026, 6, 30)]
    assert report.periods_checked == 2


def test_missing_columns(tmp_path: Path) -> None:
    path = write_csv(tmp_path / "items.csv", "bank,report_date,mdrm,amount", ["1,2026-06-30,X,1"])
    with pytest.raises(DataError, match="no entity column 'rssd_id', no value column 'value'"):
        check(path, EDITS)


def test_bad_dates(tmp_path: Path) -> None:
    path = write_csv(
        tmp_path / "items.csv", "rssd_id,report_date,mdrm,value", ["1,June 2026,RCON2160,1"]
    )
    with pytest.raises(DataError, match="1 row"):
        check(path, EDITS)


def test_missing_keys(tmp_path: Path) -> None:
    path = write_csv(tmp_path / "items.csv", "rssd_id,report_date,mdrm,value", ["1,2026-06-30,,1"])
    with pytest.raises(DataError, match="no institution identifier or no MDRM item"):
        check(path, EDITS)


def test_conflicting_values_are_rejected(tmp_path: Path) -> None:
    path = write_csv(
        tmp_path / "items.csv",
        "rssd_id,report_date,mdrm,value",
        ["1,2026-06-30,RCON2160,1", "1,2026-06-30,RCON2160,2"],
    )
    with pytest.raises(DataError, match="more than one value"):
        check(path, EDITS)


def test_identical_duplicates_are_accepted(tmp_path: Path) -> None:
    path = write_csv(
        tmp_path / "items.csv",
        "rssd_id,report_date,mdrm,value",
        ["1,2026-06-30,RCON2160,-1", "1,2026-06-30,RCON2160,-1"],
    )
    assert len(check(path, EDITS[:1]).failures) == 1


def test_unsupported_file_type(tmp_path: Path) -> None:
    path = tmp_path / "items.json"
    path.write_text("[]", encoding="utf-8")
    with pytest.raises(DataError, match="CSV or Parquet"):
        check(path, EDITS)


def test_missing_file(tmp_path: Path) -> None:
    with pytest.raises(DataError, match="does not exist"):
        check(tmp_path / "nothing.csv", EDITS)


def test_data_or_table_but_not_both(items: Path) -> None:
    with pytest.raises(DataError, match="either a data file or a table name"):
        load_items(duckdb.connect(), items, table="t")


def test_report_date_with_no_data(items: Path) -> None:
    report = check(items, EDITS, report_date=date(2030, 3, 31))
    assert report.periods_checked == 0
    assert report.failures == []


def test_checks_that_fail_to_run_are_reported(items: Path) -> None:
    connection = duckdb.connect()
    load_items(connection, items)
    broken = CompiledCheck(Edit("Z", "validity", "x"), "SELECT * FROM no_such_table", ())
    failures, errors = run_checks(connection, [broken])
    assert failures == []
    assert errors[0][0] == "Z"
    assert "no_such_table" in errors[0][1]
