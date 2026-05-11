"""Python reference implementation of CKiD U25 eGFR (Pierce 2021).

Returns None for missing/invalid input — no imputation, matching the T-SQL
implementation's behavior (which returns NULL).

Input units:
    age:       decimal years
    sex:       text whose first character (case-insensitive) is 'M' or 'F'
    height_cm: centimeters
    scr_mgdl:  serum creatinine, mg/dL (IDMS-traceable assay)
    cysc_mgl:  cystatin C, mg/L (IFCC-standardized, ERM-DA471/IFCC)
"""

from __future__ import annotations

from .coefficients import (
    AGE_MAX,
    AGE_MIN,
    CREATININE_BANDS,
    CYSTATIN_C_BANDS,
    find_band,
)


def _normalize_sex(sex: object) -> str | None:
    """Mirror UPPER(LEFT(sex,1)) IN ('M','F') from the T-SQL."""
    if sex is None:
        return None
    s = str(sex).strip()
    if not s:
        return None
    first = s[0].upper()
    return first if first in ("M", "F") else None


def _age_ok(age: float | None) -> bool:
    return age is not None and AGE_MIN <= age <= AGE_MAX


def egfr_cr(
    sex: object,
    age: float | None,
    height_cm: float | None,
    scr_mgdl: float | None,
) -> float | None:
    """CKiD U25 creatinine-based eGFR in mL/min/1.73m². None on missing/invalid input."""
    if not _age_ok(age):
        return None
    if scr_mgdl is None or scr_mgdl <= 0:
        return None
    if height_cm is None or height_cm <= 0:
        return None
    s = _normalize_sex(sex)
    if s is None:
        return None
    band = find_band(age, CREATININE_BANDS[s])  # type: ignore[arg-type]
    return (
        band.kappa
        * (band.base ** (age - band.age_offset))  # type: ignore[operator]
        * (height_cm / 100.0)
        / scr_mgdl
    )


def egfr_cys(
    sex: object,
    age: float | None,
    cysc_mgl: float | None,
) -> float | None:
    """CKiD U25 cystatin-C-based eGFR in mL/min/1.73m². None on missing/invalid input."""
    if not _age_ok(age):
        return None
    if cysc_mgl is None or cysc_mgl <= 0:
        return None
    s = _normalize_sex(sex)
    if s is None:
        return None
    band = find_band(age, CYSTATIN_C_BANDS[s])  # type: ignore[arg-type]
    return (
        band.kappa
        * (band.base ** (age - band.age_offset))  # type: ignore[operator]
        / cysc_mgl
    )


def egfr_cr_cys(
    sex: object,
    age: float | None,
    height_cm: float | None,
    scr_mgdl: float | None,
    cysc_mgl: float | None,
) -> float | None:
    """Averaged combined eGFR. None when either marker is missing — matches NIDDK guidance."""
    cr = egfr_cr(sex, age, height_cm, scr_mgdl)
    cys = egfr_cys(sex, age, cysc_mgl)
    if cr is None or cys is None:
        return None
    return (cr + cys) / 2.0
