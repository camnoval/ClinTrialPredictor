"""Tests for the tier-derived sponsor threshold reference. Plain asserts for _run_stdlib.

NO TRANSCRIBED EXPECTED VALUES. Every expectation is derived from a closed form, a
structural property, or the constant depended on. In particular nothing here asserts a
share of the corpus: the size of the over-refusal cell is the thing this reference exists
to MEASURE, and a test that pinned it would certify whatever the rule currently does --
which is the exact failure mode section 12.7 warns about ("a measurement that cannot come
back negative is not a measurement").

Interval bounds are built FROM `DEFAULT_RATIO_SCALE_FLOOR` and coverage FROM
`required_ci_percent`, so moving either constant moves the fixtures with it rather than
silently invalidating them.
"""
from __future__ import annotations

from trial_pos.services.endpoint_label import (
    is_geometric_ratio,
    DEFAULT_ALPHA, DEFAULT_RATIO_SCALE_FLOOR, TIER_A, TIER_B, TIER_C, TIER_D, TIER_E,
    classify_analysis, met_from_ci, required_ci_percent,
)
from trial_pos.services.endpoint_type import (
    GATE_APPLICABLE, GATE_NOT_APPLICABLE, GATE_UNDETERMINABLE, GATE_VERDICTS,
)
from trial_pos.services.sponsor_threshold import (
    AGREEMENT_CELLS, CELL_BOTH_ALLOW, CELL_BOTH_REFUSE, CELL_DOC, CELL_OVER_REFUSAL,
    CELL_RULE_UNREADABLE, CELL_SPONSOR_UNREADABLE, CELL_UNDER_REFUSAL, CELLS,
    DECISIVE_CELLS, REASON_CONTRAST_INTERVAL, REASON_NI_DESIGN, REASON_NOTHING_POSTED,
    REASON_GEOMETRIC_RATIO, REASON_P_VALUE, REASON_PERCENT_SCALED,
    REASON_VALUE_WITH_INTERVAL, REASON_VERDICT,
    SPONSOR_REASONS, SPONSOR_VERDICT_DOC, SPONSOR_VERDICTS, THRESHOLD_NOT_APPLIED,
    THRESHOLD_TESTED, THRESHOLD_UNKNOWN, UNREADABLE_CELLS, VERDICT_PRECEDENCE,
    allowance_precision, analysis_threshold, cell_for, cell_rate, crosstab,
    decisive_total, disagreement_ratio, false_refusal_share, has_interval,
    one_directional, outcome_threshold, over_refusal_rate, refusal_precision,
    resolve_verdicts, selection_report, trial_threshold, under_refusal_rate,
)

# ---- fixtures, every number derived from a constant ------------------------
# A p-value that decides at the default alpha, and one that decides the other way. Built
# from DEFAULT_ALPHA so they move with it.
P_SIGNIFICANT = DEFAULT_ALPHA / 10.0
P_NOT_SIGNIFICANT = min(1.0, DEFAULT_ALPHA * 10.0)

# A unit-scale ratio interval sits below the percent floor; a percent-scale one sits
# entirely at or above it. Both derived from DEFAULT_RATIO_SCALE_FLOOR, so the fixtures
# follow the constant the branch depends on.
UNIT_LO = DEFAULT_RATIO_SCALE_FLOOR / 10.0
UNIT_HI = DEFAULT_RATIO_SCALE_FLOOR / 2.0
PERCENT_LO = DEFAULT_RATIO_SCALE_FLOOR
PERCENT_HI = DEFAULT_RATIO_SCALE_FLOOR * 12.5

MATCHED_COVERAGE = required_ci_percent(DEFAULT_ALPHA)
# Far enough from the required coverage to land outside any plausible tolerance.
MISMATCHED_COVERAGE = MATCHED_COVERAGE - 10.0

RATIO_TYPE = "Hazard Ratio (HR)"
DIFFERENCE_TYPE = "Mean Difference (Final Values)"
# A single-arm quantity: `contrast_family` names no contrast for it, which is the
# "value with an interval around itself" shape.
VALUE_TYPE = "Geometric Mean"
# An equivalence statistic: the sponsor applied a threshold, and it is CONTAINMENT inside
# an acceptance window rather than exclusion of a null, so it is one this project cannot
# read. Distinct from RATIO_TYPE, which is a genuine superiority ratio.
GEOMETRIC_TYPE = "Ratio of geometric LS means"


def _row(**kwargs) -> dict:
    """An analysis row with every field this module reads present and empty by default."""
    row = {"p_value": "", "p_value_modifier": "", "non_inferiority_type": "",
           "param_type": "", "ci_lower_limit": "", "ci_upper_limit": "",
           "ci_percent": ""}
    row.update(kwargs)
    return row


# ---- vocabulary is complete and closed ------------------------------------
def test_every_verdict_is_documented():
    assert set(SPONSOR_VERDICT_DOC) == set(SPONSOR_VERDICTS)


def test_verdict_precedence_is_a_permutation_of_the_verdicts():
    # A precedence list that omitted a verdict would silently send it to the fallback.
    assert sorted(VERDICT_PRECEDENCE) == sorted(SPONSOR_VERDICTS)
    assert len(VERDICT_PRECEDENCE) == len(set(VERDICT_PRECEDENCE))


