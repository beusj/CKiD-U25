"""T-SQL renderer for the CKiD U25 eGFR inline CTE.

Produces the inline CTE pattern from the project's reference SQL artifact
(``ckid_u25_egfr_tsql.sql``, Pattern A) parameterized on source table,
column names, alias, and an optional cohort eGFR threshold.

All numeric constants come from ``coefficients.py`` — a coefficient table
update there propagates to both Python and SQL outputs.
"""

from __future__ import annotations

from .coefficients import (
    AGE_MAX,
    AGE_MIN,
    CREATININE_BANDS,
    CYSTATIN_C_BANDS,
    Band,
)


def _fmt(x: float) -> str:
    """Format a numeric constant for SQL: always show '.0' for integer values."""
    if x == int(x):
        return f"{int(x)}.0"
    return f"{x:g}"


def _cr_then(band: Band, age_expr: str, height_expr: str, scr_expr: str) -> str:
    height_m = f"({height_expr} / 100.0)"
    if band.base == 1.0:
        return f"{_fmt(band.kappa)} * {height_m} / {scr_expr}"
    return (
        f"{_fmt(band.kappa)} * POWER({_fmt(band.base)}, "
        f"{age_expr} - {_fmt(band.age_offset)})"
        f" * {height_m} / {scr_expr}"
    )


def _cys_then(band: Band, age_expr: str, cysc_expr: str) -> str:
    if band.base == 1.0:
        return f"{_fmt(band.kappa)} / {cysc_expr}"
    return (
        f"{_fmt(band.kappa)} * POWER({_fmt(band.base)}, "
        f"{age_expr} - {_fmt(band.age_offset)})"
        f" / {cysc_expr}"
    )


def _band_comment(sex_word: str, band: Band) -> str:
    suffix = " (constant)" if band.base == 1.0 else ""
    return f"-- {sex_word}: {band.label}{suffix}"


def _when_predicate(sex_char: str, band: Band, sex_expr: str, age_expr: str) -> str:
    sex_check = f"UPPER(LEFT({sex_expr}, 1)) = '{sex_char}'"
    if band.upper_exclusive is None:
        return f"WHEN {sex_check}"
    return f"WHEN {sex_check} AND {age_expr} < {_fmt(band.upper_exclusive)}"


def render_tsql_cte(
    source: str = "dbo.my_lab_source",
    *,
    age_col: str = "age_yr",
    sex_col: str = "sex",
    height_col: str = "height_cm",
    scr_col: str = "scr_mgdl",
    cysc_col: str = "cysc_mgl",
    patient_id_col: str = "patient_id",
    lab_date_col: str = "lab_date",
    source_alias: str = "l",
    egfr_threshold: float | None = 60.0,
) -> str:
    """Render the CKiD U25 inline CTE as T-SQL.

    ``egfr_threshold=None`` omits the trailing
    ``WHERE egfr_cr < X OR egfr_cys < X`` cohort filter (useful when wrapping
    the CTE as a view or computing eGFR for an entire dataset).
    """
    a = source_alias
    age = f"{a}.{age_col}"
    sex = f"{a}.{sex_col}"
    height = f"{a}.{height_col}"
    scr = f"{a}.{scr_col}"
    cysc = f"{a}.{cysc_col}"

    cr_branch_lines: list[str] = []
    for sex_char, sex_word in (("M", "MALE"), ("F", "FEMALE")):
        for band in CREATININE_BANDS[sex_char]:
            cr_branch_lines.append(f"            {_band_comment(sex_word, band)}")
            cr_branch_lines.append(f"            {_when_predicate(sex_char, band, sex, age)}")
            cr_branch_lines.append(f"                THEN {_cr_then(band, age, height, scr)}")

    cys_branch_lines: list[str] = []
    for sex_char, sex_word in (("M", "MALE"), ("F", "FEMALE")):
        for band in CYSTATIN_C_BANDS[sex_char]:
            cys_branch_lines.append(f"            {_band_comment(sex_word, band)}")
            cys_branch_lines.append(f"            {_when_predicate(sex_char, band, sex, age)}")
            cys_branch_lines.append(f"                THEN {_cys_then(band, age, cysc)}")

    cr_block = "\n".join(cr_branch_lines)
    cys_block = "\n".join(cys_branch_lines)

    age_min, age_max = _fmt(AGE_MIN), _fmt(AGE_MAX)

    where_clause = ""
    if egfr_threshold is not None:
        t = _fmt(float(egfr_threshold))
        where_clause = f"\nWHERE egfr_cr < {t}\n   OR egfr_cys < {t}"

    return f"""WITH labs_with_egfr AS (
    SELECT
        {a}.{patient_id_col},
        {a}.{lab_date_col},
        {a}.{age_col},
        {a}.{sex_col},
        {a}.{height_col},
        {a}.{scr_col},
        {a}.{cysc_col},

        /* -------- CKiD U25 Creatinine-based eGFR -------- */
        CASE
            WHEN {age} IS NULL OR {age} < {age_min} OR {age} > {age_max} THEN NULL
            WHEN {scr} IS NULL OR {scr} <= 0 THEN NULL
            WHEN {height} IS NULL OR {height} <= 0 THEN NULL
            WHEN {sex} IS NULL OR UPPER(LEFT({sex}, 1)) NOT IN ('M','F') THEN NULL

{cr_block}
        END AS egfr_cr,

        /* -------- CKiD U25 Cystatin C-based eGFR --------
           NOTE: male piecewise uses Age-15 reference and breakpoint at 15,
                 not Age-12 like the creatinine equation. */
        CASE
            WHEN {age} IS NULL OR {age} < {age_min} OR {age} > {age_max} THEN NULL
            WHEN {cysc} IS NULL OR {cysc} <= 0 THEN NULL
            WHEN {sex} IS NULL OR UPPER(LEFT({sex}, 1)) NOT IN ('M','F') THEN NULL

{cys_block}
        END AS egfr_cys
    FROM {source} AS {a}
)
SELECT
    {patient_id_col}, {lab_date_col}, {age_col}, {sex_col},
    {height_col}, {scr_col}, {cysc_col},
    egfr_cr,
    egfr_cys,
    CASE
        WHEN egfr_cr IS NOT NULL AND egfr_cys IS NOT NULL
            THEN (egfr_cr + egfr_cys) / 2.0
    END AS egfr_cr_cys
FROM labs_with_egfr{where_clause};
"""
