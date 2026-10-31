"""TieOut: run the FFIEC Call Report edits as plain SQL, before filing day.

Typical use::

    import tieout

    found = tieout.read_taxonomy("taxonomy.zip")           # the edits, as published
    report = tieout.check("items.parquet", found.edits,    # your data
                          report_date=date(2026, 9, 30))
    for failure in report.failures:
        print(failure.edit_id, failure.rssd_id, failure.item_values)
"""

from ._version import __version__
from .cdr import BulkData, BulkFormatError, load_bulk
from .compiler import (
    CompiledCheck,
    CompileResult,
    SkippedEdit,
    UnsupportedFormula,
    compile_edit,
    compile_edits,
)
from .edits import DEFAULT_TYPES, Edit, filter_edits, read_edits_csv, write_edits_csv
from .expr import FormulaSyntaxError, parse
from .groundtruth import EditOutcome, GroundTruthReport, run_ground_truth
from .mdrm import MdrmDictionary, MdrmItem
from .runner import CheckReport, Columns, DataError, Failure, check, load_items, run_checks
from .taxonomy import TaxonomyEdits, read_taxonomy

__all__ = [
    "DEFAULT_TYPES",
    "BulkData",
    "BulkFormatError",
    "CheckReport",
    "Columns",
    "CompileResult",
    "CompiledCheck",
    "DataError",
    "Edit",
    "EditOutcome",
    "Failure",
    "FormulaSyntaxError",
    "GroundTruthReport",
    "MdrmDictionary",
    "MdrmItem",
    "SkippedEdit",
    "TaxonomyEdits",
    "UnsupportedFormula",
    "__version__",
    "check",
    "compile_edit",
    "compile_edits",
    "filter_edits",
    "load_bulk",
    "load_items",
    "parse",
    "read_edits_csv",
    "read_taxonomy",
    "run_checks",
    "run_ground_truth",
    "write_edits_csv",
]
