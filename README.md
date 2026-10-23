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

## Principles

- **The regulators' rules, nothing invented.** Every check is generated from the agencies' published materials and cites its source.
- **Transparent by design.** Checks are plain SQL that a reviewer, an auditor or an examiner can read.
- **Public information only.** TieOut is built only from public regulatory materials and tested only with public or synthetic data. It contains no confidential data or code from any bank or employer.
- **Open to every institution.** Apache 2.0 licensed and free, for the smallest community bank and the largest institution alike.

## Status

TieOut is in active early development and is released one module at a time. The first module, the rule compiler, is being built now. Watch this repository to follow each release.

TieOut does not replace the validation performed by the FFIEC Central Data Repository (CDR), your filing software, or your own review.

## Official sources

- [FFIEC CDR: taxonomy download](https://cdr.ffiec.gov/public/HelpFiles/DownloadTaxHelp.htm) (includes the Call Report edits)
- [FFIEC CDR: bulk Call Report data](https://cdr.ffiec.gov/public/PWS/DownloadBulkData.aspx)
- [Federal Reserve: Micro Data Reference Manual (MDRM)](https://www.federalreserve.gov/apps/mdrm/about_mdrm.htm)
- [FDIC: resolving Call Report edits](https://www.fdic.gov/resources/bankers/call-reports/edits/resolvingedits.pdf)
- [FDIC: Call Report filing deadlines, first quarter 2026](https://fdic.gov/news/financial-institution-letters/2026/consolidated-reports-condition-and-income-first-quarter)
- [FDIC: Quarterly Banking Profile, first quarter 2026](https://www.fdic.gov/news/press-releases/2026/fdic-insured-institutions-reported-return-assets-126-percent-and-net)
- [Federal Reserve: purpose and uses of Call Report data](https://www.federalreserve.gov/reportforms/formsreview/FFIEC031_FFIEC041_20120120_omb.pdf)

## Contributing

Contributions are welcome. Please use only public regulatory materials and public or synthetic data. Never submit confidential bank data, credentials, or code you do not have the right to share.

## License

Licensed under the [Apache License 2.0](LICENSE).

## Disclaimer

TieOut is an independent open-source project. It is not affiliated with, endorsed by, or certified by the FFIEC, the FDIC, the Federal Reserve, the OCC, or any other agency or company. It is provided "as is", without warranty of any kind, and is not a substitute for official validation or a bank's own review. Users remain responsible for the accuracy of their regulatory reports.

## Author

Created and maintained by [Sathish Kumar Garlapati](https://github.com/sathishkumargarlapati).
