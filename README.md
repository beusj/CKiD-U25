# ckid-u25

Python reference implementation and T-SQL renderer for the **CKiD U25 eGFR equations** (Pierce 2021).

---

## Citation

> Pierce CB, Muñoz A, Ng DK, Warady BA, Furth SL, Schwartz GJ.
> Age- and sex-dependent clinical equations to estimate glomerular filtration rates
> in children and young adults with chronic kidney disease.
> *Kidney International* 2021;99(4):948–956.
> doi:[10.1016/j.kint.2020.10.047](https://doi.org/10.1016/j.kint.2020.10.047)

Coefficient values per NIDDK Tables 1 & 2 (Last Reviewed May 2025):
<https://www.niddk.nih.gov/research-funding/research-programs/kidney-clinical-research-epidemiology/laboratory/glomerular-filtration-rate-equations/children-adolescents-young-adults>

---

## What it does

| Module | Purpose |
|---|---|
| `coefficients.py` | Single source of truth — NIDDK piecewise band table feeds both Python and SQL |
| `python_impl.py` | `egfr_cr`, `egfr_cys`, `egfr_cr_cys` — pure Python, no dependencies |
| `sql_render.py` | `render_tsql_cte(...)` — generates the inline CTE pattern for SQL Server (or sqlite) |
| `validate.py` | `run_harness(conn)` — runs the 20-vector reference set against both Python and SQL |
| `test_vectors.py` | 20-row reference set from the original T-SQL harness, all Python-verified |

---

## Input units and constraints

| Input | Unit | Notes |
|---|---|---|
| `age` | decimal years | Valid range: 1 to 25 inclusive; returns `None`/NULL outside range |
| `sex` | text | First character matched case-insensitively: `M`/`Male`/`MALE` or `F`/`Female`/`FEMALE`. Numeric encodings are **not** supported — normalize upstream. |
| `height_cm` | centimeters | Converted to meters inline |
| `scr_mgdl` | mg/dL | Must be IDMS-traceable enzymatic creatinine |
| `cysc_mgl` | mg/L | Must be **IFCC-standardized** (ERM-DA471/IFCC) — see warning below |

> **IFCC calibration warning:** The CysC equations were developed on IFCC-standardized
> cystatin C. Verify your local lab's assay calibration before trusting any CysC-based
> eGFR result. This is a lab-services check that this package cannot perform automatically.

---

## Python usage

```python
from ckid_u25 import egfr_cr, egfr_cys, egfr_cr_cys

# Creatinine-based (kidney.epi documented example: should return ~24.68)
egfr_cr("Male", age=10.0, height_cm=90.0, scr_mgdl=1.4)   # → 24.68

# Cystatin C-based
egfr_cys("F", age=15.0, cysc_mgl=1.0)                      # → ~73.82

# Combined (averaged) — None when either marker is missing
egfr_cr_cys("M", age=10.0, height_cm=90.0, scr_mgdl=1.4, cysc_mgl=0.9)

# All functions return None on missing or out-of-range input
egfr_cr("M", age=0.5, height_cm=100.0, scr_mgdl=0.5)  # → None (age < 1)
egfr_cr("M", age=10.0, height_cm=None, scr_mgdl=1.0)  # → None (missing height)
```

---

## SQL renderer

```python
from ckid_u25.sql_render import render_tsql_cte

# Default: matches column names in ckid_u25_egfr_tsql.sql, threshold 60
print(render_tsql_cte())

# Customize for your source table and column names
sql = render_tsql_cte(
    source="dbo.labs_wide",
    age_col="age_at_collection",
    sex_col="patient_sex",
    height_col="ht_cm",
    scr_col="creatinine_result",
    cysc_col="cystatin_c_result",
    patient_id_col="mrn",
    lab_date_col="collection_dt",
    egfr_threshold=30,       # WHERE egfr_cr < 30 OR egfr_cys < 30
)

# Omit the WHERE filter (e.g. wrapping as a view or computing for all rows)
sql = render_tsql_cte(source="dbo.labs_wide", egfr_threshold=None)
```

The renderer derives all numeric constants from `coefficients.py` — a coefficient
update in one place propagates to both the Python functions and the SQL output.

---

## Validation harness

Run before using in any cohort definition:

```python
import sqlite3
from ckid_u25.validate import run_harness, print_harness_report

# Offline (no SQL Server needed)
results = run_harness(sqlite3.connect(":memory:"))
print_harness_report(results)

# Against live SQL Server (requires pyodbc)
import pyodbc
conn = pyodbc.connect("DSN=my_server;Trusted_Connection=yes")
results = run_harness(conn)
print_harness_report(results)
conn.close()

# As a DataFrame (requires pandas)
from ckid_u25.validate import to_dataframe
df = to_dataframe(results)
```

Expected output:

```
 id  status  description
----------------------------------------------------------------------
  1  ✓ PASS  M age 10, 1-<12 band (kidney.epi doc example)
  2  ✓ PASS  M age 5, 1-<12 band
...
 20  ✓ PASS  invalid sex
----------------------------------------------------------------------
  20/20 passed
```

---

## Installation and dependencies

**Runtime:** no external dependencies (pure Python stdlib).

**Development:**

```bash
# Install uv if you don't have it
curl -LsSf https://astral.sh/uv/install.sh | sh

# Clone and install with dev deps
git clone <repo>
cd ckid-u25
uv sync

# Run tests
uv run pytest
```

**No `requirements.txt` needed** — `pyproject.toml` + `uv.lock` are the authoritative
dependency spec. If you need a pip-compatible file for a legacy environment:

```bash
uv export --no-dev --format requirements-txt > requirements.txt      # runtime only
uv export --format requirements-txt > requirements-dev.txt           # + dev deps
```

**Optional extras** (not included by default):

| Extra | Install | Purpose |
|---|---|---|
| SQL Server validation | `uv add pyodbc` | `run_harness()` against live SQL Server |
| DataFrame output | `uv add pandas` | `to_dataframe()` in `validate.py` |
| kidney.epi cross-validation | Install R, then `uv add rpy2` | Activates 20 rpy2 comparison tests in `tests/test_python_impl.py` that skip otherwise |

---

## R / rpy2 cross-validation (optional)

The `tests/test_python_impl.py` file contains a parametrized test that runs all 20
reference vectors through `kidney.epi::egfr.ckid_u25.cr()` and compares against
this package's Python output to within 0.01 mL/min/1.73m². These tests skip
automatically when R or rpy2 is absent.

To activate:

```bash
# Install R (macOS)
brew install r

# Install kidney.epi in R
Rscript -e 'install.packages("kidney.epi")'

# Install rpy2
uv add rpy2

# Run — the 20 skipped tests will now run
uv run pytest
```

> **Do not use `cliot::ckid_u25_egfr()`** — that function is a relabeled Bedside
> Schwartz (k=0.413) and is not Pierce 2021. Use `kidney.epi` for cross-validation.

---

## Known traps (from the design pass)

1. **Male CysC piecewise breakpoint is at age 15, not 12** — unlike the creatinine
   equation and the female CysC equation. Easy to miss when porting.
2. **Combined eGFR is NULL when either marker is missing.** If a cohort criterion is
   "any U25 < X", filter on `egfr_cr OR egfr_cys`, not the combined column.
3. **IFCC cystatin C calibration is non-negotiable.** Pierce 2021 used
   ERM-DA471/IFCC-standardized CysC. Verify the local lab assay before trusting
   CysC results.
4. **Pierce 2021 has two candidate citations.** The canonical methods paper is
   *Kidney Int* 2021;99(4):948–956 (doi:10.1016/j.kint.2020.10.047). The `kidney.epi`
   package cites this correctly.

---

## License

Internal research tool. Cite Pierce *et al.* 2021 (*Kidney International*) in any
methods statement using these equations.
