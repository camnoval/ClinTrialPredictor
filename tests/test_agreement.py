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

# ===========================================================================
# k-CLASS. Added for the endpoint-type classifier, whose scheme is not binary.
#
# The point of these is that the k-class path and the binary path are ONE
# implementation. Anything asserted about a 2x2 below is asserted through both
# entry points, so a divergence fails rather than going unnoticed.
# ===========================================================================
from trial_pos.services.agreement import (  # noqa: E402
    MIN_PAIR_FOR_ASYMMETRY, VERDICT_CLEARS, VERDICT_FAILS, VERDICT_REPORTABLE_ONLY,
    VERDICT_UNDEFINED, asymmetries, chance_agreement_k, cohens_kappa_k, collapse_matrix,
    collapse_pairs, confusion_k, empty_matrix, kappa_verdict, merge_matrices, one_vs_rest,
    per_class_rates, raw_agreement_k, scale_matrix, standard_error_of_kappa, summarize_k,
    weighted_summary,
)


def _kappa_longhand(matrix):
    """Independent recomputation of k-class kappa, written out rather than imported.

    Deliberately a different shape of code from the module's version (it builds the
    marginals as lists first), so the two can disagree. If they agree on an arbitrary
    matrix the module is computing kappa and not something resembling it.
    """
    labels = sorted({a for a, _ in matrix} | {b for _, b in matrix}, key=str)
    n = sum(matrix.values())
    diagonal = sum(matrix.get((label, label), 0) for label in labels)
    po = diagonal / n
    rows = [sum(matrix.get((label, c), 0) for c in labels) for label in labels]
    cols = [sum(matrix.get((r, label), 0) for r in labels) for label in labels]
    pe = sum((r / n) * (c / n) for r, c in zip(rows, cols))
    return (po - pe) / (1 - pe)


_FOUR = ("pk", "dose", "efficacy", "other")


def _four_class_pairs():
    """A deliberately lopsided four-class sample with confusion in both directions.

    Lopsided on purpose: an even class mix makes chance agreement low and kappa close to
    raw agreement, which would let a bug in the chance term pass unnoticed.
    """
    pairs = []
    pairs += [("pk", "pk")] * 40
    pairs += [("pk", "dose")] * 6
    pairs += [("dose", "pk")] * 2
    pairs += [("dose", "dose")] * 10
    pairs += [("efficacy", "efficacy")] * 25
    pairs += [("efficacy", "other")] * 5
    pairs += [("other", "efficacy")] * 3
    pairs += [("other", "other")] * 9
    return pairs


def test_empty_matrix_is_complete_and_all_zero():
    matrix = empty_matrix(_FOUR)
    assert len(matrix) == len(_FOUR) ** 2
    assert set(matrix.values()) == {0}


def test_confusion_k_completes_every_cell_even_at_zero():
    # a missing key hides an empty off-diagonal, which is what one-sidedness is read from
    conf = confusion_k([("pk", "pk")], labels=_FOUR)
    assert len(conf["matrix"]) == len(_FOUR) ** 2
    assert conf["labels"] == tuple(sorted(_FOUR, key=str))


def test_confusion_k_cells_sum_to_n():
    pairs = _four_class_pairs()
    conf = confusion_k(pairs, labels=_FOUR)
    assert sum(conf["matrix"].values()) == conf["n"] == len(pairs)


def test_confusion_k_rejects_a_label_outside_the_declared_vocabulary():
    # a class the classifier was never supposed to emit is a bug at the boundary and must
    # surface there, not be absorbed into an extra row
    raised = False
    try:
        confusion_k([("pk", "not_a_class")], labels=_FOUR)
    except ValueError:
        raised = True
    assert raised


