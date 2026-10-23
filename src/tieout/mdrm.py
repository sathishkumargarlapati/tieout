"""Read the Federal Reserve's Micro Data Reference Manual (MDRM).

Every item on the Call Report has an eight-character MDRM identifier: a
four-character mnemonic for the series (``RCON``, ``RCFD``, ``RIAD`` and so
on) followed by a four-character item code. ``RCON2170`` is total assets.

The Federal Reserve publishes the full dictionary, free of charge, as
``MDRM.zip`` at https://www.federalreserve.gov/apps/mdrm/download_mdrm.htm.
The zip holds ``MDRM_CSV.csv``. TieOut reads that file as published (zipped or
unzipped) and never ships a copy of it.

The CSV starts with a one-line banner before its header row. TieOut finds the
header by its column names, so it does not depend on the exact banner text.
"""

from __future__ import annotations

import csv
import io
import re
import sys
import zipfile
from collections import defaultdict
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

__all__ = [
    "ITEM_TYPES",
    "MdrmDictionary",
    "MdrmFormatError",
    "MdrmItem",
    "normalize_form",
]

ITEM_TYPES = {
    "D": "Derived",
    "E": "Examination/supervision",
    "F": "Financial/reported",
    "J": "Projected",
    "P": "Percentage",
    "R": "Rate",
    "S": "Structure",
}
"""Item type codes, as defined on the Federal Reserve's MDRM download page."""

_COLUMN_ALIASES = {
    "mnemonic": {"mnemonic"},
    "item_code": {"itemcode", "itemnumber"},
    "start_date": {"startdate"},
    "end_date": {"enddate"},
    "item_name": {"itemname"},
    "confidentiality": {"confidentiality"},
    "item_type": {"itemtype"},
    "reporting_form": {"reportingform"},
    "description": {"description", "itemdescription"},
    "series_glossary": {"seriesglossary", "mnemonicglossary"},
}

_DATE_FORMATS = (
    "%m/%d/%Y %I:%M:%S %p",
    "%m/%d/%Y %H:%M:%S",
    "%m/%d/%Y %H:%M",
    "%m/%d/%Y",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%d",
    "%Y%m%d",
)


class MdrmFormatError(ValueError):
    """Raised when a file does not look like the MDRM dictionary."""


def normalize_form(form: str) -> str:
    """Normalize a reporting form name for comparison.

    ``"FFIEC 041"``, ``"ffiec-041"`` and ``"041"`` all become ``"FFIEC041"``.
    """
    compact = re.sub(r"[^0-9A-Za-z]", "", form).upper()
    if compact.isdigit():
        compact = "FFIEC" + compact
    return compact


@dataclass(frozen=True)
class MdrmItem:
    """One row of the MDRM dictionary."""

    mnemonic: str
    item_code: str
    item_name: str = ""
    start_date: date | None = None
    end_date: date | None = None
    confidential: bool | None = None
    item_type: str = ""
    reporting_forms: tuple[str, ...] = ()
    description: str = ""
    series_glossary: str = ""

    @property
    def mdrm(self) -> str:
        """The eight-character MDRM identifier, for example ``RCON2170``."""
        return self.mnemonic + self.item_code

    @property
    def item_type_label(self) -> str:
        """The meaning of the item type code, for example ``Financial/reported``."""
        return ITEM_TYPES.get(self.item_type, self.item_type)

    def is_active(self, on: date) -> bool:
        """Whether the item is in effect on the given date."""
        starts_ok = self.start_date is None or self.start_date <= on
        ends_ok = self.end_date is None or on <= self.end_date
        return starts_ok and ends_ok

    def on_form(self, form: str) -> bool:
        """Whether the item is listed for the given reporting form."""
        wanted = normalize_form(form)
        return any(normalize_form(name) == wanted for name in self.reporting_forms)