def test_tested_outranks_the_other_two():
    # Section 12.9 decision 2: ANY. Tested must come first or the roll-up is not ANY.
    assert VERDICT_PRECEDENCE[0] == THRESHOLD_TESTED


def test_unknown_is_the_last_resort():
    # Decision 1's whole point: unknown is never preferred to a readable verdict.
    assert VERDICT_PRECEDENCE[-1] == THRESHOLD_UNKNOWN


def test_every_reason_is_mapped_to_a_verdict():
    assert set(REASON_VERDICT) == set(SPONSOR_REASONS)
    assert set(REASON_VERDICT.values()) <= set(SPONSOR_VERDICTS)


def test_every_verdict_is_reachable_from_some_reason():
    # A verdict no reason produces is a verdict the function can never return, which
    # would make it documentation rather than code.
    assert set(REASON_VERDICT.values()) == set(SPONSOR_VERDICTS)


def test_every_cell_is_documented():
    assert set(CELL_DOC) == set(CELLS)


def test_the_cell_groups_partition_the_cells():
    groups = DECISIVE_CELLS + AGREEMENT_CELLS + UNREADABLE_CELLS
    assert sorted(groups) == sorted(CELLS)
    assert len(groups) == len(set(groups))


# ---- the six branches -----------------------------------------------------
def test_a_posted_p_value_is_a_threshold_test():
    verdict, reason = analysis_threshold(_row(p_value=str(P_SIGNIFICANT)))
    assert (verdict, reason) == (THRESHOLD_TESTED, REASON_P_VALUE)


def test_a_p_value_on_the_wrong_side_of_alpha_is_still_a_threshold_test():
    # The question is whether a threshold was APPLIED, not whether it was cleared. A
    # reference that only counted successes would agree with the label rather than with
    # the sponsor.
    verdict, _ = analysis_threshold(_row(p_value=str(P_NOT_SIGNIFICANT)))
    assert verdict == THRESHOLD_TESTED


def test_the_p_value_wins_over_the_interval():
    # p-value precedence, inherited from classify_analysis. A row with both is decided by
    # the p-value, so the reference must not re-derive it from the interval.
    row = _row(p_value=str(P_SIGNIFICANT), param_type=VALUE_TYPE,
               ci_lower_limit=str(UNIT_LO), ci_upper_limit=str(UNIT_HI))
    verdict, reason = analysis_threshold(row)
    assert (verdict, reason) == (THRESHOLD_TESTED, REASON_P_VALUE)


def test_a_non_inferiority_design_without_a_p_value_is_unknown():
    row = _row(non_inferiority_type="Non-Inferiority", param_type=DIFFERENCE_TYPE,
               ci_lower_limit=str(UNIT_LO), ci_upper_limit=str(UNIT_HI))
    verdict, reason = analysis_threshold(row)
    assert (verdict, reason) == (THRESHOLD_UNKNOWN, REASON_NI_DESIGN)


def test_a_percent_scaled_ratio_is_unknown_and_never_not_applied():
    row = _row(param_type=RATIO_TYPE, ci_lower_limit=str(PERCENT_LO),
               ci_upper_limit=str(PERCENT_HI))
    verdict, reason = analysis_threshold(row)
    assert (verdict, reason) == (THRESHOLD_UNKNOWN, REASON_PERCENT_SCALED)
    # The bioequivalence shape: a threshold very likely WAS applied. Calling it "no"
    # would be a false claim, and it is the specific collapse decision 3 forbids.
    assert verdict != THRESHOLD_NOT_APPLIED


def test_a_unit_scaled_ratio_interval_is_a_threshold_test():
    row = _row(param_type=RATIO_TYPE, ci_lower_limit=str(UNIT_LO),
               ci_upper_limit=str(UNIT_HI))
    verdict, reason = analysis_threshold(row)
    assert (verdict, reason) == (THRESHOLD_TESTED, REASON_CONTRAST_INTERVAL)


def test_a_difference_interval_is_a_threshold_test():
    row = _row(param_type=DIFFERENCE_TYPE, ci_lower_limit=str(UNIT_LO),
               ci_upper_limit=str(UNIT_HI))
    verdict, _ = analysis_threshold(row)
    assert verdict == THRESHOLD_TESTED


def test_a_value_with_an_interval_around_itself_is_not_applied():
    row = _row(param_type=VALUE_TYPE, ci_lower_limit=str(UNIT_LO),
               ci_upper_limit=str(UNIT_HI))
    verdict, reason = analysis_threshold(row)
    assert (verdict, reason) == (THRESHOLD_NOT_APPLIED, REASON_VALUE_WITH_INTERVAL)


def test_neither_a_p_value_nor_an_interval_is_unknown_not_no():
    # Section 12.9 decision 1, the load-bearing one. If this folded into NOT_APPLIED the
    # reference would agree with the keyword rule wherever the rule says pharmacokinetic,
    # for a reason that has nothing to do with endpoint type.
    verdict, reason = analysis_threshold(_row(param_type=VALUE_TYPE))
    assert (verdict, reason) == (THRESHOLD_UNKNOWN, REASON_NOTHING_POSTED)
    assert verdict != THRESHOLD_NOT_APPLIED


