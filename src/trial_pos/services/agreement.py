"""Agreement between two verdicts: the confusion matrix, raw agreement, and Cohen's kappa.

WHY THIS IS A MODULE AND NOT A FEW LINES IN A SCRIPT
====================================================
Kappa was computed inline in `scripts/validate_reconstruction.py`, which breaks the
standing rule that pure logic lives in `src/` with tests and scripts are thin I/O. It is
now needed by a third caller (the endpoint-type classifier's scoring step, beside
`validate_interval_rule.py` and the reconstruction validator), and a derivation duplicated
across scripts is a derivation that will diverge.

WHY KAPPA AND NOT RAW AGREEMENT
===============================
Raw agreement is inflated by the base rate. The endpoint-met positive rate is around 60%,
so a rule that always answers "met" scores ~60% while carrying no information at all.
Kappa subtracts the agreement expected from the two marginals, so a constant rule scores
0 no matter how lopsided the base rate.

That is not hypothetical here. The tier-C interval rule applied to percent-scaled ratio
intervals answers "met" on every single row: 78.4% raw agreement, kappa exactly 0.000.
Raw agreement alone would have read as passable.

WHY THE MATRIX TRAVELS WITH THE SCALARS
=======================================
ASYMMETRY matters as much as the summary. A rule that disagrees with the reference in ONE
direction only is biased rather than noisy, and bias is disqualifying at any level of
agreement -- it means the rule is answering a systematically different question. Only the
off-diagonal cells show that, so `confusion` returns them and `disagreement_is_one_sided`
names the condition rather than leaving every caller to re-derive it.

BINARY AND k-CLASS ARE THE SAME ARITHMETIC, AND SHARE ONE IMPLEMENTATION
========================================================================
The binary functions came first and have two live callers, so their signatures and return
values are unchanged. The k-class functions added for the endpoint-type classifier are not
a second implementation: the binary ones DELEGATE to the general ones, which derive the
label set from the matrix keys. `test_binary_and_k_class_agree_on_a_2x2` pins that, so the
two paths cannot drift. Two copies of a derivation is the thing this module exists to stop.

The general formula, for any number of classes:

    kappa = (p_observed - p_chance) / (1 - p_chance)

    p_observed = trace(M) / N                      -- the diagonal share
    p_chance   = sum over classes c of
                 (row_total(c) / N) * (col_total(c) / N)

which reduces to the binary expression exactly when there are two classes.

WHY MULTI-CLASS ALONE IS NOT ENOUGH, AND THE BINARY COLLAPSE IS REPORTED BESIDE IT
==================================================================================
The endpoint-type classifier has five or six classes, but the decision it drives is
two-valued: the tool either refuses an endpoint estimate or it does not. A classifier that
confuses pharmacokinetic with dose-finding is wrong about the class and right about the
decision, and that error should not block the feature; one that confuses dose-finding with
efficacy-shaped is wrong about the decision and should. So the gating figure is the kappa
of the COLLAPSED matrix, and the multi-class kappa is reported beside it without gating.
`collapse_matrix` exists so both come from ONE set of hand labels rather than two
labelling passes that could disagree.

TWO THINGS A SINGLE KAPPA CANNOT TELL YOU, SO BOTH ARE EXPOSED
==============================================================
1. WHICH confusion is costing you. A scalar over five classes hides whether the rule is
   muddling two adjacent classes harmlessly or mapping a whole class onto the wrong one.
   `per_class_rates` and `asymmetries` report that.
2. Whether a low kappa means a weak rule or a skewed marginal. Kappa is harsh when one
   class dominates, because a dominant class pushes p_chance up and leaves little room
   above it. `chance_agreement_k` is public for exactly this reason: a surprising kappa is
   usually explained by looking at what it subtracted.

NO KAPPA WITHOUT n
==================
Every function here returns the count it was computed on. A kappa quoted without its n is
not interpretable, and the audit prints both. Weighted matrices carry a FLOAT n, because
`scale_matrix` turns sampled counts into estimated population counts; that is deliberate,
and `weighted_summary` reports `n_sampled` beside it so the number of items a human
actually looked at is never lost behind an estimated population size.
"""
from __future__ import annotations

