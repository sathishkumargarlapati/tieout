from __future__ import annotations

from datetime import date
from pathlib import Path

import duckdb
import pytest

from tieout.cdr import BULK_ITEMS_TABLE, INSTITUTIONS_TABLE, BulkFormatError, load_bulk

from .bulk_fixture import EXPECTED_ITEMS, FILES, write_bulk_folder, write_bulk_zip


def items(connection: duckdb.DuckDBPyConnection) -> set[tuple[int, str, str]]:
    return set(
        connection.execute(f"SELECT rssd_id, mdrm, value FROM {BULK_ITEMS_TABLE}").fetchall()
    )


def test_read_zip(tmp_path: Path) -> None:
    connection = duckdb.connect()
    bulk = load_bulk(connection, write_bulk_zip(tmp_path))
    assert items(connection) == EXPECTED_ITEMS
    assert bulk.report_date == date(2026, 6, 30)
    assert bulk.schedules == ["RC", "RCRII", "RIE"]
    assert len(bulk.files_read) == 5
    assert bulk.files_skipped == ["FFIEC CDR Call Bulk Other 06302026.txt"]
    assert bulk.filers == 3
    assert bulk.values == 14
    assert bulk.confidential == 1
    assert bulk.joined_lines == 1
    assert bulk.folded_tabs == 1
    assert bulk.skipped_rows == 1
    assert bulk.conflicts == 1
    dates = connection.execute(f"SELECT DISTINCT report_date FROM {BULK_ITEMS_TABLE}").fetchall()
    assert dates == [(date(2026, 6, 30),)]


def test_panel_of_reporters(tmp_path: Path) -> None:
    connection = duckdb.connect()
    bulk = load_bulk(connection, write_bulk_zip(tmp_path))
    assert bulk.institution_name(1003) == "THIRD EXAMPLE BANK"
    assert bulk.institution_name("9999") == ""
    assert bulk.institutions["1001"]["FDIC Certificate Number"] == "11111"
    names = connection.execute(f"SELECT * FROM {INSTITUTIONS_TABLE} ORDER BY rssd_id").fetchall()
    assert names[0] == (1001, "FIRST EXAMPLE BANK")


def test_panel_with_a_description_row(tmp_path: Path) -> None:
    files = dict(FILES)
    lines = FILES["FFIEC CDR Call Bulk POR 06302026.txt"].splitlines(keepends=True)
    files["FFIEC CDR Call Bulk POR 06302026.txt"] = "".join(
        [lines[0], "\tFDIC CERT\tNAME\tCITY\t\n", *lines[1:]]
    )
    bulk = load_bulk(duckdb.connect(), write_bulk_zip(tmp_path, files))
    assert sorted(bulk.institutions) == ["1001", "1002", "1003"]


def test_read_folder(tmp_path: Path) -> None:
    connection = duckdb.connect()
    load_bulk(connection, write_bulk_folder(tmp_path))
    assert items(connection) == EXPECTED_ITEMS


def test_lines_without_trailing_tabs(tmp_path: Path) -> None:
    files = {
        "FFIEC CDR Call Schedule RC 06302026.txt": "IDRSSD\tRCON2170\n\tTOTAL ASSETS\n1001\t1000\n"
    }
    connection = duckdb.connect()
    bulk = load_bulk(connection, write_bulk_zip(tmp_path, files))
    assert items(connection) == {(1001, "RCON2170", "1000")}
    assert bulk.joined_lines == 0


def test_windows_line_endings_and_encoding(tmp_path: Path) -> None:
    path = tmp_path / "FFIEC CDR Call Bulk All Schedules 06302026.zip"
    import zipfile

    text = "IDRSSD\tTEXT4468\t\r\n\tTEXT\t\r\n1001\tCafé\t\r\n"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("FFIEC CDR Call Schedule RIE 06302026.txt", text.encode("cp1252"))
    connection = duckdb.connect()
    load_bulk(connection, path)
    assert items(connection) == {(1001, "TEXT4468", "Café")}


def test_report_date_must_be_clear(tmp_path: Path) -> None:
    files = {
        "FFIEC CDR Call Schedule RC 06302026.txt": FILES["FFIEC CDR Call Schedule RC 06302026.txt"],
        "FFIEC CDR Call Schedule RI 03312026.txt": "IDRSSD\tRIAD4340\t\n1001\t5\t\n",
    }
    path = write_bulk_zip(tmp_path, files)
    with pytest.raises(BulkFormatError, match="2026-03-31, 2026-06-30"):
        load_bulk(duckdb.connect(), path)
    bulk = load_bulk(duckdb.connect(), path, report_date=date(2026, 6, 30))
    assert bulk.report_date == date(2026, 6, 30)


def test_no_schedules(tmp_path: Path) -> None:
    files = {"FFIEC CDR Call Bulk POR 06302026.txt": FILES["FFIEC CDR Call Bulk POR 06302026.txt"]}
    with pytest.raises(BulkFormatError, match="no schedule files"):
        load_bulk(duckdb.connect(), write_bulk_zip(tmp_path, files))


def test_missing_path(tmp_path: Path) -> None:
    with pytest.raises(BulkFormatError, match="does not exist"):
        load_bulk(duckdb.connect(), tmp_path / "nothing.zip")


def test_empty_folder(tmp_path: Path) -> None:
    with pytest.raises(BulkFormatError, match=r"no \.txt files"):
        load_bulk(duckdb.connect(), tmp_path)


def test_summary(tmp_path: Path) -> None:
    summary = load_bulk(duckdb.connect(), write_bulk_zip(tmp_path)).summary()
    assert "Report date: 2026-06-30" in summary
    assert "Files read: 5 (3 schedules)" in summary
    assert "Confidential values withheld from the public data: 1" in summary
    assert "Repairs: 1 line break(s) inside values joined, 1 row(s) with extra tabs folded" in (
        summary
    )
    assert "Items left out because schedules disagree: 1" in summary