def test_confusion_k_treats_none_as_incomparable_not_disagreement():
    # this is how a hand label of "unclear" travels: mapped to None by the caller, counted,
    # and kept out of the marginals kappa depends on
    conf = confusion_k([(None, "pk"), ("pk", None), ("pk", "pk")], labels=_FOUR)
    assert conf["n"] == 1
    assert conf["n_incomparable"] == 2


def test_binary_and_k_class_agree_on_a_2x2():
    # the load-bearing test: one implementation, two entry points
    pairs = [(0, 0)] * 11 + [(0, 1)] * 3 + [(1, 0)] * 5 + [(1, 1)] * 21
    binary = confusion(pairs)["cells"]
    kclass = confusion_k(pairs, labels=(0, 1))["matrix"]
    assert binary == kclass
    assert raw_agreement(binary) == raw_agreement_k(kclass)
    assert chance_agreement(binary) == chance_agreement_k(kclass)
    assert cohens_kappa(binary) == cohens_kappa_k(kclass)


def test_k_class_kappa_matches_an_independent_recomputation():
    matrix = confusion_k(_four_class_pairs(), labels=_FOUR)["matrix"]
    assert abs(cohens_kappa_k(matrix) - _kappa_longhand(matrix)) < 1e-12


def test_k_class_raw_agreement_is_the_diagonal_share():
    pairs = _four_class_pairs()
    matrix = confusion_k(pairs, labels=_FOUR)["matrix"]
    agreed = sum(1 for ref, cand in pairs if ref == cand)
    assert abs(raw_agreement_k(matrix) - agreed / len(pairs)) < 1e-12


def test_k_class_perfect_agreement_is_one():
    pairs = [(c, c) for c in _FOUR for _ in range(3)]
    matrix = confusion_k(pairs, labels=_FOUR)["matrix"]
    assert cohens_kappa_k(matrix) == 1.0


def test_k_class_constant_candidate_scores_chance_kappa():
    # the multi-class form of the percent-scaled ratio bug: one answer on every row
    pairs = [("pk", "pk")] * 30 + [("efficacy", "pk")] * 20 + [("other", "pk")] * 10
    matrix = confusion_k(pairs, labels=_FOUR)["matrix"]
    assert cohens_kappa_k(matrix) == KAPPA_CHANCE
    assert abs(raw_agreement_k(matrix) - chance_agreement_k(matrix)) < 1e-12


def test_k_class_kappa_undefined_rather_than_nan_when_both_sides_constant():
    matrix = confusion_k([("pk", "pk")] * 7, labels=_FOUR)["matrix"]
    assert cohens_kappa_k(matrix) is None


def test_k_class_kappa_of_nothing_is_none():
    assert cohens_kappa_k(empty_matrix(_FOUR)) is None
    assert raw_agreement_k(empty_matrix(_FOUR)) is None


def test_one_vs_rest_preserves_the_total():
    matrix = confusion_k(_four_class_pairs(), labels=_FOUR)["matrix"]
    for label in _FOUR:
        assert sum(one_vs_rest(matrix, label).values()) == sum(matrix.values())


def test_one_vs_rest_diagonal_is_that_labels_hits():
    matrix = confusion_k(_four_class_pairs(), labels=_FOUR)["matrix"]
    for label in _FOUR:
        assert one_vs_rest(matrix, label)[(1, 1)] == matrix[(label, label)]


def test_per_class_recall_and_precision_are_the_row_and_column_shares():
    pairs = _four_class_pairs()
    matrix = confusion_k(pairs, labels=_FOUR)["matrix"]
    rates = per_class_rates(matrix)
    for label in _FOUR:
        # recomputed from the pair list, independently of the matrix
        truly = sum(1 for ref, _ in pairs if ref == label)
        called = sum(1 for _, cand in pairs if cand == label)
        hits = sum(1 for ref, cand in pairs if ref == label and cand == label)
        assert rates[label]["n_reference"] == truly
        assert rates[label]["n_candidate"] == called
        assert abs(rates[label]["recall"] - hits / truly) < 1e-12
        assert abs(rates[label]["precision"] - hits / called) < 1e-12


