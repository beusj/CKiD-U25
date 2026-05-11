"""CKiD U25 eGFR coefficients — single source of truth.

Source: Pierce CB, Muñoz A, Ng DK, Warady BA, Furth SL, Schwartz GJ.
Age- and sex-dependent clinical equations to estimate glomerular filtration
rates in children and young adults with chronic kidney disease.
Kidney International 2021;99(4):948-956. doi:10.1016/j.kint.2020.10.047

Coefficient values per NIDDK Tables 1 & 2 (Last Reviewed May 2025):
https://www.niddk.nih.gov/research-funding/research-programs/kidney-clinical-research-epidemiology/laboratory/glomerular-filtration-rate-equations/children-adolescents-young-adults

eGFR formula per band:
    creatinine:  kappa * base ** (age - age_offset) * (height_cm / 100) / scr_mgdl
    cystatin C:  kappa * base ** (age - age_offset) / cysc_mgl

The "18 to 25" bands are constants (base=1.0, age_offset=0.0): the power
factor evaluates to 1.0 and the formula collapses to kappa*(ht/100)/Scr or
kappa/CysC.

NIDDK Table 2 lists male CysC ages 1-<12 and 12-<15 as separate rows with
identical coefficients — they collapse to a single 1-<15 band here. The male
CysC piecewise uses Age-15 as its reference (breakpoint at 15), unlike the
creatinine equation and the female CysC equation, which use Age-12.
"""

from __future__ import annotations

from dataclasses import dataclass

AGE_MIN: float = 1.0
AGE_MAX: float = 25.0  # inclusive


@dataclass(frozen=True)
class Band:
    """One piecewise segment of a U25 equation.

    `upper_exclusive=None` denotes the terminal band — anything not matched by
    earlier bands (and within [AGE_MIN, AGE_MAX]) falls here.
    """

    upper_exclusive: float | None
    kappa: float
    base: float
    age_offset: float
    label: str


CREATININE_BANDS: dict[str, tuple[Band, ...]] = {
    "F": (
        Band(12.0, 36.1, 1.008, 12.0, "1 to <12"),
        Band(18.0, 36.1, 1.023, 12.0, "12 to <18"),
        Band(None, 41.4, 1.0, 0.0, "18 to 25"),
    ),
    "M": (
        Band(12.0, 39.0, 1.008, 12.0, "1 to <12"),
        Band(18.0, 39.0, 1.045, 12.0, "12 to <18"),
        Band(None, 50.8, 1.0, 0.0, "18 to 25"),
    ),
}

CYSTATIN_C_BANDS: dict[str, tuple[Band, ...]] = {
    "F": (
        Band(12.0, 79.9, 1.004, 12.0, "1 to <12"),
        Band(18.0, 79.9, 0.974, 12.0, "12 to <18"),
        Band(None, 68.3, 1.0, 0.0, "18 to 25"),
    ),
    "M": (
        Band(15.0, 87.2, 1.011, 15.0, "1 to <15"),
        Band(18.0, 87.2, 0.960, 15.0, "15 to <18"),
        Band(None, 77.1, 1.0, 0.0, "18 to 25"),
    ),
}


def find_band(age: float, bands: tuple[Band, ...]) -> Band:
    """First band whose range contains `age`. Caller must pre-validate age in [AGE_MIN, AGE_MAX]."""
    for band in bands:
        if band.upper_exclusive is None or age < band.upper_exclusive:
            return band
    raise ValueError(f"No band matched age={age!r}; band table must cover up to AGE_MAX")
