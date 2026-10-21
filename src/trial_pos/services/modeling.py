"""Fitting and scoring for every target (D-55, D-60, rev 10 section 12.26).

EVERYTHING IS FITTED ON THE TRAINING SIDE ONLY: medians, scales, category levels, the
phase-rate reference and the models. The test side is scored once.

TWO FAMILIES, as rev 10 decided: logistic regression with L2 (main effects only) and shallow
gradient boosting. The family is chosen BEFORE the test side is touched, on a validation
slice of the training side (its latest outcomes); the test side then scores the chosen one.

THE REFERENCE (D-55) is the outcome rate by phase on the training side, with the overall
training rate for a phase training never saw.

THE BAR (D-55): Brier skill score against that reference, with a paired-bootstrap interval
over the test trials; the score must be above 0 and so must the interval's lower end.

Values arrive as trial_features.csv strings: '' unknown, '1'/'0' tri-state, numbers,
category text. Unknown is never imputed silently: every numeric gets a missing indicator,
every tri-state an unknown column, every category an unknown level.
"""
from __future__ import annotations

import math
from typing import Iterable, NamedTuple, Optional, Sequence

import numpy as np
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score

from trial_pos.services.trial_features import KIND_CATEGORY, KIND_NUMERIC, KIND_TRISTATE

FAMILY_LOGISTIC = "logistic_l2"
FAMILY_BOOSTING = "shallow_boosting"
FAMILIES = (FAMILY_LOGISTIC, FAMILY_BOOSTING)

# Hyperparameters are fixed in advance, not tuned on any held-out data (rev 10 section 12.26).
# Logistic: inputs are standardised, so C = 1 is the conventional unit penalty.
LOGISTIC_C = 1.0
LOGISTIC_MAX_ITER = 5000
# Boosting kept shallow for about 30 to 38 training events per parameter (D-60): depth 3,
# a slow rate, a fixed number of rounds, leaves of at least 50 trials, an L2 penalty.
BOOST_MAX_DEPTH = 3
BOOST_LEARNING_RATE = 0.05
BOOST_MAX_ITER = 300
BOOST_MIN_LEAF = 50
BOOST_L2 = 1.0
RANDOM_STATE = 0                 # no randomness is used (no subsampling, no early stopping)
# A category level seen on fewer training trials than this pools into 'other'.
MIN_CATEGORY_COUNT = 20
# The validation slice for choosing the family: training trials whose outcome was settled
# in the last VALIDATION_YEARS before the boundary.
VALIDATION_YEARS = 2
BOOTSTRAP_REPLICATES = 2000
BOOTSTRAP_SEED = 20261001
CI_LEVEL = 0.95
ECE_BINS = 10                    # equal-width probability bins
PROB_CLIP = 1e-6                 # keeps log loss finite at 0 or 1

OTHER, UNKNOWN = "__other__", "__unknown__"


def _num(raw) -> float:
    if raw is None or raw == "":
        return math.nan
    return float(raw)


def signed_log(x: float) -> float:
    return math.copysign(math.log1p(abs(x)), x) if not math.isnan(x) else math.nan