def test_per_class_rate_is_none_not_zero_for_an_absent_class():
    # a class with no instances is not a class the rule got wrong
    matrix = confusion_k([("pk", "pk")] * 4 + [("dose", "pk")] * 2, labels=_FOUR)["matrix"]
    assert per_class_rates(matrix)["efficacy"]["recall"] is None
    assert per_class_rates(matrix)["efficacy"]["precision"] is None


def test_asymmetries_flags_one_directional_confusion():
    pairs = ([("pk", "pk")] * 20 + [("dose", "dose")] * 20
             + [("pk", "dose")] * MIN_PAIR_FOR_ASYMMETRY)
    matrix = confusion_k(pairs, labels=_FOUR)["matrix"]
    found = [row for row in asymmetries(matrix)
             if {row["label_a"], row["label_b"]} == {"pk", "dose"}]
    assert len(found) == 1 and found[0]["one_sided"] is True


def test_asymmetries_ignores_a_pair_too_thin_to_speak_for(     ):
    # lesson 29: a banner that fires on thin data gets ignored. One misclassification is
    # one-directional by arithmetic and saying so is noise.
    pairs = [("pk", "pk")] * 20 + [("dose", "dose")] * 20 + [("pk", "dose")]
    matrix = confusion_k(pairs, labels=_FOUR)["matrix"]
    assert MIN_PAIR_FOR_ASYMMETRY > 1          # the test only means something if so
    assert asymmetries(matrix) == []


def test_asymmetries_detects_two_directional_confusion_as_not_one_sided():
    pairs = ([("pk", "pk")] * 20 + [("dose", "dose")] * 20
             + [("pk", "dose")] * MIN_PAIR_FOR_ASYMMETRY
             + [("dose", "pk")] * MIN_PAIR_FOR_ASYMMETRY)
    matrix = confusion_k(pairs, labels=_FOUR)["matrix"]
    found = [row for row in asymmetries(matrix)
             if {row["label_a"], row["label_b"]} == {"pk", "dose"}]
    assert found and found[0]["one_sided"] is False


def test_collapse_matrix_preserves_the_total():
    matrix = confusion_k(_four_class_pairs(), labels=_FOUR)["matrix"]
    collapsed = collapse_matrix(matrix, lambda c: c in ("pk", "dose"))
    assert sum(collapsed.values()) == sum(matrix.values())


def test_collapse_matrix_sums_the_cells_that_map_together():
    matrix = confusion_k(_four_class_pairs(), labels=_FOUR)["matrix"]
    refuse = ("pk", "dose")
    collapsed = collapse_matrix(matrix, lambda c: c in refuse)
    expected = sum(count for (ref, cand), count in matrix.items()
                   if ref in refuse and cand in refuse)
    assert collapsed[(True, True)] == expected


def test_collapse_pairs_leaves_none_as_none():
    pairs = [("pk", "dose"), (None, "pk"), ("other", None)]
    out = collapse_pairs(pairs, lambda c: c == "pk")
    assert out[1][0] is None and out[2][1] is None


def test_scaling_every_cell_leaves_kappa_unchanged():
    # the structural property weighting rests on: kappa depends on proportions, not counts
    matrix = confusion_k(_four_class_pairs(), labels=_FOUR)["matrix"]
    scaled = scale_matrix(matrix, 7.5)
    assert abs(cohens_kappa_k(scaled) - cohens_kappa_k(matrix)) < 1e-12


def test_merge_matrices_is_cellwise_addition():
    a = confusion_k([("pk", "pk")] * 3 + [("dose", "pk")] * 2, labels=_FOUR)["matrix"]
    b = confusion_k([("pk", "pk")] * 4 + [("other", "other")], labels=_FOUR)["matrix"]
    merged = merge_matrices([a, b])
    assert merged[("pk", "pk")] == a[("pk", "pk")] + b[("pk", "pk")]
    assert sum(merged.values()) == sum(a.values()) + sum(b.values())


