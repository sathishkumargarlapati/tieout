"""Read the Call Report edits from the FFIEC's XBRL taxonomy.

The FFIEC Central Data Repository (CDR) publishes the Call Report taxonomy for
each reporting period at https://cdr.ffiec.gov/public/DownloadTaxonomy.aspx.
Its formula linkbase holds the edits. As the FFIEC's CDR Interchange
Specification describes:

* each edit is a ``formula`` resource whose ``select`` attribute holds the
  formula, for example ``select="cc:RCON2160[P0] >= 0"``;
* the resource's role gives the edit type: ``.../rule/ruleType1`` for
  validity edits and ``.../rule/ruleType2`` for quality edits;
* messages are label resources with the roles ``.../rule/ruleMessage1`` (a
  short description) and ``.../rule/ruleMessage2`` (a longer English one).

This module reads those files from the taxonomy as downloaded, either the zip
file or an unpacked folder, and returns the edits exactly as published.
"""

from __future__ import annotations

import fnmatch
import io
import re
import zipfile
from collections import Counter, defaultdict
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from xml.etree import ElementTree

from .edits import Edit

__all__ = ["RULE_TYPES", "TaxonomyEdits", "TaxonomyError", "read_taxonomy"]

XLINK = "{http://www.w3.org/1999/xlink}"

RULE_TYPES = {
    "ruletype1": "validity",
    "ruletype2": "quality",
    "ruletype4": "reportability",
}
"""Edit types by formula role. Other roles keep their role name, such as ``ruleType3``."""

_MESSAGE_ROLES = {"rulemessage1": "short", "rulemessage2": "long"}
_ID_SUFFIXES = re.compile(r"_(formula|msg|message)$", re.IGNORECASE)


class TaxonomyError(ValueError):
    """Raised when a taxonomy cannot be read."""


@dataclass
class TaxonomyEdits:
    """Edits read from a taxonomy, with counts that show how they were found."""

    edits: list[Edit] = field(default_factory=list)
    files_read: int = 0
    files_skipped: list[str] = field(default_factory=list)
    formulas_found: int = 0
    edits_by_type: Counter[str] = field(default_factory=Counter)
    ids_from: Counter[str] = field(default_factory=Counter)
    messages_matched: int = 0
    duplicates_dropped: int = 0

    def summary(self) -> str:
        """A short human-readable account of what was read."""
        lines = [
            f"Files read: {self.files_read}",
            f"Formulas found: {self.formulas_found}",
            "Edits by type: "
            + (", ".join(f"{k} {v}" for k, v in sorted(self.edits_by_type.items())) or "none"),
            "Edit identifiers taken from: "
            + (", ".join(f"{k} {v}" for k, v in sorted(self.ids_from.items())) or "none"),
            f"Edits with a published message: {self.messages_matched}",
        ]
        if self.duplicates_dropped:
            lines.append(f"Identical duplicates dropped: {self.duplicates_dropped}")
        if self.files_skipped:
            lines.append(f"Files skipped: {len(self.files_skipped)}")
        return "\n".join(lines)


def read_taxonomy(path: str | Path, *, source_pattern: str | None = None) -> TaxonomyEdits:
    """Read every edit from a taxonomy zip file, folder or single linkbase file.

    ``source_pattern`` keeps only files whose path inside the taxonomy matches
    the given shell-style pattern, for example ``"*041*"``.
    """
    root = Path(path)
    if not root.exists():
        raise TaxonomyError(f"{root} does not exist")
    result = TaxonomyEdits()
    formulas: list[tuple[str, str, str, str, str]] = []  # id, type, select, file, id source
    messages: dict[str, dict[str, str]] = defaultdict(dict)

    for name, data in _iter_files(root):
        if source_pattern and not fnmatch.fnmatch(name, source_pattern):
            continue
        tree = _parse_xml(data)
        if tree is None:
            result.files_skipped.append(name)
            continue
        result.files_read += 1
        for extended in tree.iter():
            if extended.get(XLINK + "type") == "extended":
                _read_extended_link(extended, name, formulas, messages)

    result.formulas_found = len(formulas)
    many_files = root.is_dir() or zipfile.is_zipfile(root)
    seen: set[tuple[str, str, str]] = set()
    for edit_id, edit_type, select, file_name, id_source in formulas:
        if (edit_id, edit_type, select) in seen:
            result.duplicates_dropped += 1
            continue
        seen.add((edit_id, edit_type, select))
        found = messages.get(edit_id, {})
        message = found.get("long") or found.get("short") or ""
        if message:
            result.messages_matched += 1
        result.edits.append(
            Edit(
                edit_id=edit_id,
                edit_type=edit_type,
                expression=select,
                message=message,
                source=f"{root.name}:{file_name}" if many_files else root.name,
            )
        )
        result.edits_by_type[edit_type] += 1
        result.ids_from[id_source] += 1
    return result


