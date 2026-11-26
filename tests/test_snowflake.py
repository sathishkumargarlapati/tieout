"""The Snowflake dialect: the same checks, written in Snowflake SQL.

No Snowflake account is used. Each check is parsed as Snowflake SQL with sqlglot, then
translated by sqlglot into DuckDB, run on synthetic data, and compared with the DuckDB
check, which the rest of the test suite verifies. sqlglot reproduces Snowflake's own
behavior where the two differ (for example, Snowflake's LEAST returns NULL when any
argument is NULL), so a difference in meaning shows up as a difference in results.
All data in these tests is synthetic.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
import sqlglot

from tieout import Edit, compile_edit, compile_edits, read_edits_csv
from tieout.cli import main
from tieout.compiler import DIALECTS, _period_date_sql
from tieout.expr import parse_period

from .conftest import load_rows

ROOT = Path(__file__).resolve().parents[1]

# Synthetic data: six institutions over four report dates.
ROWS = [
    # RCON2170 total assets, RCON2160 other assets, RCON0081 cash, history for period edits.
    (1, "2025-06-30", "RCON2170", 400),
    (1, "2025-12-31", "RCON2170", 500),
    (1, "2026-03-31", "RCON2170", 500),
    (1, "2026-06-30", "RCON2170", 520),
    (1, "2026-06-30", "RCON2160", 20),
    (1, "2026-06-30", "RCON0081", 500),
    (2, "2025-06-30", "RCON2170", 900),
    (2, "2026-03-31", "RCON2170", 600),
    (2, "2026-06-30", "RCON2170", 600),
    (2, "2026-06-30", "RCON2160", -5),
    (2, "2026-06-30", "RCON0081", 605),
    (3, "2026-03-31", "RCON2170", 10),
    (3, "2026-06-30", "RCON2170", 12.5),
    (3, "2026-06-30", "RCON2160", "n/a"),
    (3, "2026-06-30", "RCON0081", 2.5),
    (4, "2026-06-30", "RCON2170", 7),
    (4, "2026-06-30", "RCON2160", None),
    # Text and TRUE/FALSE items.
    (1, "2026-06-30", "TEXT4087", "Main St"),
    (2, "2026-06-30", "TEXT4087", "  "),
    (3, "2026-06-30", "TEXT4087", "main st"),
    (1, "2026-06-30", "RCONB867", "true"),
    (2, "2026-06-30", "RCONB867", "false"),
    (3, "2026-06-30", "RCONB867", "maybe"),
    (1, "2026-06-30", "RCFDB868", 25),
    (2, "2026-06-30", "RCFDB868", None),
    (5, "2026-06-30", "RCONB867", "true"),
    (6, "2026-06-30", "RCON2170", -3),
    (6, "2026-03-31", "RCON2170", 4),
]

FORMULAS = [
    "cc:RCON2160[P0] >= 0",
    "cc:RCON2170[P0] = cc:RCON2160[P0] + cc:RCON0081[P0]",
    "cc:RCON2170[P0] <> cc:RCON2170[-P1Q]",
    "cc:RCON2170[P0] >= cc:RCON2170[-P1Y/-06-end]",
    "cc:RCON2170[P0] > cc:RCON2170[-P2Q]",
    "cc:RCON2170[P0] >= cc:RCON2170[-P1Y]",
    "cc:RCON2170[P0] >= cc:RCON2170[-P3M]",
    "cc:RCON2170[P0] >= cc:RCON2170[-P91D]",
    "If(cc:RCON2170[-P1Q] > 0, cc:RCON2170[P0] > 0, TRUE)",
    "if( cc:RCON2160[P0] > 0,cc:RCON0081[P0] > 0,TRUE)",
    "If(MonthOf(Context.Period.EndDate) = 6, cc:RCON2170[P0] > 100, TRUE)",
    "cc:RCON2170[P0] > 0 And YearOf(Context.PeriodEndDate) + DayOf(Context.Period.EndDate) = 2056",
    "If(cc:RCONB867[P0] = TRUE, ExistsNonNil(cc:RCFDB868), TRUE)",
    "If(cc:RCONB867[P0] = TRUE, Exists(cc:RCFDB868), TRUE)",
    "ExistsNonEmpty(cc:TEXT4087[P0])",
    "ExistingOf(cc:RCFDB868[P0], cc:RCON2160[P0]) >= 0",
    "Abs(cc:RCON2160[P0]) < 10 Or Sign(cc:RCON2160[P0]) = 1",
    "Round(cc:RCON2170[P0] / 4) = Int(cc:RCON2170[P0] / 4)",
    "Round(cc:RCON0081[P0], 0) = Trunc(cc:RCON0081[P0]) And Truncate(1.25, 1) = 1.2",
    "Sqrt(Abs(cc:RCON2170[P0])) < 20 And Power(2, 3) = 8 And 2 ^ 2 = 4",
    "Sum(cc:RCON2160[P0], cc:RCON0081[P0]) = cc:RCON2170[P0]",
    "Avg(cc:RCON2160[P0], cc:RCON0081[P0]) * 2 = cc:RCON2170[P0]",
    # Min and Max over values that can all be read (see the test of unreadable values below).
    "Max(cc:RCON2170[P0], cc:RCON0081[P0]) > 500",
    "Min(cc:RCON2170[P0], 0) = 0",
    "-cc:RCON2160[P0] < 0 Xor cc:RCON0081[P0] > 500",
    "Not (cc:RCON2170[P0] < 0)",
    "cc:TEXT4087[P0] = 'Main St'",
    "Exact(Upper(Trim(cc:TEXT4087[P0])), 'MAIN ST')",
    "Len(cc:TEXT4087[P0]) > 2 And Lower(Left(cc:TEXT4087[P0], 4)) = 'main'",
    "Right(cc:TEXT4087[P0], 2) & Mid(cc:TEXT4087[P0], 1, 1) <> 'StM'",
    "Concatenate(cc:TEXT4087[P0], Char(33)) <> 'Main St!'",
    "Value('12.5') = cc:RCON2170[P0]",
    "cc:RCONB867[P0] = FALSE",
    "If(cc:RCON2160[P0] > 0, cc:RCON2170[P0], 0) >= 0",
]


def _snowflake_to_duckdb(sql: str) -> str:
    parsed = sqlglot.parse_one(sql, read="snowflake")
    return parsed.sql(dialect="duckdb")


def _results(con, sql: str) -> list[tuple]:
    return con.execute(sql).fetchall()


@pytest.mark.parametrize("formula", FORMULAS)
def test_snowflake_check_finds_what_the_duckdb_check_finds(con, formula: str) -> None:
    load_rows(con, ROWS, report_date="2026-06-30")
    edit = Edit("T-1", "validity", formula)
    duck = compile_edit(edit)
    snow = compile_edit(edit, dialect="snowflake")
    assert _results(con, _snowflake_to_duckdb(snow.sql)) == _results(con, duck.sql)
    summary = _results(con, _snowflake_to_duckdb(snow.summary_sql))
    assert summary == _results(con, duck.summary_sql)


def test_the_comparison_finds_something() -> None:
    """Guard against a comparison that passes only because both checks found nothing."""
    import duckdb

    con = duckdb.connect()
    load_rows(con, ROWS, report_date="2026-06-30")
    statuses: set[str] = set()
    for formula in FORMULAS:
        sql = compile_edit(Edit("T-1", "validity", formula), dialect="snowflake").sql
        statuses.update(row[4] for row in con.execute(_snowflake_to_duckdb(sql)).fetchall())
    assert statuses == {"fail", "error"}


def test_an_unreadable_number_inside_min_or_max_is_an_error_in_snowflake(con) -> None:
    """Snowflake's LEAST and GREATEST return NULL when any argument is NULL; DuckDB's skip it.

    Institution 3 reports RCON2160 as 'n/a'. In Snowflake the edit cannot be evaluated and is
    reported as an error. In DuckDB, greatest() skips the unreadable value and the edit passes.
    """
    load_rows(con, ROWS, report_date="2026-06-30")
    edit = Edit("T-1", "validity", "Max(cc:RCON2160[P0], cc:RCON0081[P0]) <= cc:RCON2170[P0]")
    snow = _results(con, _snowflake_to_duckdb(compile_edit(edit, dialect="snowflake").sql))
    duck = _results(con, compile_edit(edit).sql)
    assert [(row[2], row[4]) for row in snow] == [(2, "fail"), (3, "error")]
    assert [(row[2], row[4]) for row in duck] == [(2, "fail")]


def test_a_division_by_zero_is_an_error_in_snowflake(con) -> None:
    """Snowflake stops a query that divides by zero, so the check divides by NULLIF(x, 0).

    The institution is reported with status error, and the other institutions are still
    checked. DuckDB divides by zero without stopping and gives infinity, so there the
    edit below passes.
    """
    rows = [(1, "2026-06-30", "RCON2170", 10), (1, "2026-06-30", "RCON2160", 0)]
    rows += [(2, "2026-06-30", "RCON2170", 10), (2, "2026-06-30", "RCON2160", 20)]
    load_rows(con, rows)
    edit = Edit("T-1", "validity", "cc:RCON2170[P0] / cc:RCON2160[P0] > 1")
    snow = compile_edit(edit, dialect="snowflake").sql
    assert "/ NULLIF(" in snow
    assert [(row[2], row[4]) for row in _results(con, _snowflake_to_duckdb(snow))] == [
        (1, "error"),
        (2, "fail"),
    ]
    assert [(row[2], row[4]) for row in _results(con, compile_edit(edit).sql)] == [(2, "fail")]


def test_snowflake_sql_uses_no_duckdb_only_syntax() -> None:
    edits = read_edits_csv(ROOT / "examples" / "edits.csv")
    result = compile_edits(
        [*edits, *(Edit(f"T-{n}", "validity", f) for n, f in enumerate(FORMULAS))],
        dialect="snowflake",
    )
    assert result.dialect == "snowflake"
    for check in result.checks:
        for sql in (check.sql, check.summary_sql):
            sqlglot.parse_one(sql, read="snowflake")  # raises on a syntax error
            for duckdb_only in ("FILTER (", "IS NOT TRUE", "INTERVAL", "ANY_VALUE", "make_date"):
                assert duckdb_only not in sql


def test_duckdb_is_the_default_dialect() -> None:
    edit = Edit("T-1", "validity", "cc:RCON2170[P0] <> cc:RCON2170[-P1Q]")
    assert compile_edit(edit).sql == compile_edit(edit, dialect="duckdb").sql
    assert "INTERVAL 3 MONTH" in compile_edit(edit).sql
    assert "DATEADD(month, -3, p.report_date)" in compile_edit(edit, dialect="snowflake").sql


def test_an_unknown_dialect_is_an_error() -> None:
    edit = Edit("T-1", "validity", "cc:RCON2160[P0] >= 0")
    with pytest.raises(ValueError, match="duckdb, snowflake"):
        compile_edit(edit, dialect="oracle")
    with pytest.raises(ValueError, match="duckdb, snowflake"):
        compile_edits([edit], dialect="oracle")


@pytest.mark.parametrize(
    ("written", "reads"),
    [
        ("-P1Q", date(2026, 9, 30)),
        ("-P2Q", date(2026, 6, 30)),
        ("-P1Y", date(2025, 12, 31)),
        ("-P1M", date(2026, 11, 30)),
        ("-P1D", date(2026, 12, 30)),
        ("-P1Y/-06-end", date(2025, 6, 30)),
    ],
)
def test_period_dates_in_snowflake(con, written: str, reads: date) -> None:
    period = parse_period(written)
    assert period is not None
    sql = _period_date_sql(period, "DATE '2026-12-31'", "snowflake")
    assert con.execute(_snowflake_to_duckdb(f"SELECT {sql}")).fetchone()[0] == reads


def test_cli_compiles_for_snowflake(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    out = tmp_path / "checks.sql"
    edits = str(ROOT / "examples" / "edits.csv")
    assert main(["compile", edits, "--dialect", "snowflake", "--out", str(out)]) == 0
    script = out.read_text(encoding="utf-8")
    assert script.startswith("-- TieOut ") and "edit checks for Snowflake" in script.splitlines()[0]
    assert "DATEADD(month, -3, p.report_date)" in script
    capsys.readouterr()
    with pytest.raises(SystemExit):
        main(["compile", edits, "--dialect", "oracle"])
    assert "invalid choice" in capsys.readouterr().err


def test_sql_in_the_snowflake_guide_is_snowflake_sql() -> None:
    import re

    guide = (ROOT / "docs" / "snowflake.md").read_text(encoding="utf-8")
    blocks = re.findall(r"```sql\n(.*?)```", guide, re.DOTALL)
    assert blocks
    for block in blocks:
        for statement in sqlglot.parse(block, read="snowflake"):
            assert statement is not None


def test_every_dialect_is_listed() -> None:
    assert DIALECTS == ("duckdb", "snowflake")
