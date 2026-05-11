"""Validate the SQL renderer against the reference SQL and the frozen vectors.

Two layers:

1. Structural assertions on the rendered text (coefficients present, column
   names substituted, threshold WHERE emitted/omitted).

2. Functional equivalence: execute the rendered query (lightly translated
   from T-SQL to sqlite) against an in-memory sqlite DB seeded with the
   20-row test vector table, and assert the egfr_cr / egfr_cys / egfr_cr_cys
   outputs match the frozen expected values within tolerance.
"""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path

from ckid_u25.coefficients import CREATININE_BANDS, CYSTATIN_C_BANDS
from ckid_u25.sql_render import render_tsql_cte
from ckid_u25.test_vectors import ABS_TOLERANCE, TEST_VECTORS


# ---------- Structural ----------------------------------------------------- #

def test_render_contains_every_coefficient_from_table() -> None:
    sql = render_tsql_cte()
    expected = set()
    for bands in (*CREATININE_BANDS.values(), *CYSTATIN_C_BANDS.values()):
        for band in bands:
            expected.add(band.kappa)
            if band.base != 1.0:
                expected.add(band.base)
    for v in expected:
        # `%g` is the same formatting used inside the renderer for non-integers.
        token = f"{int(v)}.0" if v == int(v) else f"{v:g}"
        assert token in sql, f"coefficient {token!r} missing from rendered SQL"


def test_render_contains_every_age_breakpoint() -> None:
    sql = render_tsql_cte()
    breakpoints = set()
    for bands in (*CREATININE_BANDS.values(), *CYSTATIN_C_BANDS.values()):
        for band in bands:
            if band.upper_exclusive is not None:
                breakpoints.add(band.upper_exclusive)
            breakpoints.add(band.age_offset)
    for v in sorted(breakpoints):
        if v == 0.0:
            continue  # constant-band offset, doesn't appear as a numeric literal
        token = f"{int(v)}.0" if v == int(v) else f"{v:g}"
        assert token in sql, f"age value {token!r} missing from rendered SQL"


def test_render_substitutes_column_and_source_names() -> None:
    sql = render_tsql_cte(
        source="research.labs_wide",
        age_col="age",
        sex_col="gender",
        height_col="ht_cm",
        scr_col="creatinine",
        cysc_col="cystatin_c",
        patient_id_col="pid",
        lab_date_col="result_dt",
        source_alias="t",
        egfr_threshold=None,
    )
    assert "FROM research.labs_wide AS t" in sql
    for tok in ("t.pid", "t.result_dt", "t.age", "t.gender",
                "t.ht_cm", "t.creatinine", "t.cystatin_c"):
        assert tok in sql, f"missing parameterized column reference {tok!r}"
    # Original defaults should not leak in
    for leak in ("age_yr", "scr_mgdl", "cysc_mgl", "height_cm",
                 "patient_id", "lab_date", "dbo.my_lab_source"):
        assert leak not in sql, f"default identifier {leak!r} leaked through parameterization"


def test_threshold_clause_present_by_default_and_omittable() -> None:
    sql = render_tsql_cte(egfr_threshold=45)
    assert "WHERE egfr_cr < 45.0" in sql
    assert "OR egfr_cys < 45.0" in sql

    sql_no_thresh = render_tsql_cte(egfr_threshold=None)
    assert "WHERE egfr_cr" not in sql_no_thresh
    assert "WHERE egfr_cys" not in sql_no_thresh


def test_render_uses_male_cysc_breakpoint_15_not_12() -> None:
    """Regression for the easy-to-miss trap called out in the handoff."""
    sql = render_tsql_cte()
    # Find the CysC CASE block, then look for the male 1-<15 band predicate.
    cys_start = sql.index("CKiD U25 Cystatin C-based eGFR")
    cys_block = sql[cys_start:]
    # Male 1-<15 band: predicate is `age_yr < 15.0`, the THEN uses Age-15 reference.
    assert "-- MALE: 1 to <15" in cys_block
    assert "l.age_yr < 15.0" in cys_block
    # The male Age-12 band (which would be wrong for CysC) must NOT appear.
    # Anchor on "-- " so this doesn't match the FEMALE band by suffix.
    assert "-- MALE: 1 to <12" not in cys_block


