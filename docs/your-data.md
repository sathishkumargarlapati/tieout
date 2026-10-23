# Preparing your data

TieOut checks a single long table. Each row is one item, for one institution, on one report date:

| rssd_id | report_date | mdrm | value |
|---|---|---|---|
| 9000001 | 2026-06-30 | RCON2170 | 262500 |
| 9000001 | 2026-06-30 | RCON2160 | 1350 |
| 9000001 | 2026-03-31 | RCON2170 | 250000 |

- **rssd_id** identifies the institution, normally by its RSSD ID. Any stable identifier works.
- **report_date** is the report date (a quarter-end). Use `YYYY-MM-DD`. `MM/DD/YYYY` and `YYYYMMDD` are also accepted.
- **mdrm** is the eight-character MDRM identifier of the item, such as `RCON2170`. Case and surrounding spaces don't matter.
- **value** is the value as it will be reported: a number, text, or `true`/`false`.

The table can be a CSV file, a Parquet file, or a table or view in a DuckDB database. Your own column names work too:

```bash
tieout check --data items.parquet --edits edits.csv \
  --entity-column bank_id --date-column period_end --item-column item --value-column amount
```

## Include the quarters the edits compare against

Some edits compare this quarter with earlier ones, for example `cc:RCON2170[P0] <> cc:RCON2170[-P1Q]`. Include the earlier quarters in the data, and use `--report-date` to check only the latest:

```bash
tieout check --data items.parquet --edits edits.csv --report-date 2026-09-30
```

If an earlier quarter is missing, an edit that needs it does not run. This follows the specification's rule for missing items.

## One value per item

Each institution, report date and item may have only one value. If the same item has two different values, TieOut stops and names the item instead of guessing which one is right. Repeated rows with the same value are accepted.

## Units

Most edits compare items with each other, so units don't matter. An edit that compares an amount with a fixed number does depend on units. Such edits are written for the XBRL reports the agencies receive, in which amounts are in dollars. If your amounts are in thousands, as in the CDR's bulk download files, review those edits before relying on them.

## Running the SQL yourself

`tieout compile` writes a SQL script with one standalone query per edit. To run it in DuckDB yourself, first create the two relations it reads:

```sql
CREATE VIEW tieout_items AS
SELECT bank_id AS rssd_id, period_end AS report_date, item AS mdrm, amount AS value
FROM my_call_report_items;

CREATE VIEW tieout_periods AS
SELECT DISTINCT rssd_id, report_date FROM tieout_items
WHERE report_date = DATE '2026-09-30';
```

Each query returns the institutions and report dates where its edit does not pass.

## From Python

```python
from datetime import date

import tieout

edits = tieout.read_edits_csv("edits.csv")
report = tieout.check("items.parquet", edits, report_date=date(2026, 9, 30))

print(report.summary())
for failure in report.failures:
    print(failure.edit_id, failure.edit_type, failure.rssd_id, failure.status, failure.item_values)
report.write_csv("failures.csv")
```