class Encoder:
    """Turns feature rows into matrices, with every statistic taken from the rows it is
    fitted on. `specs`: FeatureSpec-like objects with .name and .kind."""

    def __init__(self, specs: Sequence):
        self.specs = tuple(specs)
        for s in self.specs:
            if s.kind not in (KIND_NUMERIC, KIND_TRISTATE, KIND_CATEGORY):
                raise ValueError(f"{s.name}: unknown kind {s.kind!r}")
        self._fitted = False

    def fit(self, rows: Sequence[dict]) -> "Encoder":
        if not rows:
            raise ValueError("cannot fit an encoder on no rows")
        self.median, self.mean, self.scale, self.levels = {}, {}, {}, {}
        for s in self.specs:
            if s.kind == KIND_NUMERIC:
                vals = np.array([signed_log(_num(r[s.name])) for r in rows])
                ok = vals[~np.isnan(vals)]
                med = float(np.median(ok)) if ok.size else 0.0
                filled = np.where(np.isnan(vals), med, vals)
                sd = float(filled.std())
                self.median[s.name], self.mean[s.name] = med, float(filled.mean())
                self.scale[s.name] = sd if sd > 0 else 1.0
            elif s.kind == KIND_CATEGORY:
                counts = {}
                for r in rows:
                    v = r[s.name]
                    if v != "":
                        counts[v] = counts.get(v, 0) + 1
                self.levels[s.name] = sorted(v for v, n in counts.items()
                                             if n >= MIN_CATEGORY_COUNT)
        self._fitted = True
        return self

    def _need(self):
        if not self._fitted:
            raise RuntimeError("fit the encoder first")

    def linear_columns(self) -> list:
        self._need()
        out = []
        for s in self.specs:
            if s.kind == KIND_NUMERIC:
                out += [s.name, f"{s.name}{UNKNOWN}"]
            elif s.kind == KIND_TRISTATE:
                out += [f"{s.name}=1", f"{s.name}{UNKNOWN}"]
            else:
                out += [f"{s.name}={v}" for v in self.levels[s.name]]
                out += [f"{s.name}{OTHER}", f"{s.name}{UNKNOWN}"]
        return out

    def linear(self, rows: Sequence[dict]) -> np.ndarray:
        """Standardised signed-log numerics with missing indicators; tri-states as
        (true, unknown); categories one-hot with 'other' and 'unknown' levels."""
        self._need()
        cols = []
        for s in self.specs:
            if s.kind == KIND_NUMERIC:
                v = np.array([signed_log(_num(r[s.name])) for r in rows])
                miss = np.isnan(v)
                v = np.where(miss, self.median[s.name], v)
                cols += [(v - self.mean[s.name]) / self.scale[s.name], miss.astype(float)]
            elif s.kind == KIND_TRISTATE:
                raw = [r[s.name] for r in rows]
                cols += [np.array([x == "1" for x in raw], float),
                         np.array([x == "" for x in raw], float)]
            else:
                raw = [r[s.name] for r in rows]
                levels = self.levels[s.name]
                known = set(levels)
                for lv in levels:
                    cols.append(np.array([x == lv for x in raw], float))
                cols.append(np.array([x != "" and x not in known for x in raw], float))
                cols.append(np.array([x == "" for x in raw], float))
        return np.column_stack(cols) if cols else np.zeros((len(rows), 0))

    def tree(self, rows: Sequence[dict]) -> tuple:
        """(matrix with NaN for unknown, categorical mask). Categories are integer codes over
        the fitted levels, 'other' the next code."""
        self._need()
        cols, mask = [], []
        for s in self.specs:
            raw = [r[s.name] for r in rows]
            if s.kind == KIND_NUMERIC:
                cols.append(np.array([_num(x) for x in raw]))
                mask.append(False)
            elif s.kind == KIND_TRISTATE:
                cols.append(np.array([math.nan if x == "" else float(x == "1") for x in raw]))
                mask.append(False)
            else:
                code = {v: i for i, v in enumerate(self.levels[s.name])}
                other = len(code)
                cols.append(np.array([math.nan if x == "" else code.get(x, other)
                                      for x in raw], float))
                mask.append(True)
        return np.column_stack(cols), np.array(mask, bool)


class Fitted(NamedTuple):
    family: str
    model: object
    encoder: Encoder


def fit(family: str, specs: Sequence, rows: Sequence[dict], y: Sequence[int]) -> Fitted:
    enc = Encoder(specs).fit(rows)
    y = np.asarray(y, int)
    if family == FAMILY_LOGISTIC:
        m = LogisticRegression(C=LOGISTIC_C, max_iter=LOGISTIC_MAX_ITER)
        m.fit(enc.linear(rows), y)
    elif family == FAMILY_BOOSTING:
        X, mask = enc.tree(rows)
        m = HistGradientBoostingClassifier(
            max_depth=BOOST_MAX_DEPTH, learning_rate=BOOST_LEARNING_RATE,
            max_iter=BOOST_MAX_ITER, min_samples_leaf=BOOST_MIN_LEAF,
            l2_regularization=BOOST_L2, categorical_features=mask, early_stopping=False,
            random_state=RANDOM_STATE)
        m.fit(X, y)
    else:
        raise ValueError(f"family {family!r} not in {FAMILIES}")
    return Fitted(family, m, enc)


def predict(f: Fitted, rows: Sequence[dict]) -> np.ndarray:
    if f.family == FAMILY_LOGISTIC:
        return f.model.predict_proba(f.encoder.linear(rows))[:, 1]
    X, _mask = f.encoder.tree(rows)
    return f.model.predict_proba(X)[:, 1]


def logistic_coefficients(f: Fitted) -> list:
    """(column, coefficient) for a logistic fit, largest magnitude first (ties by name)."""
    if f.family != FAMILY_LOGISTIC:
        raise ValueError("coefficients exist only for the logistic family")
    pairs = zip(f.encoder.linear_columns(), f.model.coef_[0].tolist())
    return sorted(pairs, key=lambda p: (-abs(p[1]), p[0]))


# ---- the phase-rate reference (D-55) -------------------------------------------------------
class RateTable(NamedTuple):
    rates: dict
    counts: dict
    overall: float


def rate_table(groups: Iterable[str], y: Iterable[int]) -> RateTable:
    n, k = {}, {}
    total = pos = 0
    for g, v in zip(groups, y):
        n[g] = n.get(g, 0) + 1
        k[g] = k.get(g, 0) + int(v)
        total += 1
        pos += int(v)
    if not total:
        raise ValueError("no training rows for the reference")
    return RateTable({g: k[g] / n[g] for g in n}, n, pos / total)


