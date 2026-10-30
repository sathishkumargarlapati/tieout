# TieOut

**Know that your Call Report ties out before filing day.**

Every quarter, about 4,300 U.S. banks and savings institutions file a Call Report with their federal regulators. It is one of the most important records of the condition of American banking. Supervisors at the Federal Reserve, the FDIC and the OCC use it to watch over the safety of each bank and the soundness of the nation's financial system. It informs the Federal Reserve's monetary policy work. Analysts, researchers and the public read it.

A wrong number in that record does not stay inside one bank.

TieOut brings the regulators' own published validation rules into the bank's data warehouse, so errors are found where they start: days before filing, by the people who can fix them.

## Why it matters

**For banks.** Before a Call Report can be filed, it must pass the edits published by the FFIEC agencies. Validity edits must be corrected; no explanation is accepted. Quality edits must be corrected or explained in writing. Reports are due within 30 days of each quarter-end, or 35 for some banks with foreign offices. In many banks, these edits run late, in filing software, after the report has been assembled, and every failure starts a search backwards through the data. TieOut runs the same rules early, inside the warehouse, as often as the team needs.

**For supervisors.** TieOut is built so that problems are caught before a report is filed, not after it reaches the Federal Reserve, the FDIC or the OCC.

**For the whole industry.** Every bank applies the same public rules. TieOut gives them one open implementation that anyone can read, test and improve.

## How it works

1. **Official rules in.** TieOut reads the Call Report edits directly from the FFIEC's published XBRL taxonomy for the reporting period.
2. **Readable checks out.** Each edit becomes a plain SQL check that carries its official identifier, so every result traces back to the rule behind it.
3. **Run where the data lives.** The checks run against the table where your pipeline computes Call Report item values, keyed by MDRM code, as often as you like before filing.

## Get started

TieOut needs Python 3.10 or later.

```bash
git clone https://github.com/sathishkumargarlapati/tieout.git
cd tieout
pip install .
tieout check --data examples/items.csv --edits examples/edits.csv --report-date 2026-06-30
```

```text
edit_id  edit_type  rssd_id  report_date  status  item_values
EX-01    validity   9000002  2026-06-30   fail    RCON2160@P0=-25
EX-02    validity   9000003  2026-06-30   fail    RCON2710@P0=160; RCON5597@P0=0
EX-03    quality    9000003  2026-06-30   fail    RIADC435@P0=25; RIADC390@P0=12; RIAD3123@P0=30
EX-04    quality    9000002  2026-06-30   fail    RCON2170@P0=98000; RCON2170@-P1Q=98000
EX-05    quality    9000003  2026-06-30   fail    RCONF049@-P1Q=75; RCONF049@P0=0
Checked 3 institution report date(s) against 5 edit(s).
Failures: 5 (quality 3, validity 2).
1 edit(s) were not compiled and did not run.
Edits not compiled, by reason:
      1  references with dimensions such as [P0, Context.Scenario.Branch] are not supported yet
```

Every failure shows the values that caused it. Nothing is hidden: the one edit this version cannot compile is named, with the reason. The example data is synthetic, and the example edits are adapted from the formula examples in the FFIEC's [CDR Interchange Specification](https://cdr.ffiec.gov/public/Files/CDR-Interchange-Specification.pdf).

## Run the official edits on your data

