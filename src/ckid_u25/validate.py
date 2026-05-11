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

pyodbc.Connection  (install: ``uv sync --extra sqlserver``)
    Pass a live DBAPI-2 connection to SQL Server.  The harness creates
    ``#ckid_u25_test_vectors`` as a local temp table.

sqlalchemy.engine.Connection  (install: ``uv sync --extra sqlserver``)
    Pass a SQLAlchemy connection from ``engine.connect()`` or inside a
    ``with engine.begin() as conn:`` block.  Both Core and legacy connection
    styles are supported.

Usage
-----
>>> # Offline — no SQL Server needed
>>> import sqlite3
>>> from ckid_u25.validate import run_harness, print_harness_report
>>> results = run_harness(sqlite3.connect(":memory:"))
>>> print_harness_report(results)

>>> # pyodbc
>>> import pyodbc
>>> conn = pyodbc.connect("DRIVER={ODBC Driver 18 for SQL Server};SERVER=...;DATABASE=...;Trusted_Connection=yes")
>>> results = run_harness(conn)

>>> # SQLAlchemy
>>> from sqlalchemy import create_engine
>>> engine = create_engine("mssql+pyodbc://server/database?driver=ODBC+Driver+18+for+SQL+Server&trusted_connection=yes")
>>> with engine.connect() as conn:
...     results = run_harness(conn)
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import asdict, dataclass
from typing import Any

from .python_impl import egfr_cr, egfr_cys
from .sql_render import render_tsql_cte
from .test_vectors import ABS_TOLERANCE, TEST_VECTORS

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


# ---- Connection-type detection -------------------------------------------- #


def _is_sqlite(conn: Any) -> bool:
    return isinstance(conn, sqlite3.Connection)


def _is_sqlalchemy(conn: Any) -> bool:
    # Duck-type: SQLAlchemy connections expose .dialect; avoid hard import.
    return not _is_sqlite(conn) and hasattr(conn, "dialect") and hasattr(conn, "execute")


def _translate_to_sqlite(sql: str) -> str:
    """T-SQL → sqlite shim: LEFT(x, n) → SUBSTR(x, 1, n)."""
    return re.sub(r"LEFT\(([^,]+),\s*(\d+)\)", r"SUBSTR(\1, 1, \2)", sql)


# ---- Thin adapter layer --------------------------------------------------- #
# Each adapter exposes: execute(sql), executemany(sql, rows), fetchall(), commit(), drop(table)

class _SqliteAdapter:
    def __init__(self, conn: sqlite3.Connection) -> None:
        conn.create_function("POWER", 2, lambda b, e: b ** e)
        self._conn = conn
        self._last: Any = None

    def execute(self, sql: str) -> "_SqliteAdapter":
        self._last = self._conn.execute(_translate_to_sqlite(sql))
        return self

    def executemany(self, sql: str, rows: list) -> None:
        self._conn.executemany(sql, rows)

    def fetchall(self) -> list:
        return self._last.fetchall()

    def commit(self) -> None:
        self._conn.commit()

    def drop(self, table: str) -> None:
        try:
            self._conn.execute(f"DROP TABLE IF EXISTS {table}")
        except Exception:  # noqa: BLE001
            pass


class _PyodbcAdapter:
    def __init__(self, conn: Any) -> None:
        self._conn = conn
        self._cur = conn.cursor()
        self._last: Any = None

    def execute(self, sql: str) -> "_PyodbcAdapter":
        self._cur.execute(sql)
        self._last = self._cur
        return self

    def executemany(self, sql: str, rows: list) -> None:
        self._cur.executemany(sql, rows)

    def fetchall(self) -> list:
        return self._last.fetchall()

    def commit(self) -> None:
        self._conn.commit()

    def drop(self, table: str) -> None:
        try:
            self._cur.execute(
                f"IF OBJECT_ID('tempdb..{table}') IS NOT NULL DROP TABLE {table}"
            )
        except Exception:  # noqa: BLE001
            pass


