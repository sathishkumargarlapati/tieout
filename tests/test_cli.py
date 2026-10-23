from __future__ import annotations

from pathlib import Path

import duckdb
import pytest

from tieout.cli import main

from .test_mdrm import write_mdrm
from .test_taxonomy import write_taxonomy

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE_EDITS = ROOT / "examples" / "edits.csv"
EXAMPLE_ITEMS = ROOT / "examples" / "items.csv"


def test_version(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as raised:
        main(["--version"])
    assert raised.value.code == 0
    assert capsys.readouterr().out.startswith("tieout ")


def test_extract_then_compile_then_check(tmp_path: Path) -> None:
    taxonomy = write_taxonomy(tmp_path)
    edits = tmp_path / "edits.csv"
    assert main(["extract", str(taxonomy), "--out", str(edits)]) == 0
    assert "R0001.0001,validity,cc:RCON2160[P0] >= 0" in edits.read_text(encoding="utf-8")

    sql = tmp_path / "checks.sql"
    assert main(["compile", str(edits), "--out", str(sql)]) == 0
    assert "-- Edit R0001.0001 (validity)" in sql.read_text(encoding="utf-8")

    data = tmp_path / "items.csv"
    data.write_text("rssd_id,report_date,mdrm,value\n1,2026-06-30,RCON2160,-1\n", encoding="utf-8")
    assert main(["check", "--data", str(data), "--edits", str(edits)]) == 1


def test_extract_to_standard_output(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["extract", str(write_taxonomy(tmp_path)), "--types", "quality"]) == 0
    captured = capsys.readouterr()
    assert captured.out.splitlines()[1].startswith("R0002.0002,quality,")
    assert len(captured.out.splitlines()) == 2
    assert "Formulas found: 3" in captured.err


def test_extract_finds_nothing(tmp_path: Path) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    assert main(["extract", str(empty)]) == 1


def test_compile_straight_from_the_taxonomy(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["compile", str(write_taxonomy(tmp_path)), "--types", "all"]) == 0
    captured = capsys.readouterr()
    assert captured.out.count("-- Edit R000") == 3
    assert "Compiled 3 check(s); 0 edit(s) not compiled." in captured.err


def test_compile_with_mdrm_names(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    mdrm = write_mdrm(tmp_path)
    args = ["compile", str(EXAMPLE_EDITS), "--mdrm", str(mdrm), "--as-of", "2026-06-30"]
    assert main(args) == 0
    captured = capsys.readouterr()
    assert "--   RCON2170  Total assets" in captured.out
    assert "RCON2160 is not in the MDRM dictionary (used by EX-01)" in captured.err


def test_check_example_exit_codes(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    base = ["check", "--data", str(EXAMPLE_ITEMS), "--edits", str(EXAMPLE_EDITS)]
    assert main(base) == 1
    printed = capsys.readouterr()
    assert "EX-01    validity   9000002  2026-06-30   fail    RCON2160@P0=-25" in printed.out
    assert "Failures: 5 (quality 3, validity 2)." in printed.err
    assert main([*base, "--fail-on", "never"]) == 0
    assert main([*base, "--types", "quality"]) == 0
    assert main([*base, "--types", "quality", "--fail-on", "any"]) == 1


def test_check_writes_every_failure(tmp_path: Path) -> None:
    out = tmp_path / "failures.csv"
    args = [
        "check",
        "--data",
        str(EXAMPLE_ITEMS),
        "--edits",
        str(EXAMPLE_EDITS),
        "--report-date",
        "2026-06-30",
        "--out",
        str(out),
        "--fail-on",
        "never",
    ]
    assert main(args) == 0
    assert len(out.read_text(encoding="utf-8").splitlines()) == 6


def test_check_a_duckdb_table_without_changing_the_database(tmp_path: Path) -> None:
    database = tmp_path / "bank.duckdb"
    with duckdb.connect(str(database)) as connection:
        connection.execute(
            f"CREATE TABLE call_items AS SELECT * FROM read_csv('{EXAMPLE_ITEMS}', header = true)"
        )
    args = [
        "check",
        "--database",
        str(database),
        "--table",
        "call_items",
        "--edits",
        str(EXAMPLE_EDITS),
    ]
    assert main(args) == 1
    with duckdb.connect(str(database), read_only=True) as connection:
        assert connection.execute("SHOW TABLES").fetchall() == [("call_items",)]


@pytest.mark.parametrize(
    "args",
    [
        ["check", "--edits", "x.csv"],
        ["check", "--data", "a.csv", "--table", "t", "--edits", "x.csv"],
        ["check", "--table", "t", "--edits", "x.csv"],
        ["check", "--data", "missing.csv", "--edits", str(EXAMPLE_EDITS)],
        ["compile", "missing.csv"],
    ],
)
def test_input_errors_exit_with_status_2(args: list[str]) -> None:
    assert main(args) == 2


def test_bad_date_argument() -> None:
    with pytest.raises(SystemExit) as raised:
        main(["check", "--data", "a.csv", "--edits", "b.csv", "--report-date", "June"])
    assert raised.value.code == 2


def test_mdrm_lookup(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    mdrm = write_mdrm(tmp_path)
    assert main(["mdrm", str(mdrm), "rcon2170"]) == 0
    out = capsys.readouterr().out
    assert "RCON2170  Total assets" in out
    assert "type F (Financial/reported); public; in effect 2001-03-31 to open" in out
    assert "forms: FFIEC 031, FFIEC 041, FFIEC 051" in out
    assert main(["mdrm", str(mdrm), "RCON1234", "--as-of", "2015-06-30"]) == 1
    assert "RCON1234: not found on 2015-06-30" in capsys.readouterr().out