1. Download the Call Report taxonomy for your reporting period from the [FFIEC CDR](https://cdr.ffiec.gov/public/DownloadTaxonomy.aspx).
2. Download `MDRM.zip`, the Federal Reserve's [data dictionary](https://www.federalreserve.gov/apps/mdrm/download_mdrm.htm), which names every Call Report item.
3. Run:

```bash
tieout extract taxonomy.zip --out edits.csv
tieout compile edits.csv --mdrm MDRM.zip --as-of 2026-09-30 --out checks.sql
tieout check --data items.parquet --edits edits.csv --report-date 2026-09-30 --out failures.csv
```

| Command | What it does |
|---|---|
| `tieout extract` | Reads every edit from the taxonomy (its official identifier, type, formula and published message) into a CSV file your team can review, compare between quarters and keep under version control. It reports how many formulas it found, of which types, and how each edit was identified, so you can confirm the read. |
| `tieout compile` | Writes one readable SQL query per edit. Each query names the edit, quotes the official formula and lists the items it reads, named from the MDRM dictionary. |
| `tieout check` | Runs the edits with DuckDB against a CSV file, a Parquet file or a DuckDB table, and saves every failure with the values behind it. It exits with status 1 when a validity edit fails, so a data pipeline can stop a bad number before it travels any further. |
| `tieout mdrm` | Looks items up in the MDRM dictionary: name, type, confidentiality, reporting forms and the dates they are in effect. |

The data to check is one long table: institution, report date, MDRM item and value. [Preparing your data](docs/your-data.md) explains it, and [The edit formula language](docs/rule-language.md) explains how each formula becomes SQL.

## Check TieOut against every filed Call Report

A Call Report must pass every validity edit before it is filed, so every institution's public Call Report is a test case. `tieout ground-truth` runs the compiled validity edits on all of them for a quarter and reports how often TieOut agrees with the reports the agencies accepted. Every disagreement is listed with the institution and the values involved, so it can be traced and fixed, and anyone can rerun the measurement from the public data.

```bash
tieout ground-truth "FFIEC CDR Call Bulk All Schedules 06302026.zip" --edits edits.csv --out-dir ground-truth
```

The data is the FFIEC CDR's free [bulk download](https://cdr.ffiec.gov/public/PWS/DownloadBulkData.aspx). [Ground Truth](docs/ground-truth.md) explains the method and how to read the results. `tieout bulk` converts the same download into TieOut's long format, as Parquet or CSV, for any other use.

## Principles

- **The regulators' rules, nothing invented.** Every check is generated from the agencies' published materials and cites its source.
- **Transparent by design.** Checks are plain SQL that a reviewer, an auditor or an examiner can read.
- **Nothing dropped silently.** An edit TieOut cannot compile yet is listed with the reason. An edit that cannot be evaluated on your data is reported as an error, never as a pass.
- **Public information only.** TieOut is built only from public regulatory materials and tested only with public or synthetic data. It contains no confidential data or code from any bank or employer.
- **Open to every institution.** Apache 2.0 licensed and free, for the smallest community bank and the largest institution alike.

## Status

TieOut is released one module at a time. Shipped so far:

1. **The rule compiler** (0.1): the regulators' published edits in, readable SQL checks out.
2. **Ground Truth** (0.2): those checks tested against every institution's public Call Report.

TieOut reads the formula linkbase as the FFIEC's CDR Interchange Specification describes it, and it is tested with the specification's own formula examples and with synthetic data. Ground Truth exists to measure it against real filings. If TieOut reads your taxonomy or data differently than you expect, please [open an issue](https://github.com/sathishkumargarlapati/tieout/issues). Watch this repository to follow each release.

TieOut does not replace the validation performed by the FFIEC Central Data Repository (CDR), your filing software, or your own review.

## Official sources

- [FFIEC CDR: taxonomy download](https://cdr.ffiec.gov/public/HelpFiles/DownloadTaxHelp.htm) (includes the Call Report edits)
- [FFIEC CDR: Interchange Specification](https://cdr.ffiec.gov/public/Files/CDR-Interchange-Specification.pdf) (the edit formula language)
- [FFIEC CDR: bulk Call Report data](https://cdr.ffiec.gov/public/PWS/DownloadBulkData.aspx)
- [Federal Reserve: Micro Data Reference Manual (MDRM)](https://www.federalreserve.gov/apps/mdrm/about_mdrm.htm)
- [Federal Reserve: MDRM download](https://www.federalreserve.gov/apps/mdrm/download_mdrm.htm)
- [FDIC: resolving Call Report edits](https://www.fdic.gov/resources/bankers/call-reports/edits/resolvingedits.pdf)
- [FDIC: Call Report filing deadlines, first quarter 2026](https://fdic.gov/news/financial-institution-letters/2026/consolidated-reports-condition-and-income-first-quarter)
- [FDIC: Quarterly Banking Profile, first quarter 2026](https://www.fdic.gov/news/press-releases/2026/fdic-insured-institutions-reported-return-assets-126-percent-and-net)
- [Federal Reserve: purpose and uses of Call Report data](https://www.federalreserve.gov/reportforms/formsreview/FFIEC031_FFIEC041_20120120_omb.pdf)

## Contributing

Contributions are welcome. Please use only public regulatory materials and public or synthetic data. Never submit confidential bank data, credentials, or code you do not have the right to share.

To work on TieOut:

```bash
pip install -e ".[dev]"
ruff check . && ruff format --check .
pytest
```

## License

Licensed under the [Apache License 2.0](LICENSE).

## Disclaimer

TieOut is an independent open-source project. It is not affiliated with, endorsed by, or certified by the FFIEC, the FDIC, the Federal Reserve, the OCC, or any other agency or company. It is provided "as is", without warranty of any kind, and is not a substitute for official validation or a bank's own review. Users remain responsible for the accuracy of their regulatory reports.

## Author

Created and maintained by [Sathish Kumar Garlapati](https://github.com/sathishkumargarlapati).
