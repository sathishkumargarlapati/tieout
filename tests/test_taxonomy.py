"""Tests for reading edits from a taxonomy.

The linkbases here are synthetic. They follow the structure the FFIEC CDR
Interchange Specification describes: formula resources with a ``select``
attribute and a ``ruleTypeN`` role, linked to the edit's element by an arc,
and message labels with ``ruleMessage1`` and ``ruleMessage2`` roles.
"""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pytest

from tieout import Edit, read_taxonomy
from tieout.taxonomy import TaxonomyError

RULE = "https://www.cdr.ffiec.gov/xbrl/rule/"

FORMULAS = f"""<?xml version="1.0" encoding="utf-8"?>
<linkbase xmlns="http://www.xbrl.org/2003/linkbase"
          xmlns:xlink="http://www.w3.org/1999/xlink">
  <formulaLink xlink:type="extended" xlink:role="http://www.xbrl.org/2003/role/link">
    <loc xlink:type="locator" xlink:label="edit_a"
         xlink:href="synthetic-call-report041.xsd#ffiec041_R0001.0001"/>
    <loc xlink:type="locator" xlink:label="edit_b"
         xlink:href="synthetic-call-report041.xsd#ffiec041_R0002.0002"/>
    <formula xlink:type="resource" xlink:label="f_a" id="R0001.0001_formula"
             xlink:role="{RULE}ruleType1" select="cc:RCON2160[P0] &gt;= 0"/>
    <formula xlink:type="resource" xlink:label="f_b"
             xlink:role="{RULE}ruleType2"
             select="cc:RCON2170[P0] &lt;&gt; cc:RCON2170[-P1Q]"/>
    <formula xlink:type="resource" xlink:label="R0003.0003_formula"
             xlink:role="{RULE}ruleType3"
      >If(cc:RCONF049[-P1Q] &gt; 0, cc:RCONF049[P0] &gt; 0, TRUE)</formula>
    <formula xlink:type="resource" xlink:label="f_empty" xlink:role="{RULE}ruleType1" select=" "/>
    <formulaArc xlink:type="arc" xlink:from="edit_a" xlink:to="f_a"
                xlink:arcrole="https://www.cdr.ffiec.gov/2003/arcrole/concept-formula"/>
    <formulaArc xlink:type="arc" xlink:from="edit_b" xlink:to="f_b"
                xlink:arcrole="https://www.cdr.ffiec.gov/2003/arcrole/concept-formula"/>
  </formulaLink>
</linkbase>
"""

MESSAGES = f"""<?xml version="1.0" encoding="utf-8"?>
<linkbase xmlns="http://www.xbrl.org/2003/linkbase"
          xmlns:xlink="http://www.w3.org/1999/xlink">
  <labelLink xlink:type="extended" xlink:role="http://www.xbrl.org/2003/role/link">
    <loc xlink:type="locator" xlink:label="edit_a"
         xlink:href="synthetic-call-report041.xsd#ffiec041_R0001.0001"/>
    <label xlink:type="resource" xlink:label="msg_a_short"
           xlink:role="{RULE}ruleMessage1">Line 1 || must not be negative</label>
    <label xlink:type="resource" xlink:label="msg_a_long"
           xlink:role="{RULE}ruleMessage2">The   synthetic item
           must not be negative.</label>
    <label xlink:type="resource" xlink:label="R0002.0002_msg"
           xlink:role="{RULE}ruleMessage1">Explain an unchanged total</label>
    <label xlink:type="resource" xlink:label="other"
           xlink:role="http://www.xbrl.org/2003/role/label">Not a message</label>
    <labelArc xlink:type="arc" xlink:from="edit_a" xlink:to="msg_a_short"/>
    <labelArc xlink:type="arc" xlink:from="edit_a" xlink:to="msg_a_long"/>
  </labelLink>
</linkbase>
"""


