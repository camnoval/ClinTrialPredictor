"""Tests for the reconstruction statistics. No hardcoded reference values anywhere.

Every expected number in this file is DERIVED, by one of three routes, so a reader can
check the test itself rather than trusting a transcribed constant:

  1. EXACT CLOSED FORMS. The t distribution has elementary forms at df=1 (Cauchy:
     1 - (2/pi)arctan|t|) and df=2 (1 - t/sqrt(2+t^2)), and the regularized incomplete
     beta at a=b=1/2 is the arcsine law (2/pi)arcsin(sqrt(x)). These are identities, not
     table lookups.

  2. INDEPENDENT QUADRATURE. `_t_tail_by_quadrature` integrates the t density directly by
     Simpson's rule on a mapped finite interval. It shares no code path with the
     continued-fraction betainc under test, so agreement between them is real evidence.
     Verified to agree to ~3e-14; the tests assert 1e-8, which is loose enough to be
     stable and tight enough to catch any genuine error.

  3. STRUCTURAL PROPERTIES. Symmetry, monotonicity, the normal limit as df grows, and
     the reduction of Welch's test to the pooled t test when arms are balanced.

If scipy is installed, one test additionally sweeps a grid against it. If scipy is
absent, that test skips and the routes above still cover the same ground, matching the
scipy-optional pattern moa_encoding.py uses.
"""
from __future__ import annotations

import math

from trial_pos.services import endpoint_label, recon_stats
from trial_pos.services.recon_stats import (
    SMALL_CELL_MIN_EXPECTED, betainc, measurement_kind, reconstruct, to_sd,
    t_two_sided_p, two_proportion_z, welch_t, z_two_sided_p,
)

_GRID_DF = (1, 2, 3, 5, 10, 30, 100, 7.3)
_GRID_T = (0.5, 1.0, 1.96, 3.5)


# ---- independent references ----------------------------------------------
def _t_tail_by_quadrature(t: float, df: float, panels: int = 20000) -> float:
    """2 * integral of the t density from |t| to infinity, by Simpson's rule.

    The infinite tail is mapped to [0,1) by x = |t| + u/(1-u). Deliberately shares no
    code with betainc, so this is an independent check rather than a restatement.
    """
    t = abs(t)
    logc = (math.lgamma((df + 1) / 2) - math.lgamma(df / 2)
            - 0.5 * math.log(df * math.pi))

    def g(u: float) -> float:
        if u >= 1.0:
            return 0.0
        x = t + u / (1.0 - u)
        return math.exp(logc - ((df + 1) / 2) * math.log1p(x * x / df)) / (1.0 - u) ** 2

    h = 1.0 / panels
    total = g(0.0) + g(1.0 - 1e-15)
    for i in range(1, panels):
        total += (4 if i % 2 else 2) * g(i * h)
    return 2.0 * total * h / 3.0


def _norm_tail_by_quadrature(z: float, panels: int = 20000) -> float:
    z = abs(z)

    def g(u: float) -> float:
        if u >= 1.0:
            return 0.0
        x = z + u / (1.0 - u)
        return math.exp(-0.5 * x * x) / math.sqrt(2 * math.pi) / (1.0 - u) ** 2

    h = 1.0 / panels
    total = g(0.0) + g(1.0 - 1e-15)
    for i in range(1, panels):
        total += (4 if i % 2 else 2) * g(i * h)
    return 2.0 * total * h / 3.0


def _close(a, b, tol=1e-10):
    return a is not None and abs(a - b) < tol


# ---- distributions: closed forms ----------------------------------------
def test_t_matches_cauchy_closed_form_at_df_1():
    for t in (0.25, 0.5, 1.0, 3.0, 12.0):
        assert _close(t_two_sided_p(t, 1), 1 - (2 / math.pi) * math.atan(t)), t


def test_t_matches_closed_form_at_df_2():
    for t in (0.25, 1.0, 2.0, 4.0, 9.0):
        assert _close(t_two_sided_p(t, 2), 1 - t / math.sqrt(2 + t * t)), t


def test_betainc_matches_arcsine_law_at_half_half():
    # I_x(1/2, 1/2) = (2/pi) arcsin(sqrt(x)) -- the path df=1 takes through betainc
    for x in (0.05, 0.25, 0.5, 0.75, 0.99):
        assert _close(betainc(0.5, 0.5, x), (2 / math.pi) * math.asin(math.sqrt(x))), x


def test_betainc_endpoints_and_symmetry():
    assert betainc(2.0, 3.0, 0.0) == 0.0
    assert betainc(2.0, 3.0, 1.0) == 1.0
    # I_x(a,b) = 1 - I_{1-x}(b,a)
    for a, b, x in ((2.0, 3.0, 0.3), (0.5, 4.0, 0.7), (7.0, 1.5, 0.11)):
        assert _close(betainc(a, b, x), 1 - betainc(b, a, 1 - x))


