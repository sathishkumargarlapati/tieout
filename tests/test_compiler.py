from __future__ import annotations

from datetime import date

import pytest

from tieout import Edit, MdrmDictionary, MdrmItem, compile_edit, compile_edits
from tieout.compiler import UnsupportedFormula
from tieout.expr import FormulaSyntaxError

from .conftest import load_rows, run_formula

Q4_2025, Q1_2026, Q2_2026 = "2025-12-31", "2026-03-31", "2026-06-30"


def test_simple_validity_edit(con) -> None:
    rows = [(1, Q1_2026, "RCON2160", 5), (2, Q1_2026, "RCON2160", -1), (3, Q1_2026, "RCON2160", 0)]
    assert run_formula(con, "cc:RCON2160[P0] >= 0", rows) == [(2, Q1_2026, "fail")]


def test_edit_does_not_run_when_an_item_is_not_reported(con) -> None:
    rows = [(1, Q1_2026, "RCON2170", 100), (2, Q1_2026, "RCON0081", 100)]
    formula = "cc:RCON2170[P0] = cc:RCON0081[P0]"
    assert run_formula(con, formula, rows) == []


def test_reported_null_value_counts_as_not_reported(con) -> None:
    rows = [(1, Q1_2026, "RCON2160", None)]
    assert run_formula(con, "cc:RCON2160[P0] >= 0", rows) == []


def test_sum_of_items(con) -> None:
    rows = [
        (1, Q1_2026, "RCON2170", 300),
        (1, Q1_2026, "RCON0081", 100),
        (1, Q1_2026, "RCON0071", 200),
        (2, Q1_2026, "RCON2170", 301),
        (2, Q1_2026, "RCON0081", 100),
        (2, Q1_2026, "RCON0071", 200),
    ]
    formula = "cc:RCON2170[P0] = cc:RCON0081[P0] + cc:RCON0071[P0]"
    assert run_formula(con, formula, rows) == [(2, Q1_2026, "fail")]


def test_prior_quarter_comparison_across_a_year_end(con) -> None:
    rows = [
        (1, Q4_2025, "RCON2170", 500),
        (1, Q1_2026, "RCON2170", 500),
        (2, Q4_2025, "RCON2170", 500),
        (2, Q1_2026, "RCON2170", 510),
    ]
    formula = "cc:RCON2170[P0] <> cc:RCON2170[-P1Q]"
    # The fourth quarter of 2025 has no prior quarter in the data, so the edit does not run there.
    assert run_formula(con, formula, rows) == [(1, Q1_2026, "fail")]


@pytest.mark.parametrize(
    ("current", "prior", "expected"),
    [
        ("2026-06-30", "2026-03-31", True),
        ("2026-09-30", "2026-06-30", True),
        ("2026-12-31", "2026-09-30", True),
        ("2026-03-31", "2025-12-31", True),
    ],
)
def test_prior_quarter_dates(con, current: str, prior: str, expected: bool) -> None:
    rows = [(1, prior, "RCON2170", 7), (1, current, "RCON2170", 7)]
    failures = run_formula(con, "cc:RCON2170[P0] <> cc:RCON2170[-P1Q]", rows, report_date=current)
    assert (failures == [(1, current, "fail")]) is expected


def test_prior_year_and_anchored_month(con) -> None:
    rows = [
        (1, "2025-06-30", "RIAD4011", 40),
        (1, "2025-12-31", "RIAD4011", 99),
        (1, "2026-12-31", "RIAD4011", 39),
    ]
    anchored = "cc:RIAD4011[P0] >= cc:RIAD4011[-P1Y/-06-end]"
    assert run_formula(con, anchored, rows, report_date="2026-12-31") == [(1, "2026-12-31", "fail")]
    year_ago = "cc:RIAD4011[P0] >= cc:RIAD4011[-P1Y]"
    assert run_formula(con, year_ago, rows, report_date="2026-12-31") == [(1, "2026-12-31", "fail")]
    december = "cc:RIAD4011[P0] < cc:RIAD4011[-P1Y/12-end]"
    assert run_formula(con, december, rows, report_date="2026-12-31") == []


def test_if_with_precondition(con) -> None:
    rows = [
        (1, Q1_2026, "RCONF049", 10),
        (1, Q2_2026, "RCONF049", 0),
        (2, Q1_2026, "RCONF049", 0),
        (2, Q2_2026, "RCONF049", 0),
        (3, Q1_2026, "RCONF049", 10),
        (3, Q2_2026, "RCONF049", 4),
    ]
    formula = "If (cc:RCONF049[-P1Q] > 0, cc:RCONF049[P0] > 0, TRUE)"
    assert run_formula(con, formula, rows, report_date=Q2_2026) == [(1, Q2_2026, "fail")]


