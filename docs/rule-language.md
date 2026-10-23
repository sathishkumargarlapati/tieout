# The edit formula language

The FFIEC publishes every Call Report edit as a formula in the Call Report XBRL taxonomy. The syntax comes from the FFIEC's [CDR Interchange Specification](https://cdr.ffiec.gov/public/Files/CDR-Interchange-Specification.pdf). This page explains how TieOut reads that syntax and turns each formula into SQL.

A formula is a test that must come out TRUE. Two examples from the specification:

```text
cc:RCON2170[P0] <> cc:RCON2170[-P1Q]
If (cc:RCONF049[-P1Q] > 0, cc:RCONF049[P0] > 0, TRUE)
```

## Items and periods

`cc:RCON2170` is a reported item, named by its eight-character MDRM identifier. The prefix before the colon (such as `cc`) names the taxonomy schema; TieOut matches items by the MDRM identifier alone.

The brackets name the period to read, relative to the report date being checked:

| Written as | Meaning | On 31 December 2026 it reads |
|---|---|---|
| `[P0]`, or no brackets | The report date itself | 31 Dec 2026 |
| `[-P1Q]` | One quarter earlier | 30 Sep 2026 |
| `[-P2Q]` | Two quarters earlier | 30 Jun 2026 |
| `[-P1Y]` | One year earlier | 31 Dec 2025 |
| `[-P1M]` | One month earlier | 30 Nov 2026 |
| `[-P1D]` | One day earlier | 30 Dec 2026 |
| `[-P1Y/-06-end]` or `[-P1Y/06-end]` | End of June, one year earlier | 30 Jun 2025 |

Quarter, month and year offsets land on the last day of the month, so they line up with Call Report dates (quarter-ends).

## Operators

From loosest to tightest binding:

| Operators | Meaning |
|---|---|
| `Xor` | Exactly one side is TRUE |
| `Or` | Either side is TRUE |
| `And` | Both sides are TRUE |
| `Not` | Negation |
| `=` `<>` `!=` `<` `>` `<=` `>=` `=<` `=>` | Comparisons (they do not chain: write `(a < b) And (b < c)`) |
| `&` | Joins text |
| `+` `-` | Addition, subtraction |
| `*` `/` | Multiplication, division |
| `-` (prefix) | Negation |
| `^` | Power |

Keywords and function names are case-insensitive: `If`, `IF` and `if` are the same.

## Functions

| Function | Meaning |
|---|---|
| `If(condition, then, else)` | `then` when the condition is TRUE, otherwise `else` |
| `And(a, b, ...)`, `Or(a, b, ...)`, `Not(a)`, `Xor(a, b)` | Function forms of the logical operators |
| `Exists(item)` | The item was reported, even if empty |
| `ExistsNonNil(item)` | The item was reported with a value |
| `ExistsNonEmpty(item)` | The item was reported with a value that is not blank |
| `ExistingOf(item, item, ...)` | The first of the items that was reported |
| `Abs`, `Sqrt`, `Sign`, `Int` | Absolute value, square root, sign, round down to a whole number |
| `Round(x)`, `Round(x, digits)` | Round (halves round away from zero) |
| `Trunc(x)`, `Truncate(x, digits)` | Cut off digits without rounding |
| `Power(x, y)` | x to the power y |
| `Min(...)`, `Max(...)`, `Sum(...)`, `Avg(...)` | Smallest, largest, total and average of the arguments |
| `MonthOf(d)`, `YearOf(d)`, `DayOf(d)` (also `Month`, `Year`, `Day`) | Parts of a date |
| `Context.Period.EndDate` (also `Context.PeriodEndDate`) | The report date being checked |
| `Len`, `Upper`, `Lower`, `Trim`, `Left`, `Right`, `Mid`, `Concatenate`, `Exact`, `Value`, `Char` | Text functions |

## How an edit runs

**Missing items.** The CDR Interchange Specification says an edit must not run if any of the items it uses are missing. TieOut follows that rule. An item counts as missing when there is no row for it, or its value is empty (NULL). There are two exceptions:

- Items inside `Exists`, `ExistsNonNil` and `ExistsNonEmpty` do not stop an edit, because those functions are testing whether the item was reported.
- An edit using `ExistingOf(a, b, ...)` runs when at least one of the items is reported.

**Values.** Each value is read as the formula needs it. In arithmetic and number comparisons it is read as a number. Compared with quoted text, it is read as text. Compared with TRUE or FALSE, it is read as TRUE/FALSE, and the text `true` and `false` both work.

**Results.** Each check returns one row per institution and report date where the edit does not pass:

- `fail`: the formula came out FALSE.
- `error`: the formula could not be evaluated, usually because a value that has to be a number is not one. This includes an `If` whose condition cannot be evaluated: TieOut reports an error rather than quietly taking the `else` branch.

The row also shows every value the edit read, for example `RCON2170@P0=98000; RCON2170@-P1Q=98000`, so the reason for a failure is visible straight away.

**Arithmetic.** Numbers are computed as double-precision floating point, as in DuckDB. Whole-number amounts are exact. `Round` rounds halves away from zero; the specification does not say which rounding rule to use, so treat edits that depend on rounding a value ending in exactly .5 with care.

## What is not compiled yet

When an edit uses something not listed above, TieOut does not compile it. Examples:

- references with extra dimensions, such as `[P0, Context.Scenario.Branch]`;
- references to other rules by name, such as `RR1`;
- functions not in the table above;
- formulas that do not come out TRUE or FALSE.

These edits are never dropped silently. `tieout compile` and `tieout check` list each one with the reason, and the generated SQL script lists them at the end.
