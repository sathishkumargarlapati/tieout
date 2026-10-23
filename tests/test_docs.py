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
