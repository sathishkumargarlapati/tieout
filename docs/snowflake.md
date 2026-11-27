# Running the checks in Snowflake

`tieout compile` can write the checks in Snowflake SQL, so they run where the data already is:

```bash
tieout compile edits.csv --mdrm MDRM.zip --as-of 2026-09-30 --dialect snowflake --out checks.sql
```

The script reads the same two relations as the DuckDB checks, and each check returns the same columns: `edit_id`, `edit_type`, `rssd_id`, `report_date`, `status` and `item_values`.

## Create the two relations

Create `tieout_items` and `tieout_periods` as views over your own table, in the database and schema where you will run the script:

```sql
CREATE OR REPLACE VIEW tieout_items AS
SELECT bank_id AS rssd_id, period_end::DATE AS report_date, UPPER(TRIM(item)) AS mdrm, amount AS value
FROM my_call_report_items;

CREATE OR REPLACE VIEW tieout_periods AS
SELECT DISTINCT rssd_id, report_date FROM tieout_items
WHERE report_date = '2026-09-30'::DATE;
```

[Preparing your data](your-data.md) describes the columns. Include the earlier quarters the edits compare against in `tieout_items`, and only the report date to check in `tieout_periods`.

Unlike `tieout check`, the SQL does not check the data before it runs. Make sure each institution, report date and item has one value: if an item has two, a check reads one of them.

## Run the script

The script holds one query per edit, separated by semicolons. Run it in a Snowsight worksheet or with any client that runs a file of statements. Each query returns the institutions and report dates where its edit does not pass. An empty result means the edit passed, or did not run because an item it uses was not reported.

## How the Snowflake SQL differs

The checks mean the same thing in both dialects. Where Snowflake SQL needs different syntax, TieOut writes it:

| DuckDB | Snowflake |
|---|---|
| `ANY_VALUE(i.value) FILTER (WHERE ...)` | `MAX(CASE WHEN ... THEN i.value END)` |
| `COUNT(*) FILTER (WHERE ...)` | `COUNT(CASE WHEN ... THEN 1 END)` |
| `report_date - INTERVAL 3 MONTH` | `DATEADD(month, -3, report_date)` |
| `make_date(year, month, 1)` | `DATE_FROM_PARTS(year, month, 1)` |
| `TRY_CAST(value AS DOUBLE)` | `TRY_CAST(CAST(value AS VARCHAR) AS DOUBLE)`, because Snowflake's `TRY_CAST` accepts only text |
| `WHERE passed IS NOT TRUE` | `WHERE NOT COALESCE(passed, FALSE)` |

Two results can differ, because the two databases treat them differently:

- **Division by zero.** Snowflake stops the whole query when it divides by zero. The Snowflake checks divide by `NULLIF(divisor, 0)`, so an institution whose divisor is zero gets status `error` and the others are still checked. DuckDB divides by zero without stopping and gives infinity.
- **`Min` and `Max` with a value that is not a number.** Snowflake's `LEAST` and `GREATEST` return NULL when any argument is NULL, so the edit gets status `error`. DuckDB's skip that argument.

## How it is tested

TieOut's tests do not use a Snowflake account. Each Snowflake check is parsed as Snowflake SQL and translated back into DuckDB with [sqlglot](https://github.com/tobymao/sqlglot), which models Snowflake's behavior. The tests then run it on synthetic data and compare it with the DuckDB check. If the checks give unexpected results in your Snowflake account, please [open an issue](https://github.com/sathishkumargarlapati/tieout/issues).

## Sources

- [Snowflake: TRY_CAST](https://docs.snowflake.com/en/sql-reference/functions/try_cast)
- [Snowflake: DIV0](https://docs.snowflake.com/en/sql-reference/functions/div0) (shows that `1/0` is an error)
- [Snowflake: LEAST](https://docs.snowflake.com/en/sql-reference/functions/least)
- [Snowflake: LAST_DAY](https://docs.snowflake.com/en/sql-reference/functions/last_day)
- [Snowflake: DATE_FROM_PARTS](https://docs.snowflake.com/en/sql-reference/functions/date_from_parts)