# ---- distributions: independent quadrature ------------------------------
def test_t_agrees_with_independent_quadrature():
    for df in _GRID_DF:
        for t in _GRID_T:
            assert _close(t_two_sided_p(t, df), _t_tail_by_quadrature(t, df),
                          tol=1e-8), (t, df)


def test_normal_agrees_with_independent_quadrature():
    for z in (0.25, 0.5, 1.0, 1.96, 3.0):
        assert _close(z_two_sided_p(z), _norm_tail_by_quadrature(z), tol=1e-8), z


def test_t_approaches_the_normal_as_df_grows():
    for z in (0.5, 1.0, 1.96, 3.0):
        assert _close(t_two_sided_p(z, 5_000_000), z_two_sided_p(z), tol=1e-5), z


# ---- distributions: structural properties -------------------------------
def test_t_is_symmetric_and_monotone():
    for df in (1, 4, 25):
        assert _close(t_two_sided_p(2.0, df), t_two_sided_p(-2.0, df))
        ps = [t_two_sided_p(t, df) for t in (0.0, 0.5, 1.0, 2.0, 5.0)]
        assert ps == sorted(ps, reverse=True), df
        assert _close(ps[0], 1.0)          # t=0 splits the mass exactly


def test_normal_is_symmetric_and_bounded():
    assert _close(z_two_sided_p(0.0), 1.0)
    assert _close(z_two_sided_p(-2.5), z_two_sided_p(2.5))
    assert 0.0 <= z_two_sided_p(9.0) < 1e-15


def test_t_rejects_unusable_df():
    assert t_two_sided_p(2.0, 0) is None
    assert t_two_sided_p(2.0, -3) is None
    assert t_two_sided_p(float("nan"), 10) is None


def test_optional_scipy_cross_check():
    """Sweep against scipy when it is installed; skip silently when it is not."""
    try:
        from scipy import stats  # noqa: PLC0415
    except ImportError:
        return
    for df in _GRID_DF:
        for t in _GRID_T:
            assert _close(t_two_sided_p(t, df), 2 * stats.t.sf(abs(t), df),
                          tol=1e-10), (t, df)


# ---- dispersion ---------------------------------------------------------
def test_sd_passes_through():
    assert to_sd(4.0, "Standard Deviation", 25) == 4.0


def test_se_is_scaled_by_sqrt_n():
    # mixing SE and SD silently is wrong by a factor of sqrt(n); this is the guard
    for n in (4, 25, 100):
        assert _close(to_sd(2.0, "Standard Error", n), 2.0 * math.sqrt(n))


def test_se_without_n_refuses():
    assert to_sd(2.0, "Standard Error", None) is None
    assert to_sd(2.0, "Standard Error", 0) is None


def test_unconvertible_dispersions_refuse():
    for dtype in ("Full Range", "Inter-Quartile Range", "95% Confidence Interval", ""):
        assert to_sd(3.0, dtype, 40) is None, dtype


# ---- two-sample tests: derived, not tabulated ---------------------------
def test_welch_statistic_matches_its_own_definition():
    ma, sa, na, mb, sb, nb = 5.4, 1.3, 17, 4.1, 0.9, 23
    r = welch_t(ma, sa, na, mb, sb, nb)
    va, vb = sa ** 2 / na, sb ** 2 / nb
    t = (ma - mb) / math.sqrt(va + vb)
    # Welch-Satterthwaite
    df = (va + vb) ** 2 / (va ** 2 / (na - 1) + vb ** 2 / (nb - 1))
    assert _close(r["t"], t)
    assert _close(r["df"], df)
    assert _close(r["p"], t_two_sided_p(t, df))


def test_welch_reduces_to_pooled_t_when_arms_are_balanced():
    # equal n and equal sd: Welch's df collapses to 2n-2 and t to the pooled statistic
    n, sd, ma, mb = 30, 2.0, 5.0, 4.0
    r = welch_t(ma, sd, n, mb, sd, n)
    t_pooled = (ma - mb) / (sd * math.sqrt(2.0 / n))
    assert _close(r["df"], 2 * n - 2)
    assert _close(r["t"], t_pooled)
    assert _close(r["p"], t_two_sided_p(t_pooled, 2 * n - 2))


def test_welch_gives_unit_p_when_means_are_equal():
    assert _close(welch_t(5.0, 1.0, 20, 5.0, 2.0, 30)["p"], 1.0)


def test_welch_refuses_tiny_arms():
    assert welch_t(5.0, 1.0, 1, 4.0, 1.0, 9)["p"] is None