def rate_predict(t: RateTable, groups: Iterable[str]) -> np.ndarray:
    return np.array([t.rates.get(g, t.overall) for g in groups])


# ---- D-69: the phase x disease-area reference -------------------------------------------------
# A (phase, MeSH C category) cell is used only with at least this many training trials: below
# it the standard error of a rate near 0.6 exceeds 0.09, more than the gaps between phases.
MIN_AREA_CELL = 30


class AreaTable(NamedTuple):
    cells: dict                   # (phase, code) -> (rate, n)
    phase: RateTable


def area_table(phases: Sequence[str], code_sets: Sequence, y: Sequence[int]) -> AreaTable:
    """Training-side rates by (phase, category); a trial counts once in each of its
    categories. The phase table is the fallback."""
    n, k = {}, {}
    for ph, codes, v in zip(phases, code_sets, y):
        for c in sorted(codes):
            n[(ph, c)] = n.get((ph, c), 0) + 1
            k[(ph, c)] = k.get((ph, c), 0) + int(v)
    return AreaTable({cell: (k[cell] / n[cell], n[cell]) for cell in n},
                     rate_table(phases, y))


def area_predict(t: AreaTable, phases: Sequence[str], code_sets: Sequence) -> np.ndarray:
    """Mean of the trial's usable (phase, category) cell rates; the phase rate when it has
    none (no category, or only cells under MIN_AREA_CELL)."""
    out = []
    for ph, codes in zip(phases, code_sets):
        rates = [t.cells[(ph, c)][0] for c in sorted(codes)
                 if (ph, c) in t.cells and t.cells[(ph, c)][1] >= MIN_AREA_CELL]
        out.append(float(np.mean(rates)) if rates
                   else t.phase.rates.get(ph, t.phase.overall))
    return np.array(out)


# ---- metrics ------------------------------------------------------------------------------------
def brier(p, y) -> float:
    p, y = np.asarray(p, float), np.asarray(y, float)
    return float(np.mean((p - y) ** 2))


def brier_skill(p, ref, y) -> float:
    b_ref = brier(ref, y)
    return math.nan if b_ref == 0 else 1.0 - brier(p, y) / b_ref


def log_loss(p, y) -> float:
    p = np.clip(np.asarray(p, float), PROB_CLIP, 1 - PROB_CLIP)
    y = np.asarray(y, float)
    return float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p)))


def auroc(p, y) -> float:
    y = np.asarray(y, int)
    return math.nan if len(set(y.tolist())) < 2 else float(roc_auc_score(y, p))


def reliability(p, y, bins: int = ECE_BINS) -> list:
    """(lower, upper, n, mean predicted, observed rate) per equal-width bin with trials."""
    p, y = np.asarray(p, float), np.asarray(y, float)
    edges = np.linspace(0.0, 1.0, bins + 1)
    idx = np.clip(np.digitize(p, edges[1:-1], right=False), 0, bins - 1)
    out = []
    for b in range(bins):
        m = idx == b
        if m.any():
            out.append((float(edges[b]), float(edges[b + 1]), int(m.sum()),
                        float(p[m].mean()), float(y[m].mean())))
    return out


def ece(p, y, bins: int = ECE_BINS) -> float:
    n = len(p)
    return float(sum(k / n * abs(mp - oy) for _lo, _hi, k, mp, oy in reliability(p, y, bins)))


def paired_bootstrap(stat, arrays: Sequence, replicates: int = BOOTSTRAP_REPLICATES,
                     seed: int = BOOTSTRAP_SEED, level: float = CI_LEVEL) -> tuple:
    """(lower, upper) percentile interval of stat(*arrays) over resamples of the SAME trial
    indices for every array (so model and reference are compared on identical trials)."""
    arrays = [np.asarray(a) for a in arrays]
    n = len(arrays[0])
    if not n or any(len(a) != n for a in arrays):
        raise ValueError("arrays must be non-empty and of one length")
    rng = np.random.default_rng(seed)
    vals = []
    for _ in range(replicates):
        i = rng.integers(0, n, n)
        v = stat(*(a[i] for a in arrays))
        if not math.isnan(v):
            vals.append(v)
    if not vals:
        return (math.nan, math.nan)
    tail = (1 - level) / 2
    return (float(np.quantile(vals, tail)), float(np.quantile(vals, 1 - tail)))


def passes_bar(score: float, interval: tuple) -> Optional[bool]:
    """D-55: the skill score and its interval's lower end are both above 0. None if
    undefined."""
    if math.isnan(score) or math.isnan(interval[0]):
        return None
    return score > 0 and interval[0] > 0