import math
from typing import Callable, Hashable, Iterable, Optional

# Cell keys for the 2x2. (reference verdict, candidate verdict), so the FIRST index is
# always the thing being compared against -- the sponsor's own posted verdict, or the
# operator's hand label. Naming them rather than using bare tuples keeps the orientation
# from being reversed by a caller reading the dict.
CELL_KEYS = ((0, 0), (0, 1), (1, 0), (1, 1))

# A kappa of exactly this means the rule agrees no better than chance given the marginals.
KAPPA_CHANCE = 0.0

# Below this, a rule is not carrying usable information about the reference. It is a
# reporting threshold only -- nothing in this module filters on it -- and it is named here
# rather than written into a script so the callers quote the same number.
KAPPA_UNINFORMATIVE_MAX = 0.2

KAPPA_DOC = (
    "Cohen's kappa: agreement corrected for what the marginals would produce by chance. "
    "0 means the candidate carries no information about the reference, which is what a "
    "constant answer scores regardless of how high its raw agreement looks."
)

# Verdict names for `kappa_verdict`. A threshold that decides whether a rule reaches users
# is a verdict-producing threshold, so the CALLER passes the numbers in as flags and this
# module only names the bands.
VERDICT_CLEARS = "clears"
VERDICT_REPORTABLE_ONLY = "reportable_not_gating"
VERDICT_FAILS = "fails"
VERDICT_UNDEFINED = "undefined"

VERDICT_DOC = {
    VERDICT_CLEARS: "at or above the gating minimum: the rule may decide what users see",
    VERDICT_REPORTABLE_ONLY: ("informative but below the gating minimum: quote it, do not "
                              "let it decide anything a user reads"),
    VERDICT_FAILS: "below the reportable minimum: the rule carries too little information",
    VERDICT_UNDEFINED: ("kappa is undefined -- no comparable pairs, or both sides are "
                        "constant and identical. Report the degeneracy, not a number"),
}

# Minimum confusion on a pair of labels before `asymmetries` will call it one-directional.
# A single misclassification is one-directional by arithmetic, and a banner that fires on
# it is a banner that gets ignored (lesson 29).
MIN_PAIR_FOR_ASYMMETRY = 5


# ---- k-class core ---------------------------------------------------------
def _labels_of(matrix: dict) -> tuple:
    """The label set a matrix is over, in a stable order.

    Derived from the keys rather than carried alongside, so a matrix that has been scaled,
    merged or collapsed cannot disagree with its own label list. Sorted by string form so
    the order is deterministic across runs and across label types.
    """
    seen = set()
    for ref, cand in matrix:
        seen.add(ref)
        seen.add(cand)
    return tuple(sorted(seen, key=lambda v: str(v)))


def empty_matrix(labels: Iterable[Hashable]) -> dict:
    """Every (ref, cand) cell present at zero.

    Completeness is not cosmetic. A missing key hides an empty off-diagonal, and an empty
    off-diagonal is precisely what one-directional disagreement is read from.
    """
    ordered = tuple(labels)
    return {(a, b): 0 for a in ordered for b in ordered}


def _completed(matrix: dict) -> dict:
    """Fill in any absent cells over the labels the matrix already mentions."""
    complete = empty_matrix(_labels_of(matrix))
    complete.update(matrix)
    return complete