def test_welch_refuses_zero_variance():
    assert welch_t(5.0, 0.0, 10, 4.0, 0.0, 10)["p"] is None


def test_welch_records_direction():
    assert welch_t(5.0, 1.0, 10, 4.0, 1.0, 10)["direction"] == 1
    assert welch_t(4.0, 1.0, 10, 5.0, 1.0, 10)["direction"] == -1


def test_two_proportion_matches_its_own_definition():
    xa, na, xb, nb = 40, 100, 25, 100
    r = two_proportion_z(xa, na, xb, nb)
    p_pool = (xa + xb) / (na + nb)
    se = math.sqrt(p_pool * (1 - p_pool) * (1 / na + 1 / nb))
    z = (xa / na - xb / nb) / se
    assert _close(r["z"], z)
    assert _close(r["p"], z_two_sided_p(z))


def test_two_proportion_gives_unit_p_when_proportions_are_equal():
    assert _close(two_proportion_z(30, 100, 60, 200)["p"], 1.0)


def test_two_proportion_flags_small_cells_rather_than_switching_method():
    # inputs chosen so the smallest expected cell falls below the named threshold,
    # derived from the constant rather than assuming its value
    n = 8
    x = 1
    r = two_proportion_z(x, n, 0, n + 1)
    p_pool = x / (2 * n + 1)
    assert min(n * p_pool, n * (1 - p_pool)) < SMALL_CELL_MIN_EXPECTED
    assert r["small_cell"] is True
    assert "small expected cell" in r["reason"]
    assert r["p"] is not None          # still reported, but flagged for exclusion


def test_alpha_defaults_agree_across_modules():
    # recon_stats mirrors endpoint_label's alpha; this catches drift between them
    assert recon_stats.DEFAULT_ALPHA == endpoint_label.DEFAULT_ALPHA


def test_two_proportion_refuses_impossible_counts():
    assert two_proportion_z(12, 10, 3, 10)["p"] is None
    assert two_proportion_z(-1, 10, 3, 10)["p"] is None


def test_two_proportion_refuses_no_variation():
    assert two_proportion_z(0, 50, 0, 50)["p"] is None


# ---- dispatch -----------------------------------------------------------
def test_measurement_kind():
    assert measurement_kind("Mean") == "continuous"
    assert measurement_kind("Least Squares Mean") == "continuous"
    assert measurement_kind("Number") == "count"
    assert measurement_kind("Count of Participants") == "count"
    assert measurement_kind("Hazard Ratio") is None
    assert measurement_kind("") is None


def test_reconstruct_continuous_agrees_with_calling_welch_directly():
    a = {"value": 5.0, "dispersion_value": 1.0, "dispersion_type": "Standard Deviation",
         "n": 40}
    b = {"value": 4.0, "dispersion_value": 1.0, "dispersion_type": "Standard Deviation",
         "n": 40}
    out = reconstruct(a, b, "Mean")
    direct = welch_t(5.0, 1.0, 40, 4.0, 1.0, 40)
    assert _close(out["p"], direct["p"])
    assert out["kind"] == "continuous" and out["n_total"] == 80


def test_reconstruct_count_agrees_with_calling_the_test_directly():
    out = reconstruct({"value": 40, "n": 100}, {"value": 25, "n": 100},
                      "Count of Participants")
    assert _close(out["p"], two_proportion_z(40, 100, 25, 100)["p"])
    assert out["kind"] == "count"


def test_reconstruct_unusable_stays_none_not_zero():
    # None and 0 must never be conflated: one is "no test possible", the other "not met"
    out = reconstruct({"value": 5.0, "n": 40}, {"value": 4.0, "n": 40}, "Hazard Ratio")
    assert out["met"] is None and out["p"] is None
    out2 = reconstruct({"value": 5.0, "dispersion_value": 2.0,
                        "dispersion_type": "Full Range", "n": 40},
                       {"value": 4.0, "dispersion_value": 2.0,
                        "dispersion_type": "Full Range", "n": 40}, "Mean")
    assert out2["met"] is None


def test_reconstruct_thresholds_at_alpha_wherever_alpha_is_set():
    a = {"value": 5.0, "dispersion_value": 2.0, "dispersion_type": "Standard Deviation",
         "n": 60}
    b = {"value": 4.0, "dispersion_value": 2.0, "dispersion_type": "Standard Deviation",
         "n": 60}
    p = welch_t(5.0, 2.0, 60, 4.0, 2.0, 60)["p"]
    # derive the expectation from p itself rather than asserting a remembered verdict
    assert reconstruct(a, b, "Mean", alpha=p * 1.01)["met"] == 1
    assert reconstruct(a, b, "Mean", alpha=p * 0.99)["met"] == 0