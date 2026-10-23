from __future__ import annotations

import pytest

from tieout.expr import (
    Binary,
    Boolean,
    Call,
    Concept,
    FormulaSyntaxError,
    Name,
    Number,
    Period,
    String,
    Unary,
    concepts,
    parse,
    parse_period,
)

# Formula examples taken from the FFIEC CDR Interchange Specification and the
# FFIEC's related taxonomy documentation.
SPEC_EXAMPLES = [
    "if( cc:RCON2710[P0] > 0,cc:RCON5597[P0] > 0,TRUE)",
    "((cc:RIADC435[P0])+(cc:RIADC390[P0]))<=(cc:RIAD3123[P0])",
    "(cc:RIAD4150[P0] > 0)",
    "cc:RCON2160[P0] >= 0",
    "cc:RCON2170[P0] <> cc:RCON2170[-P1Q]",
    "If (cc:RCONF049[-P1Q] > 0, cc:RCONF049[P0] > 0, TRUE)",
    "cc:RCON2200[P0] = SUM(sc:RCON2200[P0, Context.Scenario.Branch])",
    "If (RR1 OR RR2 OR RR3, ExistsNonNil(cc:RCFDB868) , TRUE)",
    "MonthOf(Context.PeriodEndDate) = 12",
    "(cc:RCONB894[-P1Y/12-end] + cc:RCONB895[-P1Y/12-end])>100000000",
    "'This is Bank' & CHAR(39) & 's data'",
]


def item(name: str, context: str = "", period: Period | None = None) -> Concept:
    return Concept("cc", name, context, period if period is not None else Period(), ())


@pytest.mark.parametrize("formula", SPEC_EXAMPLES)
def test_specification_examples_parse(formula: str) -> None:
    parse(formula)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("", Period()),
        ("P0", Period()),
        ("p0", Period()),
        ("-P1Q", Period(months=-3)),
        ("-P2Q", Period(months=-6)),
        ("-P1Y", Period(months=-12)),
        ("-P3M", Period(months=-3)),
        ("-P1D", Period(days=-1)),
        ("P1Q", Period(months=3)),
        ("-P1Y/-06-end", Period(months=-12, anchor_month=6)),
        ("-P1Y/12-end", Period(months=-12, anchor_month=12)),
    ],
)
def test_parse_period(text: str, expected: Period) -> None:
    assert parse_period(text) == expected


@pytest.mark.parametrize("text", ["P1", "-P1W", "-P1Y/13-end", "-P1Y/06", "P0Q0", "Q1"])
def test_parse_period_rejects_unknown_forms(text: str) -> None:
    assert parse_period(text) is None


@pytest.mark.parametrize(
    ("text", "label"),
    [
        ("P0", "P0"),
        ("-P1Q", "-P1Q"),
        ("-P3M", "-P1Q"),
        ("-P4Q", "-P1Y"),
        ("-P2M", "-P2M"),
        ("-P1D", "-P1D"),
        ("P1Q", "P1Q"),
        ("-P1Y/-06-end", "-P1Y/06-end"),
    ],
)
def test_period_labels(text: str, label: str) -> None:
    period = parse_period(text)
    assert period is not None
    assert period.label == label


def test_concept_reference() -> None:
    node = parse("cc:RCON2170[-P1Q]")
    assert node == item("RCON2170", "-P1Q", Period(months=-3))
    assert isinstance(node, Concept)
    assert node.mdrm == "RCON2170"
    assert node.key == "RCON2170@-P1Q"


def test_concept_without_context_is_the_current_period() -> None:
    node = parse("cc:RCFDB868")
    assert isinstance(node, Concept)
    assert node.period == Period()
    assert node.key == "RCFDB868@P0"


def test_concept_names_are_upper_cased_in_keys() -> None:
    node = parse("cc:rcon2170[P0]")
    assert isinstance(node, Concept)
    assert node.mdrm == "RCON2170"


def test_concept_with_dimensions() -> None:
    node = parse("sc:RCON2200[P0, Context.Scenario.Branch]")
    assert isinstance(node, Concept)
    assert node.period == Period()
    assert node.dimensions == ("Context.Scenario.Branch",)


def test_concept_with_unknown_period() -> None:
    node = parse("cc:RCON2170[P7]")
    assert isinstance(node, Concept)
    assert node.period is None
    assert node.key == "RCON2170@P7"