def confusion_k(pairs: Iterable[tuple],
                labels: Optional[Iterable[Hashable]] = None) -> dict:
    """[(reference, candidate), ...] -> the full k x k matrix keyed (ref, cand).

    Pairs where either side is None are SKIPPED and counted as `n_incomparable`: an
    undecidable verdict is not a disagreement, and folding it into one would manufacture a
    finding. This is how a hand label of "unclear" is handled -- the caller maps it to None,
    and it stays countable without distorting the marginals that kappa depends on.

    `labels` declares the vocabulary. A value outside it RAISES rather than being absorbed
    into an extra row, because a class the classifier was never supposed to emit is a bug
    at the caller's boundary and must surface there. When `labels` is None the vocabulary is
    taken from the data, which is right for exploratory use and wrong for validation, so
    validation callers pass it explicitly.
    """
    declared = tuple(labels) if labels is not None else None
    if declared is not None:
        allowed = set(declared)
        matrix = empty_matrix(declared)
    else:
        allowed = None
        matrix = {}
    n_incomparable = 0
    for ref, cand in pairs:
        if ref is None or cand is None:
            n_incomparable += 1
            continue
        if allowed is not None:
            for side, value in (("reference", ref), ("candidate", cand)):
                if value not in allowed:
                    raise ValueError(
                        f"{side} label {value!r} is outside the declared vocabulary "
                        f"{declared!r}")
        matrix[(ref, cand)] = matrix.get((ref, cand), 0) + 1
    matrix = _completed(matrix) if matrix else matrix
    return {"matrix": matrix,
            "labels": _labels_of(matrix),
            "n": sum(matrix.values()),
            "n_incomparable": n_incomparable}


def raw_agreement_k(matrix: dict) -> Optional[float]:
    """Diagonal share of the matrix. None when there is nothing to compare.

    None rather than 0.0 deliberately: no data is not perfect disagreement.
    """
    n = sum(matrix.values())
    if n == 0:
        return None
    return sum(matrix[(label, label)] for label in _labels_of(matrix)) / n


def chance_agreement_k(matrix: dict) -> Optional[float]:
    """Agreement the two MARGINALS alone would produce. None when there is no data.

    This is the quantity kappa subtracts, and it is exposed rather than kept private
    because it is what explains a surprising kappa. A rule answering one class on every row
    has a degenerate marginal, chance agreement equal to its raw agreement, and therefore a
    kappa of zero. It also explains the other direction: with one dominant class, chance
    agreement is high and kappa is harsh on a rule that is mostly right.
    """
    n = sum(matrix.values())
    if n == 0:
        return None
    labels = _labels_of(matrix)
    total = 0.0
    for label in labels:
        ref_marginal = sum(matrix[(label, c)] for c in labels) / n
        cand_marginal = sum(matrix[(r, label)] for r in labels) / n
        total += ref_marginal * cand_marginal
    return total


def cohens_kappa_k(matrix: dict) -> Optional[float]:
    """Kappa from a k x k matrix. None when undefined rather than a misleading number.

    Undefined in two cases, both real:
      - no comparable pairs at all;
      - chance agreement of exactly 1, which happens when BOTH sides are constant and
        identical. Agreement is then perfect and uninformative simultaneously, and the
        formula divides by zero. Returning None forces the caller to report the degeneracy
        instead of printing nan or 1.0.
    """
    n = sum(matrix.values())
    if n == 0:
        return None
    observed = raw_agreement_k(matrix)
    chance = chance_agreement_k(matrix)
    if chance is None or chance >= 1.0:
        return None
    return (observed - chance) / (1.0 - chance)


def one_vs_rest(matrix: dict, label: Hashable) -> dict:
    """Collapse a k x k matrix to the 2x2 for one label against everything else.

    Returned in the binary cell layout, so every binary function in this module accepts it
    directly and a per-class figure is computed by the same code as a top-level one.
    """
    labels = _labels_of(matrix)
    cells = {k: 0 for k in CELL_KEYS}
    for ref in labels:
        for cand in labels:
            key = (1 if ref == label else 0, 1 if cand == label else 0)
            cells[key] += matrix[(ref, cand)]
    return cells