def test_specification_example_with_lower_case_if(con) -> None:
    rows = [
        (1, Q1_2026, "RCON2710", 5),
        (1, Q1_2026, "RCON5597", 0),
        (2, Q1_2026, "RCON2710", 5),
        (2, Q1_2026, "RCON5597", 3),
        (3, Q1_2026, "RCON2710", 0),
        (3, Q1_2026, "RCON5597", 0),
    ]
    formula = "if( cc:RCON2710[P0] > 0,cc:RCON5597[P0] > 0,TRUE)"
    assert run_formula(con, formula, rows) == [(1, Q1_2026, "fail")]


def test_month_of_the_report_date(con) -> None:
    rows = [(1, Q1_2026, "RIAD4011", 1), (1, Q2_2026, "RIAD4011", 1)]
    formula = "If(MonthOf(Context.Period.EndDate) <> 3, cc:RIAD4011[P0] > 1, TRUE)"
    assert run_formula(con, formula, rows) == [(1, Q2_2026, "fail")]
    alias = "If(Month(Context.PeriodEndDate) = 3, cc:RIAD4011[P0] > 1, TRUE)"
    assert run_formula(con, alias, rows) == [(1, Q1_2026, "fail")]


def test_exists_functions_test_for_the_item_itself(con) -> None:
    rows = [
        (1, Q1_2026, "RCONB867", 1),
        (2, Q1_2026, "RCONB867", 1),
        (2, Q1_2026, "RCFDB868", 25),
        (3, Q1_2026, "RCONB867", 0),
        (4, Q1_2026, "RCONB867", 1),
        (4, Q1_2026, "RCFDB868", None),
    ]
    non_nil = "If(cc:RCONB867[P0] = 1, ExistsNonNil(cc:RCFDB868), TRUE)"
    assert run_formula(con, non_nil, rows) == [(1, Q1_2026, "fail"), (4, Q1_2026, "fail")]
    exists = "If(cc:RCONB867[P0] = 1, Exists(cc:RCFDB868), TRUE)"
    assert run_formula(con, exists, rows) == [(1, Q1_2026, "fail")]


def test_exists_non_empty(con) -> None:
    rows = [(1, Q1_2026, "TEXT4087", "  "), (2, Q1_2026, "TEXT4087", "Main St")]
    assert run_formula(con, "ExistsNonEmpty(cc:TEXT4087[P0])", rows) == [(1, Q1_2026, "fail")]


def test_existing_of_uses_the_first_reported_item(con) -> None:
    rows = [
        (1, Q1_2026, "RCFD2170", 10),
        (1, Q1_2026, "RCON2170", 99),
        (2, Q1_2026, "RCON2170", 99),
        (3, Q1_2026, "RCON0010", 1),
    ]
    formula = "ExistingOf(cc:RCFD2170[P0], cc:RCON2170[P0]) > 50"
    assert run_formula(con, formula, rows) == [(1, Q1_2026, "fail")]


def test_existing_of_reads_the_first_reported_value_even_if_it_is_bad(con) -> None:
    rows = [(1, Q1_2026, "RCFD2170", "abc"), (1, Q1_2026, "RCON2170", 10)]
    formula = "ExistingOf(cc:RCFD2170[P0], cc:RCON2170[P0]) > 5"
    assert run_formula(con, formula, rows) == [(1, Q1_2026, "error")]


def test_if_with_a_condition_that_cannot_be_evaluated_is_an_error(con) -> None:
    rows = [(1, Q1_2026, "RCON2710", "abc"), (1, Q1_2026, "RCON5597", 0)]
    formula = "If(cc:RCON2710[P0] > 0, cc:RCON5597[P0] > 0, TRUE)"
    assert run_formula(con, formula, rows) == [(1, Q1_2026, "error")]


def test_value_that_is_not_a_number_is_an_error(con) -> None:
    rows = [(1, Q1_2026, "RCON2160", "abc"), (2, Q1_2026, "RCON2160", "12")]
    assert run_formula(con, "cc:RCON2160[P0] >= 0", rows) == [(1, Q1_2026, "error")]


def test_text_comparison(con) -> None:
    rows = [(1, Q1_2026, "RCONC592", "true"), (2, Q1_2026, "RCONC592", "false")]
    assert run_formula(con, "cc:RCONC592[P0] = 'true'", rows) == [(2, Q1_2026, "fail")]


