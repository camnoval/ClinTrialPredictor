"""Reconstruct a two-arm primary comparison from posted group measurements. Pure engine.

WHY THIS EXISTS, AND WHAT IT IS NOT
===================================
The gap audit found 1,644 trials that posted primary outcomes and no analysis, of which
887 were multi-arm. Those trials HAD a comparator and withheld the comparison. This module
recomputes the comparison from the per-group values AACT does carry, so those trials can
carry a label.

The resulting p-value is OURS, not the sponsor's. It uses:
  - no pre-specified analysis model          - no covariate adjustment
  - no stratification                        - no ITT/PP distinction
  - no multiplicity or interim adjustment    - no repeated-measures structure
A sponsor's protocol-specified ANCOVA on a stratified population will not generally agree
with a Welch t-test on two posted summaries. So any label this produces belongs in its own
tier, is excluded from headline numbers, and is only trustworthy to the extent that
validate_reconstruction.py measures its agreement with sponsor verdicts on the overlap
where BOTH exist. Validate first, then decide -- that ordering is the whole point.

No scipy dependency: the t distribution is evaluated through a regularized incomplete beta
(continued-fraction) so the engine stays testable in a bare environment, matching the
pattern moa_encoding.py uses for its Beta quantiles.

Correctness is established in tests/test_recon_stats.py without any hardcoded reference
values: exact closed forms at df=1 and df=2, the arcsine identity for I_x(1/2,1/2), an
independent Simpson quadrature of the density that shares no code with the continued
fraction, the normal limit as df grows, and a scipy sweep that runs only when scipy
happens to be installed. A transcribed constant cannot be checked by reading it, and can
certify a bug if it was transcribed to match one.
"""
from __future__ import annotations

import math
from typing import Optional

# ---- distributions --------------------------------------------------------
# Continued-fraction iteration limits. These are numerical-algorithm constants, not
# tunable choices: _EPS is near double precision, _FPMIN guards underflow to zero.
_MAXIT = 300
_EPS = 3.0e-16
_FPMIN = 1.0e-300

# Significance threshold. Mirrors endpoint_label.DEFAULT_ALPHA, which is the source of
# truth for the project; test_recon_stats asserts the two agree so they cannot drift.
DEFAULT_ALPHA = 0.05

# Textbook minimum expected count per cell for the normal approximation to the binomial.
# Below this the z test is unreliable, so results are FLAGGED rather than silently
# swapped for an exact test -- a flagged result can be excluded downstream and audited,
# a silently-swapped method cannot.
SMALL_CELL_MIN_EXPECTED = 5


def _betacf(a: float, b: float, x: float) -> float:
    """Continued fraction for the incomplete beta function (modified Lentz)."""
    qab, qap, qam = a + b, a + 1.0, a - 1.0
    c = 1.0
    d = 1.0 - qab * x / qap
    if abs(d) < _FPMIN:
        d = _FPMIN
    d = 1.0 / d
    h = d
    for m in range(1, _MAXIT + 1):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        if abs(d) < _FPMIN:
            d = _FPMIN
        c = 1.0 + aa / c
        if abs(c) < _FPMIN:
            c = _FPMIN
        d = 1.0 / d
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        if abs(d) < _FPMIN:
            d = _FPMIN
        c = 1.0 + aa / c
        if abs(c) < _FPMIN:
            c = _FPMIN
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < _EPS:
            break
    return h


def betainc(a: float, b: float, x: float) -> float:
    """Regularized incomplete beta I_x(a, b)."""
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    lbeta = (math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b)
             + a * math.log(x) + b * math.log1p(-x))
    front = math.exp(lbeta)
    if x < (a + 1.0) / (a + b + 2.0):
        return front * _betacf(a, b, x) / a
    return 1.0 - math.exp(
        math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b)
        + b * math.log1p(-x) + a * math.log(x)) * _betacf(b, a, 1.0 - x) / b


def t_two_sided_p(t: float, df: float) -> Optional[float]:
    """Two-sided p-value for Student's t. None if df is not usable."""
    if df is None or df <= 0 or not math.isfinite(df):
        return None
    if not math.isfinite(t):
        return None
    x = df / (df + t * t)
    return betainc(0.5 * df, 0.5, x)


def z_two_sided_p(z: float) -> float:
    """Two-sided p-value for the standard normal, via erfc."""
    return math.erfc(abs(z) / math.sqrt(2.0))


# ---- dispersion normalisation --------------------------------------------
def to_sd(dispersion_value: float, dispersion_type: str, n: float) -> Optional[float]:
    """Posted dispersion -> standard deviation.

    AACT records dispersion as SD, SE, or an interval, and mixing them silently is a
    classic way to be wrong by a factor of sqrt(n). Anything not convertible returns None
    so the trial stays unlabelled rather than mislabelled.
    """
    if dispersion_value is None or dispersion_value < 0:
        return None
    d = str(dispersion_type or "").strip().lower()
    if "standard deviation" in d or d == "sd":
        return float(dispersion_value)
    if "standard error" in d or d in ("se", "sem"):
        if not n or n <= 0:
            return None
        return float(dispersion_value) * math.sqrt(n)
    # full-range, inter-quartile range and confidence intervals are not convertible
    # without distributional assumptions this module refuses to make
    return None


