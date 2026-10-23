from __future__ import annotations

import zipfile
from datetime import date
from pathlib import Path

import pytest

from tieout.mdrm import MdrmDictionary, MdrmFormatError, MdrmItem, normalize_form

# A synthetic file laid out like the Federal Reserve's MDRM_CSV.csv: a one-line
# banner, a header row, then one row per item. The values are made up.
HEADER = (
    "Mnemonic,Item Code,Start Date,End Date,Item Name,Confidentiality,ItemType,"
    "Reporting Form,Description,SeriesGlossary"
)
ROWS = [
    "RCON,2170,3/31/2001 12:00:00 AM,12/31/9999 12:00:00 AM,Total assets,N,F,"
    '"FFIEC 031, FFIEC 041, FFIEC 051",Synthetic description,Synthetic glossary',
    "RCON,1234,1/1/1990 12:00:00 AM,12/31/2010 12:00:00 AM,Old name,N,F,FFIEC 041,,",
    "RCON,1234,3/31/2020 12:00:00 AM,12/31/9999 12:00:00 AM,New name,N,F,FFIEC 041,,",
    "RIAD,10,1/1/2001 12:00:00 AM,12/31/9999 12:00:00 AM,Zero-padded item,Y,D,FFIEC 051,,",
    ",,,,,,,,,",
]


def write_mdrm(directory: Path, banner: str = "PUBLIC", rows: list[str] = ROWS) -> Path:
    path = directory / "MDRM_CSV.csv"
    path.write_text("\n".join([banner, HEADER, *rows]) + "\n", encoding="utf-8")
    return path


def test_load_csv(tmp_path: Path) -> None:
    dictionary = MdrmDictionary.load(write_mdrm(tmp_path))
    assert len(dictionary) == 4
    item = dictionary.describe("RCON2170")
    assert item == MdrmItem(
        mnemonic="RCON",
        item_code="2170",
        item_name="Total assets",
        start_date=date(2001, 3, 31),
        end_date=date(9999, 12, 31),
        confidential=False,
        item_type="F",
        reporting_forms=("FFIEC 031", "FFIEC 041", "FFIEC 051"),
        description="Synthetic description",
        series_glossary="Synthetic glossary",
    )
    assert item.mdrm == "RCON2170"
    assert item.item_type_label == "Financial/reported"


def test_load_zip(tmp_path: Path) -> None:
    csv_path = write_mdrm(tmp_path)
    zip_path = tmp_path / "MDRM.zip"
    with zipfile.ZipFile(zip_path, "w") as archive:
        archive.writestr("readme.txt", "synthetic")
        archive.write(csv_path, "MDRM_CSV.csv")
    assert len(MdrmDictionary.load(zip_path)) == 4


def test_banner_text_does_not_matter(tmp_path: Path) -> None:
    assert len(MdrmDictionary.load(write_mdrm(tmp_path, banner="Anything at all"))) == 4


def test_lookups_by_date(tmp_path: Path) -> None:
    dictionary = MdrmDictionary.load(write_mdrm(tmp_path))
    assert "rcon1234" in dictionary
    assert len(dictionary.get("RCON1234")) == 2
    assert [i.item_name for i in dictionary.get("RCON1234", date(2005, 6, 30))] == ["Old name"]
    assert dictionary.get("RCON1234", date(2015, 6, 30)) == []
    assert dictionary.describe("RCON1234").item_name == "New name"
    assert dictionary.describe("RCON1234", date(2015, 6, 30)) is None
    assert dictionary.describe("RCON9999") is None


def test_short_numeric_item_codes_are_zero_padded(tmp_path: Path) -> None:
    dictionary = MdrmDictionary.load(write_mdrm(tmp_path))
    item = dictionary.describe("RIAD0010")
    assert item is not None
    assert item.confidential is True
    assert item.item_type_label == "Derived"


def test_items_for_form(tmp_path: Path) -> None:
    dictionary = MdrmDictionary.load(write_mdrm(tmp_path))
    assert {i.mdrm for i in dictionary.items_for_form("FFIEC 041")} == {"RCON2170", "RCON1234"}
    assert {i.mdrm for i in dictionary.items_for_form("051", date(2026, 6, 30))} == {
        "RCON2170",
        "RIAD0010",
    }


def test_windows_encoded_file(tmp_path: Path) -> None:
    path = tmp_path / "MDRM_CSV.csv"
    row = "RCON,2170,3/31/2001,12/31/9999,Café assets,N,F,FFIEC 041,,"
    path.write_bytes("\n".join(["PUBLIC", HEADER, row]).encode("cp1252"))
    assert MdrmDictionary.load(path).describe("RCON2170").item_name == "Café assets"


def test_iso_dates_and_alternative_column_names(tmp_path: Path) -> None:
    path = tmp_path / "mdrm.csv"
    path.write_text(
        "Mnemonic,Item Number,Item Name,Start Date,End Date\n"
        "RCON,2170,Total assets,2001-03-31,9999-12-31\n",
        encoding="utf-8",
    )
    item = MdrmDictionary.load(path).describe("RCON2170")
    assert item is not None
    assert item.start_date == date(2001, 3, 31)
    assert item.confidential is None


def test_not_an_mdrm_file(tmp_path: Path) -> None:
    path = tmp_path / "other.csv"
    path.write_text("a,b,c\n1,2,3\n", encoding="utf-8")
    with pytest.raises(MdrmFormatError, match="header row"):
        MdrmDictionary.load(path)


def test_zip_without_csv(tmp_path: Path) -> None:
    path = tmp_path / "MDRM.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("readme.txt", "synthetic")
    with pytest.raises(MdrmFormatError, match="no CSV"):
        MdrmDictionary.load(path)


@pytest.mark.parametrize(
    ("form", "normalized"),
    [
        ("FFIEC 041", "FFIEC041"),
        ("ffiec-041", "FFIEC041"),
        ("041", "FFIEC041"),
        ("FR Y-9C", "FRY9C"),
    ],
)
def test_normalize_form(form: str, normalized: str) -> None:
    assert normalize_form(form) == normalized