def test_boolean_comparison(con) -> None:
    rows = [(1, Q1_2026, "RCONC592", "true"), (2, Q1_2026, "RCONC592", "false")]
    assert run_formula(con, "cc:RCONC592[P0] = TRUE", rows) == [(2, Q1_2026, "fail")]


@pytest.mark.parametrize(
    ("formula", "failing"),
    [
        ("Abs(cc:RCON2160[P0]) <= 10", [2]),
        ("Round(cc:RCON2160[P0] / 3, 1) = 0.7", [2]),
        ("Min(cc:RCON2160[P0], 5) = 2", [2]),
        ("Max(cc:RCON2160[P0], 5) = 5", [2]),
        ("Sum(cc:RCON2160[P0], 1) = 3", [2]),
        ("Avg(cc:RCON2160[P0], 4) = 3", [2]),
        ("Int(cc:RCON2160[P0] / 4) = 0", [2]),
        ("Power(cc:RCON2160[P0], 2) = 4", [2]),
        ("cc:RCON2160[P0] ^ 2 = 4", [2]),
        ("-cc:RCON2160[P0] = -2", [2]),
        ("cc:RCON2160[P0] >= - -1", []),
        ("Not (cc:RCON2160[P0] > 10)", [2]),
        ("cc:RCON2160[P0] = 2 Xor cc:RCON2160[P0] = 3", [2]),
        ("And(cc:RCON2160[P0] > 0, cc:RCON2160[P0] < 10)", [2]),
        ("Or(cc:RCON2160[P0] = 2, cc:RCON2160[P0] = 7)", [2]),
        ("Len(cc:RCON2160[P0] & '') = 1", [2]),
    ],
)
def test_functions_and_operators(con, formula: str, failing: list[int]) -> None:
    rows = [(1, Q1_2026, "RCON2160", 2), (2, Q1_2026, "RCON2160", 25)]
    assert [f[0] for f in run_formula(con, formula, rows)] == failing


def test_text_functions(con) -> None:
    rows = [(1, Q1_2026, "TEXT4087", "Main St"), (2, Q1_2026, "TEXT4087", "x")]
    cases = {
        "Len(cc:TEXT4087[P0]) > 3": [2],
        "Upper(cc:TEXT4087[P0]) = 'MAIN ST'": [2],
        "Left(cc:TEXT4087[P0], 4) = 'Main'": [2],
        "Right(cc:TEXT4087[P0], 2) = 'St'": [2],
        "Mid(cc:TEXT4087[P0], 2, 3) = 'ain'": [2],
        "Exact(Trim(cc:TEXT4087[P0]), 'Main St')": [2],
        "Concatenate(cc:TEXT4087[P0], '!') = 'Main St!'": [2],
        "cc:TEXT4087[P0] & Char(33) = 'Main St!'": [2],
    }
    for formula, failing in cases.items():
        assert [f[0] for f in run_formula(con, formula, rows)] == failing, formula


@pytest.mark.parametrize(
    ("formula", "reason"),
    [
        ("cc:RCON2200[P0] = SUM(sc:RCON2200[P0, Context.Scenario.Branch])", "dimensions"),
        ("If (RR1 OR RR2, ExistsNonNil(cc:RCFDB868), TRUE)", "the name 'RR1'"),
        ("cc:RCON2170[P9] > 0", "period reference [P9]"),
        ("Foo(cc:RCON2170[P0]) > 0", "the function Foo() is not supported"),
        ("cc:RCON2170[P0] + 1", "does not produce TRUE or FALSE"),
        ("If(cc:RCON2170[P0] > 0, TRUE)", "If() expects 3 argument(s), got 2"),
        ("Exists(1) Or cc:A[P0] > 0", "accepts only an item reference"),
        ("1 = 1", "does not read any reported item"),
        ("(cc:RCON2170[P0] > 0) + 1 > 0", "TRUE/FALSE value is used where a number"),
        ("ExistingOf(cc:A[P0], 1) > 0", "ExistingOf() accepts only item references"),
    ],
)
def test_unsupported_formulas(formula: str, reason: str) -> None:
    with pytest.raises(
        UnsupportedFormula,
        match=reason.replace("(", r"\(")
        .replace(")", r"\)")
        .replace("[", r"\[")
        .replace("]", r"\]"),
    ):
        compile_edit(Edit("T-1", "validity", formula))


def test_syntax_errors_propagate() -> None:
    with pytest.raises(FormulaSyntaxError):
        compile_edit(Edit("T-1", "validity", "cc:RCON2170[P0] >"))


