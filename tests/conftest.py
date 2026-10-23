"""Shared test helpers. All data in these tests is synthetic."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date

import duckdb
import pytest

from tieout import Edit, compile_edit


@pytest.fixture
def con() -> Iterable[duckdb.DuckDBPyConnection]:
    connection = duckdb.connect()
    yield connection
    connection.close()


def load_rows(
    connection: duckdb.DuckDBPyConnection,
    rows: Iterable[tuple[int, str, str, object]],
    report_date: str | None = None,
) -> None:
    """Create tieout_items and tieout_periods from (rssd_id, date, mdrm, value) rows."""
    connection.execute(
        "CREATE OR REPLACE TABLE tieout_items "
        "(rssd_id BIGINT, report_date DATE, mdrm VARCHAR, value VARCHAR)"
    )
    for rssd_id, when, mdrm, value in rows:
        connection.execute(
            "INSERT INTO tieout_items VALUES (?, ?, ?, ?)",
            [rssd_id, date.fromisoformat(when), mdrm, None if value is None else str(value)],
        )
    where = f"WHERE report_date = DATE '{report_date}'" if report_date else ""
    connection.execute(
        "CREATE OR REPLACE VIEW tieout_periods AS "
        f"SELECT DISTINCT rssd_id, report_date FROM tieout_items {where}"
    )


def run_formula(
    connection: duckdb.DuckDBPyConnection,
    formula: str,
    rows: Iterable[tuple[int, str, str, object]],
    report_date: str | None = None,
) -> list[tuple[int, str, str]]:
    """Compile a formula, run it on the rows, and return (rssd_id, date, status) failures."""
    load_rows(connection, rows, report_date)
    check = compile_edit(Edit("T-1", "validity", formula))
    result = connection.execute(check.sql).fetchall()
    return [(row[2], row[3].isoformat(), row[4]) for row in result]
