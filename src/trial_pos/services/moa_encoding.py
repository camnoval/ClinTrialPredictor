"""DSAI crown-jewel MoA target encoding -- pure engine (offline-testable).

This is the model-stage build of the feature `audit_moa_prior.py` validated (AUROC 0.674,
77% coverage). The audit approximated the time-respecting leave-one-out with TOP's own
train/valid/test split; this engine implements the REAL DSAI rule:

  for a drug-indication U with phase-2-end year Y_U and MoA-class set C_U, and for each
  class c in C_U, count the approvals/total of the OTHER drug-indications V with c in C_V
  and phaseendyear_V <= Y_U (== included; DSAI's `phaseendyear.x >= phaseendyear.y`),
  EXCLUDING U's own outcome (R7 leave-one-out). Beta(1/3, 2.7)-smooth per class, then
  average the smoothed rate over the drug's classes; sum the raw counts over the classes.

Granularity:
  any -> class_approvals / class_counts / meancll50 / meanclu50   (needs only MoA + year)
  dt  -> dtclass_* / dtmeancl*50   (restrict history to units sharing the index disease-type)
  ta  -> taclass_* / tameancl*50   (restrict history to units sharing the index TA)
dt/ta require a per-unit (dt_key, ta_key); they degrade to missing when the key is absent,
so this engine runs today at `any` granularity and gains dt/ta once the disease/TA key exists.

Leakage is the dominant risk (R7). The engine exposes three MODES so the driver can run the
mandatory check -- the two wrong ways MUST inflate AUROC vs the safe way:
  safe        -- year cutoff + own-outcome mask                     (the real feature)
  leak_future -- drop the year cutoff (count history from any year, incl. the future)
  leak_own    -- keep the cutoff but DO NOT mask the index's own outcome

Constants and smoothing match audit_moa_prior.py exactly (PRIOR_A=1/3, PRIOR_B=2.7).
"""
from __future__ import annotations

import bisect
import math
from dataclasses import dataclass, field
from typing import Iterable, Optional

# DSAI smoothing prior: Beta(1/3, 2.7) -> prior mean ~0.11 (a sensible low approval base).
PRIOR_A, PRIOR_B = 1.0 / 3.0, 2.7

SAFE, LEAK_FUTURE, LEAK_OWN, LEAK_BOTH = "safe", "leak_future", "leak_own", "leak_both"
# leak_both == audit_moa_prior.py's check: the prior built WITH holdout outcomes (no year
# cutoff AND own not masked). It is the strongest, always-inflating wrong way and is the
# hard R7 gate. leak_own / leak_future decompose WHICH mechanism leaks; future-of-others
# only inflates under within-class temporal autocorrelation, so it is diagnostic, not a gate.


# ---- pure scalar helpers (no third-party deps) -----------------------------
def smoothed_logit(approvals: float, counts: float) -> float:
    """Beta(1/3,2.7) posterior-mean approval rate on the logit scale (matches audit)."""
    a = PRIOR_A + approvals
    b = PRIOR_B + (counts - approvals)
    rate = a / (a + b)
    return math.log(rate) - math.log(1.0 - rate)


def sigmoid(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-x))


def beta_quantiles(approvals: float, counts: float, qs=(0.25, 0.75)):
    """25th/75th pctl of Beta(1/3+ap, 2.7+(cnt-ap)) -> (meancll50, meanclu50) inputs.

    scipy-guarded so the core count/LOO/mask logic stays testable in a bare env; returns
    (nan, nan) if scipy is unavailable (the driver reports that and emits NaN columns).
    """
    try:
        from scipy.stats import beta  # local import; heavy/optional
    except Exception:
        return tuple(float("nan") for _ in qs)
    a = PRIOR_A + approvals
    b = PRIOR_B + (counts - approvals)
    return tuple(float(beta.ppf(q, a, b)) for q in qs)


def auroc(y, p) -> float:
    """Mann-Whitney AUROC (pure; ties 0.5). Matches audit_moa_prior.auroc."""
    pos = [pi for yi, pi in zip(y, p) if yi == 1]
    neg = [pi for yi, pi in zip(y, p) if yi == 0]
    if not pos or not neg:
        return float("nan")
    wins = 0.0
    for pp in pos:
        for nn in neg:
            wins += 1.0 if pp > nn else 0.5 if pp == nn else 0.0
    return wins / (len(pos) * len(neg))


