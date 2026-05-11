"""Validate python_impl against the frozen 20-vector reference set.

If R + rpy2 + kidney.epi are available locally, ALSO run a head-to-head
comparison between python_impl and kidney.epi on the same vectors. When any
piece of the R stack is missing the rpy2 test skips — pinning is still
provided by the frozen expected values in ``test_vectors.py``.
"""

from __future__ import annotations

import pytest

from ckid_u25 import egfr_cr, egfr_cr_cys, egfr_cys
from ckid_u25.test_vectors import ABS_TOLERANCE, TEST_VECTORS, TestVector


def _approx_equal(actual: float | None, expected: float | None) -> bool:
    if expected is None:
        return actual is None
    if actual is None:
        return False
    return abs(actual - expected) <= ABS_TOLERANCE


@pytest.mark.parametrize("v", TEST_VECTORS, ids=lambda v: f"v{v.test_id:02d}")
def test_creatinine_eGFR_matches_reference(v: TestVector) -> None:
    got = egfr_cr(v.sex, v.age_yr, v.height_cm, v.scr_mgdl)
    assert _approx_equal(got, v.expected_cr), (
        f"[{v.test_id}] {v.description}: got egfr_cr={got!r}, expected={v.expected_cr!r}"
    )


@pytest.mark.parametrize("v", TEST_VECTORS, ids=lambda v: f"v{v.test_id:02d}")
def test_cystatin_eGFR_matches_reference(v: TestVector) -> None:
    got = egfr_cys(v.sex, v.age_yr, v.cysc_mgl)
    assert _approx_equal(got, v.expected_cys), (
        f"[{v.test_id}] {v.description}: got egfr_cys={got!r}, expected={v.expected_cys!r}"
    )


def test_combined_egfr_is_none_when_either_marker_missing() -> None:
    # Cr-only path: CysC missing -> combined is None even though egfr_cr is valid
    assert egfr_cr_cys("M", 10.0, 90.0, 1.4, None) is None
    # CysC-only path: Scr missing -> combined is None even though egfr_cys is valid
    assert egfr_cr_cys("F", 10.0, 140.0, None, 0.9) is None
    # Both present -> simple average
    cr = egfr_cr("M", 10.0, 90.0, 1.4)
    cys = egfr_cys("M", 10.0, 0.9)
    combined = egfr_cr_cys("M", 10.0, 90.0, 1.4, 0.9)
    assert cr is not None and cys is not None and combined is not None
    assert abs(combined - (cr + cys) / 2.0) < 1e-12


def test_kidney_epi_documented_anchor_exact() -> None:
    """Tighter tolerance on the single value documented by kidney.epi."""
    got = egfr_cr("Male", 10.0, 90.0, 1.4)
    assert got is not None
    assert abs(got - 24.68) <= 0.01


def test_male_cysc_breakpoint_is_at_15_not_12() -> None:
    """Regression: easy-to-miss trap — male CysC uses Age-15 reference, not Age-12."""
    # At age 14 the male CysC band is still 1-<15 (Age-15 reference).
    # Compute by hand and confirm:
    expected = 87.2 * (1.011 ** (14.0 - 15.0)) / 1.0
    assert abs(egfr_cys("M", 14.0, 1.0) - expected) < 1e-9  # type: ignore[operator]


def test_sex_normalization_accepts_common_encodings() -> None:
    cr_val = egfr_cr("Male", 10.0, 90.0, 1.4)
    for sex_in in ("M", "m", "MALE", "Male", "male"):
        assert egfr_cr(sex_in, 10.0, 90.0, 1.4) == cr_val
    # Numeric encodings are NOT supported (handoff: "normalize upstream").
    assert egfr_cr(1, 10.0, 90.0, 1.4) is None
    assert egfr_cr("", 10.0, 90.0, 1.4) is None
    assert egfr_cr(None, 10.0, 90.0, 1.4) is None


def test_age_boundaries() -> None:
    # age == AGE_MIN (1.0) -> in range
    assert egfr_cr("M", 1.0, 80.0, 0.5) is not None
    # age == AGE_MAX (25.0) -> in range, terminal band
    assert egfr_cr("M", 25.0, 180.0, 1.0) is not None
    # just below / above
    assert egfr_cr("M", 0.999, 80.0, 0.5) is None
    assert egfr_cr("M", 25.001, 180.0, 1.0) is None


# --------------------------------------------------------------------------- #
# Optional: head-to-head against kidney.epi via rpy2.                          #
# Skips cleanly when R / rpy2 / kidney.epi is not installed.                   #
# --------------------------------------------------------------------------- #

def _try_load_kidney_epi():
    try:
        from rpy2.robjects.packages import importr  # type: ignore[import-not-found]
    except ImportError:
        return None
    try:
        return importr("kidney.epi")
    except Exception:  # noqa: BLE001 — R may be installed but package missing
        return None


@pytest.mark.parametrize("v", TEST_VECTORS, ids=lambda v: f"v{v.test_id:02d}")
def test_creatinine_matches_kidney_epi(v: TestVector) -> None:
    pkg = _try_load_kidney_epi()
    if pkg is None:
        pytest.skip("rpy2 + kidney.epi not available; pinned against frozen vectors only")
    if v.scr_mgdl is None or v.height_cm is None or v.age_yr is None or v.sex is None:
        pytest.skip("vector lacks Cr inputs")
    if not (1.0 <= v.age_yr <= 25.0):
        pytest.skip("vector is out-of-range; kidney.epi returns NA, already covered in frozen vectors")
    r_val = float(
        pkg.egfr_ckid_u25_cr(  # function exposed by rpy2 as snake_case
            creatinine=v.scr_mgdl,
            age=v.age_yr,
            height_cm=v.height_cm,
            sex=v.sex,
            creatinine_units="mg/dl",
        )[0]
    )
    py_val = egfr_cr(v.sex, v.age_yr, v.height_cm, v.scr_mgdl)
    assert py_val is not None
    assert abs(py_val - r_val) <= 0.01, f"py={py_val}, kidney.epi={r_val}"
