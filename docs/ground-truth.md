# Ground Truth: checking TieOut against every filed Call Report

A Call Report must pass every validity edit before it is filed. That makes the public Call Reports of every filer a natural test set for TieOut: run the compiled validity edits on all of them, and count how often TieOut agrees with the reports the agencies accepted.

Every disagreement is a lead. Either TieOut has a gap (in reading the taxonomy, parsing a formula or compiling it), or the public data differs from the report as filed.

## Run it

1. Download the public data for one quarter from the [FFIEC CDR bulk download](https://cdr.ffiec.gov/public/PWS/DownloadBulkData.aspx): product "Call Reports -- Single Period", tab-delimited format. You get `FFIEC CDR Call Bulk All Schedules MMDDYYYY.zip`.
2. Download the Call Report taxonomy for the same report date from the [FFIEC CDR](https://cdr.ffiec.gov/public/DownloadTaxonomy.aspx), and extract its edits:

   ```bash
   tieout extract taxonomy.zip --out edits.csv
   ```

3. Run:

   ```bash
   tieout ground-truth "FFIEC CDR Call Bulk All Schedules 06302026.zip" --edits edits.csv --out-dir ground-truth
   ```

Read the FFIEC CDR's terms of use before you download.

## What you get

| File | Contents |
|---|---|
| `ground-truth.md` | The report: institutions checked, edits run, the agreement rate, every edit that disagreed with a filed report (with an example institution and the values involved), edits that never ran, and edits not compiled yet |
| `ground-truth.csv` | One row per edit: institutions evaluated, passed, failed, could not be evaluated, not run |
| `failures.csv` | Every disagreement: edit, institution, report date, status and the values read |

The agreement rate is the share of evaluations that passed. An evaluation is one edit run for one institution.

## How to read the results

- **Edits run only on reported items.** Following the FFIEC CDR Interchange Specification, an edit runs for an institution only when every item it uses is in the data. Confidential items are not published (the bulk files mark them `CONF`), so edits that use them do not run. The report counts these as "not run", outside the agreement rate.
- **Forms.** Edits are written for a form (FFIEC 031, 041 or 051). An edit for one form can disagree with a report filed on another. To check one form, put its filers' RSSD IDs in a file, one per line, and add `--only-rssd filers.txt`.
- **Same quarter.** Use the taxonomy for the same report date as the data, because edits change over time.
- **Amended reports.** The bulk data holds each institution's latest submission.

## How the bulk files are read

`tieout bulk` converts the download into TieOut's long format, which you can also use on its own:

```bash
tieout bulk "FFIEC CDR Call Bulk All Schedules 06302026.zip" --out items.parquet
```

The zip holds a Panel of Reporters file listing the institutions, and one tab-delimited file per schedule; large schedules come in parts such as `(1 of 2)`. Each schedule file has a header row of MDRM identifiers starting with `IDRSSD`, then a row of item descriptions, then one row per institution.

Researchers who process these files have documented their quirks (see [ffiec.pq](https://iangow.github.io/ffiec.pq/)). TieOut handles each one and counts what it did:

- Values are not quoted, and every line ends with a tab. A line break inside a text value is detected because the line before it does not end with a tab, and the two pieces are joined with a space.
- A tab inside a text value adds a field. Extra fields are folded into the last column.
- `CONF` marks a confidential value; it is left out and counted.
- An empty field means the item was not reported.
- The same item can appear in more than one schedule. Identical values are kept once. If two schedules disagree, the item is left out for that institution and counted.

A run on synthetic data of the same size (4,300 institutions, 60 schedule files, about 10 million values) loads in under 30 seconds.
