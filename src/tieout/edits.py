"""The edit model and the plain CSV edit file.

An *edit* is one of the validation rules the FFIEC agencies publish for the
Call Report. TieOut keeps each edit exactly as published: its official
identifier, its type, its formula and its message, plus a note of where it
came from. The CSV file written by ``tieout extract`` is a reviewable copy of
those edits that people can read, diff between quarters and check into
version control.
"""

from __future__ import annotations

import csv
import io
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TextIO

__all__ = [
    "DEFAULT_TYPES",
    "EDIT_CSV_COLUMNS",
    "Edit",
    "EditFileError",
    "filter_edits",
    "read_edits_csv",
    "write_edits_csv",
]

EDIT_CSV_COLUMNS = ("edit_id", "edit_type", "expression", "message", "source")

DEFAULT_TYPES = ("validity", "quality")
"""Edit types compiled when no other types are requested."""


class EditFileError(ValueError):
    """Raised when an edit file is missing columns or values."""


@dataclass(frozen=True)
class Edit:
    """One published Call Report edit.

    ``edit_id`` is the official identifier and ``edit_type`` is, for example,
    ``validity`` or ``quality``. ``expression`` is the formula exactly as
    published, ``message`` is the published explanation if one is available,
    and ``source`` records where the edit was read from.
    """

    edit_id: str
    edit_type: str
    expression: str
    message: str = ""
    source: str = ""


def read_edits_csv(path: str | Path) -> list[Edit]:
    """Read edits from a CSV file with the columns in :data:`EDIT_CSV_COLUMNS`.

    Only ``edit_id``, ``edit_type`` and ``expression`` are required.
    """
    text = Path(path).read_text(encoding="utf-8-sig")
    reader = csv.DictReader(io.StringIO(text))
    fields = {name.strip().lower() for name in (reader.fieldnames or [])}
    missing = [name for name in ("edit_id", "edit_type", "expression") if name not in fields]
    if missing:
        raise EditFileError(f"{path}: missing column(s): {', '.join(missing)}")
    edits = []
    for line_number, row in enumerate(reader, start=2):
        values = {(key or "").strip().lower(): (value or "").strip() for key, value in row.items()}
        if not any(values.values()):
            continue
        for name in ("edit_id", "edit_type", "expression"):
            if not values.get(name):
                raise EditFileError(f"{path}, line {line_number}: {name} is empty")
        edits.append(
            Edit(
                edit_id=values["edit_id"],
                edit_type=values["edit_type"].lower(),
                expression=values["expression"],
                message=values.get("message", ""),
                source=values.get("source", ""),
            )
        )
    return edits


def write_edits_csv(edits: Iterable[Edit], target: str | Path | TextIO) -> None:
    """Write edits to a CSV file or an open text stream."""
    if isinstance(target, (str, Path)):
        with Path(target).open("w", encoding="utf-8", newline="") as handle:
            write_edits_csv(edits, handle)
        return
    writer = csv.writer(target, lineterminator="\n")
    writer.writerow(EDIT_CSV_COLUMNS)
    for edit in edits:
        writer.writerow([edit.edit_id, edit.edit_type, edit.expression, edit.message, edit.source])


def filter_edits(
    edits: Iterable[Edit],
    types: Sequence[str] | None = DEFAULT_TYPES,
    ids: Sequence[str] | None = None,
) -> list[Edit]:
    """Keep the edits of the given types and, if given, the given identifiers.

    ``types=None`` keeps every type.
    """
    wanted_types = {t.lower() for t in types} if types is not None else None
    wanted_ids = set(ids) if ids is not None else None
    return [
        edit
        for edit in edits
        if (wanted_types is None or edit.edit_type.lower() in wanted_types)
        and (wanted_ids is None or edit.edit_id in wanted_ids)
    ]