def test_a_named_contrast_with_no_interval_is_unknown():
    # A contrast named but nothing inferential posted. Still decision 1's third state:
    # naming a comparison is not applying a threshold to it.
    verdict, reason = analysis_threshold(_row(param_type=DIFFERENCE_TYPE))
    assert (verdict, reason) == (THRESHOLD_UNKNOWN, REASON_NOTHING_POSTED)


def test_a_half_interval_is_unknown():
    # One bound present is not an interval, and must not be read as a value-with-CI.
    verdict, reason = analysis_threshold(
        _row(param_type=VALUE_TYPE, ci_lower_limit=str(UNIT_LO)))
    assert (verdict, reason) == (THRESHOLD_UNKNOWN, REASON_NOTHING_POSTED)


def test_every_branch_agrees_with_the_declared_reason_table():
    # The function and REASON_VERDICT cannot drift: one reason table, checked against the
    # branch that produced it, across every shape the fixtures cover.
    rows = (
        _row(p_value=str(P_SIGNIFICANT)),
        _row(non_inferiority_type="Equivalence", param_type=DIFFERENCE_TYPE,
             ci_lower_limit=str(UNIT_LO), ci_upper_limit=str(UNIT_HI)),
        _row(param_type=RATIO_TYPE, ci_lower_limit=str(PERCENT_LO),
             ci_upper_limit=str(PERCENT_HI)),
        _row(param_type=GEOMETRIC_TYPE, ci_lower_limit=str(UNIT_LO),
             ci_upper_limit=str(UNIT_HI)),
        _row(param_type=RATIO_TYPE, ci_lower_limit=str(UNIT_LO),
             ci_upper_limit=str(UNIT_HI)),
        _row(param_type=VALUE_TYPE, ci_lower_limit=str(UNIT_LO),
             ci_upper_limit=str(UNIT_HI)),
        _row(),
    )
    seen = set()
    for row in rows:
        verdict, reason = analysis_threshold(row)
        assert REASON_VERDICT[reason] == verdict
        seen.add(reason)
    # and the fixture set exercises every declared reason
    assert seen == set(SPONSOR_REASONS)


# ---- the coverage refusal is TESTED, deliberately -------------------------
def test_a_coverage_mismatched_contrast_interval_is_still_a_threshold_test():
    # The label pipeline withholds the DIRECTION of this verdict. A threshold was still
    # applied, and the direction is not the question this module asks. Decision 3 sends
    # only tier E and the percent-scaled ratio to unknown; this is neither.
    row = _row(param_type=DIFFERENCE_TYPE, ci_lower_limit=str(UNIT_LO),
               ci_upper_limit=str(UNIT_HI), ci_percent=str(MISMATCHED_COVERAGE))
    verdict, reason = analysis_threshold(row)
    assert (verdict, reason) == (THRESHOLD_TESTED, REASON_CONTRAST_INTERVAL)


def test_coverage_does_not_change_the_verdict():
    # Structural: the same row at matched and mismatched coverage must agree, which is
    # what makes `coverage_tolerance` unused in the body rather than forgotten.
    base = dict(param_type=DIFFERENCE_TYPE, ci_lower_limit=str(UNIT_LO),
                ci_upper_limit=str(UNIT_HI))
    matched = analysis_threshold(_row(ci_percent=str(MATCHED_COVERAGE), **base))
    mismatched = analysis_threshold(_row(ci_percent=str(MISMATCHED_COVERAGE), **base))
    assert matched == mismatched


def test_the_coverage_refusal_really_is_a_refusal_in_the_label_pipeline():
    # Guards the premise of the test above: if classify_analysis stopped refusing this
    # row, the branch it is contrasted with would no longer exist.
    row = _row(param_type=DIFFERENCE_TYPE, ci_lower_limit=str(UNIT_LO),
               ci_upper_limit=str(UNIT_HI), ci_percent=str(MISMATCHED_COVERAGE))
    tier, met, _ = classify_analysis(row)
    assert (tier, met) == (TIER_D, None)


# ---- coupling to the tier machinery, checked rather than assumed ----------
def test_headline_tier_rows_are_always_threshold_tested():
    # Tier A and B mean a p-value decided the row. Any such row must be TESTED here, or
    # the reference would deny a threshold the label pipeline read directly.
    for design in ("", "Non-Inferiority"):
        row = _row(p_value=str(P_SIGNIFICANT), non_inferiority_type=design)
        tier, _, _ = classify_analysis(row)
        assert tier in (TIER_A, TIER_B)
        assert analysis_threshold(row)[0] == THRESHOLD_TESTED


def test_tier_c_rows_are_always_threshold_tested():
    row = _row(param_type=DIFFERENCE_TYPE, ci_lower_limit=str(UNIT_LO),
               ci_upper_limit=str(UNIT_HI), ci_percent=str(MATCHED_COVERAGE))
    tier, met, _ = classify_analysis(row)
    assert (tier, met is not None) == (TIER_C, True)
    assert analysis_threshold(row)[0] == THRESHOLD_TESTED


def test_tier_e_rows_are_always_unknown():
    row = _row(non_inferiority_type="Non-Inferiority", param_type=DIFFERENCE_TYPE,
               ci_lower_limit=str(UNIT_LO), ci_upper_limit=str(UNIT_HI))
    tier, _, _ = classify_analysis(row)
    assert tier == TIER_E
    assert analysis_threshold(row)[0] == THRESHOLD_UNKNOWN


