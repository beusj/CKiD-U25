"""Offline and online validation harness for the CKiD U25 eGFR implementation.

``run_harness()`` runs the 20-vector reference set through:
  1. The Python implementation (``python_impl``)
  2. The rendered SQL executed against a provided database connection

Returns a list of result dicts so callers can inspect or tabulate results
without requiring pandas.  Pass the result list to
``print_harness_report()`` for a human-readable pass/fail summary, or
``to_dataframe()`` to get a ``pandas.DataFrame`` if pandas is installed.

Supported connection types
--------------------------
sqlite3.Connection
    Pass an in-memory or on-disk sqlite3 connection.  The harness creates the
    ``ckid_u25_test_vectors`` table and registers a ``POWER(b, e)`` scalar
    function automatically.

pyodbc.Connection  (optional, requires ``pip install pyodbc``)
    Pass a live connection to SQL Server (or any ODBC target with T-SQL
    syntax).  The harness creates ``#ckid_u25_test_vectors`` as a temp table.

Usage (offline — no SQL Server needed)
---------------------------------------
>>> import sqlite3
>>> from ckid_u25.validate import run_harness, print_harness_report
>>> results = run_harness(sqlite3.connect(":memory:"))
>>> print_harness_report(results)
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import asdict, dataclass
from typing import Any

from .coefficients import AGE_MAX, AGE_MIN
from .python_impl import egfr_cr, egfr_cys
from .sql_render import render_tsql_cte
from .test_vectors import ABS_TOLERANCE, TEST_VECTORS, TestVector

# ---- Public result type --------------------------------------------------- #


@dataclass
class HarnessRow:
    test_id: int
    description: str
    # Python implementation results
    py_cr: float | None
    py_cys: float | None
    py_cr_ok: bool
    py_cys_ok: bool
    # SQL execution results
    sql_cr: float | None
    sql_cys: float | None
    sql_cr_ok: bool
    sql_cys_ok: bool
    # Overall
    status: str  # "PASS" or "FAIL"


# ---- Internal helpers ----------------------------------------------------- #


def _approx(actual: float | None, expected: float | None) -> bool:
    if expected is None:
        return actual is None
    return actual is not None and abs(actual - expected) <= ABS_TOLERANCE


def _is_sqlite(conn: Any) -> bool:
    return isinstance(conn, sqlite3.Connection)


def _translate_to_sqlite(sql: str) -> str:
    """Light T-SQL → sqlite shim: LEFT(x, n) → SUBSTR(x, 1, n)."""
    return re.sub(r"LEFT\(([^,]+),\s*(\d+)\)", r"SUBSTR(\1, 1, \2)", sql)


# ---- Table setup ---------------------------------------------------------- #

_TABLE = "ckid_u25_test_vectors"
_TEMP_TABLE = "#ckid_u25_test_vectors"  # SQL Server temp table


def _create_and_populate(conn: Any, table: str) -> None:
    # Use conn.execute() shorthand for sqlite3 (avoids cursor-level transaction
    # isolation subtleties in Python 3.12+); use a cursor for pyodbc/DBAPI-only.
    if _is_sqlite(conn):
        conn.execute(f"""
            CREATE TABLE {table} (
                patient_id INTEGER,
                lab_date   TEXT,
                age_yr     REAL,
                sex        TEXT,
                height_cm  REAL,
                scr_mgdl   REAL,
                cysc_mgl   REAL
            )
        """)
        conn.executemany(
            f"INSERT INTO {table} VALUES (?, ?, ?, ?, ?, ?, ?)",
            [(v.test_id, "2026-01-01", v.age_yr, v.sex,
              v.height_cm, v.scr_mgdl, v.cysc_mgl) for v in TEST_VECTORS],
        )
        conn.commit()
    else:
        cur = conn.cursor()
        cur.execute(f"""
            CREATE TABLE {table} (
                patient_id INTEGER,
                lab_date   TEXT,
                age_yr     REAL,
                sex        TEXT,
                height_cm  REAL,
                scr_mgdl   REAL,
                cysc_mgl   REAL
            )
        """)
        cur.executemany(
            f"INSERT INTO {table} VALUES (?, ?, ?, ?, ?, ?, ?)",
            [(v.test_id, "2026-01-01", v.age_yr, v.sex,
              v.height_cm, v.scr_mgdl, v.cysc_mgl) for v in TEST_VECTORS],
        )
        conn.commit()


def _fetch_results(conn: Any, table: str) -> dict[int, tuple]:
    """Execute the rendered query and return rows keyed by test_id."""
    if _is_sqlite(conn):
        conn.create_function("POWER", 2, lambda b, e: b ** e)
        sql = _translate_to_sqlite(
            render_tsql_cte(source=table, egfr_threshold=None)
        ).rstrip().rstrip(";")
        rows = conn.execute(sql).fetchall()
    else:
        sql = render_tsql_cte(source=table, egfr_threshold=None).rstrip().rstrip(";")
        cur = conn.cursor()
        cur.execute(sql)
        rows = cur.fetchall()
    return {row[0]: row for row in rows}


# ---- Public API ----------------------------------------------------------- #


def run_harness(conn: Any) -> list[HarnessRow]:
    """Run the 20-vector harness and return per-row results.

    Creates a temporary table in the connected database, inserts the 20
    reference vectors, executes the rendered SQL query, then compares both
    the Python and SQL outputs to the frozen expected values.

    The temporary table is ``ckid_u25_test_vectors`` (sqlite3) or
    ``#ckid_u25_test_vectors`` (SQL Server via pyodbc).  Both are cleaned up
    automatically after the harness completes.
    """
    table = _TEMP_TABLE if not _is_sqlite(conn) else _TABLE

    # Setup
    _create_and_populate(conn, table)

    # SQL execution
    sql_rows = _fetch_results(conn, table)

    # Tear down — ignore errors (temp tables drop with the connection anyway)
    try:
        if _is_sqlite(conn):
            conn.execute(f"DROP TABLE IF EXISTS {table}")
        else:
            cur = conn.cursor()
            cur.execute(f"IF OBJECT_ID('tempdb..{table}') IS NOT NULL DROP TABLE {table}")
    except Exception:  # noqa: BLE001
        pass

    # Build results
    results: list[HarnessRow] = []
    for v in TEST_VECTORS:
        py_cr = egfr_cr(v.sex, v.age_yr, v.height_cm, v.scr_mgdl)
        py_cys = egfr_cys(v.sex, v.age_yr, v.cysc_mgl)
        py_cr_ok = _approx(py_cr, v.expected_cr)
        py_cys_ok = _approx(py_cys, v.expected_cys)

        row = sql_rows.get(v.test_id)
        sql_cr = row[7] if row is not None else None
        sql_cys = row[8] if row is not None else None
        sql_cr_ok = _approx(sql_cr, v.expected_cr)
        sql_cys_ok = _approx(sql_cys, v.expected_cys)

        status = "PASS" if all([py_cr_ok, py_cys_ok, sql_cr_ok, sql_cys_ok]) else "FAIL"
        results.append(HarnessRow(
            test_id=v.test_id,
            description=v.description,
            py_cr=py_cr, py_cys=py_cys,
            py_cr_ok=py_cr_ok, py_cys_ok=py_cys_ok,
            sql_cr=sql_cr, sql_cys=sql_cys,
            sql_cr_ok=sql_cr_ok, sql_cys_ok=sql_cys_ok,
            status=status,
        ))
    return results


def print_harness_report(results: list[HarnessRow]) -> None:
    """Print a human-readable PASS/FAIL table to stdout."""
    passed = sum(1 for r in results if r.status == "PASS")
    print(f"\n{'id':>3}  {'status':<5}  {'description'}")
    print("-" * 70)
    for r in results:
        mark = "✓" if r.status == "PASS" else "✗"
        print(f"{r.test_id:>3}  {mark} {r.status:<4}  {r.description}")
        if r.status == "FAIL":
            if not r.py_cr_ok:
                print(f"          py_cr:  got={r.py_cr}, expected from test_vectors")
            if not r.py_cys_ok:
                print(f"          py_cys: got={r.py_cys}, expected from test_vectors")
            if not r.sql_cr_ok:
                print(f"          sql_cr: got={r.sql_cr}, expected from test_vectors")
            if not r.sql_cys_ok:
                print(f"          sql_cys: got={r.sql_cys}, expected from test_vectors")
    print("-" * 70)
    print(f"  {passed}/{len(results)} passed\n")


def to_dataframe(results: list[HarnessRow]):  # type: ignore[return]
    """Return results as a pandas DataFrame (requires ``pip install pandas``)."""
    try:
        import pandas as pd  # type: ignore[import-not-found]
    except ImportError as exc:
        raise ImportError("pandas is not installed; run `uv add pandas` first") from exc
    return pd.DataFrame([asdict(r) for r in results])