def logloss(y, p) -> float:
    eps = 1e-6
    s = 0.0
    for yi, pi in zip(y, p):
        pi = min(1 - eps, max(eps, pi))
        s += -(yi * math.log(pi) + (1 - yi) * math.log(1 - pi))
    return s / len(y)


# ---- unit record + history structures --------------------------------------
@dataclass(frozen=True, slots=True)
class UnitRow:
    unit_key: str
    year: Optional[int]          # phaseendyear of the drug-indication (None if unknown)
    label: int                   # 0/1 drug-indication approval
    classes: frozenset           # MoA class keys (target_chembl_id or moa:: string)
    drug: str = ""               # drug/regimen id (for drug-level LOO mask; '' -> unit mask)
    dt: frozenset = field(default_factory=frozenset)   # disease-type keys (multi-hot; empty=unassigned)
    ta: frozenset = field(default_factory=frozenset)   # therapeutic-area keys (multi-hot)


@dataclass(frozen=True, slots=True)
class _Member:
    year: int
    label: int
    drug: str
    unit_key: str
    dt: frozenset
    ta: frozenset


class _Group:
    """Time-sorted members of one class, for O(log n) `any`-granularity queries."""
    __slots__ = ("years", "cum_ap", "total", "total_ap")

    def __init__(self, members: list[tuple[int, int]]):
        members.sort(key=lambda m: m[0])            # by year
        self.years = [m[0] for m in members]
        cum = [0]
        for _, lab in members:
            cum.append(cum[-1] + lab)
        self.cum_ap = cum
        self.total = len(members)
        self.total_ap = cum[-1]

    def le(self, year: int) -> tuple[int, int]:
        """(#members, #approvals) with member-year <= year (ties included)."""
        k = bisect.bisect_right(self.years, year)
        return k, self.cum_ap[k]


class _ClassHist:
    """One class's members: the full time-sorted group + per-drug sub-groups, so the LOO can
    remove either just the index UNIT or the index DRUG's whole contribution (all its units)."""
    __slots__ = ("all", "by_drug")

    def __init__(self, members: list[tuple[int, int, str]]):
        self.all = _Group([(y, l) for y, l, _ in members])
        drugs: dict[str, list] = {}
        for y, l, d in members:
            drugs.setdefault(d, []).append((y, l))
        self.by_drug = {d: _Group(m) for d, m in drugs.items()}


class History:
    """Per-class history, built ONLY from year+label-known units.

    `any` granularity uses the fast bisect group (validated against the audit). `dt`/`ta` are
    multi-hot: each member carries its full dt/ta SET, and a history unit counts toward an
    index's dt/ta prior iff it SHARES >=1 tag with the index (each unit counted once, even
    when tags overlap on two values). Those scans are linear over the class's member list.
    A unit with unknown year is excluded from history and cannot be scored under `safe`.
    """
    def __init__(self, units: Iterable[UnitRow]):
        raw_any: dict[str, list] = {}
        self._members: dict[str, list] = {}         # class -> [_Member] for dt/ta scans
        self.n_hist_units = 0
        for u in units:
            if u.year is None or u.label not in (0, 1):
                continue
            self.n_hist_units += 1
            drug = u.drug or u.unit_key
            mem = _Member(u.year, u.label, drug, u.unit_key, u.dt, u.ta)
            for c in u.classes:
                raw_any.setdefault(c, []).append((u.year, u.label, drug))
                self._members.setdefault(c, []).append(mem)
        self._any = {c: _ClassHist(m) for c, m in raw_any.items()}


@dataclass(slots=True)
class Encoded:
    covered: bool = False
    n_classes_hist: int = 0
    ap_sum: float = 0.0
    cnt_sum: float = 0.0
    mean_logit: Optional[float] = None   # avg smoothed-logit over classes-with-history
    cll50: float = float("nan")          # avg Beta 25th pctl
    clu50: float = float("nan")          # avg Beta 75th pctl
    per_class: list = field(default_factory=list)  # [(ap_c, cnt_c)] for classes with history