def test_tier_d_carries_more_than_one_sponsor_verdict():
    # THE reason this module classifies analysis rows and never the tier. Tier D is
    # reached by several routes and they do NOT agree about whether a threshold was
    # applied, so a mapping keyed on the tier alone would be wrong by construction.
    tier_d_rows = (
        _row(param_type=VALUE_TYPE, ci_lower_limit=str(UNIT_LO),
             ci_upper_limit=str(UNIT_HI)),                       # value with a CI
        _row(param_type=RATIO_TYPE, ci_lower_limit=str(PERCENT_LO),
             ci_upper_limit=str(PERCENT_HI)),                    # percent-scaled ratio
        _row(),                                                  # nothing posted
    )
    verdicts = set()
    for row in tier_d_rows:
        tier, _, _ = classify_analysis(row)
        assert tier == TIER_D
        verdicts.add(analysis_threshold(row)[0])
    assert len(verdicts) > 1


# ---- has_interval ---------------------------------------------------------
def test_has_interval_agrees_with_met_from_ci_on_readability():
    # Structural: with a null supplied, met_from_ci's None can only mean the interval was
    # unreadable, which is exactly what has_interval claims to report.
    cases = ((str(UNIT_LO), str(UNIT_HI)), ("", str(UNIT_HI)), (str(UNIT_LO), ""),
             ("", ""), ("not a number", "also not"))
    for lower, upper in cases:
        assert has_interval(lower, upper) == (
            met_from_ci(lower, upper, 0.0)[0] is not None)


def test_has_interval_is_indifferent_to_bound_order():
    assert has_interval(UNIT_HI, UNIT_LO) == has_interval(UNIT_LO, UNIT_HI)


# ---- outcome roll-up ------------------------------------------------------
def test_any_tested_analysis_makes_the_outcome_tested():
    # Decision 2. The descriptive row must not demote the tested one.
    analyses = [_row(param_type=VALUE_TYPE, ci_lower_limit=str(UNIT_LO),
                     ci_upper_limit=str(UNIT_HI)),
                _row(p_value=str(P_SIGNIFICANT))]
    assert outcome_threshold(analyses)["verdict"] == THRESHOLD_TESTED


def test_the_roll_up_does_not_depend_on_analysis_order():
    analyses = [_row(p_value=str(P_SIGNIFICANT)),
                _row(param_type=VALUE_TYPE, ci_lower_limit=str(UNIT_LO),
                     ci_upper_limit=str(UNIT_HI)),
                _row()]
    forward = outcome_threshold(analyses)["verdict"]
    backward = outcome_threshold(list(reversed(analyses)))["verdict"]
    assert forward == backward


def test_a_readable_no_beats_a_silent_row():
    analyses = [_row(), _row(param_type=VALUE_TYPE, ci_lower_limit=str(UNIT_LO),
                             ci_upper_limit=str(UNIT_HI))]
    assert outcome_threshold(analyses)["verdict"] == THRESHOLD_NOT_APPLIED


def test_an_outcome_with_no_analyses_is_unknown():
    record = outcome_threshold([])
    assert record["verdict"] == THRESHOLD_UNKNOWN
    assert record["n_analyses"] == 0


def test_reason_counts_sum_to_the_analysis_count():
    analyses = [_row(p_value=str(P_SIGNIFICANT)),
                _row(param_type=VALUE_TYPE, ci_lower_limit=str(UNIT_LO),
                     ci_upper_limit=str(UNIT_HI)),
                _row()]
    record = outcome_threshold(analyses)
    assert sum(record["reasons"].values()) == record["n_analyses"]


def test_verdict_counts_sum_to_the_analysis_count():
    analyses = [_row(p_value=str(P_SIGNIFICANT)), _row(),
                _row(param_type=VALUE_TYPE, ci_lower_limit=str(UNIT_LO),
                     ci_upper_limit=str(UNIT_HI))]
    record = outcome_threshold(analyses)
    counted = record["n_tested"] + record["n_not_applied"] + record["n_unknown"]
    assert counted == record["n_analyses"]


def test_disagreement_is_flagged_when_analyses_differ():
    same = [_row(p_value=str(P_SIGNIFICANT)), _row(p_value=str(P_NOT_SIGNIFICANT))]
    differ = [_row(p_value=str(P_SIGNIFICANT)), _row()]
    assert outcome_threshold(same)["disagreed"] is False
    assert outcome_threshold(differ)["disagreed"] is True


# ---- the public resolver --------------------------------------------------
def test_the_resolver_follows_the_declared_precedence():
    # Structural: for every suffix of VERDICT_PRECEDENCE, resolving it must return that
    # suffix's first element. Derived from the tuple, so reordering the constant moves
    # the expectation with it rather than breaking the test.
    for index in range(len(VERDICT_PRECEDENCE)):
        suffix = VERDICT_PRECEDENCE[index:]
        assert resolve_verdicts(reversed(suffix)) == suffix[0]


def test_the_resolver_defaults_to_unknown_on_nothing():
    assert resolve_verdicts([]) == THRESHOLD_UNKNOWN


def test_the_resolver_is_idempotent_on_a_single_verdict():
    for verdict in SPONSOR_VERDICTS:
        assert resolve_verdicts([verdict]) == verdict