# ---- two-sample tests ----------------------------------------------------
def welch_t(mean_a, sd_a, n_a, mean_b, sd_b, n_b) -> dict:
    """Welch's unequal-variance t test. Returns diagnostics alongside the p-value."""
    for v in (mean_a, sd_a, n_a, mean_b, sd_b, n_b):
        if v is None:
            return {"p": None, "reason": "missing input", "method": "welch_t"}
    if n_a < 2 or n_b < 2:
        return {"p": None, "reason": "n < 2 in an arm", "method": "welch_t"}
    va, vb = (sd_a ** 2) / n_a, (sd_b ** 2) / n_b
    denom = va + vb
    if denom <= 0:
        return {"p": None, "reason": "zero variance in both arms", "method": "welch_t"}
    t = (mean_a - mean_b) / math.sqrt(denom)
    df = (denom ** 2) / ((va ** 2) / (n_a - 1) + (vb ** 2) / (n_b - 1))
    return {"p": t_two_sided_p(t, df), "t": t, "df": df, "method": "welch_t",
            "reason": "", "direction": 1 if mean_a > mean_b else -1}


def two_proportion_z(x_a, n_a, x_b, n_b) -> dict:
    """Pooled-variance two-proportion z test.

    Flags small expected cells rather than switching silently to an exact test: a flagged
    result can be excluded downstream, a silently-swapped method cannot be audited.
    """
    for v in (x_a, n_a, x_b, n_b):
        if v is None:
            return {"p": None, "reason": "missing input", "method": "two_proportion_z"}
    if n_a <= 0 or n_b <= 0 or x_a < 0 or x_b < 0 or x_a > n_a or x_b > n_b:
        return {"p": None, "reason": "counts out of range", "method": "two_proportion_z"}
    p_a, p_b = x_a / n_a, x_b / n_b
    p_pool = (x_a + x_b) / (n_a + n_b)
    se = math.sqrt(p_pool * (1 - p_pool) * (1 / n_a + 1 / n_b))
    if se <= 0:
        return {"p": None, "reason": "no variation in either arm",
                "method": "two_proportion_z"}
    z = (p_a - p_b) / se
    small = min(n_a * p_pool, n_a * (1 - p_pool),
                n_b * p_pool, n_b * (1 - p_pool)) < SMALL_CELL_MIN_EXPECTED
    return {"p": z_two_sided_p(z), "z": z, "method": "two_proportion_z",
            "reason": (f"small expected cell (<{SMALL_CELL_MIN_EXPECTED})"
                       if small else ""),
            "small_cell": small, "direction": 1 if p_a > p_b else -1}


# ---- dispatch ------------------------------------------------------------
_CONTINUOUS_PARAMS = ("mean", "least squares mean", "geometric mean", "median")
_COUNT_PARAMS = ("number", "count of participants", "count of units", "participants")


def measurement_kind(param_type) -> Optional[str]:
    """'continuous' | 'count' | None, from an AACT measurement param_type.

    Medians are classed continuous only because AACT gives no better handle; a t test on
    posted medians is an approximation, and the caller records the param_type so that
    subset can be examined separately in validation.
    """
    s = str(param_type or "").strip().lower()
    if not s:
        return None
    if any(k in s for k in _COUNT_PARAMS):
        return "count"
    if any(k in s for k in _CONTINUOUS_PARAMS):
        return "continuous"
    return None


def reconstruct(group_a: dict, group_b: dict, param_type,
                alpha: float = DEFAULT_ALPHA) -> dict:
    """Two posted group summaries -> a reconstructed verdict.

    group dicts carry: value, dispersion_value, dispersion_type, n.
    Returns met/p/method/flags. met is None whenever the inputs do not support a test,
    which is the common case and must stay distinguishable from met=0.
    """
    kind = measurement_kind(param_type)
    if kind is None:
        return {"met": None, "p": None, "method": "", "kind": "",
                "reason": f"param_type not usable: {param_type!r}"}
    if kind == "count":
        res = two_proportion_z(group_a.get("value"), group_a.get("n"),
                               group_b.get("value"), group_b.get("n"))
    else:
        sd_a = to_sd(group_a.get("dispersion_value"), group_a.get("dispersion_type"),
                     group_a.get("n"))
        sd_b = to_sd(group_b.get("dispersion_value"), group_b.get("dispersion_type"),
                     group_b.get("n"))
        res = welch_t(group_a.get("value"), sd_a, group_a.get("n"),
                      group_b.get("value"), sd_b, group_b.get("n"))
    p = res.get("p")
    return {
        "met": None if p is None else int(p <= alpha),
        "p": p,
        "method": res.get("method", ""),
        "kind": kind,
        "direction": res.get("direction"),
        "small_cell": res.get("small_cell", False),
        "reason": res.get("reason", ""),
        "n_total": (group_a.get("n") or 0) + (group_b.get("n") or 0),
    }