def test_merge_matrices_completes_over_the_union_of_labels():
    a = confusion_k([("pk", "pk")], labels=("pk", "dose"))["matrix"]
    b = confusion_k([("other", "other")], labels=("other", "efficacy"))["matrix"]
    merged = merge_matrices([a, b])
    labels = {label for pair in merged for label in pair}
    assert len(merged) == len(labels) ** 2


def test_weighted_summary_equals_the_pooled_figure_under_proportional_sampling():
    # when every stratum is sampled at the same rate the weights are equal, so weighting
    # cannot move the answer. If it does, the weighting is not what it claims to be.
    first = confusion_k([("pk", "pk")] * 8 + [("pk", "dose")] * 2, labels=_FOUR)["matrix"]
    second = confusion_k([("efficacy", "efficacy")] * 7 + [("other", "efficacy")] * 3,
                         labels=_FOUR)["matrix"]
    pooled = merge_matrices([first, second])
    weighted = weighted_summary({"a": {"matrix": first, "weight": 4.0},
                                 "b": {"matrix": second, "weight": 4.0}})
    assert abs(weighted["kappa"] - cohens_kappa_k(pooled)) < 1e-12


def test_weighted_summary_reports_the_sampled_count_apart_from_the_population_size():
    first = confusion_k([("pk", "pk")] * 10, labels=_FOUR)["matrix"]
    second = confusion_k([("efficacy", "other")] * 5, labels=_FOUR)["matrix"]
    weighted = weighted_summary({"a": {"matrix": first, "weight": 3.0},
                                 "b": {"matrix": second, "weight": 10.0}})
    assert weighted["n_sampled"] == 15
    assert weighted["n"] == 10 * 3.0 + 5 * 10.0


def test_weighted_summary_of_nothing_reports_none_not_zero():
    out = weighted_summary({})
    assert out["kappa"] is None and out["n_sampled"] == 0


def test_kappa_verdict_names_the_three_bands_from_the_thresholds_it_is_given():
    gate, report = 0.6, 0.4
    assert kappa_verdict(gate, gate, report) == VERDICT_CLEARS
    assert kappa_verdict(gate + 0.1, gate, report) == VERDICT_CLEARS
    assert kappa_verdict(report, gate, report) == VERDICT_REPORTABLE_ONLY
    assert kappa_verdict(report - 0.01, gate, report) == VERDICT_FAILS
    assert kappa_verdict(None, gate, report) == VERDICT_UNDEFINED


def test_kappa_verdict_refuses_inverted_thresholds():
    raised = False
    try:
        kappa_verdict(0.5, 0.4, 0.6)
    except ValueError:
        raised = True
    assert raised


def test_standard_error_falls_as_the_square_root_of_n():
    # closed form: SE scales as 1/sqrt(n) at fixed cell proportions, so quadrupling the
    # counts must halve it. This is what makes 200 labels legible as a precision claim.
    pairs = [(0, 0)] * 9 + [(0, 1)] * 3 + [(1, 0)] * 4 + [(1, 1)] * 14
    small = confusion(pairs)["cells"]
    large = {k: v * 4 for k, v in small.items()}
    assert abs(standard_error_of_kappa(small) / 2
               - standard_error_of_kappa(large)) < 1e-12


def test_summarize_k_distributions_are_the_marginals():
    pairs = _four_class_pairs()
    out = summarize_k(pairs, labels=_FOUR)
    for label in _FOUR:
        called = sum(1 for _, cand in pairs if cand == label)
        truly = sum(1 for ref, _ in pairs if ref == label)
        assert abs(out["candidate_distribution"][label] - called / len(pairs)) < 1e-12
        assert abs(out["reference_distribution"][label] - truly / len(pairs)) < 1e-12