def encode(u: UnitRow, hist: History, gran: str = "any", mode: str = SAFE,
           own_mask: str = "drug", include_priorless: bool = False) -> Encoded:
    """Encode one unit at one granularity/mode.

    own_mask: 'drug' removes the index DRUG's whole contribution (all its units sharing the
    regimen) -> the faithful "approval rate of OTHER drugs sharing that MoA", the
    generalization-honest default. 'unit' removes only the index drug-indication row (the
    literal plan wording), which lets same-drug sibling indications leak in as recognition (R8).
    include_priorless=False matches the audit: only classes with >=1 historical member count.
    """
    # dt/ta need >=1 tag on the index; missing -> not covered at that granularity
    if gran in ("dt", "ta") and not (u.dt if gran == "dt" else u.ta):
        return Encoded()
    # safe/leak_own use the year cutoff -> need a year
    if mode in (SAFE, LEAK_OWN) and u.year is None:
        return Encoded()

    drug_id = u.drug or u.unit_key
    logits, cll, clu, per_class = [], [], [], []
    ap_sum = cnt_sum = 0.0
    for c in u.classes:
        if gran == "any":
            cnt, ap = _count_any(hist._any.get(c), u, drug_id, mode, own_mask)
        else:
            cnt, ap = _count_overlap(hist._members.get(c), u, drug_id, mode, own_mask, gran)
        if cnt <= 0:
            continue
        ap = max(0.0, min(float(ap), float(cnt)))
        ap_sum += ap
        cnt_sum += cnt
        logits.append(smoothed_logit(ap, cnt))
        lo, hi = beta_quantiles(ap, cnt)
        cll.append(lo)
        clu.append(hi)
        per_class.append((ap, cnt))
    if not logits and not include_priorless:
        return Encoded(covered=(mode in (LEAK_FUTURE, LEAK_BOTH)) and bool(u.classes))
    e = Encoded(covered=bool(u.classes), n_classes_hist=len(logits),
                ap_sum=ap_sum, cnt_sum=cnt_sum, per_class=per_class)
    if logits:
        e.mean_logit = sum(logits) / len(logits)
        e.cll50 = sum(cll) / len(cll)
        e.clu50 = sum(clu) / len(clu)
    return e


def _count_any(ch, u, drug_id, mode, own_mask):
    """(cnt, ap) for one class at `any` granularity via the fast bisect group."""
    if ch is None:
        return 0, 0
    own = ch.by_drug.get(drug_id) if own_mask == "drug" else None
    if mode in (LEAK_OWN, LEAK_BOTH):
        own_le = own_tot = (0, 0)                 # own NOT masked
    elif own is not None:
        own_le, own_tot = own.le(u.year), (own.total, own.total_ap)
    else:                                          # unit mask (or no drug id): drop one row
        own_le = own_tot = (1, u.label)
    if mode == LEAK_BOTH:
        return ch.all.total, ch.all.total_ap
    if mode == LEAK_FUTURE:
        return ch.all.total - own_tot[0], ch.all.total_ap - own_tot[1]
    k, a = ch.all.le(u.year)
    if mode == LEAK_OWN:
        return k, a                                # cutoff, no mask
    return k - own_le[0], a - own_le[1]            # SAFE: cutoff + mask


def _count_overlap(members, u, drug_id, mode, own_mask, gran):
    """(cnt, ap) for one class at dt/ta granularity: linear scan, count a history unit iff it
    shares >=1 tag with the index (once), applying the mode's cutoff + LOO mask."""
    if not members:
        return 0, 0
    S = u.dt if gran == "dt" else u.ta
    use_cutoff = mode in (SAFE, LEAK_OWN)
    mask_own = mode in (SAFE, LEAK_FUTURE)
    cnt = ap = 0
    for m in members:
        mset = m.dt if gran == "dt" else m.ta
        if not (mset & S):                         # must share a disease-type / TA
            continue
        if mask_own:
            if (m.drug == drug_id) if own_mask == "drug" else (m.unit_key == u.unit_key):
                continue
        if use_cutoff and m.year > u.year:
            continue
        cnt += 1
        ap += m.label
    return cnt, ap