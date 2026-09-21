"""Tests for the agreement module. Plain asserts so tests/_run_stdlib.py works.

NO TRANSCRIBED EXPECTED VALUES. Every expectation is derived from a closed form, an
independent computation, a structural property, or the constant depended on. Where a
kappa value is checked it is recomputed here from the definition, so a bug in
`cohens_kappa` cannot be certified by a number copied out of its own output.
"""
from __future__ import annotations

from trial_pos.services.agreement import (
    CELL_KEYS, KAPPA_CHANCE, chance_agreement, cohens_kappa, confusion,
    disagreement_is_one_sided, raw_agreement, summarize,
)


def _kappa_from_definition(c00, c01, c10, c11):
    """Independent recomputation of kappa from its definition, for cross-checking.

    Written out longhand rather than calling the module, so the two implementations can
    disagree. If they agree on arbitrary cell counts the module is computing kappa and
    not something that merely resembles it.
    """
    n = c00 + c01 + c10 + c11
    po = (c00 + c11) / n
    # marginals: reference is the first index, candidate the second
    ref0, ref1 = (c00 + c01) / n, (c10 + c11) / n
    cand0, cand1 = (c00 + c10) / n, (c01 + c11) / n
    pe = ref0 * cand0 + ref1 * cand1
    return (po - pe) / (1 - pe)


# ---- confusion ------------------------------------------------------------
def test_every_cell_present_even_at_zero():
    # a missing key would hide an empty off-diagonal, which is the thing one-sidedness
    # is read from
    conf = confusion([(1, 1)])
    assert set(conf["cells"]) == set(CELL_KEYS)


def test_cells_sum_to_n():
    pairs = [(0, 0), (0, 1), (1, 0), (1, 1), (1, 1)]
    conf = confusion(pairs)
    assert sum(conf["cells"].values()) == conf["n"] == len(pairs)


def test_none_is_incomparable_not_disagreement():
    # an undecidable verdict must not be counted as the candidate being wrong
    conf = confusion([(None, 1), (1, None), (None, None), (1, 1)])
    assert conf["n"] == 1
    assert conf["n_incomparable"] == 3
    assert conf["cells"][(0, 1)] == 0 and conf["cells"][(1, 0)] == 0


def test_orientation_reference_first():
    # one pair where reference says not-met and candidate says met
    conf = confusion([(0, 1)])
    assert conf["cells"][(0, 1)] == 1
    assert conf["cells"][(1, 0)] == 0


# ---- raw agreement --------------------------------------------------------
def test_raw_agreement_is_the_diagonal_share():
    pairs = [(0, 0), (1, 1), (0, 1), (1, 0)]
    cells = confusion(pairs)["cells"]
    diagonal = cells[(0, 0)] + cells[(1, 1)]
    assert raw_agreement(cells) == diagonal / len(pairs)


def test_raw_agreement_of_nothing_is_none_not_zero():
    assert raw_agreement(confusion([])["cells"]) is None


def test_perfect_agreement_is_one():
    cells = confusion([(0, 0), (1, 1), (1, 1)])["cells"]
    assert raw_agreement(cells) == 1.0


# ---- kappa ----------------------------------------------------------------
def test_kappa_matches_an_independent_recomputation():
    # arbitrary, deliberately lopsided cell counts
    c00, c01, c10, c11 = 7, 3, 11, 29
    pairs = ([(0, 0)] * c00 + [(0, 1)] * c01 + [(1, 0)] * c10 + [(1, 1)] * c11)
    cells = confusion(pairs)["cells"]
    assert abs(cohens_kappa(cells) - _kappa_from_definition(c00, c01, c10, c11)) < 1e-12


def test_constant_candidate_scores_chance_kappa():
    # THE case this module exists for: the candidate answers 1 every time. Raw agreement
    # is whatever the reference base rate happens to be; kappa must be exactly chance.
    pairs = [(1, 1)] * 359 + [(0, 1)] * 184       # shape of the percent-scaled 2x2
    cells = confusion(pairs)["cells"]
    assert cohens_kappa(cells) == KAPPA_CHANCE
    # and raw agreement is NOT low, which is the trap
    assert raw_agreement(cells) > 0.5


def test_kappa_is_bounded_above_by_one_and_reached_only_on_perfection():
    perfect = confusion([(0, 0)] * 5 + [(1, 1)] * 5)["cells"]
    assert cohens_kappa(perfect) == 1.0
    imperfect = confusion([(0, 0)] * 5 + [(1, 1)] * 4 + [(1, 0)])["cells"]
    assert cohens_kappa(imperfect) < 1.0


def test_kappa_negative_when_worse_than_chance():
    # candidate systematically inverts the reference
    cells = confusion([(0, 1)] * 10 + [(1, 0)] * 10)["cells"]
    assert cohens_kappa(cells) < KAPPA_CHANCE


def test_kappa_undefined_rather_than_nan_when_both_sides_constant():
    # both always 1: agreement is perfect and meaningless at once, and the formula
    # divides by zero. None forces the caller to report the degeneracy.
    cells = confusion([(1, 1)] * 20)["cells"]
    assert chance_agreement(cells) == 1.0
    assert cohens_kappa(cells) is None


def test_kappa_of_nothing_is_none():
    assert cohens_kappa(confusion([])["cells"]) is None


def test_chance_agreement_is_what_kappa_subtracts():
    pairs = [(0, 0)] * 4 + [(0, 1)] * 6 + [(1, 0)] * 2 + [(1, 1)] * 8
    cells = confusion(pairs)["cells"]
    observed, chance = raw_agreement(cells), chance_agreement(cells)
    assert abs(cohens_kappa(cells) - (observed - chance) / (1 - chance)) < 1e-12


# ---- one-sidedness --------------------------------------------------------
def test_one_sided_disagreement_detected():
    cells = confusion([(1, 1)] * 5 + [(0, 1)] * 3)["cells"]
    assert disagreement_is_one_sided(cells) is True


def test_two_sided_disagreement_detected():
    cells = confusion([(0, 1)] * 3 + [(1, 0)] * 2)["cells"]
    assert disagreement_is_one_sided(cells) is False


def test_no_disagreement_is_none_not_true():
    # "there was none" and "all of it ran one way" are different findings
    cells = confusion([(0, 0), (1, 1)])["cells"]
    assert disagreement_is_one_sided(cells) is None


# ---- summarize ------------------------------------------------------------
def test_summarize_exposes_the_constant_rule():
    pairs = [(1, 1)] * 359 + [(0, 1)] * 184
    out = summarize(pairs)
    assert out["candidate_positive_rate"] == 1.0
    assert out["kappa"] == KAPPA_CHANCE
    assert out["one_sided_disagreement"] is True
    assert out["n"] == len(pairs)


def test_summarize_agrees_with_its_parts():
    pairs = [(0, 0)] * 3 + [(0, 1)] * 2 + [(1, 0)] * 4 + [(1, 1)] * 9
    out = summarize(pairs)
    cells = confusion(pairs)["cells"]
    assert out["cells"] == cells
    assert out["raw_agreement"] == raw_agreement(cells)
    assert out["kappa"] == cohens_kappa(cells)


def test_summarize_positive_rates_are_the_marginals():
    pairs = [(0, 0)] * 3 + [(0, 1)] * 2 + [(1, 0)] * 4 + [(1, 1)] * 9
    out = summarize(pairs)
    n = out["n"]
    assert out["reference_positive_rate"] == (4 + 9) / n
    assert out["candidate_positive_rate"] == (2 + 9) / n