def test_the_outcome_roll_up_uses_the_resolver():
    # Pins that the two paths agree, so a script using the resolver directly and the
    # outcome roll-up cannot disagree about the same set of verdicts.
    analyses = [_row(), _row(param_type=VALUE_TYPE, ci_lower_limit=str(UNIT_LO),
                             ci_upper_limit=str(UNIT_HI)),
                _row(p_value=str(P_SIGNIFICANT))]
    record = outcome_threshold(analyses)
    direct = resolve_verdicts(analysis_threshold(a)[0] for a in analyses)
    assert record["verdict"] == direct


# ---- trial roll-up --------------------------------------------------------
def test_the_trial_roll_up_is_any_over_outcomes():
    outcomes = {
        "descriptive": [_row(param_type=VALUE_TYPE, ci_lower_limit=str(UNIT_LO),
                             ci_upper_limit=str(UNIT_HI))],
        "tested": [_row(p_value=str(P_SIGNIFICANT))],
    }
    assert trial_threshold(outcomes)["verdict"] == THRESHOLD_TESTED


def test_trial_verdict_counts_sum_to_the_outcome_count():
    outcomes = {
        "a": [_row(p_value=str(P_SIGNIFICANT))],
        "b": [_row(param_type=VALUE_TYPE, ci_lower_limit=str(UNIT_LO),
                   ci_upper_limit=str(UNIT_HI))],
        "c": [_row()],
    }
    record = trial_threshold(outcomes)
    assert sum(record["verdicts"].values()) == record["n_outcomes"]
    assert record["n_outcomes"] == len(outcomes)


def test_trial_reason_counts_sum_over_the_outcomes():
    outcomes = {"a": [_row(p_value=str(P_SIGNIFICANT)), _row()],
                "b": [_row(param_type=VALUE_TYPE, ci_lower_limit=str(UNIT_LO),
                           ci_upper_limit=str(UNIT_HI))]}
    record = trial_threshold(outcomes)
    expected = sum(len(rows) for rows in outcomes.values())
    assert sum(record["reasons"].values()) == expected


def test_a_trial_with_no_outcomes_is_unknown():
    assert trial_threshold({})["verdict"] == THRESHOLD_UNKNOWN


# ---- cells ----------------------------------------------------------------
def test_every_gate_and_sponsor_pair_has_a_cell():
    for gate in GATE_VERDICTS:
        for sponsor in SPONSOR_VERDICTS:
            assert cell_for(gate, sponsor) in CELLS


def test_the_decisive_cells_are_the_two_disagreements():
    assert cell_for(GATE_NOT_APPLICABLE, THRESHOLD_TESTED) == CELL_OVER_REFUSAL
    assert cell_for(GATE_APPLICABLE, THRESHOLD_NOT_APPLIED) == CELL_UNDER_REFUSAL
    assert set(DECISIVE_CELLS) == {CELL_OVER_REFUSAL, CELL_UNDER_REFUSAL}


def test_the_agreement_cells_are_the_two_agreements():
    assert cell_for(GATE_NOT_APPLICABLE, THRESHOLD_NOT_APPLIED) == CELL_BOTH_REFUSE
    assert cell_for(GATE_APPLICABLE, THRESHOLD_TESTED) == CELL_BOTH_ALLOW


def test_an_undeterminable_rule_is_never_a_disagreement():
    # Undeterminable is not a refusal (`refuses_estimate` is False), so pairing it with a
    # sponsor who tested must not be counted as the rule erring.
    for sponsor in SPONSOR_VERDICTS:
        assert cell_for(GATE_UNDETERMINABLE, sponsor) == CELL_RULE_UNREADABLE


def test_an_unknown_sponsor_is_never_a_disagreement():
    for gate in (GATE_APPLICABLE, GATE_NOT_APPLICABLE):
        assert cell_for(gate, THRESHOLD_UNKNOWN) == CELL_SPONSOR_UNREADABLE


def test_the_rule_unreadable_check_comes_first():
    # Both sides silent: precedence must be stated, and it is the rule's.
    assert cell_for(GATE_UNDETERMINABLE, THRESHOLD_UNKNOWN) == CELL_RULE_UNREADABLE


def test_every_gate_verdict_has_an_explicit_branch():
    # Structural: iterate the vocabulary rather than the three known names, so a fourth
    # gate verdict added to GATE_VERDICTS without a branch fails here instead of being
    # silently treated as "applicable".
    for gate in GATE_VERDICTS:
        for sponsor in SPONSOR_VERDICTS:
            assert cell_for(gate, sponsor) in CELLS


def test_cell_for_raises_on_an_unknown_verdict():
    for bad in (("not_a_gate", THRESHOLD_TESTED), (GATE_APPLICABLE, "not_a_verdict")):
        try:
            cell_for(*bad)
        except ValueError:
            continue
        raise AssertionError(f"cell_for{bad} should raise rather than default")


# ---- crosstab -------------------------------------------------------------
def _every_pair() -> list:
    return [(g, s) for g in GATE_VERDICTS for s in SPONSOR_VERDICTS]


def test_the_grid_covers_every_combination():
    table = crosstab([])
    assert set(table["grid"]) == {(g, s) for g in GATE_VERDICTS
                                  for s in SPONSOR_VERDICTS}


def test_grid_counts_sum_to_the_total():
    pairs = _every_pair() * 3
    table = crosstab(pairs)
    assert sum(table["grid"].values()) == table["total"] == len(pairs)


