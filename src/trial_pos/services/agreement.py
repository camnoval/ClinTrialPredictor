"""Agreement between two binary verdicts: the 2x2, raw agreement, and Cohen's kappa.

WHY THIS IS A MODULE AND NOT A FEW LINES IN A SCRIPT
====================================================
Kappa was computed inline in `scripts/validate_reconstruction.py`, which breaks the
standing rule that pure logic lives in `src/` with tests and scripts are thin I/O. It is
now needed by a second caller (`scripts/validate_interval_rule.py`, which measures the
tier-C interval rule against sponsor-posted p-values), and a derivation duplicated across
two scripts is a derivation that will diverge.

WHY KAPPA AND NOT RAW AGREEMENT
===============================
Raw agreement is inflated by the base rate. The endpoint-met positive rate is around 57%,
so a rule that always answers "met" scores ~57% while carrying no information at all.
Kappa subtracts the agreement expected from the two marginals, so a constant rule scores
0 no matter how lopsided the base rate.

That is not hypothetical here. The tier-C interval rule applied to percent-scaled ratio
intervals answers "met" on every single row: 66.1% raw agreement, kappa exactly 0.000.
Raw agreement alone would have read as passable.

WHY THE 2x2 TRAVELS WITH THE SCALARS
====================================
`ASYMMETRY` matters as much as the summary. A rule that disagrees with the reference in
ONE direction only is biased rather than noisy, and bias is disqualifying at any level of
agreement -- it means the rule is answering a systematically different question. Only the
off-diagonal cells show that, so `confusion` returns them and `disagreement_is_one_sided`
names the condition rather than leaving every caller to re-derive it.

NO KAPPA WITHOUT n
==================
Every function here returns the count it was computed on. A kappa quoted without its n is
not interpretable, and the audit prints both.
"""
from __future__ import annotations

from typing import Iterable, Optional

# Cell keys for the 2x2. (reference verdict, candidate verdict), so the FIRST index is
# always the thing being compared against -- the sponsor's own posted verdict in both
# current callers. Naming them rather than using bare tuples keeps the orientation from
# being reversed by a caller reading the dict.
CELL_KEYS = ((0, 0), (0, 1), (1, 0), (1, 1))

# A kappa of exactly this means the rule agrees no better than chance given the marginals.
KAPPA_CHANCE = 0.0

# Below this, a rule is not carrying usable information about the reference. It is a
# reporting threshold only -- nothing in this module filters on it -- and it is named here
# rather than written into a script so the two callers quote the same number.
KAPPA_UNINFORMATIVE_MAX = 0.2

KAPPA_DOC = (
    "Cohen's kappa: agreement corrected for what the marginals would produce by chance. "
    "0 means the candidate carries no information about the reference, which is what a "
    "constant answer scores regardless of how high its raw agreement looks."
)


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
    n = sum(cells.values())
    if n == 0:
        return None
    return (cells[(0, 0)] + cells[(1, 1)]) / n


def chance_agreement(cells: dict) -> Optional[float]:
    """Agreement the two MARGINALS alone would produce. None when there is no data.

    This is the quantity kappa subtracts, and it is exposed rather than kept private
    because it is what explains a surprising kappa: a rule answering "met" on every row
    has a degenerate marginal, chance agreement equal to its raw agreement, and therefore
    a kappa of zero.
    """
    n = sum(cells.values())
    if n == 0:
        return None
    total = 0.0
    for value in (0, 1):
        ref_marginal = sum(cells[(value, c)] for c in (0, 1)) / n
        cand_marginal = sum(cells[(r, value)] for r in (0, 1)) / n
        total += ref_marginal * cand_marginal
    return total


def cohens_kappa(cells: dict) -> Optional[float]:
    """Kappa from a 2x2. None when undefined rather than a misleading number.

    Undefined in two cases, both real:
      - no comparable pairs at all;
      - chance agreement of exactly 1, which happens when BOTH sides are constant and
        identical. Agreement is then perfect and uninformative simultaneously, and the
        formula divides by zero. Returning None forces the caller to report the
        degeneracy instead of printing nan or 1.0.
    """
    n = sum(cells.values())
    if n == 0:
        return None
    observed = raw_agreement(cells)
    chance = chance_agreement(cells)
    if chance is None or chance >= 1.0:
        return None
    return (observed - chance) / (1.0 - chance)


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