# ---------- Functional (sqlite) -------------------------------------------- #

def _translate_to_sqlite(sql: str) -> str:
    """T-SQL → sqlite shim: LEFT(x, n) → SUBSTR(x, 1, n). Other constructs are portable."""
    return re.sub(r"LEFT\(([^,]+),\s*(\d+)\)", r"SUBSTR(\1, 1, \2)", sql)


def _seed_sqlite() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.create_function("POWER", 2, lambda b, e: b ** e)
    conn.execute(
        """
        CREATE TABLE my_lab_source (
            patient_id INTEGER,
            lab_date   TEXT,
            age_yr     REAL,
            sex        TEXT,
            height_cm  REAL,
            scr_mgdl   REAL,
            cysc_mgl   REAL
        )
        """
    )
    conn.executemany(
        "INSERT INTO my_lab_source VALUES (?, ?, ?, ?, ?, ?, ?)",
        [(v.test_id, "2026-01-01", v.age_yr, v.sex,
          v.height_cm, v.scr_mgdl, v.cysc_mgl) for v in TEST_VECTORS],
    )
    return conn


def test_rendered_sql_matches_frozen_vectors_via_sqlite() -> None:
    # Render against `my_lab_source` directly — sqlite has no `dbo.` schema.
    sql = render_tsql_cte(source="my_lab_source", egfr_threshold=None)
    sql = _translate_to_sqlite(sql).rstrip().rstrip(";")

    conn = _seed_sqlite()
    rows = conn.execute(sql).fetchall()
    conn.close()
    by_pid = {row[0]: row for row in rows}

    def _approx(actual: float | None, expected: float | None) -> bool:
        if expected is None:
            return actual is None
        return actual is not None and abs(actual - expected) <= ABS_TOLERANCE

    failures: list[str] = []
    for v in TEST_VECTORS:
        if v.test_id not in by_pid:
            failures.append(f"[{v.test_id}] missing row in rendered-SQL output")
            continue
        row = by_pid[v.test_id]
        got_cr, got_cys = row[7], row[8]
        if not _approx(got_cr, v.expected_cr):
            failures.append(f"[{v.test_id}] {v.description}: cr got={got_cr!r}, want={v.expected_cr!r}")
        if not _approx(got_cys, v.expected_cys):
            failures.append(f"[{v.test_id}] {v.description}: cys got={got_cys!r}, want={v.expected_cys!r}")
    assert not failures, "rendered SQL diverged from frozen vectors:\n" + "\n".join(failures)


def test_combined_egfr_is_null_in_sql_when_either_marker_missing() -> None:
    """The CASE for egfr_cr_cys returns NULL whenever either eGFR is NULL — matches NIDDK guidance."""
    sql = render_tsql_cte(source="my_lab_source", egfr_threshold=None)
    sql = _translate_to_sqlite(sql).rstrip().rstrip(";")
    conn = _seed_sqlite()
    rows = conn.execute(sql).fetchall()
    conn.close()
    by_pid = {row[0]: row for row in rows}
    # vector 17: missing height -> egfr_cr NULL, egfr_cys valid -> combined NULL
    assert by_pid[17][7] is None and by_pid[17][8] is not None and by_pid[17][9] is None
    # vector 19: CysC missing -> egfr_cr valid, egfr_cys NULL -> combined NULL
    assert by_pid[19][7] is not None and by_pid[19][8] is None and by_pid[19][9] is None
    # vector 1 (kidney.epi anchor) has only Cr inputs -> combined NULL
    assert by_pid[1][7] is not None and by_pid[1][8] is None and by_pid[1][9] is None


def test_reference_sql_file_is_present_for_handoff_traceability() -> None:
    """Smoke check: the reference SQL artifact lives next to this checkout for traceability."""
    ref = Path.home() / "Downloads" / "ckid_u25_egfr_tsql.sql"
    if not ref.exists():
        # The reference SQL lives outside the repo by design (handoff artifact);
        # not present in CI. Skip silently rather than fail.
        return
    text = ref.read_text()
    # Spot-checks: ensure the reference contains the validated coefficients
    for token in ("39.0", "50.8", "41.4", "36.1", "87.2", "77.1", "79.9", "68.3"):
        assert token in text, f"reference SQL missing kappa token {token}"