def write_taxonomy(directory: Path) -> Path:
    folder = directory / "synthetic-taxonomy"
    folder.mkdir()
    (folder / "call-report041-formula.xml").write_text(FORMULAS, encoding="utf-8")
    (folder / "call-report041-messages.xml").write_text(MESSAGES, encoding="utf-8")
    (folder / "notes.txt").write_text("ignored", encoding="utf-8")
    return folder


def test_read_folder(tmp_path: Path) -> None:
    found = read_taxonomy(write_taxonomy(tmp_path))
    assert found.files_read == 2
    assert found.formulas_found == 3
    assert found.edits == [
        Edit(
            "R0001.0001",
            "validity",
            "cc:RCON2160[P0] >= 0",
            "The synthetic item must not be negative.",
            "synthetic-taxonomy:call-report041-formula.xml",
        ),
        Edit(
            "R0002.0002",
            "quality",
            "cc:RCON2170[P0] <> cc:RCON2170[-P1Q]",
            "Explain an unchanged total",
            "synthetic-taxonomy:call-report041-formula.xml",
        ),
        Edit(
            "R0003.0003",
            "ruletype3",
            "If(cc:RCONF049[-P1Q] > 0, cc:RCONF049[P0] > 0, TRUE)",
            "",
            "synthetic-taxonomy:call-report041-formula.xml",
        ),
    ]
    assert found.ids_from == {"formula id": 1, "linked element": 1, "formula label": 1}
    assert found.edits_by_type == {"validity": 1, "quality": 1, "ruletype3": 1}
    assert found.messages_matched == 2
    assert "Formulas found: 3" in found.summary()


def test_read_zip_with_nested_zip(tmp_path: Path) -> None:
    inner = io.BytesIO()
    with zipfile.ZipFile(inner, "w") as archive:
        archive.writestr("formula.xml", FORMULAS)
    outer = tmp_path / "taxonomy.zip"
    with zipfile.ZipFile(outer, "w") as archive:
        archive.writestr("041/inner.zip", inner.getvalue())
        archive.writestr("041/messages.xml", MESSAGES)
    found = read_taxonomy(outer)
    assert [e.edit_id for e in found.edits] == ["R0001.0001", "R0002.0002", "R0003.0003"]
    assert found.edits[0].source == "taxonomy.zip:041/inner.zip!/formula.xml"
    assert found.edits[0].message == "The synthetic item must not be negative."


def test_read_single_file(tmp_path: Path) -> None:
    path = tmp_path / "formula.xml"
    path.write_text(FORMULAS, encoding="utf-8")
    found = read_taxonomy(path)
    assert len(found.edits) == 3
    assert found.edits[0].source == "formula.xml"
    assert found.edits[0].message == ""


def test_source_pattern(tmp_path: Path) -> None:
    found = read_taxonomy(write_taxonomy(tmp_path), source_pattern="*messages*")
    assert found.files_read == 1
    assert found.edits == []


def test_identical_duplicates_are_dropped(tmp_path: Path) -> None:
    folder = write_taxonomy(tmp_path)
    (folder / "copy-formula.xml").write_text(FORMULAS, encoding="utf-8")
    found = read_taxonomy(folder)
    assert len(found.edits) == 3
    assert found.duplicates_dropped == 3
    assert "Identical duplicates dropped: 3" in found.summary()


def test_unsafe_and_broken_files_are_skipped(tmp_path: Path) -> None:
    folder = write_taxonomy(tmp_path)
    (folder / "entities.xml").write_text(
        '<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "aaaa">]><x>&a;</x>', encoding="utf-8"
    )
    (folder / "broken.xml").write_text("<linkbase><unclosed></linkbase>", encoding="utf-8")
    found = read_taxonomy(folder)
    assert sorted(found.files_skipped) == ["broken.xml", "entities.xml"]
    assert len(found.edits) == 3


def test_missing_path(tmp_path: Path) -> None:
    with pytest.raises(TaxonomyError, match="does not exist"):
        read_taxonomy(tmp_path / "nowhere")