def test_named_cells_partition_the_grid():
    # The collapse is auditable: the named cells must account for every grid entry
    # exactly once, so the two representations can never drift apart.
    pairs = _every_pair() * 2
    table = crosstab(pairs)
    assert sum(table["cells"].values()) == table["total"]
    for (gate, sponsor), count in table["grid"].items():
        if count:
            assert table["cells"][cell_for(gate, sponsor)] >= count


def test_decisive_total_excludes_the_unreadable_cells():
    pairs = _every_pair()
    table = crosstab(pairs)
    unreadable = sum(table["cells"][cell] for cell in UNREADABLE_CELLS)
    assert decisive_total(table) == table["total"] - unreadable


def test_decisive_total_is_zero_when_no_side_decided():
    table = crosstab([(GATE_UNDETERMINABLE, THRESHOLD_UNKNOWN)] * 4)
    assert decisive_total(table) == 0


# ---- rates ----------------------------------------------------------------
def test_rates_are_none_rather_than_zero_when_nothing_was_decided():
    # A rate with no denominator is not a rate of zero, and 0.0 would print as a pass.
    table = crosstab([(GATE_UNDETERMINABLE, THRESHOLD_UNKNOWN)])
    assert over_refusal_rate(table) is None
    assert under_refusal_rate(table) is None


def test_over_refusal_rate_is_the_cell_over_the_decisive_total():
    pairs = _every_pair() * 5
    table = crosstab(pairs)
    expected = table["cells"][CELL_OVER_REFUSAL] / decisive_total(table)
    assert over_refusal_rate(table) == expected


def test_a_rule_that_never_errs_scores_zero_on_both_rates():
    pairs = ([(GATE_NOT_APPLICABLE, THRESHOLD_NOT_APPLIED)] * 7
             + [(GATE_APPLICABLE, THRESHOLD_TESTED)] * 11)
    table = crosstab(pairs)
    assert over_refusal_rate(table) == 0.0
    assert under_refusal_rate(table) == 0.0


def test_a_rule_that_always_errs_scores_one_across_the_two_rates():
    pairs = ([(GATE_NOT_APPLICABLE, THRESHOLD_TESTED)] * 5
             + [(GATE_APPLICABLE, THRESHOLD_NOT_APPLIED)] * 9)
    table = crosstab(pairs)
    assert over_refusal_rate(table) + under_refusal_rate(table) == 1.0


def test_the_four_decided_cells_rates_sum_to_one():
    pairs = _every_pair() * 3
    table = crosstab(pairs)
    total = sum(cell_rate(table, cell)
                for cell in DECISIVE_CELLS + AGREEMENT_CELLS)
    assert abs(total - 1.0) < 1e-12


def test_cell_rate_raises_on_an_unknown_cell():
    table = crosstab(_every_pair())
    try:
        cell_rate(table, "not_a_cell")
    except ValueError:
        return
    raise AssertionError("cell_rate should raise on an unregistered cell")


def test_refusal_precision_uses_the_refusal_denominator():
    over, both = 3, 12
    pairs = ([(GATE_NOT_APPLICABLE, THRESHOLD_TESTED)] * over
             + [(GATE_NOT_APPLICABLE, THRESHOLD_NOT_APPLIED)] * both
             + [(GATE_APPLICABLE, THRESHOLD_TESTED)] * 50)
    table = crosstab(pairs)
    assert refusal_precision(table) == both / (both + over)
    # and it is a DIFFERENT denominator from the over-refusal rate, which is the reason
    # both are reported
    assert over_refusal_rate(table) != 1.0 - refusal_precision(table)


def test_refusal_precision_is_none_when_the_rule_refused_nothing_decided():
    table = crosstab([(GATE_APPLICABLE, THRESHOLD_TESTED)] * 6)
    assert refusal_precision(table) is None


def test_false_refusal_share_is_the_complement_of_refusal_precision():
    pairs = _every_pair() * 4
    table = crosstab(pairs)
    assert abs(false_refusal_share(table) + refusal_precision(table) - 1.0) < 1e-12


def test_false_refusal_share_is_none_when_precision_is():
    table = crosstab([(GATE_APPLICABLE, THRESHOLD_TESTED)] * 3)
    assert false_refusal_share(table) is None


def test_allowance_precision_uses_the_allowance_denominator():
    under, both = 7, 21
    pairs = ([(GATE_APPLICABLE, THRESHOLD_NOT_APPLIED)] * under
             + [(GATE_APPLICABLE, THRESHOLD_TESTED)] * both
             + [(GATE_NOT_APPLICABLE, THRESHOLD_NOT_APPLIED)] * 13)
    table = crosstab(pairs)
    assert allowance_precision(table) == both / (both + under)


def test_allowance_precision_is_none_when_the_rule_allowed_nothing_decided():
    table = crosstab([(GATE_NOT_APPLICABLE, THRESHOLD_NOT_APPLIED)] * 4)
    assert allowance_precision(table) is None


