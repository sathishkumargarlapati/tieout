"""A synthetic bulk Call Report download, laid out like the FFIEC CDR's.

Every institution, name and value here is made up. The files reproduce the
documented quirks of the real ones: quoted header names, a row of item
descriptions, lines that end with a tab, "CONF" for confidential values, a
line break and an extra tab inside text values, a schedule split into two
parts, and an item that appears in two schedules.
"""

from __future__ import annotations

import zipfile
from pathlib import Path

POR = (
    "IDRSSD\tFDIC Certificate Number\tFinancial Institution Name\tFinancial Institution City\t\n"
    "1001\t11111\tFIRST EXAMPLE BANK\tSPRINGFIELD\t\n"
    "1002\t22222\tSECOND EXAMPLE BANK\tRIVERTON\t\n"
    "1003\t33333\tTHIRD EXAMPLE BANK\tLAKEVIEW\t\n"
)

RC = (
    '"IDRSSD"\t"RCON2170"\t"RCON2160"\t"RCON2948"\t\n'
    '\t"TOTAL ASSETS"\t"OTHER ASSETS"\t"TOTAL LIABILITIES"\t\n'
    "1001\t1000\t50\t900\t\n"
    "1002\t2000\tCONF\t1800\t\n"
    "1003\t3000\t-5\t\t\n"
    "TOTAL\t\t\t\t\n"
)

RCRII_1 = '"IDRSSD"\t"RCONA223"\t\n\t"TOTAL RISK-WEIGHTED ASSETS"\t\n1001\t700\t\n1002\t1500\t\n'

# RCON2170 appears again: the same value for 1001, a different one for 1002.
RCRII_2 = (
    '"IDRSSD"\t"RCON2170"\t"RCONB696"\t\n'
    '\t"TOTAL ASSETS"\t"SYNTHETIC ITEM"\t\n'
    "1001\t1000\t10\t\n"
    "1002\t2001\t20\t\n"
)

# A line break inside 1001's text, and an extra tab inside 1002's.
RIE = (
    '"IDRSSD"\t"RIAD4468"\t"TEXT4468"\t\n'
    '\t"AMOUNT"\t"TEXT"\t\n'
    "1001\t25\tLine one\nline two\t\n"
    "1002\t30\tA\tB\t\n"
)

FILES = {
    "Readme.txt": "Synthetic test data.\n",
    "FFIEC CDR Call Bulk POR 06302026.txt": POR,
    "FFIEC CDR Call Schedule RC 06302026.txt": RC,
    "FFIEC CDR Call Schedule RCRII 06302026(1 of 2).txt": RCRII_1,
    "FFIEC CDR Call Schedule RCRII 06302026(2 of 2).txt": RCRII_2,
    "FFIEC CDR Call Schedule RIE 06302026.txt": RIE,
    "FFIEC CDR Call Bulk Other 06302026.txt": "IDRSSD\t\n1001\t\n",
}

EXPECTED_ITEMS = {
    (1001, "RCON2170", "1000"),
    (1001, "RCON2160", "50"),
    (1001, "RCON2948", "900"),
    (1001, "RCONA223", "700"),
    (1001, "RCONB696", "10"),
    (1001, "RIAD4468", "25"),
    (1001, "TEXT4468", "Line one line two"),
    (1002, "RCON2948", "1800"),
    (1002, "RCONA223", "1500"),
    (1002, "RCONB696", "20"),
    (1002, "RIAD4468", "30"),
    (1002, "TEXT4468", "A B"),
    (1003, "RCON2170", "3000"),
    (1003, "RCON2160", "-5"),
}


def write_bulk_zip(directory: Path, files: dict[str, str] | None = None) -> Path:
    path = directory / "FFIEC CDR Call Bulk All Schedules 06302026.zip"
    with zipfile.ZipFile(path, "w") as archive:
        for name, text in (files or FILES).items():
            archive.writestr(name, text.encode("utf-8"))
    return path


def write_bulk_folder(directory: Path, files: dict[str, str] | None = None) -> Path:
    folder = directory / "bulk"
    folder.mkdir()
    for name, text in (files or FILES).items():
        (folder / name).write_bytes(text.encode("utf-8"))
    return folder