class MdrmDictionary:
    """The MDRM dictionary, indexed by MDRM identifier.

    An identifier can have several rows, for example when an item was retired
    and later reused; :meth:`get` returns all of them, or those in effect on a
    given date.
    """

    def __init__(self, items: Iterable[MdrmItem]) -> None:
        self._items = list(items)
        self._by_id: dict[str, list[MdrmItem]] = defaultdict(list)
        for item in self._items:
            self._by_id[item.mdrm].append(item)

    @classmethod
    def load(cls, path: str | Path) -> MdrmDictionary:
        """Load ``MDRM.zip`` or ``MDRM_CSV.csv`` as published by the Federal Reserve."""
        return cls(_read_items(_read_text(Path(path))))

    def __len__(self) -> int:
        return len(self._items)

    def __iter__(self) -> Iterator[MdrmItem]:
        return iter(self._items)

    def __contains__(self, mdrm: object) -> bool:
        return isinstance(mdrm, str) and mdrm.upper() in self._by_id

    def get(self, mdrm: str, as_of: date | None = None) -> list[MdrmItem]:
        """All rows for an identifier, or only those in effect on ``as_of``."""
        rows = self._by_id.get(mdrm.upper(), [])
        if as_of is None:
            return list(rows)
        return [row for row in rows if row.is_active(as_of)]

    def describe(self, mdrm: str, as_of: date | None = None) -> MdrmItem | None:
        """The best single row for an identifier.

        With ``as_of``, the row in effect on that date. Without it, the row
        with the latest start date.
        """
        rows = self.get(mdrm, as_of)
        if not rows:
            return None
        return max(rows, key=lambda row: row.start_date or date.min)

    def items_for_form(self, form: str, as_of: date | None = None) -> list[MdrmItem]:
        """Rows listed for a reporting form such as ``FFIEC 041``."""
        return [
            item
            for item in self._items
            if item.on_form(form) and (as_of is None or item.is_active(as_of))
        ]


def _read_text(path: Path) -> str:
    if zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as archive:
            names = [n for n in archive.namelist() if n.lower().endswith(".csv")]
            if not names:
                raise MdrmFormatError(f"{path}: the zip file holds no CSV file")
            preferred = [n for n in names if "mdrm" in n.lower()] or names
            data = archive.read(preferred[0])
    else:
        data = path.read_bytes()
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return data.decode("cp1252", errors="replace")


def _normalize_header(cell: str) -> str:
    return re.sub(r"[^0-9a-z]", "", cell.lower())


def _parse_date(text: str) -> date | None:
    value = text.strip()
    if not value:
        return None
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    return None


def _read_items(text: str) -> list[MdrmItem]:
    csv.field_size_limit(min(sys.maxsize, 2**31 - 1))
    reader = csv.reader(io.StringIO(text))
    columns: dict[str, int] | None = None
    for row_number, row in enumerate(reader, start=1):
        normalized = [_normalize_header(cell) for cell in row]
        found = {
            field_name: normalized.index(alias)
            for field_name, aliases in _COLUMN_ALIASES.items()
            for alias in aliases
            if alias in normalized
        }
        if "mnemonic" in found and "item_code" in found:
            columns = found
            break
        if row_number >= 10:
            break
    if columns is None:
        raise MdrmFormatError(
            "Could not find the MDRM header row (a row with 'Mnemonic' and 'Item Code')"
        )

    def cell(row: list[str], name: str) -> str:
        index = columns.get(name)
        if index is None or index >= len(row):
            return ""
        return row[index].strip()

    items = []
    for row in reader:
        mnemonic = cell(row, "mnemonic").upper()
        item_code = cell(row, "item_code").upper()
        if not mnemonic or not item_code:
            continue
        if item_code.isdigit() and len(item_code) < 4:
            item_code = item_code.zfill(4)
        confidentiality = cell(row, "confidentiality").upper()
        forms = tuple(
            part.strip() for part in re.split(r"[,;]", cell(row, "reporting_form")) if part.strip()
        )
        items.append(
            MdrmItem(
                mnemonic=mnemonic,
                item_code=item_code,
                item_name=cell(row, "item_name"),
                start_date=_parse_date(cell(row, "start_date")),
                end_date=_parse_date(cell(row, "end_date")),
                confidential={"Y": True, "N": False}.get(confidentiality),
                item_type=cell(row, "item_type").upper(),
                reporting_forms=forms,
                description=cell(row, "description"),
                series_glossary=cell(row, "series_glossary"),
            )
        )
    return items