def per_class_rates(matrix: dict) -> dict:
    """label -> {n_reference, n_candidate, hits, recall, precision, kappa_one_vs_rest}.

    The scalar kappa over k classes cannot say WHICH class the rule is failing, and with an
    uneven class mix the failing class can be small enough to leave the scalar looking
    healthy. Recall is read along the reference row (of the items that truly are this class,
    how many did the rule find); precision down the candidate column (of the items the rule
    called this class, how many were). Both are None when their denominator is zero, never
    0.0, because a class with no instances is not a class the rule got wrong.
    """
    labels = _labels_of(matrix)
    out = {}
    for label in labels:
        ref_total = sum(matrix[(label, c)] for c in labels)
        cand_total = sum(matrix[(r, label)] for r in labels)
        hits = matrix[(label, label)]
        out[label] = {
            "n_reference": ref_total,
            "n_candidate": cand_total,
            "hits": hits,
            "recall": (hits / ref_total) if ref_total else None,
            "precision": (hits / cand_total) if cand_total else None,
            "kappa_one_vs_rest": cohens_kappa(one_vs_rest(matrix, label)),
        }
    return out


def asymmetries(matrix: dict, min_pair_count: int = MIN_PAIR_FOR_ASYMMETRY) -> list:
    """For each unordered pair of labels, how the confusion between them runs.

    A rule that maps class A onto class B and never the reverse is biased on that pair --
    a systematic misreading rather than noise -- and that is the multi-class form of the
    one-directional disagreement test. Pairs whose total confusion is below
    `min_pair_count` are omitted rather than reported as one-sided, because a single
    misclassification is one-directional by arithmetic and saying so is noise (lesson 29).

    Sorted by total confusion descending, so the worst pair is first.
    """
    labels = _labels_of(matrix)
    out = []
    for i, a in enumerate(labels):
        for b in labels[i + 1:]:
            ab = matrix[(a, b)]
            ba = matrix[(b, a)]
            total = ab + ba
            if total == 0 or total < min_pair_count:
                continue
            out.append({
                "label_a": a,
                "label_b": b,
                "a_called_b": ab,
                "b_called_a": ba,
                "total": total,
                "one_sided": (ab == 0) != (ba == 0),
            })
    out.sort(key=lambda row: (-row["total"], str(row["label_a"]), str(row["label_b"])))
    return out


def collapse_matrix(matrix: dict, mapping: Callable[[Hashable], Hashable]) -> dict:
    """Re-key a matrix through a label mapping, summing cells that land together.

    This is how the multi-class hand labels produce the BINARY gate figure: the decision
    that reaches a user is two-valued even though the classes are not, and the number that
    gates the feature has to be the one that matches what ships. Done by collapsing the
    matrix rather than by re-labelling the sample, so both figures rest on one set of hand
    labels and cannot disagree with each other.
    """
    out: dict = {}
    for (ref, cand), count in matrix.items():
        key = (mapping(ref), mapping(cand))
        out[key] = out.get(key, 0) + count
    return _completed(out) if out else out


def collapse_pairs(pairs: Iterable[tuple],
                   mapping: Callable[[Hashable], Hashable]) -> list:
    """Map both sides of each pair, preserving None as None.

    None must survive the mapping untouched: an incomparable item is incomparable in every
    collapse of the vocabulary, and passing it through a mapping with no entry for it would
    either raise or invent a class.
    """
    out = []
    for ref, cand in pairs:
        out.append((None if ref is None else mapping(ref),
                    None if cand is None else mapping(cand)))
    return out


def scale_matrix(matrix: dict, factor: float) -> dict:
    """Multiply every cell by a factor, producing float counts.

    Used to weight a stratum back up to the population it was sampled from. The result is
    an ESTIMATED population matrix, not a count of observed items, which is why `n` on
    anything derived from it is a float. Kappa on a weighted matrix answers "what would this
    rule's kappa be on the population"; the unweighted pooled figure answers "what was it on
    the sample". They are different questions and both belong in the report.
    """
    return {key: count * factor for key, count in matrix.items()}