def test_literals() -> None:
    assert parse("12.5") == Number("12.5")
    assert parse("'abc'") == String("abc")
    assert parse('"abc"') == String("abc")
    assert parse("TRUE") == Boolean(True)
    assert parse("false") == Boolean(False)
    assert parse("True()") == Boolean(True)


def test_names() -> None:
    assert parse("Context.Period.EndDate") == Name("Context.Period.EndDate")
    assert parse("RR1") == Name("RR1")


def test_arithmetic_precedence() -> None:
    a, b, c = item("A"), item("B"), item("C")
    assert parse("cc:A + cc:B * cc:C") == Binary("+", a, Binary("*", b, c))
    assert parse("(cc:A + cc:B) * cc:C") == Binary("*", Binary("+", a, b), c)
    assert parse("cc:A - cc:B - cc:C") == Binary("-", Binary("-", a, b), c)


def test_unary_minus_and_power() -> None:
    assert parse("-2^2") == Unary("-", Binary("^", Number("2"), Number("2")))
    assert parse("2^-1") == Binary("^", Number("2"), Unary("-", Number("1")))
    assert parse("- -1") == Unary("-", Unary("-", Number("1")))


def test_logical_precedence() -> None:
    a, b, c = (Binary(">", item(n), Number("0")) for n in ("A", "B", "C"))
    assert parse("cc:A > 0 And cc:B > 0 Or cc:C > 0") == Binary("or", Binary("and", a, b), c)
    assert parse("cc:A > 0 Or cc:B > 0 And cc:C > 0") == Binary("or", a, Binary("and", b, c))
    assert parse("Not cc:A > 0") == Unary("not", a)
    assert parse("cc:A > 0 Xor cc:B > 0") == Binary("xor", a, b)


def test_keywords_are_case_insensitive() -> None:
    assert parse("cc:A > 0 AND cc:B > 0") == parse("cc:A > 0 and cc:B > 0")
    assert parse("IF(cc:A > 0, TRUE, FALSE)") == Call(
        "IF", (Binary(">", item("A"), Number("0")), Boolean(True), Boolean(False))
    )


@pytest.mark.parametrize(
    ("formula", "op"),
    [
        ("cc:A = 1", "="),
        ("cc:A <> 1", "<>"),
        ("cc:A != 1", "<>"),
        ("cc:A =< 1", "<="),
        ("cc:A => 1", ">="),
        ("cc:A <= 1", "<="),
        ("cc:A >= 1", ">="),
        ("cc:A < 1", "<"),
        ("cc:A > 1", ">"),
    ],
)
def test_comparison_operators(formula: str, op: str) -> None:
    node = parse(formula)
    assert isinstance(node, Binary)
    assert node.op == op


def test_text_concatenation_binds_tighter_than_comparison() -> None:
    node = parse("'a' & 'b' = 'ab'")
    assert node == Binary("=", Binary("&", String("a"), String("b")), String("ab"))


def test_function_calls() -> None:
    assert parse("Abs(cc:A)") == Call("Abs", (item("A"),))
    assert parse("Foo()") == Call("Foo", ())
    assert parse("And(cc:A > 0, cc:B > 0)") == Call(
        "And", (Binary(">", item("A"), Number("0")), Binary(">", item("B"), Number("0")))
    )


def test_concepts_helper_lists_every_reference() -> None:
    node = parse("If(cc:A[P0] > 0, cc:B[P0] + cc:A[-P1Q] > 0, TRUE)")
    assert [c.key for c in concepts(node)] == ["A@P0", "B@P0", "A@-P1Q"]


@pytest.mark.parametrize(
    ("formula", "reason"),
    [
        ("", "The formula is empty"),
        ("cc:A > ", "The formula ends where a value was expected"),
        ("(cc:A > 0", "Expected ')'"),
        ("cc:A > 0)", "Unexpected ')'"),
        ("cc:A # 1", "Unexpected character '#'"),
        ("cc:A < cc:B < cc:C", "Comparisons cannot be chained"),
        ("cc:[P0]", "Expected an item name"),
        ("Abs(cc:A", "Expected ',' or ')'"),
        ("And > 1", "Expected a value before 'And'"),
    ],
)
def test_syntax_errors(formula: str, reason: str) -> None:
    with pytest.raises(FormulaSyntaxError) as raised:
        parse(formula)
    assert raised.value.reason.startswith(reason)
    assert raised.value.formula == formula


def test_syntax_error_position() -> None:
    with pytest.raises(FormulaSyntaxError) as raised:
        parse("cc:A > 0 # 1")
    assert raised.value.position == 9
    assert "at character 10" in str(raised.value)
