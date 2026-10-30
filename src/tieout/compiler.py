"""Compile Call Report edits into readable DuckDB SQL checks.

Each edit becomes one standalone SQL query. The query reads two relations:

``tieout_items``
    One row per institution, report date and MDRM item, with the columns
    ``rssd_id``, ``report_date`` (DATE), ``mdrm`` (for example ``RCON2170``)
    and ``value``.

``tieout_periods``
    The institutions and report dates to check: ``rssd_id`` and
    ``report_date``.

The query returns one row for every institution and report date where the
edit does not pass, with ``status`` set to ``fail`` when the formula is false
and ``error`` when it cannot be evaluated, for example because a value that
must be a number is not one.

Following the FFIEC CDR Interchange Specification, an edit does not run for
an institution and date when any item it uses has not been reported. Items
that appear only inside ``Exists``, ``ExistsNonNil`` or ``ExistsNonEmpty``
are exempt, because those functions test for the item itself.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date

from ._version import __version__
from .edits import Edit
from .expr import (
    Binary,
    Boolean,
    Call,
    Concept,
    FormulaSyntaxError,
    Name,
    Node,
    Number,
    Period,
    String,
    Unary,
    concepts,
    parse,
)
from .mdrm import MdrmDictionary

__all__ = [
    "ITEMS_RELATION",
    "PERIODS_RELATION",
    "CompileResult",
    "CompiledCheck",
    "SkippedEdit",
    "UnsupportedFormula",
    "compile_edit",
    "compile_edits",
]

ITEMS_RELATION = "tieout_items"
PERIODS_RELATION = "tieout_periods"

# Value kinds used while compiling.
NUM, TEXT, BOOL, DATE, ANY = "num", "text", "bool", "date", "any"
_CONCRETE = (NUM, TEXT, BOOL, DATE)

# SQL precedence levels used to decide where parentheses are needed.
_OR, _AND, _NOT, _CMP, _ADD, _MUL, _NEG, _ATOM = 1, 2, 3, 4, 6, 7, 8, 10

_PERIOD_END_NAMES = {"context.period.enddate", "context.periodenddate"}
_PRESENCE = {"exists": "exists", "existsnonnil": "reported", "existsnonempty": "nonempty"}
_DATE_PARTS = {
    "monthof": "month",
    "month": "month",
    "yearof": "year",
    "year": "year",
    "dayof": "day",
    "day": "day",
}
_MATH_1 = {"abs": "abs", "sqrt": "sqrt", "sign": "sign", "int": "floor"}
_TEXT_1 = {"upper": "upper", "lower": "lower", "trim": "trim"}


class UnsupportedFormula(Exception):
    """The formula uses something this version of TieOut cannot compile yet."""


@dataclass(frozen=True)
class CompiledCheck:
    """An edit compiled into a SQL query."""

    edit: Edit
    sql: str
    """The check: one row per institution and report date where the edit does not pass."""
    items: tuple[str, ...]
    """MDRM identifiers the check reads, in order of first appearance."""
    summary_sql: str = ""
    """The same check, counted: one row with the institutions and report dates where the
    edit ran (``evaluated``) and how many ``passed``, ``failed`` or gave ``errors``."""


@dataclass(frozen=True)
class SkippedEdit:
    """An edit that was not compiled, and why."""

    edit: Edit
    reason: str


@dataclass
class CompileResult:
    """The checks compiled from a set of edits, and the edits left out."""

    checks: list[CompiledCheck] = field(default_factory=list)
    skipped: list[SkippedEdit] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_sql_script(self, generated_from: str = "") -> str:
        """All checks as one SQL script, with a header that explains how to run them."""
        lines = [
            f"-- TieOut {__version__}: Call Report edit checks for DuckDB",
        ]
        if generated_from:
            lines.append(f"-- Generated from: {_one_line(generated_from)}")
        lines += [
            f"-- {len(self.checks)} checks compiled; "
            f"{len(self.skipped)} edits not compiled (listed at the end).",
            "--",
            "-- Each check reads two relations, which you create before running it:",
            "--",
            f"--   {ITEMS_RELATION}    one row per institution, report date and MDRM item,",
            "--                   with columns rssd_id, report_date (DATE),",
            "--                   mdrm (VARCHAR, for example 'RCON2170') and value",
            f"--   {PERIODS_RELATION}  the institutions and report dates to check,",
            "--                   with columns rssd_id and report_date (DATE)",
            "--",
            "-- For example:",
            f"--   CREATE VIEW {PERIODS_RELATION} AS",
            f"--   SELECT DISTINCT rssd_id, report_date FROM {ITEMS_RELATION};",
            "--",
            "-- Each check returns one row for every institution and report date where",
            "-- the edit does not pass: status 'fail' when the formula is false, and",
            "-- 'error' when it cannot be evaluated (for example, a value that must be",
            "-- a number is not one). Following the FFIEC CDR Interchange Specification,",
            "-- an edit does not run when an item it uses has not been reported.",
            "",
        ]
        for check in self.checks:
            lines.append(check.sql + ";")
            lines.append("")
        if self.skipped:
            lines.append("-- Edits not compiled by this version of TieOut:")
            for skipped in self.skipped:
                edit = skipped.edit
                lines.append(
                    f"--   {_one_line(edit.edit_id)} ({_one_line(edit.edit_type)}): "
                    f"{_one_line(skipped.reason)}"
                )
            lines.append("")
        return "\n".join(lines)


def compile_edit(
    edit: Edit,
    *,
    dictionary: MdrmDictionary | None = None,
    as_of: date | None = None,
) -> CompiledCheck:
    """Compile one edit into a SQL query.

    Raises :class:`~tieout.expr.FormulaSyntaxError` if the formula cannot be
    parsed and :class:`UnsupportedFormula` if it uses something TieOut cannot
    compile yet. With an MDRM ``dictionary``, the query's header names each
    item it reads.
    """
    tree = parse(edit.expression)
    return _CheckBuilder(edit, tree, dictionary, as_of).build()


def compile_edits(
    edits: Iterable[Edit],
    *,
    dictionary: MdrmDictionary | None = None,
    as_of: date | None = None,
) -> CompileResult:
    """Compile many edits, setting aside the ones that cannot be compiled.

    With an MDRM ``dictionary``, items that are not in it, or not in effect on
    ``as_of``, are reported as warnings.
    """
    result = CompileResult()
    unknown: dict[str, list[str]] = {}
    inactive: dict[str, list[str]] = {}
    for edit in edits:
        try:
            check = compile_edit(edit, dictionary=dictionary, as_of=as_of)
        except FormulaSyntaxError as exc:
            result.skipped.append(SkippedEdit(edit, f"formula could not be parsed: {exc.reason}"))
            continue
        except UnsupportedFormula as exc:
            result.skipped.append(SkippedEdit(edit, str(exc)))
            continue
        result.checks.append(check)
        if dictionary is not None:
            for mdrm in check.items:
                if not dictionary.get(mdrm):
                    unknown.setdefault(mdrm, []).append(edit.edit_id)
                elif as_of is not None and not dictionary.get(mdrm, as_of):
                    inactive.setdefault(mdrm, []).append(edit.edit_id)
    for mdrm, edit_ids in sorted(unknown.items()):
        result.warnings.append(
            f"{mdrm} is not in the MDRM dictionary (used by {_list_ids(edit_ids)})"
        )
    for mdrm, edit_ids in sorted(inactive.items()):
        result.warnings.append(
            f"{mdrm} is not in effect on {as_of} according to the MDRM dictionary "
            f"(used by {_list_ids(edit_ids)})"
        )
    return result


# --------------------------------------------------------------------------
# Implementation
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class _Sql:
    text: str
    precedence: int
    kind: str


def _quote_ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _quote_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _one_line(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _list_ids(edit_ids: list[str]) -> str:
    shown = ", ".join(edit_ids[:5])
    if len(edit_ids) > 5:
        shown += f" and {len(edit_ids) - 5} more"
    return shown


def _period_date_sql(period: Period, report_date: str) -> str:
    """SQL for the date a period reference points to, relative to ``report_date``."""
    if period.is_current:
        return report_date
    if period.days:
        sign = "-" if period.days < 0 else "+"
        shifted = f"{report_date} {sign} INTERVAL {abs(period.days)} DAY"
        resolved = f"CAST({shifted} AS DATE)"
    elif period.months:
        sign = "-" if period.months < 0 else "+"
        shifted = f"{report_date} {sign} INTERVAL {abs(period.months)} MONTH"
        resolved = f"CAST(last_day({shifted}) AS DATE)"
    else:
        shifted = report_date
        resolved = report_date
    if period.anchor_month is not None:
        return f"CAST(last_day(make_date(year({shifted}), {period.anchor_month}, 1)) AS DATE)"
    return resolved


def _kind(node: Node) -> str:
    """The kind of value a node produces, before any conversion."""
    if isinstance(node, Number):
        return NUM
    if isinstance(node, String):
        return TEXT
    if isinstance(node, Boolean):
        return BOOL
    if isinstance(node, Concept):
        return ANY
    if isinstance(node, Name):
        return DATE if node.name.lower() in _PERIOD_END_NAMES else ANY
    if isinstance(node, Unary):
        return BOOL if node.op == "not" else NUM
    if isinstance(node, Binary):
        if node.op in ("+", "-", "*", "/", "^"):
            return NUM
        if node.op == "&":
            return TEXT
        return BOOL
    if isinstance(node, Call):
        f = node.function
        if f == "if" and len(node.args) == 3:
            return _combine(_kind(node.args[1]), _kind(node.args[2]))
        if f == "existingof":
            return _combine(*(_kind(arg) for arg in node.args))
        if f in _PRESENCE or f in ("and", "or", "not", "xor", "exact"):
            return BOOL
        if (
            f in _MATH_1
            or f in _DATE_PARTS
            or f
            in (
                "round",
                "trunc",
                "truncate",
                "power",
                "min",
                "max",
                "sum",
                "avg",
                "len",
                "value",
            )
        ):
            return NUM
        if f in _TEXT_1 or f in ("left", "right", "mid", "concatenate", "char"):
            return TEXT
    return ANY


def _combine(*kinds: str) -> str:
    for kind in kinds:
        if kind != ANY:
            return kind
    return ANY


def _comparison_kind(left: Node, right: Node) -> str:
    kinds = {_kind(left), _kind(right)}
    for kind in (TEXT, BOOL, DATE):
        if kind in kinds:
            return kind
    return NUM


class _CheckBuilder:
    def __init__(
        self,
        edit: Edit,
        tree: Node,
        dictionary: MdrmDictionary | None,
        as_of: date | None,
    ) -> None:
        self.edit = edit
        self.tree = tree
        self.dictionary = dictionary
        self.as_of = as_of
        self.refs: dict[str, Concept] = {}
        self.columns: dict[str, str] = {}  # column name in `inputs` -> SQL expression
        self.counted: set[str] = set()
        self.gates: list[str] = []

    # -- entry point -------------------------------------------------------

    def build(self) -> CompiledCheck:
        for concept in concepts(self.tree):
            if concept.period is None:
                raise UnsupportedFormula(
                    f"the period reference [{concept.context}] is not supported yet"
                )
            if concept.dimensions:
                raise UnsupportedFormula(
                    f"references with dimensions such as [{concept.context}] are not supported yet"
                )
            self.refs.setdefault(concept.key, concept)
        if not self.refs:
            raise UnsupportedFormula("the formula does not read any reported item")
        if _kind(self.tree) not in (BOOL, ANY):
            raise UnsupportedFormula("the formula does not produce TRUE or FALSE")
        self._collect_gates(self.tree, exempt=False)
        passed = self.emit(self.tree, BOOL)
        sql, summary_sql = self._render(_wrap(passed, _ATOM))
        return CompiledCheck(
            edit=self.edit,
            sql=sql,
            items=tuple(dict.fromkeys(concept.mdrm for concept in self.refs.values())),
            summary_sql=summary_sql,
        )

    # -- gating ----------------------------------------------------------------

    def _gate(self, condition: str) -> None:
        if condition not in self.gates:
            self.gates.append(condition)

    def _collect_gates(self, node: Node, exempt: bool) -> None:
        if isinstance(node, Concept):
            if not exempt:
                self._gate(f"reported.{_quote_ident(node.key)} IS NOT NULL")
        elif isinstance(node, Call):
            if node.function in _PRESENCE:
                for arg in node.args:
                    self._collect_gates(arg, exempt=True)
            elif node.function == "existingof":
                columns = []
                for arg in node.args:
                    if not isinstance(arg, Concept):
                        raise UnsupportedFormula("ExistingOf() accepts only item references")
                    columns.append(f"reported.{_quote_ident(arg.key)}")
                if columns and not exempt:
                    self._gate(f"COALESCE({', '.join(columns)}) IS NOT NULL")
            else:
                for arg in node.args:
                    self._collect_gates(arg, exempt)
        elif isinstance(node, Unary):
            self._collect_gates(node.operand, exempt)
        elif isinstance(node, Binary):
            self._collect_gates(node.left, exempt)
            self._collect_gates(node.right, exempt)

    # -- columns -------------------------------------------------------------------

    def _value_column(self, concepts_: list[Concept], kind: str) -> _Sql:
        """A typed column in ``inputs`` for one item, or the first reported of several."""
        raws = [f"reported.{_quote_ident(concept.key)}" for concept in concepts_]
        raw = raws[0] if len(raws) == 1 else f"COALESCE({', '.join(raws)})"
        base = "|".join(concept.key for concept in concepts_)
        if kind == NUM:
            name, expression = base, f"TRY_CAST({raw} AS DOUBLE)"
        elif kind == TEXT:
            name, expression = f"{base}:text", f"CAST({raw} AS VARCHAR)"
        elif kind == BOOL:
            name, expression = f"{base}:bool", f"TRY_CAST({raw} AS BOOLEAN)"
        else:
            name, expression = f"{base}:date", f"TRY_CAST({raw} AS DATE)"
        self.columns.setdefault(name, expression)
        return _Sql(_quote_ident(name), _ATOM, kind)

    def _presence_column(self, concept: Concept, function: str) -> _Sql:
        raw = f"reported.{_quote_ident(concept.key)}"
        flag = _PRESENCE[function]
        name = f"{concept.key}:{flag}"
        if flag == "exists":
            self.counted.add(concept.key)
            expression = f"reported.{_quote_ident(concept.key + ':rows')} > 0"
        elif flag == "reported":
            expression = f"{raw} IS NOT NULL"
        else:
            expression = f"{raw} IS NOT NULL AND trim(CAST({raw} AS VARCHAR)) <> ''"
        self.columns.setdefault(name, expression)
        return _Sql(_quote_ident(name), _ATOM, BOOL)

    # -- expressions -------------------------------------------------------------

    def emit(self, node: Node, want: str) -> _Sql:
        if isinstance(node, Concept):
            return self._value_column([node], want if want in _CONCRETE else NUM)
        result = self._emit(node, want)
        return self._coerce(result, want)

    def _coerce(self, sql: _Sql, want: str) -> _Sql:
        have = sql.kind
        if want not in _CONCRETE or have in (want, ANY):
            return sql
        if want == TEXT:
            return _Sql(f"CAST({sql.text} AS VARCHAR)", _ATOM, TEXT)
        if want == NUM and have == TEXT:
            return _Sql(f"TRY_CAST({sql.text} AS DOUBLE)", _ATOM, NUM)
        if want == BOOL and have == TEXT:
            return _Sql(f"TRY_CAST({sql.text} AS BOOLEAN)", _ATOM, BOOL)
        if want == DATE and have == TEXT:
            return _Sql(f"TRY_CAST({sql.text} AS DATE)", _ATOM, DATE)
        raise UnsupportedFormula(f"a {_describe(have)} is used where a {_describe(want)} is needed")

    def _emit(self, node: Node, want: str) -> _Sql:
        if isinstance(node, Number):
            return _Sql(node.text, _ATOM, NUM)
        if isinstance(node, String):
            return _Sql(_quote_literal(node.value), _ATOM, TEXT)
        if isinstance(node, Boolean):
            return _Sql("TRUE" if node.value else "FALSE", _ATOM, BOOL)
        if isinstance(node, Name):
            if node.name.lower() in _PERIOD_END_NAMES:
                return _Sql("report_date", _ATOM, DATE)
            raise UnsupportedFormula(
                f"the name {node.name!r} is not supported yet "
                "(references to other rules are not compiled)"
            )
        if isinstance(node, Unary):
            if node.op == "not":
                operand = self.emit(node.operand, BOOL)
                return _Sql(f"NOT {_wrap(operand, _ATOM)}", _NOT, BOOL)
            operand = self.emit(node.operand, NUM)
            if node.op == "+":
                return operand
            inner = _wrap(operand, _NEG)
            if inner.startswith("-"):
                inner = f"({inner})"  # never emit "--", which starts a SQL comment
            return _Sql(f"-{inner}", _NEG, NUM)
        if isinstance(node, Binary):
            return self._binary(node)
        if isinstance(node, Call):
            return self._call(node, want)
        raise UnsupportedFormula(f"unexpected element {node!r}")  # pragma: no cover

    def _binary(self, node: Binary) -> _Sql:
        op = node.op
        if op in ("+", "-", "*", "/"):
            level = _ADD if op in ("+", "-") else _MUL
            left, right = self.emit(node.left, NUM), self.emit(node.right, NUM)
            text = f"{_wrap(left, level)} {op} {_wrap(right, level + 1)}"
            return _Sql(text, level, NUM)
        if op == "^":
            left, right = self.emit(node.left, NUM), self.emit(node.right, NUM)
            return _Sql(f"power({left.text}, {right.text})", _ATOM, NUM)
        if op == "&":
            left, right = self.emit(node.left, TEXT), self.emit(node.right, TEXT)
            return _Sql(f"{_wrap(left, _ATOM)} || {_wrap(right, _ATOM)}", _CMP, TEXT)
        if op in ("and", "or"):
            level = _AND if op == "and" else _OR
            left, right = self.emit(node.left, BOOL), self.emit(node.right, BOOL)
            text = f"{_wrap(left, level)} {op.upper()} {_wrap(right, level + 1)}"
            return _Sql(text, level, BOOL)
        if op == "xor":
            left, right = self.emit(node.left, BOOL), self.emit(node.right, BOOL)
            return _Sql(f"{_wrap(left, _CMP + 1)} <> {_wrap(right, _CMP + 1)}", _CMP, BOOL)
        # Comparisons.
        kind = _comparison_kind(node.left, node.right)
        left, right = self.emit(node.left, kind), self.emit(node.right, kind)
        return _Sql(f"{_wrap(left, _CMP + 1)} {op} {_wrap(right, _CMP + 1)}", _CMP, BOOL)

    def _call(self, node: Call, want: str) -> _Sql:
        f, args = node.function, node.args

        def arity(low: int, high: int | None = None) -> None:
            high = low if high is None else high
            if not low <= len(args) <= high:
                expected = str(low) if low == high else f"{low} to {high}"
                if high > 99:
                    expected = f"at least {low}"
                raise UnsupportedFormula(
                    f"{node.name}() expects {expected} argument(s), got {len(args)}"
                )

        if f == "if":
            arity(3)
            condition = self.emit(args[0], BOOL)
            kind = want if want in _CONCRETE else _combine(_kind(args[1]), _kind(args[2]))
            kind = kind if kind in _CONCRETE else NUM
            then, otherwise = self.emit(args[1], kind), self.emit(args[2], kind)
            # No ELSE: when the condition cannot be evaluated the result is NULL
            # (reported as an error), rather than silently taking the else branch.
            text = (
                f"CASE WHEN {condition.text} THEN {then.text} "
                f"WHEN NOT {_wrap(condition, _ATOM)} THEN {otherwise.text} END"
            )
            return _Sql(text, _ATOM, kind)
        if f in ("and", "or"):
            arity(1, 999)
            level = _AND if f == "and" else _OR
            parts = [_wrap(self.emit(arg, BOOL), level + 1) for arg in args]
            return _Sql(f" {f.upper()} ".join(parts), level, BOOL)
        if f == "not":
            arity(1)
            operand = self.emit(args[0], BOOL)
            return _Sql(f"NOT {_wrap(operand, _ATOM)}", _NOT, BOOL)
        if f == "xor":
            arity(2)
            return self._binary(Binary("xor", args[0], args[1]))
        if f in _PRESENCE:
            arity(1)
            if not isinstance(args[0], Concept):
                raise UnsupportedFormula(f"{node.name}() accepts only an item reference")
            return self._presence_column(args[0], f)
        if f == "existingof":
            arity(1, 999)
            items = [arg for arg in args if isinstance(arg, Concept)]
            if len(items) != len(args):
                raise UnsupportedFormula("ExistingOf() accepts only item references")
            return self._value_column(items, want if want in _CONCRETE else NUM)
        if f in _MATH_1:
            arity(1)
            return _Sql(f"{_MATH_1[f]}({self.emit(args[0], NUM).text})", _ATOM, NUM)
        if f in ("round", "trunc", "truncate"):
            arity(1, 2)
            sql_name = "round" if f == "round" else "trunc"
            value = self.emit(args[0], NUM).text
            if len(args) == 2:
                return _Sql(f"{sql_name}({value}, {self._integer(args[1])})", _ATOM, NUM)
            return _Sql(f"{sql_name}({value})", _ATOM, NUM)
        if f == "power":
            arity(2)
            base, exponent = self.emit(args[0], NUM), self.emit(args[1], NUM)
            return _Sql(f"power({base.text}, {exponent.text})", _ATOM, NUM)
        if f in ("min", "max"):
            arity(1, 999)
            values = ", ".join(self.emit(arg, NUM).text for arg in args)
            return _Sql(f"{'least' if f == 'min' else 'greatest'}({values})", _ATOM, NUM)
        if f in ("sum", "avg"):
            arity(1, 999)
            total = " + ".join(_wrap(self.emit(arg, NUM), _ADD + 1) for arg in args)
            if f == "sum":
                return _Sql(f"({total})", _ATOM, NUM)
            return _Sql(f"(({total}) / {len(args)})", _ATOM, NUM)
        if f in _DATE_PARTS:
            arity(1)
            return _Sql(f"{_DATE_PARTS[f]}({self.emit(args[0], DATE).text})", _ATOM, NUM)
        if f == "len":
            arity(1)
            return _Sql(f"length({self.emit(args[0], TEXT).text})", _ATOM, NUM)
        if f in _TEXT_1:
            arity(1)
            return _Sql(f"{_TEXT_1[f]}({self.emit(args[0], TEXT).text})", _ATOM, TEXT)
        if f in ("left", "right"):
            arity(2)
            text = self.emit(args[0], TEXT).text
            return _Sql(f"{f}({text}, {self._integer(args[1])})", _ATOM, TEXT)
        if f == "mid":
            arity(3)
            text = self.emit(args[0], TEXT).text
            start, length = self._integer(args[1]), self._integer(args[2])
            return _Sql(f"substr({text}, {start}, {length})", _ATOM, TEXT)
        if f == "concatenate":
            arity(1, 999)
            parts = ", ".join(self.emit(arg, TEXT).text for arg in args)
            return _Sql(f"concat({parts})", _ATOM, TEXT)
        if f == "exact":
            arity(2)
            left, right = self.emit(args[0], TEXT), self.emit(args[1], TEXT)
            return _Sql(f"{_wrap(left, _CMP + 1)} = {_wrap(right, _CMP + 1)}", _CMP, BOOL)
        if f == "value":
            arity(1)
            return _Sql(f"TRY_CAST({self.emit(args[0], TEXT).text} AS DOUBLE)", _ATOM, NUM)
        if f == "char":
            arity(1)
            return _Sql(f"chr({self._integer(args[0])})", _ATOM, TEXT)
        raise UnsupportedFormula(f"the function {node.name}() is not supported yet")

    def _integer(self, node: Node) -> str:
        if isinstance(node, Number) and node.text.isdigit():
            return node.text
        return f"CAST({self.emit(node, NUM).text} AS INTEGER)"

    # -- rendering -----------------------------------------------------------------

    def _render(self, passed: str) -> tuple[str, str]:
        edit = self.edit
        refs = list(self.refs.values())
        mdrms = list(dict.fromkeys(concept.mdrm for concept in refs))
        dates = {
            concept.key: _period_date_sql(concept.period or Period(), "p.report_date")
            for concept in refs
        }

        banner = [
            "-- " + "-" * 72,
            f"-- Edit {_one_line(edit.edit_id)} ({_one_line(edit.edit_type)})",
            f"-- Formula: {_one_line(edit.expression)}",
        ]
        if edit.message:
            banner.append(f"-- Message: {_one_line(edit.message)}")
        if edit.source:
            banner.append(f"-- Source:  {_one_line(edit.source)}")
        banner.append("-- Items:")
        for mdrm in mdrms:
            banner.append(f"--   {mdrm}{self._item_note(mdrm)}")
        banner.append("-- " + "-" * 72)

        selects = ["        p.rssd_id", "        p.report_date"]
        for key, concept in self.refs.items():
            condition = f"i.mdrm = {_quote_literal(concept.mdrm)} AND i.report_date = {dates[key]}"
            selects.append(
                f"        ANY_VALUE(i.value) FILTER (WHERE {condition}) AS {_quote_ident(key)}"
            )
            if key in self.counted:
                selects.append(
                    f"        COUNT(*) FILTER (WHERE {condition}) AS {_quote_ident(key + ':rows')}"
                )
        mdrm_list = ", ".join(_quote_literal(mdrm) for mdrm in mdrms)
        needed_dates = list(dict.fromkeys(dates.values()))
        if len(needed_dates) == 1:
            date_filter = f"        AND i.report_date = {needed_dates[0]}"
        else:
            date_filter = f"        AND i.report_date IN ({', '.join(needed_dates)})"

        inputs = ["        reported.rssd_id", "        reported.report_date"]
        inputs += [
            f"        {expression} AS {_quote_ident(name)}"
            for name, expression in self.columns.items()
        ]
        shown = ", ".join(
            f"{_quote_literal(key + '=')} || "
            f"coalesce(CAST(reported.{_quote_ident(key)} AS VARCHAR), 'not reported')"
            for key in self.refs
        )
        inputs.append(f"        concat_ws('; ', {shown}) AS item_values")

        lines = [
            *banner,
            "WITH reported AS (",
            "    -- Values as reported: one row per institution and report date,",
            "    -- one column per item and period the edit reads.",
            "    SELECT",
            ",\n".join(selects),
            f"    FROM {PERIODS_RELATION} AS p",
            f"    LEFT JOIN {ITEMS_RELATION} AS i",
            "        ON i.rssd_id = p.rssd_id",
            f"        AND i.mdrm IN ({mdrm_list})",
            date_filter,
            "    GROUP BY p.rssd_id, p.report_date",
            "),",
            "inputs AS (",
        ]
        if self.gates:
            lines.append("    -- The edit runs only when every item it uses has been reported.")
        lines += [
            "    SELECT",
            ",\n".join(inputs),
            "    FROM reported",
        ]
        if self.gates:
            lines.append(f"    WHERE {self.gates[0]}")
            lines += [f"      AND {gate}" for gate in self.gates[1:]]
        lines += [
            "),",
            "evaluated AS (",
            "    SELECT",
            "        inputs.*,",
            f"        {passed} AS passed",
            "    FROM inputs",
            ")",
        ]
        check = [
            "SELECT",
            f"    {_quote_literal(edit.edit_id)} AS edit_id,",
            f"    {_quote_literal(edit.edit_type)} AS edit_type,",
            "    rssd_id,",
            "    report_date,",
            "    CASE WHEN passed IS NULL THEN 'error' ELSE 'fail' END AS status,",
            "    item_values",
            "FROM evaluated",
            "WHERE passed IS NOT TRUE",
            "ORDER BY rssd_id, report_date",
        ]
        summary = [
            "SELECT",
            f"    {_quote_literal(edit.edit_id)} AS edit_id,",
            f"    {_quote_literal(edit.edit_type)} AS edit_type,",
            "    count(*) AS evaluated,",
            "    count(*) FILTER (WHERE passed) AS passed,",
            "    count(*) FILTER (WHERE NOT passed) AS failed,",
            "    count(*) FILTER (WHERE passed IS NULL) AS errors",
            "FROM evaluated",
        ]
        body = lines[len(banner) :]
        return "\n".join([*lines, *check]), "\n".join([*body, *summary])

    def _item_note(self, mdrm: str) -> str:
        if self.dictionary is None:
            return ""
        item = self.dictionary.describe(mdrm, self.as_of)
        if item is None:
            if self.dictionary.get(mdrm):
                return f"  (not in effect on {self.as_of} according to the MDRM dictionary)"
            return "  (not found in the MDRM dictionary)"
        return f"  {_one_line(item.item_name)}" if item.item_name else ""


def _wrap(sql: _Sql, minimum: int) -> str:
    """Parenthesize ``sql`` unless it binds at least as tightly as ``minimum``."""
    return sql.text if sql.precedence >= minimum else f"({sql.text})"


def _describe(kind: str) -> str:
    return {NUM: "number", TEXT: "text value", BOOL: "TRUE/FALSE value", DATE: "date"}.get(
        kind, "value"
    )
