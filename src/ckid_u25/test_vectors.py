"""20-row reference test vector set for CKiD U25 eGFR.

Mirrors the validation harness in ``ckid_u25_egfr_tsql.sql`` verbatim — same
test_id ordering, same descriptions, same expected values. Expected values
were independently hand-computed AND re-verified against a Python equivalent
during the SQL handoff design pass (handoff doc, "Validated facts").

Test #1 is the kidney.epi documented example
(``egfr.ckid_u25.cr(creatinine=1.4, age=10, height_cm=90, sex="Male") == 24.68``)
and serves as the cross-package validation anchor.

Tolerance: 0.05 mL/min/1.73m² (matches the SQL harness; loose because the
NIDDK table reports kappa to one decimal place).
"""

from __future__ import annotations

from dataclasses import dataclass

ABS_TOLERANCE: float = 0.05


@dataclass(frozen=True)
class TestVector:
    __test__ = False  # not a pytest test class
    test_id: int
    description: str
    sex: str | None
    age_yr: float | None
    height_cm: float | None
    scr_mgdl: float | None
    cysc_mgl: float | None
    expected_cr: float | None
    expected_cys: float | None


TEST_VECTORS: tuple[TestVector, ...] = (
    # Creatinine: kidney.epi documented example (verified)
    TestVector(1, "M age 10, 1-<12 band (kidney.epi doc example)",
               "Male", 10.0, 90.0, 1.4, None, 24.68, None),
    # Creatinine: each band, both sexes
    TestVector(2, "M age 5, 1-<12 band",
               "M", 5.0, 110.0, 0.5, None, 81.16, None),
    TestVector(3, "M age 14, 12-<18 band",
               "M", 14.0, 160.0, 1.0, None, 68.14, None),
    TestVector(4, "M age 12.0, boundary (12-<18 band, K=39.0)",
               "Male", 12.0, 140.0, 0.7, None, 78.00, None),
    TestVector(5, "M age 20, 18-25 band (K=50.8)",
               "M", 20.0, 180.0, 1.2, None, 76.20, None),
    TestVector(6, "F age 10, 1-<12 band",
               "F", 10.0, 140.0, 0.6, None, 82.91, None),
    TestVector(7, "F age 13, 12-<18 band",
               "Female", 13.0, 155.0, 0.8, None, 71.55, None),
    TestVector(8, "F age 20, 18-25 band (K=41.4)",
               "F", 20.0, 165.0, 1.0, None, 68.31, None),
    # Cystatin C: each band, both sexes (note male breakpoint at 15)
    TestVector(9, "M age 5, 1-<15 band (Age-15 exponent)",
               "M", 5.0, None, None, 0.9, None, 86.85),
    TestVector(10, "M age 16, 15-<18 band",
               "M", 16.0, None, None, 1.2, None, 69.76),
    TestVector(11, "M age 20, 18-25 band (K=77.1)",
               "M", 20.0, None, None, 1.1, None, 70.09),
    TestVector(12, "F age 10, 1-<12 band",
               "F", 10.0, None, None, 0.9, None, 88.07),
    TestVector(13, "F age 15, 12-<18 band",
               "F", 15.0, None, None, 1.0, None, 73.82),
    TestVector(14, "F age 18.0, boundary (18-25 band, K=68.3)",
               "Female", 18.0, None, None, 1.1, None, 62.09),
    # Out-of-range and missing-input handling (all expected None)
    TestVector(15, "age 0.5 out of range",
               "M", 0.5, 100.0, 0.5, 0.8, None, None),
    TestVector(16, "age 26 out of range",
               "F", 26.0, 165.0, 1.0, 1.0, None, None),
    TestVector(17, "missing height (Cr only); cysC unaffected",
               "M", 10.0, None, 1.0, 1.0, None, 82.56),
    TestVector(18, "missing Scr; cysC unaffected",
               "F", 10.0, 140.0, None, 0.9, None, 88.07),
    TestVector(19, "missing CysC",
               "M", 10.0, 90.0, 1.4, None, 24.68, None),
    TestVector(20, "invalid sex",
               "X", 10.0, 90.0, 1.4, 0.9, None, None),
)