def merge_matrices(matrices: Iterable[dict]) -> dict:
    """Cell-wise sum over matrices that may be over different label subsets.

    Addition is a tested operation here rather than an inline loop, for the same reason
    `merge_entity_coverage` exists: the widened pull's coverage bug was an inline sum over
    the wrong collection, and a merge that silently dropped a label present in only one
    input would be the same failure in a new place.
    """
    out: dict = {}
    for matrix in matrices:
        for key, count in matrix.items():
            out[key] = out.get(key, 0) + count
    return _completed(out) if out else {}


def kappa_verdict(kappa: Optional[float], gating_minimum: float,
                  reportable_minimum: float) -> str:
    """Name the band a kappa falls in. The thresholds are the CALLER's flags.

    Three bands rather than two, because "not good enough to decide what a user sees" and
    "carries no information" are different findings with different next steps: the first
    says keep the rule and report it, the second says the rule or the class scheme is wrong.

    Raises when the two minima are given the wrong way round, since a silently inverted
    pair of thresholds returns a verdict that reads correct and is not.
    """
    if gating_minimum < reportable_minimum:
        raise ValueError(f"gating_minimum {gating_minimum} is below reportable_minimum "
                         f"{reportable_minimum}; the bands would be inverted")
    if kappa is None:
        return VERDICT_UNDEFINED
    if kappa >= gating_minimum:
        return VERDICT_CLEARS
    if kappa >= reportable_minimum:
        return VERDICT_REPORTABLE_ONLY
    return VERDICT_FAILS


def summarize_k(pairs: Iterable[tuple],
                labels: Optional[Iterable[Hashable]] = None) -> dict:
    """[(reference, candidate), ...] -> the k-class record.

    `candidate_distribution` is included for the same reason the binary summary reports a
    positive rate: it is what exposes a degenerate rule at a glance. A rule answering one
    class on 100% of rows beside a kappa near zero says "constant", which no summary
    statistic communicates on its own.
    """
    conf = confusion_k(pairs, labels)
    matrix, n = conf["matrix"], conf["n"]
    label_set = conf["labels"]
    return {
        "matrix": matrix,
        "labels": label_set,
        "n": n,
        "n_incomparable": conf["n_incomparable"],
        "raw_agreement": raw_agreement_k(matrix),
        "chance_agreement": chance_agreement_k(matrix),
        "kappa": cohens_kappa_k(matrix),
        "per_class": per_class_rates(matrix),
        "asymmetries": asymmetries(matrix),
        "candidate_distribution": {
            label: (sum(matrix[(r, label)] for r in label_set) / n) if n else None
            for label in label_set},
        "reference_distribution": {
            label: (sum(matrix[(label, c)] for c in label_set) / n) if n else None
            for label in label_set},
    }


def weighted_summary(per_stratum: dict) -> dict:
    """{stratum: {"matrix": M, "weight": w}} -> kappa on the weighted population matrix.

    A stratified sample that deliberately over-samples the decisive cells does NOT produce
    a pooled kappa describing the population: the sample's class mix is not the
    population's, and kappa depends on the marginals. Scaling each stratum's matrix by
    N_h / n_h and taking kappa on the sum answers the population question. The unweighted
    pooled figure answers the sample question, and must be labelled as not being the
    population estimate wherever it is printed.

    `n` here is an estimated population size and is a float. `n_sampled` is the honest count
    of items a human actually looked at, and it is what any claim of precision rests on.
    """
    scaled = []
    n_sampled = 0
    for bucket in per_stratum.values():
        matrix = bucket["matrix"]
        n_sampled += sum(matrix.values())
        scaled.append(scale_matrix(matrix, bucket["weight"]))
    merged = merge_matrices(scaled)
    if not merged or sum(merged.values()) == 0:
        return {"matrix": merged, "labels": _labels_of(merged) if merged else (),
                "n": 0.0, "n_sampled": n_sampled, "raw_agreement": None,
                "chance_agreement": None, "kappa": None}
    return {
        "matrix": merged,
        "labels": _labels_of(merged),
        "n": sum(merged.values()),
        "n_sampled": n_sampled,
        "raw_agreement": raw_agreement_k(merged),
        "chance_agreement": chance_agreement_k(merged),
        "kappa": cohens_kappa_k(merged),
    }