def test_the_two_precisions_are_independent_denominators():
    # A rule can be excellent at allowing and terrible at refusing. If the two statistics
    # moved together, neither would localise anything.
    pairs = ([(GATE_APPLICABLE, THRESHOLD_TESTED)] * 100
             + [(GATE_NOT_APPLICABLE, THRESHOLD_TESTED)] * 90
             + [(GATE_NOT_APPLICABLE, THRESHOLD_NOT_APPLIED)] * 10)
    table = crosstab(pairs)
    assert allowance_precision(table) == 1.0
    assert refusal_precision(table) < allowance_precision(table)


def test_a_rule_that_refuses_less_scores_better_on_the_pooled_rate_only():
    # THE denominator argument, as a test. Two rules with IDENTICAL refusal precision:
    # the second simply refuses a tenth as often. The pooled over-refusal rate rewards
    # it; refusal precision correctly does not. This is why the stop rule reads the
    # precision.
    eager = crosstab([(GATE_NOT_APPLICABLE, THRESHOLD_TESTED)] * 80
                     + [(GATE_NOT_APPLICABLE, THRESHOLD_NOT_APPLIED)] * 20
                     + [(GATE_APPLICABLE, THRESHOLD_TESTED)] * 100)
    timid = crosstab([(GATE_NOT_APPLICABLE, THRESHOLD_TESTED)] * 8
                     + [(GATE_NOT_APPLICABLE, THRESHOLD_NOT_APPLIED)] * 2
                     + [(GATE_APPLICABLE, THRESHOLD_TESTED)] * 100)
    assert refusal_precision(eager) == refusal_precision(timid)
    assert over_refusal_rate(timid) < over_refusal_rate(eager)


# ---- the disagreement ratio ----------------------------------------------
def test_the_disagreement_ratio_is_the_two_cells_divided():
    over, under = 24, 3
    pairs = ([(GATE_NOT_APPLICABLE, THRESHOLD_TESTED)] * over
             + [(GATE_APPLICABLE, THRESHOLD_NOT_APPLIED)] * under)
    table = crosstab(pairs)
    assert disagreement_ratio(table) == over / under


def test_the_ratio_sees_an_imbalance_the_boolean_cannot():
    # The gap this function exists to close: heavily lopsided but neither cell empty, so
    # the established `one_sided` convention reports False and reads as reassurance.
    min_pair = 5
    over, under = min_pair * 16, min_pair * 2
    pairs = ([(GATE_NOT_APPLICABLE, THRESHOLD_TESTED)] * over
             + [(GATE_APPLICABLE, THRESHOLD_NOT_APPLIED)] * under)
    table = crosstab(pairs)
    assert one_directional(table, min_pair=min_pair) is False
    assert disagreement_ratio(table) == over / under
    assert disagreement_ratio(table) > 1.0


def test_the_ratio_is_none_rather_than_infinite_when_nothing_errs_the_other_way():
    table = crosstab([(GATE_NOT_APPLICABLE, THRESHOLD_TESTED)] * 9)
    assert disagreement_ratio(table) is None


def test_a_balanced_rule_has_a_ratio_of_one():
    n = 11
    pairs = ([(GATE_NOT_APPLICABLE, THRESHOLD_TESTED)] * n
             + [(GATE_APPLICABLE, THRESHOLD_NOT_APPLIED)] * n)
    assert disagreement_ratio(crosstab(pairs)) == 1.0


# ---- one-directionality ---------------------------------------------------
def test_one_directional_is_none_when_neither_cell_is_big_enough():
    min_pair = 5
    pairs = ([(GATE_NOT_APPLICABLE, THRESHOLD_TESTED)] * (min_pair - 1)
             + [(GATE_APPLICABLE, THRESHOLD_NOT_APPLIED)] * (min_pair - 1))
    assert one_directional(crosstab(pairs), min_pair=min_pair) is None


def test_one_directional_is_true_when_only_one_cell_is_populated():
    min_pair = 5
    pairs = [(GATE_NOT_APPLICABLE, THRESHOLD_TESTED)] * (min_pair * 4)
    assert one_directional(crosstab(pairs), min_pair=min_pair) is True


def test_one_directional_is_false_when_both_cells_are_populated():
    min_pair = 5
    pairs = ([(GATE_NOT_APPLICABLE, THRESHOLD_TESTED)] * (min_pair * 2)
             + [(GATE_APPLICABLE, THRESHOLD_NOT_APPLIED)] * (min_pair * 2))
    assert one_directional(crosstab(pairs), min_pair=min_pair) is False


def test_one_directionality_is_symmetric_in_the_two_cells():
    min_pair = 5
    over_only = [(GATE_NOT_APPLICABLE, THRESHOLD_TESTED)] * (min_pair * 3)
    under_only = [(GATE_APPLICABLE, THRESHOLD_NOT_APPLIED)] * (min_pair * 3)
    assert (one_directional(crosstab(over_only), min_pair=min_pair)
            == one_directional(crosstab(under_only), min_pair=min_pair))