def _iter_files(root: Path) -> Iterator[tuple[str, bytes]]:
    if root.is_dir():
        for path in sorted(root.rglob("*")):
            if path.is_file():
                relative = path.relative_to(root).as_posix()
                if path.suffix.lower() in (".xml", ".xsd"):
                    yield relative, path.read_bytes()
                elif path.suffix.lower() == ".zip":
                    yield from _iter_zip(path.read_bytes(), prefix=relative + "!/")
    elif zipfile.is_zipfile(root):
        yield from _iter_zip(root.read_bytes(), prefix="")
    else:
        yield root.name, root.read_bytes()


def _iter_zip(data: bytes, prefix: str, depth: int = 0) -> Iterator[tuple[str, bytes]]:
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        for info in sorted(archive.infolist(), key=lambda i: i.filename):
            if info.is_dir():
                continue
            name = PurePosixPath(info.filename)
            suffix = name.suffix.lower()
            if suffix in (".xml", ".xsd"):
                yield prefix + info.filename, archive.read(info)
            elif suffix == ".zip" and depth < 2:
                yield from _iter_zip(
                    archive.read(info), prefix=f"{prefix}{info.filename}!/", depth=depth + 1
                )


def _parse_xml(data: bytes) -> ElementTree.Element | None:
    # Taxonomy files never need entity declarations; refuse them rather than
    # expand them.
    if b"<!ENTITY" in data:
        return None
    try:
        return ElementTree.fromstring(data)
    except ElementTree.ParseError:
        return None


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _role_key(role: str) -> str:
    return role.rstrip("/").rsplit("/", 1)[-1].lower()


def _strip_suffix(value: str) -> str:
    return _ID_SUFFIXES.sub("", value.strip())


def _ids_from_href(href: str) -> list[str]:
    """Edit identifiers a locator may point to.

    ``call-report041-2003-09-30-v1.xsd#ffiec041_R0401.2073`` points to the
    element ``ffiec041_R0401.2073``, the edit ``R0401.2073``.
    """
    fragment = href.split("#", 1)[1] if "#" in href else href
    fragment = fragment.strip()
    if not fragment:
        return []
    ids = []
    if "_" in fragment:
        ids.append(fragment.split("_", 1)[1])
    ids.append(fragment)
    return ids


def _read_extended_link(
    extended: ElementTree.Element,
    file_name: str,
    formulas: list[tuple[str, str, str, str, str]],
    messages: dict[str, dict[str, str]],
) -> None:
    locators: dict[str, list[str]] = defaultdict(list)
    resources: list[ElementTree.Element] = []
    incoming: dict[str, list[str]] = defaultdict(list)
    for element in extended.iter():
        xlink_type = element.get(XLINK + "type")
        if xlink_type == "locator":
            locators[element.get(XLINK + "label", "")].append(element.get(XLINK + "href", ""))
        elif xlink_type == "resource":
            resources.append(element)
        elif xlink_type == "arc":
            incoming[element.get(XLINK + "to", "")].append(element.get(XLINK + "from", ""))

    def located_ids(label: str) -> list[str]:
        ids: list[str] = []
        for source_label in incoming.get(label, []):
            for href in locators.get(source_label, []):
                ids.extend(_ids_from_href(href))
        return ids

    for resource in resources:
        label = resource.get(XLINK + "label", "")
        role = _role_key(resource.get(XLINK + "role", ""))
        if _local_name(resource.tag).lower() == "formula":
            select = resource.get("select")
            if select is None:
                select = "".join(resource.itertext())
            select = select.strip()
            if not select:
                continue
            edit_type = RULE_TYPES.get(role, role or "unknown")
            element_id = resource.get("id")
            via_locator = located_ids(label)
            if element_id:
                edit_id, id_source = _strip_suffix(element_id), "formula id"
            elif via_locator:
                edit_id, id_source = via_locator[0], "linked element"
            else:
                edit_id, id_source = _strip_suffix(label), "formula label"
            if edit_id:
                formulas.append((edit_id, edit_type, select, file_name, id_source))
        elif role in _MESSAGE_ROLES:
            text = " ".join("".join(resource.itertext()).split())
            if not text:
                continue
            keys = set(located_ids(label))
            for candidate in (resource.get("id"), label):
                if candidate:
                    keys.add(_strip_suffix(candidate))
            for key in keys:
                messages[key].setdefault(_MESSAGE_ROLES[role], text)
