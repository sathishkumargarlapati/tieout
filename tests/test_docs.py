"""Keep the documentation honest: the examples it shows must match what TieOut does."""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path

import duckdb
import pytest

from tieout.cli import main
from tieout.compiler import _period_date_sql
from tieout.expr import parse_period

ROOT = Path(__file__).resolve().parents[1]


def test_readme_example_output_is_real(capsys: pytest.CaptureFixture[str]) -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    section = readme.split("## Get started", 1)[1]
    shown = re.search(r"```text\n(.*?)```", section, re.DOTALL)
    assert shown is not None
    status = main(
        [
            "check",
            "--data",
            str(ROOT / "examples" / "items.csv"),
            "--edits",
            str(ROOT / "examples" / "edits.csv"),
            "--report-date",
            "2026-06-30",
        ]
    )
    assert status == 1
    captured = capsys.readouterr()
    assert captured.out + captured.err == shown.group(1)


@pytest.mark.parametrize(
    ("written", "reads"),
    [
        ("P0", date(2026, 12, 31)),
        ("-P1Q", date(2026, 9, 30)),
        ("-P2Q", date(2026, 6, 30)),
        ("-P1Y", date(2025, 12, 31)),
        ("-P1M", date(2026, 11, 30)),
        ("-P1D", date(2026, 12, 30)),
        ("-P1Y/-06-end", date(2025, 6, 30)),
        ("-P1Y/06-end", date(2025, 6, 30)),
    ],
)
def test_period_table_in_the_formula_guide(written: str, reads: date) -> None:
    guide = (ROOT / "docs" / "rule-language.md").read_text(encoding="utf-8")
    assert f"`[{written}]`" in guide or written == "P0"
    period = parse_period(written)
    assert period is not None
    sql = _period_date_sql(period, "DATE '2026-12-31'")
    assert duckdb.connect().execute(f"SELECT {sql}").fetchone()[0] == reads


def test_conflict_example_in_the_data_guide_is_real(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    guide = (ROOT / "docs" / "your-data.md").read_text(encoding="utf-8")
    section = guide.split("## One value per item", 1)[1]
    shown = re.search(r"```text\n(.*?)```", section, re.DOTALL)
    assert shown is not None
    data = tmp_path / "items.csv"
    data.write_text(
        "rssd_id,report_date,mdrm,value\n"
        "1,2026-06-30,RCON2160,1\n1,2026-06-30,RCON2160,2\n"
        "2,2026-06-30,RCON2170,5\n2,2026-06-30,RCON2170,7\n",
        encoding="utf-8",
    )
    status = main(["check", "--data", str(data), "--edits", str(ROOT / "examples" / "edits.csv")])
    assert status == 2
    assert capsys.readouterr().err == shown.group(1)
