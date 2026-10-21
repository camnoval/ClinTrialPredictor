"""Tests for modeling. Plain asserts so tests/_run_stdlib.py works."""
from __future__ import annotations

import math
import random

import numpy as np

from trial_pos.services import modeling as M
from trial_pos.services.trial_features import (
    GROUP_DESIGN, KIND_CATEGORY, KIND_NUMERIC, KIND_TRISTATE, FeatureSpec,
)

NUM = FeatureSpec("x", GROUP_DESIGN, KIND_NUMERIC, (("studies", "phase"),), "")
TRI = FeatureSpec("t", GROUP_DESIGN, KIND_TRISTATE, (("studies", "phase"),), "")
CAT = FeatureSpec("c", GROUP_DESIGN, KIND_CATEGORY, (("studies", "phase"),), "")
SPECS = (NUM, TRI, CAT)


def _synthetic(n, seed):
    """x carries the signal; t and c are noise with unknowns."""
    rng = random.Random(seed)
    rows, y = [], []
    for _ in range(n):
        x = rng.gauss(0, 1)
        rows.append({"x": "" if rng.random() < 0.1 else f"{x:.4f}",
                     "t": rng.choice(["1", "0", ""]), "c": rng.choice(["a", "b", ""])})
        y.append(int(rng.random() < 1 / (1 + math.exp(-2 * x))))
    return rows, np.array(y)


def test_brier_and_skill_follow_their_definitions():
    y = np.array([1, 0, 0, 1])
    rate = y.mean()
    const = np.full(len(y), rate)
    assert math.isclose(M.brier(const, y), rate * (1 - rate))
    assert M.brier_skill(const, const, y) == 0.0
    assert M.brier_skill(y.astype(float), const, y) == 1.0
    assert math.isnan(M.brier_skill(const, y.astype(float), y))      # perfect reference
    assert math.isfinite(M.log_loss(np.array([0.0, 1.0]), np.array([1, 0])))


def test_reliability_partitions_the_trials_and_ece_is_zero_when_calibrated():
    p = np.array([0.25] * 4 + [0.75] * 4)
    y = np.array([1, 0, 0, 0, 1, 1, 1, 0])
    assert sum(k for _a, _b, k, _m, _o in M.reliability(p, y)) == len(p)
    assert M.ece(p, y) < 1e-12


def test_the_paired_bootstrap_is_deterministic_and_brackets_a_mean():
    rng = np.random.default_rng(1)
    a = rng.normal(size=200)
    stat = lambda v: float(np.mean(v))                                 # noqa: E731
    iv = M.paired_bootstrap(stat, (a,), replicates=500)
    assert iv == M.paired_bootstrap(stat, (a,), replicates=500)
    assert iv[0] <= stat(a) <= iv[1]
    for bad in ((), (a, a[:-1])):
        try:
            M.paired_bootstrap(stat, bad or (np.array([]),))
        except ValueError:
            continue
        raise AssertionError("expected ValueError")


def test_the_bar_needs_the_score_and_the_lower_end_above_zero():
    assert M.passes_bar(0.02, (0.001, 0.04)) is True
    assert M.passes_bar(0.02, (-0.001, 0.04)) is False
    assert M.passes_bar(-0.01, (-0.03, 0.01)) is False
    assert M.passes_bar(math.nan, (0.0, 0.0)) is None


def test_the_rate_table_falls_back_to_the_overall_rate():
    t = M.rate_table(["a", "a", "b"], [1, 0, 1])
    got = M.rate_predict(t, ["a", "b", "never_seen"])
    assert got.tolist() == [t.rates["a"], t.rates["b"], t.overall]


def test_encoder_statistics_come_from_the_fitted_rows_only():
    fit_rows = [{"x": "1", "t": "1", "c": "a"}] * M.MIN_CATEGORY_COUNT + \
               [{"x": "", "t": "", "c": "rare"}]
    enc = M.Encoder(SPECS).fit(fit_rows)
    assert enc.levels["c"] == ["a"]                           # 'rare' pooled
    cols = enc.linear_columns()
    X = enc.linear([{"x": "", "t": "", "c": "unseen"}])
    row = dict(zip(cols, X[0].tolist()))
    assert row[f"x{M.UNKNOWN}"] == 1.0 and row[f"t{M.UNKNOWN}"] == 1.0
    assert row[f"c{M.OTHER}"] == 1.0 and row["c=a"] == 0.0
    T, mask = enc.tree([{"x": "", "t": "0", "c": "a"}])
    assert math.isnan(T[0, 0]) and T[0, 1] == 0.0 and mask.tolist() == [False, False, True]
    try:
        M.Encoder(SPECS).linear(fit_rows)
    except RuntimeError:
        return
    raise AssertionError("an unfitted encoder must refuse")


def test_both_families_learn_a_planted_signal_deterministically():
    rows, y = _synthetic(1500, 1)
    test_rows, ty = _synthetic(800, 2)
    base = np.full(len(ty), y.mean())
    for fam in M.FAMILIES:
        p1 = M.predict(M.fit(fam, SPECS, rows, y), test_rows)
        p2 = M.predict(M.fit(fam, SPECS, rows, y), test_rows)
        assert np.array_equal(p1, p2), fam
        assert M.brier_skill(p1, base, ty) > 0, fam


def test_coefficients_are_ranked_and_exist_only_for_the_logistic_family():
    rows, y = _synthetic(600, 3)
    lr = M.fit(M.FAMILY_LOGISTIC, SPECS, rows, y)
    coefs = M.logistic_coefficients(lr)
    assert [abs(b) for _c, b in coefs] == sorted((abs(b) for _c, b in coefs), reverse=True)
    assert coefs[0][0] == "x"                                # the planted signal leads
    try:
        M.logistic_coefficients(M.fit(M.FAMILY_BOOSTING, SPECS, rows, y))
    except ValueError:
        return
    raise AssertionError("expected ValueError")


def test_an_unknown_family_raises():
    rows, y = _synthetic(50, 4)
    try:
        M.fit("forest", SPECS, rows, y)
    except ValueError:
        return
    raise AssertionError("expected ValueError")


def test_the_area_reference_uses_large_cells_and_falls_back_to_the_phase_rate():
    big, small = M.MIN_AREA_CELL, M.MIN_AREA_CELL - 1
    phases = ["P2"] * (big + small) + ["P3"] * big
    codes = [{"C04"}] * big + [{"C14"}] * small + [set()] * big
    y = [1] * big + [0] * small + [1] * (big // 2) + [0] * (big - big // 2)
    t = M.area_table(phases, codes, y)
    got = M.area_predict(t, ["P2", "P2", "P2", "P3"], [{"C04"}, {"C14"}, set(), {"C04"}])
    assert got[0] == t.cells[("P2", "C04")][0]                  # a large cell
    assert got[1] == t.phase.rates["P2"]                        # a small cell falls back
    assert got[2] == t.phase.rates["P2"]                        # no category
    assert got[3] == t.phase.rates["P3"]                        # no such cell
    both = M.area_predict(M.area_table(["P"] * 2 * big, [{"A", "B"}] * big + [{"A"}] * big,
                                       [1] * big + [0] * big), ["P"], [{"A", "B"}])
    tab = M.area_table(["P"] * 2 * big, [{"A", "B"}] * big + [{"A"}] * big,
                       [1] * big + [0] * big)
    assert both[0] == (tab.cells[("P", "A")][0] + tab.cells[("P", "B")][0]) / 2