# ---- selection bias -------------------------------------------------------
def test_proportional_posting_gives_a_ratio_of_one_everywhere():
    # Closed form: if every group posts at the same rate, no group is over-represented.
    population = {"phase1": 400, "pivotal": 200, "other": 100}
    rate = 4
    posted = {g: n // rate for g, n in population.items()}
    report = selection_report(posted, population)
    for group in population:
        assert abs(report["groups"][group]["ratio"] - 1.0) < 1e-12
        assert abs(report["groups"][group]["posting_rate"] - 1.0 / rate) < 1e-12


def test_the_representation_ratio_is_the_share_ratio():
    population = {"phase1": 1000, "pivotal": 100}
    posted = {"phase1": 50, "pivotal": 50}
    report = selection_report(posted, population)
    for group, row in report["groups"].items():
        assert abs(row["ratio"] - row["posted_share"] / row["population_share"]) < 1e-12


def test_over_and_under_representation_appear_on_the_right_sides_of_one():
    # The shape section 12.7 records: the group that posts more is over-represented.
    population = {"phase1": 1000, "pivotal": 1000}
    posted = {"phase1": 50, "pivotal": 500}
    report = selection_report(posted, population)
    assert report["groups"]["pivotal"]["ratio"] > 1.0
    assert report["groups"]["phase1"]["ratio"] < 1.0


def test_shares_sum_to_one_across_the_groups():
    population = {"a": 300, "b": 500, "c": 200}
    posted = {"a": 30, "b": 100, "c": 10}
    report = selection_report(posted, population)
    for key in ("posted_share", "population_share"):
        assert abs(sum(row[key] for row in report["groups"].values()) - 1.0) < 1e-12


def test_a_group_that_posted_nothing_has_a_rate_of_zero_not_none():
    # Posted nothing WITH a population is a real rate of zero; that is different from
    # having no population at all, which is the None case below.
    report = selection_report({"a": 0}, {"a": 50})
    assert report["groups"]["a"]["posting_rate"] == 0.0


def test_a_group_with_no_population_has_no_rate():
    report = selection_report({"a": 3}, {"a": 0, "b": 10})
    assert report["groups"]["a"]["posting_rate"] is None
    assert report["groups"]["a"]["ratio"] is None


def test_an_empty_report_has_no_overall_rate():
    report = selection_report({}, {})
    assert report["posting_rate"] is None
    assert report["groups"] == {}


def test_the_overall_posting_rate_is_the_pooled_one():
    population = {"a": 300, "b": 700}
    posted = {"a": 30, "b": 140}
    report = selection_report(posted, population)
    expected = sum(posted.values()) / sum(population.values())
    assert abs(report["posting_rate"] - expected) < 1e-12


def test_groups_absent_from_one_side_still_appear():
    # A group that exists in the population and posted nothing must not vanish from the
    # table: a stratum with no coverage is the finding, not an omission.
    report = selection_report({"a": 5}, {"a": 50, "b": 50})
    assert set(report["groups"]) == {"a", "b"}
    assert report["groups"]["b"]["posted"] == 0



# ---- the geometric ratio is unknown, not tested ---------------------------
def test_a_geometric_ratio_is_unknown_not_tested():
    # The bug this branch fixes. These rows previously fell through to
    # REASON_CONTRAST_INTERVAL and were counted as the sponsor having threshold-tested the
    # endpoint, which inflated the over-refusal cell by 714 of 2,359 and made the keyword
    # rule look wrong for refusing endpoints the reference could not adjudicate either.
    verdict, reason = analysis_threshold(
        _row(param_type=GEOMETRIC_TYPE, ci_lower_limit=str(UNIT_LO),
             ci_upper_limit=str(UNIT_HI)))
    assert (verdict, reason) == (THRESHOLD_UNKNOWN, REASON_GEOMETRIC_RATIO)
    assert verdict != THRESHOLD_TESTED
    assert verdict != THRESHOLD_NOT_APPLIED


def test_the_reference_agrees_with_the_label_pipeline_on_what_is_geometric():
    # Coupling checked rather than assumed: the reference must send to unknown exactly the
    # rows endpoint_label refuses as geometric, or the two modules disagree about the same
    # statistic and the cross-tab compares a rule against a reference built on a different
    # reading of the data.
    for param_type in ("Ratio of geometric LS means", "Geometric mean ratio",
                       "GMC ratio", "Least Square Mean Ratio", RATIO_TYPE,
                       DIFFERENCE_TYPE, VALUE_TYPE):
        row = _row(param_type=param_type, ci_lower_limit=str(UNIT_LO),
                   ci_upper_limit=str(UNIT_HI))
        if is_geometric_ratio(param_type):
            assert analysis_threshold(row)[1] == REASON_GEOMETRIC_RATIO, param_type
        else:
            assert analysis_threshold(row)[1] != REASON_GEOMETRIC_RATIO, param_type


def test_a_posted_p_value_outranks_the_geometric_reading():
    # A sponsor who posted a p-value stated their own verdict against an alpha, whatever
    # statistic the interval used.
    verdict, reason = analysis_threshold(
        _row(param_type=GEOMETRIC_TYPE, p_value=str(P_SIGNIFICANT),
             ci_lower_limit=str(UNIT_LO), ci_upper_limit=str(UNIT_HI)))
    assert (verdict, reason) == (THRESHOLD_TESTED, REASON_P_VALUE)


def test_percent_scaled_outranks_the_geometric_reading():
    # Both send to unknown, so the verdict cannot distinguish them; the REASON must, so
    # the two refusals stay separately countable.
    verdict, reason = analysis_threshold(
        _row(param_type="Ratio of adjusted geometric means",
             ci_lower_limit=str(PERCENT_LO), ci_upper_limit=str(PERCENT_HI)))
    assert (verdict, reason) == (THRESHOLD_UNKNOWN, REASON_PERCENT_SCALED)