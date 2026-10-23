# Changelog

## 0.1.0 (unreleased)

The first module: the rule compiler.

- `tieout extract` reads the Call Report edits (formula, type and published message) from the FFIEC's XBRL taxonomy, either the zip file or an unpacked folder, into a CSV file people can review.
- `tieout compile` turns each edit into one readable, standalone DuckDB SQL query. The query names the edit, quotes the official formula and lists the items it reads, named from the Federal Reserve's MDRM dictionary when that is supplied.
- `tieout check` runs the edits against a CSV file, a Parquet file or a DuckDB table. It shows the values behind every failure and exits with status 1 when a validity edit fails.
- `tieout mdrm` looks items up in the MDRM dictionary.
- Parses the edit formula language described in the FFIEC CDR Interchange Specification: periods such as `-P1Q` and `-P1Y/-06-end`, `If`, the `Exists` family, and math, date and text functions.
- Edits that cannot be compiled yet are listed with a reason and are never dropped silently.
