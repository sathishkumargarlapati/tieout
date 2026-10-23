"""Parse the formula language used by the FFIEC Call Report edits.

The FFIEC Central Data Repository (CDR) publishes each Call Report edit as a
formula in the Call Report XBRL taxonomy. The FFIEC's *CDR Interchange
Specification* describes the syntax. Two examples from it::

    cc:RCON2170[P0] <> cc:RCON2170[-P1Q]
    If (cc:RCONF049[-P1Q] > 0, cc:RCONF049[P0] > 0, TRUE)

``cc:RCON2170`` names a reported item by its MDRM identifier, and the part in
brackets says which period to read: ``P0`` is the report date being checked,
``-P1Q`` is one quarter earlier, ``-P1Y/-06-end`` is June of the prior year.

This module turns a formula into a small syntax tree. It evaluates nothing;
:mod:`tieout.compiler` turns the tree into SQL.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass, field

__all__ = [
    "Binary",
    "Boolean",
    "Call",
    "Concept",
    "FormulaSyntaxError",
    "Name",
    "Node",
    "Number",
    "Period",
    "String",
    "Unary",
    "concepts",
    "parse",
    "parse_period",
    "walk",
]


class FormulaSyntaxError(ValueError):
    """Raised when a formula cannot be parsed."""

    def __init__(self, reason: str, formula: str, position: int) -> None:
        self.reason = reason
        self.formula = formula
        self.position = position
        super().__init__(f"{reason} (at character {position + 1} of: {formula})")


# --------------------------------------------------------------------------
# Periods
# --------------------------------------------------------------------------

_PERIOD_RE = re.compile(
    r"^(?P<sign>[+-]?)P(?P<amount>\d+)(?P<unit>[YQMD])(?:/-?(?P<month>\d{1,2})-end)?$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Period:
    """A period relative to the report date being checked.

    ``months`` and ``days`` shift the report date; negative values are earlier.
    When ``anchor_month`` is set, the shifted date moves to the end of that
    month in the same year, so ``-P1Y/-06-end`` read on 31 December 2026 means
    30 June 2025.
    """

    months: int = 0
    days: int = 0
    anchor_month: int | None = None

    @property
    def is_current(self) -> bool:
        return self.months == 0 and self.days == 0 and self.anchor_month is None

    @property
    def label(self) -> str:
        """Canonical text such as ``P0``, ``-P1Q`` or ``-P1Y/06-end``."""
        if self.is_current:
            return "P0"
        sign = "-" if (self.months < 0 or self.days < 0) else ""
        if self.days:
            text = f"{sign}P{abs(self.days)}D"
        elif self.months == 0:
            text = "P0"
        elif self.months % 12 == 0:
            text = f"{sign}P{abs(self.months) // 12}Y"
        elif self.months % 3 == 0:
            text = f"{sign}P{abs(self.months) // 3}Q"
        else:
            text = f"{sign}P{abs(self.months)}M"
        if self.anchor_month is not None:
            text += f"/{self.anchor_month:02d}-end"
        return text


CURRENT = Period()


def parse_period(text: str) -> Period | None:
    """Parse the period part of a context reference.

    Returns ``None`` when the text is not a period form TieOut understands.
    """
    value = text.strip()
    if value == "" or value.upper() == "P0":
        return CURRENT
    match = _PERIOD_RE.match(value)
    if match is None:
        return None
    amount = int(match["amount"])
    if match["sign"] == "-":
        amount = -amount
    unit = match["unit"].upper()
    months = days = 0
    if unit == "Y":
        months = 12 * amount
    elif unit == "Q":
        months = 3 * amount
    elif unit == "M":
        months = amount
    else:
        days = amount
    anchor = int(match["month"]) if match["month"] else None
    if anchor is not None and not 1 <= anchor <= 12:
        return None
    return Period(months=months, days=days, anchor_month=anchor)


# --------------------------------------------------------------------------
# Syntax tree
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Number:
    """A numeric literal, kept exactly as written."""

    text: str
    position: int = field(default=0, compare=False)


@dataclass(frozen=True)
class String:
    """A quoted text literal."""

    value: str
    position: int = field(default=0, compare=False)


@dataclass(frozen=True)
class Boolean:
    """``TRUE`` or ``FALSE``."""

    value: bool
    position: int = field(default=0, compare=False)


@dataclass(frozen=True)
class Concept:
    """A reported item, such as ``cc:RCON2170[-P1Q]``.

    ``context`` is the raw text between the brackets. ``period`` is ``None``
    when that text is not a supported period form, and ``dimensions`` holds any
    further qualifiers after a comma, such as ``Context.Scenario.Branch``.
    """

    prefix: str
    name: str
    context: str = ""
    period: Period | None = CURRENT
    dimensions: tuple[str, ...] = ()
    position: int = field(default=0, compare=False)

    @property
    def mdrm(self) -> str:
        """The MDRM identifier, for example ``RCON2170``."""
        return self.name.upper()

    @property
    def key(self) -> str:
        """Identifier for this item in this period, for example ``RCON2170@-P1Q``."""
        label = self.period.label if self.period is not None else self.context
        return f"{self.mdrm}@{label}"


@dataclass(frozen=True)
class Name:
    """A bare name, such as ``Context.Period.EndDate`` or a rule reference."""

    name: str
    position: int = field(default=0, compare=False)


@dataclass(frozen=True)
class Call:
    """A function call, such as ``If(a, b, c)`` or ``Abs(x)``."""

    name: str
    args: tuple[Node, ...] = ()
    position: int = field(default=0, compare=False)

    @property
    def function(self) -> str:
        """The function name in lower case, for lookups."""
        return self.name.lower()


@dataclass(frozen=True)
class Unary:
    """``-x``, ``+x`` or ``Not x``. ``op`` is ``"-"``, ``"+"`` or ``"not"``."""

    op: str
    operand: Node
    position: int = field(default=0, compare=False)


@dataclass(frozen=True)
class Binary:
    """A binary operation.

    ``op`` is one of ``+ - * / ^ &``, a comparison ``= <> < > <= >=``, or
    ``and``, ``or``, ``xor``.
    """

    op: str
    left: Node
    right: Node
    position: int = field(default=0, compare=False)


Node = Number | String | Boolean | Concept | Name | Call | Unary | Binary


def walk(node: Node) -> Iterator[Node]:
    """Yield ``node`` and every node beneath it, depth first."""
    yield node
    if isinstance(node, Call):
        for arg in node.args:
            yield from walk(arg)
    elif isinstance(node, Unary):
        yield from walk(node.operand)
    elif isinstance(node, Binary):
        yield from walk(node.left)
        yield from walk(node.right)


def concepts(node: Node) -> list[Concept]:
    """Every item reference in the formula, in order of appearance."""
    return [n for n in walk(node) if isinstance(n, Concept)]


# --------------------------------------------------------------------------
# Lexer
# --------------------------------------------------------------------------

_TOKEN_RE = re.compile(
    r"""
    (?P<SPACE>\s+)
  | (?P<NUMBER>(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)
  | (?P<STRING>'[^']*'|"[^"]*")
  | (?P<CONTEXT>\[[^\[\]]*\])
  | (?P<NAME>[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*)
  | (?P<OP><=|>=|<>|=<|=>|!=|[=<>+\-*/^&])
  | (?P<LPAREN>\()
  | (?P<RPAREN>\))
  | (?P<COMMA>,)
  | (?P<COLON>:)
    """,
    re.VERBOSE,
)

_COMPARISONS = {
    "=": "=",
    "<>": "<>",
    "!=": "<>",
    "<": "<",
    ">": ">",
    "<=": "<=",
    ">=": ">=",
    "=<": "<=",
    "=>": ">=",
}


@dataclass(frozen=True)
class _Token:
    kind: str
    text: str
    position: int


def _tokenize(formula: str) -> list[_Token]:
    tokens: list[_Token] = []
    position = 0
    while position < len(formula):
        match = _TOKEN_RE.match(formula, position)
        if match is None:
            raise FormulaSyntaxError(
                f"Unexpected character {formula[position]!r}", formula, position
            )
        kind = match.lastgroup
        assert kind is not None
        if kind != "SPACE":
            tokens.append(_Token(kind, match.group(), position))
        position = match.end()
    tokens.append(_Token("EOF", "", len(formula)))
    return tokens


# --------------------------------------------------------------------------
# Parser
# --------------------------------------------------------------------------


class _Parser:
    """Recursive-descent parser.

    Precedence, loosest first: ``Xor``, ``Or``, ``And``, ``Not``, comparisons,
    ``&`` (text concatenation), ``+ -``, ``* /``, unary minus, ``^``.
    Comparisons do not chain; ``a < b < c`` is rejected.
    """

    def __init__(self, formula: str) -> None:
        self.formula = formula
        self.tokens = _tokenize(formula)
        self.index = 0

    # -- token helpers -----------------------------------------------------

    def peek(self, offset: int = 0) -> _Token:
        return self.tokens[min(self.index + offset, len(self.tokens) - 1)]

    def advance(self) -> _Token:
        token = self.tokens[self.index]
        if token.kind != "EOF":
            self.index += 1
        return token

    def error(self, reason: str, token: _Token | None = None) -> FormulaSyntaxError:
        token = token or self.peek()
        return FormulaSyntaxError(reason, self.formula, token.position)

    def expect(self, kind: str, what: str) -> _Token:
        token = self.peek()
        if token.kind != kind:
            found = "the end of the formula" if token.kind == "EOF" else repr(token.text)
            raise self.error(f"Expected {what} but found {found}")
        return self.advance()

    def at_keyword(self, *words: str) -> bool:
        token = self.peek()
        return token.kind == "NAME" and token.text.lower() in words

    def at_op(self, *ops: str) -> bool:
        token = self.peek()
        return token.kind == "OP" and token.text in ops

    # -- grammar -------------------------------------------------------------

    def parse(self) -> Node:
        if self.peek().kind == "EOF":
            raise self.error("The formula is empty")
        node = self.expression()
        if self.peek().kind != "EOF":
            raise self.error(f"Unexpected {self.peek().text!r}")
        return node

    def expression(self) -> Node:
        return self.xor_expr()

    def xor_expr(self) -> Node:
        left = self.or_expr()
        while self.at_keyword("xor"):
            token = self.advance()
            left = Binary("xor", left, self.or_expr(), token.position)
        return left

    def or_expr(self) -> Node:
        left = self.and_expr()
        while self.at_keyword("or"):
            token = self.advance()
            left = Binary("or", left, self.and_expr(), token.position)
        return left

    def and_expr(self) -> Node:
        left = self.not_expr()
        while self.at_keyword("and"):
            token = self.advance()
            left = Binary("and", left, self.not_expr(), token.position)
        return left

    def not_expr(self) -> Node:
        if self.at_keyword("not"):
            token = self.advance()
            return Unary("not", self.not_expr(), token.position)
        return self.comparison()

    def comparison(self) -> Node:
        left = self.concat()
        if self.at_op(*_COMPARISONS):
            token = self.advance()
            left = Binary(_COMPARISONS[token.text], left, self.concat(), token.position)
            if self.at_op(*_COMPARISONS):
                raise self.error("Comparisons cannot be chained; add parentheses")
        return left

    def concat(self) -> Node:
        left = self.additive()
        while self.at_op("&"):
            token = self.advance()
            left = Binary("&", left, self.additive(), token.position)
        return left

    def additive(self) -> Node:
        left = self.term()
        while self.at_op("+", "-"):
            token = self.advance()
            left = Binary(token.text, left, self.term(), token.position)
        return left

    def term(self) -> Node:
        left = self.unary()
        while self.at_op("*", "/"):
            token = self.advance()
            left = Binary(token.text, left, self.unary(), token.position)
        return left

    def unary(self) -> Node:
        if self.at_op("-", "+"):
            token = self.advance()
            return Unary(token.text, self.unary(), token.position)
        return self.power()

    def power(self) -> Node:
        base = self.primary()
        if self.at_op("^"):
            token = self.advance()
            return Binary("^", base, self.unary(), token.position)
        return base

    def primary(self) -> Node:
        token = self.peek()
        if token.kind == "NUMBER":
            self.advance()
            return Number(token.text, token.position)
        if token.kind == "STRING":
            self.advance()
            return String(token.text[1:-1], token.position)
        if token.kind == "LPAREN":
            self.advance()
            node = self.expression()
            self.expect("RPAREN", "')'")
            return node
        if token.kind == "NAME":
            following = self.peek(1).kind
            if following == "COLON":
                return self.concept()
            if following == "LPAREN":
                return self.call()
            lowered = token.text.lower()
            if lowered in ("true", "false"):
                self.advance()
                return Boolean(lowered == "true", token.position)
            if lowered in ("and", "or", "xor"):
                raise self.error(f"Expected a value before {token.text!r}")
            self.advance()
            return Name(token.text, token.position)
        if token.kind == "EOF":
            raise self.error("The formula ends where a value was expected")
        raise self.error(f"Expected a value but found {token.text!r}")

    def concept(self) -> Concept:
        prefix = self.advance()
        self.advance()  # the colon
        name = self.expect("NAME", "an item name after ':'")
        context = ""
        if self.peek().kind == "CONTEXT":
            context = self.advance().text[1:-1].strip()
        parts = [part.strip() for part in context.split(",")] if context else [""]
        period = parse_period(parts[0])
        dimensions = tuple(part for part in parts[1:] if part)
        return Concept(prefix.text, name.text, context, period, dimensions, prefix.position)

    def call(self) -> Node:
        name = self.advance()
        self.advance()  # the opening parenthesis
        args: list[Node] = []
        if self.peek().kind != "RPAREN":
            args.append(self.expression())
            while self.peek().kind == "COMMA":
                self.advance()
                args.append(self.expression())
        self.expect("RPAREN", "',' or ')'")
        lowered = name.text.lower()
        if lowered in ("true", "false") and not args:
            return Boolean(lowered == "true", name.position)
        return Call(name.text, tuple(args), name.position)


def parse(formula: str) -> Node:
    """Parse a Call Report edit formula into a syntax tree.

    Raises :class:`FormulaSyntaxError` if the formula is not well formed.
    """
    return _Parser(formula).parse()
