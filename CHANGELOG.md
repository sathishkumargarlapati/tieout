# Changelog

## 0.2.0 (unreleased)

The second module: Ground Truth.

- `tieout ground-truth` runs the compiled validity edits on every institution's public Call Report for a quarter. It reports how often TieOut agrees with the reports the agencies accepted, and lists every disagreement with an example institution and the values involved. Writes `ground-truth.md`, `ground-truth.csv` and `failures.csv`.
- `tieout bulk` converts the FFIEC CDR bulk download ("Call Reports -- Single Period", tab-delimited) into TieOut's long format, as Parquet or CSV. It handles the files' documented quirks (line breaks and tabs inside text values, `CONF` for confidential values, schedules split into parts, items repeated across schedules) and counts every repair.
- `--only-rssd` limits a ground-truth run to a list of institutions, for example the filers of one form.
- Each compiled check now also has a counted form (`CompiledCheck.summary_sql`): how many institutions the edit ran on, and how many passed, failed or could not be evaluated.
- Python API: `tieout.load_bulk` and `tieout.run_ground_truth`.

## 0.1.0 (unreleased)

The first module: the rule compiler.

- `tieout extract` reads the Call Report edits (formula, type and published message) from the FFIEC's XBRL taxonomy, either the zip file or an unpacked folder, into a CSV file people can review.
- `tieout compile` turns each edit into one readable, standalone DuckDB SQL query. The query names the edit, quotes the official formula and lists the items it reads, named from the Federal Reserve's MDRM dictionary when that is supplied.
- `tieout check` runs the edits against a CSV file, a Parquet file or a DuckDB table. It shows the values behind every failure and exits with status 1 when a validity edit fails.
- `tieout mdrm` looks items up in the MDRM dictionary.
- Parses the edit formula language described in the FFIEC CDR Interchange Specification: periods such as `-P1Q` and `-P1Y/-06-end`, `If`, the `Exists` family, and math, date and text functions.
- Edits that cannot be compiled yet are listed with a reason and are never dropped silently.