def test_compile_edits_sets_aside_what_it_cannot_compile() -> None:
    edits = [
        Edit("A", "validity", "cc:RCON2160[P0] >= 0"),
        Edit("B", "validity", "cc:RCON2160[P0] >="),
        Edit("C", "quality", "Foo(cc:RCON2160[P0])"),
    ]
    result = compile_edits(edits)
    assert [c.edit.edit_id for c in result.checks] == ["A"]
    assert [(s.edit.edit_id, s.reason.split(":")[0]) for s in result.skipped] == [
        ("B", "formula could not be parsed"),
        ("C", "the function Foo() is not supported yet"),
    ]


def test_compiled_check_lists_items_once_in_order() -> None:
    check = compile_edit(
        Edit("A", "quality", "cc:RCON2170[P0] <> cc:RCON2170[-P1Q] Or cc:RCON0081[P0] > 0")
    )
    assert check.items == ("RCON2170", "RCON0081")


def test_sql_is_readable() -> None:
    sql = compile_edit(
        Edit(
            "A", "validity", "cc:RCON2170[P0] = cc:RCON0081[P0] + cc:RCON0071[P0] + cc:RCON2122[P0]"
        )
    ).sql
    assert '("RCON2170@P0" = "RCON0081@P0" + "RCON0071@P0" + "RCON2122@P0") AS passed' in sql
    assert "-- Edit A (validity)" in sql


def test_parentheses_are_kept_where_needed() -> None:
    sql = compile_edit(
        Edit(
            "A", "validity", "cc:A[P0] - (cc:B[P0] - cc:C[P0]) >= cc:D[P0] * (cc:E[P0] + cc:F[P0])"
        )
    ).sql
    assert '("A@P0" - ("B@P0" - "C@P0") >= "D@P0" * ("E@P0" + "F@P0")) AS passed' in sql


def test_quotes_and_newlines_in_edit_text_are_safe(con) -> None:
    edit = Edit(
        "O'Brien-1",
        "valid'ity",
        "cc:RCON2160[P0] >= 0",
        message="Line one\nDROP TABLE tieout_items; -- not code",
        source="a\nb",
    )
    check = compile_edit(edit)
    assert "\nDROP" not in check.sql
    load_rows(con, [(1, Q1_2026, "RCON2160", -1)])
    assert con.execute(check.sql).fetchall()[0][:2] == ("O'Brien-1", "valid'ity")


def test_item_values_show_what_the_edit_read(con) -> None:
    load_rows(con, [(1, Q1_2026, "RCON2170", 500), (1, Q4_2025, "RCON2170", 500)])
    check = compile_edit(Edit("A", "quality", "cc:RCON2170[P0] <> cc:RCON2170[-P1Q]"))
    rows = con.execute(check.sql).fetchall()
    assert rows[0][5] == "RCON2170@P0=500; RCON2170@-P1Q=500"


def test_sql_script_runs_in_duckdb(con) -> None:
    load_rows(con, [(1, Q1_2026, "RCON2160", -1)])
    result = compile_edits(
        [
            Edit("A", "validity", "cc:RCON2160[P0] >= 0"),
            Edit("B", "quality", "Foo(cc:RCON2160[P0])"),
        ]
    )
    script = result.to_sql_script(generated_from="test")
    assert "-- Edits not compiled by this version of TieOut:" in script
    assert "--   B (quality): the function Foo() is not supported yet" in script
    assert con.execute(script).fetchall()[0][0] == "A"


def _dictionary() -> MdrmDictionary:
    return MdrmDictionary(
        [
            MdrmItem("RCON", "2170", "Total assets", date(2001, 3, 31), date(9999, 12, 31)),
            MdrmItem("RCON", "1234", "A retired item", date(2001, 3, 31), date(2010, 12, 31)),
        ]
    )


def test_dictionary_names_items_and_flags_unknown_ones() -> None:
    edits = [Edit("A", "validity", "cc:RCON2170[P0] >= cc:RCON1234[P0] + cc:RCON9999[P0]")]
    result = compile_edits(edits, dictionary=_dictionary(), as_of=date(2026, 6, 30))
    sql = result.checks[0].sql
    assert "--   RCON2170  Total assets" in sql
    assert "--   RCON1234  (not in effect on 2026-06-30 according to the MDRM dictionary)" in sql
    assert "--   RCON9999  (not found in the MDRM dictionary)" in sql
    assert result.warnings == [
        "RCON9999 is not in the MDRM dictionary (used by A)",
        "RCON1234 is not in effect on 2026-06-30 according to the MDRM dictionary (used by A)",
    ]