# ---- binary: unchanged behaviour, now delegating to the k-class core -------
def confusion(pairs: Iterable[tuple]) -> dict:
    """[(reference, candidate), ...] -> {(ref, cand): count} over all four cells.

    Pairs where either side is None are SKIPPED and counted separately as
    `n_incomparable`: an undecidable verdict is not a disagreement, and folding it into
    one would manufacture a finding. Every cell is present even at zero, so an empty
    off-diagonal is visible as a zero rather than as an absent key -- which is the whole
    point when the question is whether disagreement runs one way.
    """
    cells = {k: 0 for k in CELL_KEYS}
    n_incomparable = 0
    for ref, cand in pairs:
        if ref is None or cand is None:
            n_incomparable += 1
            continue
        key = (int(bool(ref)), int(bool(cand)))
        cells[key] += 1
    return {"cells": cells,
            "n": sum(cells.values()),
            "n_incomparable": n_incomparable}


def raw_agreement(cells: dict) -> Optional[float]:
    """Proportion on the diagonal. None when there is nothing to compare.

    None rather than 0.0 deliberately: no data is not perfect disagreement.
    """
    return raw_agreement_k(cells)


def chance_agreement(cells: dict) -> Optional[float]:
    """Agreement the two MARGINALS alone would produce. None when there is no data."""
    return chance_agreement_k(cells)


def cohens_kappa(cells: dict) -> Optional[float]:
    """Kappa from a 2x2. None when undefined rather than a misleading number."""
    return cohens_kappa_k(cells)


def disagreement_is_one_sided(cells: dict) -> Optional[bool]:
    """True when every disagreement runs the same way. None when there is none at all.

    One-directional disagreement means the candidate is biased rather than noisy -- it is
    answering a systematically different question -- and that disqualifies a rule however
    high its raw agreement. None (no disagreement anywhere) is distinguished from False
    (disagreement in both directions) because the two are different findings.
    """
    a, b = cells[(0, 1)], cells[(1, 0)]
    if a == 0 and b == 0:
        return None
    return a == 0 or b == 0


def summarize(pairs: Iterable[tuple]) -> dict:
    """[(reference, candidate), ...] -> everything above in one record.

    `candidate_positive_rate` is included because it is what exposes a constant rule at a
    glance: a rate of 1.0 beside a kappa of 0.0 says the candidate answered "met" every
    time, which no summary statistic on its own communicates.
    """
    conf = confusion(pairs)
    cells, n = conf["cells"], conf["n"]
    cand_positive = cells[(0, 1)] + cells[(1, 1)]
    ref_positive = cells[(1, 0)] + cells[(1, 1)]
    return {
        "cells": cells,
        "n": n,
        "n_incomparable": conf["n_incomparable"],
        "raw_agreement": raw_agreement(cells),
        "chance_agreement": chance_agreement(cells),
        "kappa": cohens_kappa(cells),
        "one_sided_disagreement": disagreement_is_one_sided(cells),
        "candidate_positive_rate": (cand_positive / n) if n else None,
        "reference_positive_rate": (ref_positive / n) if n else None,
    }


def standard_error_of_kappa(cells: dict) -> Optional[float]:
    """Large-sample standard error of a binary kappa, for SIZING only.

    Here so nobody quotes a kappa off 200 hand labels as though it were exact. This is the
    simple approximation that treats the marginals as fixed; it is a rough width, not an
    inference, and it is undefined wherever kappa is. It exists because a threshold decision
    taken on a point estimate whose standard error is 0.06 is a different decision from one
    taken where it is 0.006, and 200 labels put us nearer the former.
    """
    n = sum(cells.values())
    if n == 0:
        return None
    chance = chance_agreement(cells)
    observed = raw_agreement(cells)
    if chance is None or chance >= 1.0 or observed is None:
        return None
    return math.sqrt(observed * (1.0 - observed) / n) / (1.0 - chance)