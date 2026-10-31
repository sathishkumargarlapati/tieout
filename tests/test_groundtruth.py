from __future__ import annotations

import io
from datetime import date
from pathlib import Path

import duckdb
import pytest

from tieout import Edit
from tieout.cdr import load_bulk
from tieout.cli import main
from tieout.groundtruth import read_rssd_list, run_ground_truth

from .bulk_fixture import write_bulk_zip

EDITS = [
    Edit("V1", "validity", "cc:RCON2160[P0] >= 0", "Must not be negative."),
    Edit("V2", "validity", "cc:RCON2170[P0] >= cc:RCON2948[P0]"),
    Edit("V3", "validity", "cc:RIAD4468[P0] > 0"),
    Edit("V4", "validity", "cc:RCONZZZZ[P0] > 0"),
    Edit("Q1", "quality", "cc:RCON2170[P0] <> 1000"),
    Edit("U1", "validity", "Foo(cc:RCON2170[P0])"),
]


@pytest.fixture
def loaded(tmp_path: Path):
    connection = duckdb.connect()
    bulk = load_bulk(connection, write_bulk_zip(tmp_path))
    return connection, bulk


def outcome(report, edit_id: str):
    return next(o for o in report.outcomes if o.edit.edit_id == edit_id)


def test_counts(loaded) -> None:
    connection, bulk = loaded
    report = run_ground_truth(connection, EDITS, bulk=bulk)
    assert report.report_date == date(2026, 6, 30)
    assert report.institutions == 3
    assert [o.edit.edit_id for o in report.outcomes] == ["V1", "V2", "V3", "V4"]
    assert [(s.edit.edit_id, s.reason) for s in report.skipped] == [
        ("U1", "the function Foo() is not supported yet")
    ]
    v1 = outcome(report, "V1")
    assert (v1.evaluated, v1.passed, v1.failed, v1.errors, v1.not_run) == (2, 1, 1, 0, 1)
    assert [(f.rssd_id, f.item_values) for f in v1.failures] == [(1003, "RCON2160@P0=-5")]
    # Institution 1002's RCON2170 is left out (its schedules disagree); 1003 has no RCON2948.
    v2 = outcome(report, "V2")
    assert (v2.evaluated, v2.passed, v2.not_run) == (1, 1, 2)
    assert outcome(report, "V4").evaluated == 0
    assert (report.evaluations, report.passes, report.failures, report.errors) == (5, 4, 1, 0)
    assert report.agreement == pytest.approx(0.8)
    assert [o.edit.edit_id for o in report.edits_with_failures()] == ["V1"]
    assert [o.edit.edit_id for o in report.edits_never_run()] == ["V4"]


def test_other_edit_types(loaded) -> None:
    connection, bulk = loaded
    report = run_ground_truth(connection, EDITS, bulk=bulk, types=("quality",))
    q1 = outcome(report, "Q1")
    assert (q1.evaluated, q1.passed, q1.failed) == (2, 1, 1)


def test_only_some_institutions(loaded) -> None:
    connection, bulk = loaded
    report = run_ground_truth(connection, EDITS, bulk=bulk, only_rssd={"1001", "1002"})
    assert report.institutions == 2
    v1 = outcome(report, "V1")
    assert (v1.evaluated, v1.passed, v1.failed, v1.not_run) == (1, 1, 0, 1)


def test_markdown_report(loaded) -> None:
    connection, bulk = loaded
    text = run_ground_truth(connection, EDITS, bulk=bulk).to_markdown(edits_source="edits.csv")
    assert text.startswith("# TieOut Ground Truth: public Call Reports for 2026-06-30")
    assert "Of 5 evaluations, 80.00% agreed with the filed reports." in text
    assert "| V1 | validity | 1 | 0 | 2 | RSSD 1003 (THIRD EXAMPLE BANK): RCON2160@P0=-5 |" in text
    assert "- **V1**: `cc:RCON2160[P0] >= 0`" in text
    assert "1 edits never ran because an item they use was not reported" in text
    assert "| the function Foo() is not supported yet | 1 |" in text
    assert "- Edits: edits.csv" in text
    assert "- Confidential values withheld from the public data: 1" in text


def test_clean_report(loaded) -> None:
    connection, bulk = loaded
    report = run_ground_truth(connection, EDITS[1:3], bulk=bulk)
    assert report.agreement == 1.0
    assert "None. Every edit that ran agreed with every filed report." in report.to_markdown()


def test_csv(loaded) -> None:
    connection, bulk = loaded
    out = io.StringIO()
    run_ground_truth(connection, EDITS, bulk=bulk).write_csv(out)
    lines = out.getvalue().splitlines()
    assert lines[0] == "edit_id,edit_type,evaluated,passed,failed,errors,not_run,expression"
    assert lines[1] == "V1,validity,2,1,1,0,1,cc:RCON2160[P0] >= 0"


def test_summary(loaded) -> None:
    connection, bulk = loaded
    summary = run_ground_truth(connection, EDITS, bulk=bulk).summary()
    assert "Evaluations: 5; passed 4 (80.00%); failed 1; could not be evaluated 0" in summary


def test_rssd_list(tmp_path: Path) -> None:
    path = tmp_path / "banks.csv"
    path.write_text('IDRSSD,name\n"1001",First\n1002\tSecond\n\nnot a number\n', encoding="utf-8")
    assert read_rssd_list(path) == {"1001", "1002"}


def test_cli_bulk_and_ground_truth(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    bulk = write_bulk_zip(tmp_path)
    parquet = tmp_path / "items.parquet"
    assert main(["bulk", str(bulk), "--out", str(parquet)]) == 0
    count = duckdb.connect().execute(f"SELECT count(*) FROM read_parquet('{parquet}')").fetchone()
    assert count == (14,)
    csv_out = tmp_path / "items.csv"
    assert main(["bulk", str(bulk), "--out", str(csv_out)]) == 0
    assert csv_out.read_text(encoding="utf-8").startswith("rssd_id,report_date,mdrm,value\n")
    assert main(["bulk", str(bulk), "--out", str(tmp_path / "items.json")]) == 2

    edits = tmp_path / "edits.csv"
    edits.write_text(
        "edit_id,edit_type,expression\nV1,validity,cc:RCON2160[P0] >= 0\n"
        "V2,validity,cc:RCON2170[P0] >= cc:RCON2948[P0]\n",
        encoding="utf-8",
    )
    out_dir = tmp_path / "results"
    args = ["ground-truth", str(bulk), "--edits", str(edits), "--out-dir", str(out_dir)]
    assert main(args) == 0
    assert "agreed with the filed reports" in (out_dir / "ground-truth.md").read_text("utf-8")
    assert (out_dir / "ground-truth.csv").read_text("utf-8").count("\n") == 3
    failures = (out_dir / "failures.csv").read_text("utf-8").splitlines()
    assert failures[1].startswith("V1,validity,1003,2026-06-30,fail,RCON2160@P0=-5")
    assert "Evaluations: 3; passed 2 (66.67%)" in capsys.readouterr().err

    banks = tmp_path / "banks.txt"
    banks.write_text("1001\n", encoding="utf-8")
    assert main([*args, "--only-rssd", str(banks)]) == 0
    assert "Institutions checked | 1 |" in (out_dir / "ground-truth.md").read_text("utf-8")


def test_cli_bad_bulk_input(tmp_path: Path) -> None:
    assert main(["bulk", str(tmp_path / "missing.zip"), "--out", str(tmp_path / "x.parquet")]) == 2