class _SqlAlchemyAdapter:
    def __init__(self, conn: Any) -> None:
        self._conn = conn
        self._last: Any = None

    def execute(self, sql: str) -> "_SqlAlchemyAdapter":
        try:
            from sqlalchemy import text  # type: ignore[import-not-found]
        except ImportError as exc:
            raise ImportError("sqlalchemy is not installed; run `uv sync --extra sqlserver`") from exc
        self._last = self._conn.execute(text(sql))
        return self

    def executemany(self, sql: str, rows: list) -> None:
        # SQLAlchemy text() uses :param style; build individual INSERT statements
        # using safe literal formatting (all values are internal test fixtures).
        try:
            from sqlalchemy import text  # type: ignore[import-not-found]
        except ImportError as exc:
            raise ImportError("sqlalchemy is not installed; run `uv sync --extra sqlserver`") from exc
        for row in rows:
            values = ", ".join(
                "NULL" if v is None else f"'{v}'" if isinstance(v, str) else str(v)
                for v in row
            )
            tbl = sql.split("INTO ")[1].split(" ")[0]
            self._conn.execute(text(f"INSERT INTO {tbl} VALUES ({values})"))

    def fetchall(self) -> list:
        return list(self._last.fetchall())

    def commit(self) -> None:
        # SQLAlchemy autocommit / engine.begin() handles this; explicit commit is a no-op here.
        try:
            self._conn.commit()
        except Exception:  # noqa: BLE001
            pass

    def drop(self, table: str) -> None:
        try:
            from sqlalchemy import text  # type: ignore[import-not-found]
            self._conn.execute(
                text(f"IF OBJECT_ID('tempdb..{table}') IS NOT NULL DROP TABLE {table}")
            )
        except Exception:  # noqa: BLE001
            pass


def _adapter(conn: Any) -> Any:
    if _is_sqlite(conn):
        return _SqliteAdapter(conn)
    if _is_sqlalchemy(conn):
        return _SqlAlchemyAdapter(conn)
    return _PyodbcAdapter(conn)


# ---- Table name selection ------------------------------------------------- #

_TABLE = "ckid_u25_test_vectors"       # sqlite / local
_TEMP_TABLE = "#ckid_u25_test_vectors"  # SQL Server temp table


def _table_name(conn: Any) -> str:
    return _TABLE if _is_sqlite(conn) else _TEMP_TABLE


# ---- Internal helpers ----------------------------------------------------- #


def _approx(actual: float | None, expected: float | None) -> bool:
    if expected is None:
        return actual is None
    return actual is not None and abs(actual - expected) <= ABS_TOLERANCE


# ---- Public API ----------------------------------------------------------- #


def run_harness(conn: Any) -> list[HarnessRow]:
    """Run the 20-vector harness against both Python impl and SQL.

    Creates a temporary table, inserts the 20 reference vectors, executes the
    rendered SQL query, compares Python and SQL outputs to frozen expected
    values, and tears down the temporary table.

    Accepts sqlite3, pyodbc, or SQLAlchemy connections — see module docstring
    for connection string examples.
    """
    table = _table_name(conn)
    db = _adapter(conn)

    # Create and populate
    db.execute(f"""
        CREATE TABLE {table} (
            patient_id INTEGER,
            lab_date   VARCHAR(10),
            age_yr     FLOAT,
            sex        VARCHAR(10),
            height_cm  FLOAT,
            scr_mgdl   FLOAT,
            cysc_mgl   FLOAT
        )
    """)
    db.executemany(
        f"INSERT INTO {table} VALUES (?, ?, ?, ?, ?, ?, ?)",
        [(v.test_id, "2026-01-01", v.age_yr, v.sex,
          v.height_cm, v.scr_mgdl, v.cysc_mgl) for v in TEST_VECTORS],
    )
    db.commit()

    # Execute rendered SQL
    sql = render_tsql_cte(source=table, egfr_threshold=None).rstrip().rstrip(";")
    if _is_sqlite(conn):
        sql = _translate_to_sqlite(sql)
    rows = db.execute(sql).fetchall()
    sql_rows: dict[int, Any] = {row[0]: row for row in rows}

    # Tear down
    db.drop(table)

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
    """Return results as a pandas DataFrame (requires ``uv add pandas``)."""
    try:
        import pandas as pd  # type: ignore[import-not-found]
    except ImportError as exc:
        raise ImportError("pandas is not installed; run `uv add pandas` first") from exc
    return pd.DataFrame([asdict(r) for r in results])
