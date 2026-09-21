"""Tests for the endpoint-met label engine. Plain asserts so tests/_run_stdlib.py works."""
from __future__ import annotations

from trial_pos.services.endpoint_label import (
    TIER_A, TIER_B, TIER_C, TIER_D, TIER_DOC, TIER_ORDER, LABEL_DERIVED_FIELDS,
    aggregate_outcome, aggregate_trial, analysis_design, broad_label, classify_analysis,
    classify_stop_reason, label_row, met_from_ci, met_from_p, null_value_for,
    parse_p_value,
    CONTRAST_DIFFERENCE, CONTRAST_RATIO, DEFAULT_RATIO_SCALE_FLOOR, NA_REASON_DOC,
    NA_REASON_NONE, NA_REASON_PERCENT_SCALED, NULL_FOR_CONTRAST,
    RATIO_SCALE_PERCENT, RATIO_SCALE_UNIT, REFUSAL_PERCENT_SCALED_RATIO,
    contrast_family, is_percent_scaled_ratio, ratio_scale, resolve_null_value,
    TIER_E, DEFAULT_ALPHA, CI_PERCENT_PROPORTION_MAX, COVERAGE_LOOSER, COVERAGE_MATCHED,
    COVERAGE_TIGHTER, COVERAGE_UNKNOWN, DEFAULT_COVERAGE_TOLERANCE_POINTS,
    DESIGN_DETAIL_EQUIVALENCE, DESIGN_DETAIL_NI_OR_EQUIVALENCE,
    DESIGN_DETAIL_NON_INFERIORITY, DESIGN_DETAIL_SUPERIORITY, DESIGN_DETAIL_UNSTATED,
    DESIGN_NI, HEADLINE_TIERS, NA_REASON_COVERAGE_MISMATCH, NA_REASON_FOR_REFUSAL,
    NA_REASON_NI_DESIGN, REFUSAL_COVERAGE, REFUSAL_COVERAGE_MISMATCH, REFUSAL_KINDS,
    REFUSAL_NI_DESIGN, REFUSAL_NI_MARGIN_UNAVAILABLE, REFUSAL_PERCENT_SCALED,
    analysis_design_detail, coverage_band, coverage_supports, normalize_ci_percent,
    refusal_kind, required_ci_percent,
)


# ---- p-value parsing -------------------------------------------------------
def test_parse_plain_p_value():
    assert parse_p_value("0.03") == ("=", 0.03)


def test_parse_modifier_column_wins():
    assert parse_p_value("0.001", "<") == ("<", 0.001)


def test_parse_inline_comparator():
    assert parse_p_value("<0.001") == ("<", 0.001)


def test_parse_rejects_out_of_range():
    # a value above 1 is not a p-value; refuse rather than silently mislabel
    assert parse_p_value("12.4") == (None, None)


def test_parse_blank_and_na():
    assert parse_p_value("") == (None, None)
    assert parse_p_value("NA") == (None, None)
    assert parse_p_value(None) == (None, None)


# ---- p-value -> met -------------------------------------------------------
def test_met_equality_both_sides():
    assert met_from_p("=", 0.01)[0] == 1
    assert met_from_p("=", 0.20)[0] == 0


def test_met_bounded_below_alpha_is_decidable():
    assert met_from_p("<", 0.001)[0] == 1


def test_met_bounded_above_alpha_is_indeterminate():
    # "p < 0.5" tells us nothing at alpha 0.05 and must not be forced either way
    assert met_from_p("<", 0.5)[0] is None


def test_met_greater_than_alpha_is_not_met():
    assert met_from_p(">", 0.05)[0] == 0
    assert met_from_p(">", 0.01)[0] is None


def test_alpha_is_a_parameter():
    assert met_from_p("=", 0.03, alpha=0.01)[0] == 0
    assert met_from_p("=", 0.03, alpha=0.05)[0] == 1


# ---- interval fallback ----------------------------------------------------
def test_null_value_by_param_type():
    assert null_value_for("Hazard Ratio") == 1.0
    assert null_value_for("Mean Difference") == 0.0
    assert null_value_for("Mean") is None          # single-arm mean has no null


def test_contrast_wins_over_the_single_arm_guard():
    # REGRESSION: a single-arm-first ordering blocked these on "percentage",
    # "proportion", "response" and "mean", pushing 95 analysis rows from decided to
    # undecided. A string that names a contrast IS a contrast.
    assert null_value_for("Difference in Percentage") == 0.0
    assert null_value_for("Proportion difference") == 0.0
    assert null_value_for("Difference in response rate") == 0.0
    assert null_value_for("Geometric Mean Ratio") == 1.0
    assert null_value_for("Estimated treatment differences") == 0.0
    assert null_value_for("Least Squares Mean Differences") == 0.0
    assert null_value_for("Adjusted difference in proportion") == 0.0
    assert null_value_for("Difference in Percentage vs Placebo") == 0.0
    assert null_value_for("% diff Geometric Least Squared Mean") == 0.0


def test_ratio_expressed_as_a_percentage_is_refused():
    # a GMR(%) interval is centred on 100, not 1; the field does not say which, and
    # guessing wrong is wrong by 99
    assert null_value_for("Geometric Least Squares Mean Ratio (%)") is None


def test_comparative_param_types_seen_in_the_live_data():
    # added after the gap audit surfaced these verbatim in ctgov.outcome_analyses
    assert null_value_for("Cox Proportional Hazard") == 1.0
    assert null_value_for("Unstratified HR") == 1.0
    assert null_value_for("Stratified HR w/time-dependent covariate") == 1.0
    assert null_value_for("Treatment Contrast") == 0.0
    assert null_value_for("Percent reduction over Placebo") == 0.0


def test_single_arm_quantities_refuse_a_null():
    # an ORR of 40% with CI 25-55 "excludes zero" trivially; labelling that as endpoint-met
    # would manufacture positives out of descriptive statistics
    for pt in ("objective response rate", "Objective Response Rate (ORR) (percent)",
               "Percentage of Participants", "Proportion responding", "6-month PFS",
               "1-year OS", "Retention Rate", "Point Estimate", "probability",
               "Least Squares (LS) Mean", "Effect size", "Percentage reduction"):
        assert null_value_for(pt) is None, pt


def test_qualified_percentage_and_median_still_resolve():
    # the single-arm guard must not swallow genuine contrasts
    assert null_value_for("Percentage difference") == 0.0
    assert null_value_for("Median Difference (Net)") == 0.0


def test_vaccine_effectiveness_stays_undecided():
    # reported both as a ratio (null 1) and a percentage (null 0); the field never says
    assert null_value_for("Relative Vaccine Effectiveness") is None


def test_ci_excludes_and_contains_null():
    assert met_from_ci(1.2, 2.4, 1.0)[0] == 1
    assert met_from_ci(0.8, 1.4, 1.0)[0] == 0
    assert met_from_ci(-3.0, -0.5, 0.0)[0] == 1    # excluding below counts too


def test_ci_reversed_bounds_are_ordered():
    assert met_from_ci(2.4, 1.2, 1.0)[0] == 1


def test_ci_missing_null_is_indeterminate():
    assert met_from_ci(1.2, 2.4, None)[0] is None


# ---- design detection -----------------------------------------------------
def test_design_detection():
    assert analysis_design("Superiority") == "superiority"
    assert analysis_design("Non-Inferiority") == "ni"
    assert analysis_design("Equivalence") == "ni"
    assert analysis_design("") == "unstated"


def test_combined_value_mentioning_ni_is_ni():
    # AACT ships combined values; a value naming non-inferiority must not read as
    # plain superiority
    assert analysis_design("Non-Inferiority or Equivalence") == "ni"


def test_uppercase_underscore_enums_from_the_live_server():
    # observed verbatim in ctgov.outcome_analyses: uppercase, underscored
    assert analysis_design("SUPERIORITY_OR_OTHER") == "superiority"
    assert analysis_design("SUPERIORITY") == "superiority"
    assert analysis_design("NON_INFERIORITY") == "ni"
    assert analysis_design("NON_INFERIORITY_OR_EQUIVALENCE") == "ni"
    assert analysis_design("EQUIVALENCE") == "ni"
    assert analysis_design("OTHER") == "unstated"


def test_underscored_ni_lands_in_tier_b_not_tier_a():
    # the bug this guards: NON_INFERIORITY missing every pattern and filing as tier A
    tier, met, _ = classify_analysis({"non_inferiority_type": "NON_INFERIORITY",
                                      "p_value": 0.001, "p_value_modifier": "<"})
    assert (tier, met) == (TIER_B, 1)


# ---- tier assignment ------------------------------------------------------
def test_superiority_p_is_tier_a():
    tier, met, _ = classify_analysis({"non_inferiority_type": "Superiority",
                                      "p_value": "0.02"})
    assert (tier, met) == (TIER_A, 1)


def test_noninferiority_p_is_tier_b():
    tier, met, _ = classify_analysis({"non_inferiority_type": "Non-Inferiority",
                                      "p_value": "0.001"})
    assert (tier, met) == (TIER_B, 1)


def test_interval_only_is_tier_c():
    tier, met, _ = classify_analysis({"param_type": "Hazard Ratio", "p_value": "",
                                      "ci_lower_limit": "1.10", "ci_upper_limit": "1.90",
                                      "ci_percent": "95.0"})
    assert (tier, met) == (TIER_C, 1)


def test_single_arm_descriptive_is_tier_d():
    tier, met, _ = classify_analysis({"param_type": "Mean", "p_value": "",
                                      "ci_lower_limit": "12", "ci_upper_limit": "18"})
    assert (tier, met) == (TIER_D, None)


def test_p_value_takes_precedence_over_interval():
    tier, met, _ = classify_analysis({"non_inferiority_type": "Superiority",
                                      "p_value": "0.30", "param_type": "Hazard Ratio",
                                      "ci_lower_limit": "1.2", "ci_upper_limit": "1.9"})
    assert (tier, met) == (TIER_A, 0)


def test_unstated_design_records_the_assumption():
    tier, met, reason = classify_analysis({"p_value": "0.01"})
    assert tier == TIER_A and met == 1
    assert "assumed superiority" in reason


# ---- outcome aggregation --------------------------------------------------
def test_outcome_takes_strongest_tier_then_any_met():
    v = aggregate_outcome([
        {"non_inferiority_type": "Superiority", "p_value": "0.40"},
        {"non_inferiority_type": "Superiority", "p_value": "0.01"},
        {"param_type": "Hazard Ratio", "ci_lower_limit": "0.9", "ci_upper_limit": "1.1"},
    ])
    assert v["tier"] == TIER_A and v["met"] == 1 and v["n_analyses"] == 3


def test_outcome_with_no_decidable_analysis():
    v = aggregate_outcome([{"param_type": "Mean", "p_value": ""}])
    assert v["tier"] == TIER_D and v["met"] is None and v["n_decidable"] == 0


# ---- trial aggregation ----------------------------------------------------
def test_multi_endpoint_family_all_four_readings():
    agg = aggregate_trial({
        "o1": [{"non_inferiority_type": "Superiority", "p_value": "0.01"}],
        "o2": [{"non_inferiority_type": "Superiority", "p_value": "0.60"}],
    })
    assert agg["n_primary_outcomes"] == 2
    assert agg["n_primary_analyzed"] == 2
    assert agg["n_primary_met"] == 1
    assert abs(agg["frac_primary_met"] - 0.5) < 1e-9
    assert agg["any_primary_met"] == 1
    assert agg["all_primary_met"] == 0


def test_tier_min_reflects_the_weakest_counted_outcome():
    agg = aggregate_trial({
        "o1": [{"non_inferiority_type": "Superiority", "p_value": "0.01"}],
        "o2": [{"param_type": "Risk Ratio", "ci_lower_limit": "1.2",
                "ci_upper_limit": "1.8", "ci_percent": "95.0"}],
    })
    assert agg["tier_max"] == TIER_A
    assert agg["tier_min"] == TIER_C          # headline filters must exclude this trial


def test_trial_with_no_analyses_is_unknown_not_zero():
    agg = aggregate_trial({"o1": [{"param_type": "Mean", "p_value": ""}]})
    assert agg["any_primary_met"] is None and agg["all_primary_met"] is None
    assert agg["frac_primary_met"] is None


# ---- why_stopped ----------------------------------------------------------
def test_futility_phrasings_from_the_live_data():
    # each of these was a CANDIDATE MISS in the gap audit: rigid literals failed because
    # real text inserts words between qualifier and object
    for text in ("Failed primary endpoint",
                 "The study did not achieve the statistical success to continue.",
                 "Lack of drug efficacy",
                 "Insufficient response rate",
                 "data analysis showed insufficient drug efficacy",
                 "interim analysis found the study drug to be ineffective",
                 "Preliminary terminated due to inefficacy",
                 "The trial did not show any positive effects.",
                 "low response rate, no evidence of PFS or OS improved.",
                 "Terminated due to failure to meet the primary efficacy endpoint",
                 "the observed reduced efficacy of CellCept compared to placebo",
                 "early termination for discouraging results",
                 "no response seen in patients"):
        assert classify_stop_reason(text) == "futility", text


def test_efficacy_success_phrasing_from_the_live_data():
    assert classify_stop_reason(
        "Interim analysis found study had achieved primary objective") == "efficacy_success"


def test_external_evidence_is_its_own_class():
    # this trial's endpoint was never assessed, so it is not this trial's result
    assert classify_stop_reason(
        "Interim results of another trial showed inferior activity of treatment") \
        == "external_evidence"
    assert classify_stop_reason("data from a similar did not show efficacy.") \
        == "external_evidence"


def test_benefit_risk_is_its_own_class():
    # mixes efficacy and safety; cannot be attributed to either
    for text in ("the overall benefit to risk profile of ocrelizumab was not favorable",
                 "Due to an unfavorable benefit/risk ratio.",
                 "The benefit/ risk profile does not support continuation of this study."):
        assert classify_stop_reason(text) == "benefit_risk", text


def test_negated_span_is_removed_not_the_whole_field():
    # a document-wide veto threw away genuine futility whenever a later clause
    # disclaimed safety; deleting just the negated span fixes it
    for text in ("Lack of efficacy of the drug; no safety concern",
                 "The decision to terminate was completely related to efficacy and "
                 "there were no safety concerns.",
                 "An interim analysis revealed the study was unlikely to attain a "
                 "positive outcome for the efficacy analysis. No safety concerns "
                 "were detected."):
        assert classify_stop_reason(text) == "futility", text


def test_removal_does_not_disarm_the_guard():
    # where the ONLY efficacy mention sits inside the negated span, the veto must hold
    assert classify_stop_reason("Sponsor decision, no safety or efficacy concerns") \
        == "business"
    assert classify_stop_reason("Insufficient enrollment, no safety or efficacy "
                                "concerns") == "operational"
    assert classify_stop_reason("The study was terminated for inability to enroll "
                                "patients. There were no efficacy/safety concerns.") \
        == "operational"


def test_disclaimers_keep_their_object_attached():
    # REGRESSION: splitting on "and" tore "No safety and/or efficacy concerns" into
    # "No safety" plus a fragment; the orphaned word matched the safety family and
    # misfiled five business-reason terminations. The span is deleted whole instead.
    for text in ("Study terminated on 7 April 2015 for business reasons. No safety "
                 "and/or efficacy concerns contributed to the termination of the study",
                 "This study was closed to enrollment due to business reasons. "
                 "Premature closure was not prompted by any safety or efficacy "
                 "concerns.",
                 "Company decision to discontinue the AVE1642 development program, "
                 "not due to any safety or efficacy concerns",
                 "The study was terminated early for business reasons, and not due to "
                 "concerns regarding safety or lack of efficacy."):
        assert classify_stop_reason(text) == "business", text


def test_guard_needs_a_negation_not_just_the_word_concerns():
    # a genuine safety stop that happens to contain "concerns" must survive
    assert classify_stop_reason("IDMC recommendation for safety concerns") == "safety"
    assert classify_stop_reason("DSMB stopped study because placebo arm had more "
                                "adverse events") == "safety"


def test_no_need_and_no_longer_necessary_are_not_safety_stops():
    assert classify_stop_reason("The study was terminated since there was no need for "
                                "further safety or efficacy data to be collected.") \
        == "other"


def test_futility_qualifiers_match_as_prefixes_with_slack():
    # "lack of A CLINICAL benefit" needs three words of slack; "insufficiently" must
    # match the "insufficien" prefix
    assert classify_stop_reason("Accrual was terminated for lack of a clinical "
                                "benefit.") == "futility"
    assert classify_stop_reason("pharmaceutical company closed study because the "
                                "treatment was not effective") == "futility"
    assert classify_stop_reason("aml assessment finds treatment failure in all "
                                "evaluable patients") == "futility"


def test_external_evidence_when_the_other_study_is_named_obliquely():
    assert classify_stop_reason(
        "drug not meeting primary endpoint in the main study T-Force GOLD") \
        == "external_evidence"
    assert classify_stop_reason(
        "The interim analysis of another associated study is not very effective") \
        == "external_evidence"


def test_benefit_risk_accepts_a_colon_separator():
    assert classify_stop_reason("Unfavourable benefit:risk") == "benefit_risk"


def test_negation_guard_prevents_inverted_labels():
    # flexible patterns would otherwise read "no ... efficacy" as futility
    assert classify_stop_reason("Sponsor decision, no safety or efficacy concerns") \
        == "business"
    assert classify_stop_reason("Termination was not due to lack of efficacy") == "other"
    assert classify_stop_reason("no safety concerns; terminated for slow enrollment") \
        == "operational"


def test_guard_does_not_disarm_genuine_safety():
    assert classify_stop_reason("Unacceptable toxicity") == "safety"
    assert classify_stop_reason("side effect profile did not match expectations") \
        == "safety"


def test_new_classes_do_not_feed_the_broad_label():
    # recorded, then deliberately excluded -- keeps the broad negative class interpretable
    assert broad_label(None, "external_evidence") == (None, "unknown")
    assert broad_label(None, "benefit_risk") == (None, "unknown")


def test_stop_reason_families():
    assert classify_stop_reason("Terminated due to lack of efficacy") == "futility"
    assert classify_stop_reason("Slow enrollment") == "operational"
    assert classify_stop_reason("Business decision") == "business"
    assert classify_stop_reason("Unacceptable toxicity") == "safety"
    assert classify_stop_reason("") == "none"


def test_efficacy_stop_beats_futility_vocabulary():
    assert classify_stop_reason("Stopped early: met the primary endpoint at interim") \
        == "efficacy_success"


def test_unmatched_text_is_other_never_futility():
    # a false futility positive corrupts the target, so the default must be conservative
    assert classify_stop_reason("Study closed per DSMB recommendation") == "other"


def test_futility_precedence_over_operational():
    assert classify_stop_reason("Terminated for futility; enrollment also slow") \
        == "futility"


# ---- broad label ----------------------------------------------------------
def test_analysis_always_wins_over_stop_reason():
    assert broad_label(1, "futility") == (1, "analysis")


def test_futility_becomes_negative_only_without_an_analysis():
    assert broad_label(None, "futility") == (0, "futility_stop")


def test_efficacy_stop_becomes_positive():
    assert broad_label(None, "efficacy_success") == (1, "efficacy_stop")


def test_safety_stop_is_opt_in():
    assert broad_label(None, "safety") == (None, "unknown")
    assert broad_label(None, "safety", include_safety=True) == (0, "safety_stop")


def test_operational_stop_stays_unknown():
    assert broad_label(None, "operational") == (None, "unknown")


# ---- end-to-end row ------------------------------------------------------
def test_label_row_strict_and_broad_agree_when_posted():
    rec = label_row({"nct_id": "NCT001", "why_stopped": ""},
                    {"o1": [{"non_inferiority_type": "Superiority", "p_value": "0.02"}]})
    assert rec["endpoint_met_strict"] == 1
    assert rec["endpoint_met_broad"] == 1
    assert rec["label_source_broad"] == "analysis"


def test_label_row_broad_recovers_a_futility_stop():
    rec = label_row({"nct_id": "NCT002", "why_stopped": "lack of efficacy at interim"},
                    {})
    assert rec["endpoint_met_strict"] is None
    assert rec["endpoint_met_broad"] == 0
    assert rec["why_stopped_class"] == "futility"


def test_label_row_single_arm_stays_unknown_in_both_variants():
    rec = label_row({"nct_id": "NCT003", "why_stopped": ""},
                    {"o1": [{"param_type": "Mean", "p_value": ""}]})
    assert rec["endpoint_met_strict"] is None
    assert rec["endpoint_met_broad"] is None
    assert rec["tier_min"] == TIER_D


# ---- documentation invariants --------------------------------------------
def test_every_tier_is_documented():
    assert set(TIER_DOC) == set(TIER_ORDER)
    assert all(TIER_DOC[t].strip() for t in TIER_ORDER)


def test_label_inputs_are_declared_so_features_can_assert_against_them():
    for field in ("why_stopped", "overall_status", "termination_score",
                  "safety_termination"):
        assert field in LABEL_DERIVED_FIELDS

# ===========================================================================
# RATIO SCALE -- the percent-scaled-ratio refusal
# ===========================================================================
# Regression cover for a live bug: 480 primary analysis rows across 130 trials named
# their scale ("Ratio ... x 100") and a further 1,868 rows across 357 trials did not
# ('Geometric mean ratio'), and all of them were tested against null=1, which an
# interval on a percentage scale cannot contain. Measured against sponsor-posted
# p-values the rule scored kappa 0.000 with 100% one-directional disagreement.
#
# Expected values below are derived from DEFAULT_RATIO_SCALE_FLOOR and NULL_FOR_CONTRAST
# rather than written as literals, so moving the flag cannot leave a test asserting the
# old threshold.
def test_contrast_family_names_the_shape_not_the_scale():
    assert contrast_family("Hazard Ratio") == CONTRAST_RATIO
    assert contrast_family("Mean Difference") == CONTRAST_DIFFERENCE
    assert contrast_family("Mean") is None                  # single-arm: no contrast


def test_null_value_for_cannot_drift_from_contrast_family():
    # the two must stay consistent by construction, not by coincidence: null_value_for is
    # defined THROUGH contrast_family, and this pins that relationship
    for param_type in ("Hazard Ratio", "Mean Difference", "Geometric Mean Ratio",
                       "Proportion difference", "Mean", "Odds Ratio (OR)", ""):
        family = contrast_family(param_type)
        expected = NULL_FOR_CONTRAST[family] if family is not None else None
        assert null_value_for(param_type) == expected, param_type


def test_ratio_scale_reads_the_interval_not_the_name():
    floor = DEFAULT_RATIO_SCALE_FLOOR
    assert ratio_scale(floor / 10.0, floor / 5.0) == RATIO_SCALE_UNIT
    assert ratio_scale(floor, floor * 2) == RATIO_SCALE_PERCENT
    # the floor is INCLUSIVE, and just below it is not percent
    assert ratio_scale(floor - 0.01, floor * 2) == RATIO_SCALE_UNIT


def test_ratio_scale_is_order_independent():
    floor = DEFAULT_RATIO_SCALE_FLOOR
    assert ratio_scale(floor * 3, floor * 2) == ratio_scale(floor * 2, floor * 3)


def test_ratio_scale_without_an_interval_is_none_not_a_refusal():
    # no interval is already tier D on its own ground; it is not a scale verdict
    assert ratio_scale(None, None) is None
    assert ratio_scale("1.2", "") is None


def test_ratio_scale_honours_a_non_default_floor():
    # the flag has to actually move the boundary, or it is decoration
    lo, hi = DEFAULT_RATIO_SCALE_FLOOR * 2, DEFAULT_RATIO_SCALE_FLOOR * 3
    assert ratio_scale(lo, hi) == RATIO_SCALE_PERCENT
    assert ratio_scale(lo, hi, floor=hi * 2) == RATIO_SCALE_UNIT


def test_percent_scaled_ratio_detected_only_for_ratios():
    floor = DEFAULT_RATIO_SCALE_FLOOR
    assert is_percent_scaled_ratio("Geometric mean ratio", floor * 8.5, floor * 10.4)
    # a unit-scale ratio is not refused
    assert not is_percent_scaled_ratio("Hazard Ratio", 1.1, 1.9)
    # a DIFFERENCE with large bounds is a large difference, not a percent-scaled ratio.
    # The floor must never leak across contrast families.
    assert not is_percent_scaled_ratio("Mean Difference", floor * 8, floor * 12)


def test_resolve_null_value_refuses_percent_scaled_ratio_by_name():
    floor = DEFAULT_RATIO_SCALE_FLOOR
    null, reason = resolve_null_value("Geometric mean ratio", floor * 8.5, floor * 10.4)
    assert null is None
    assert reason == REFUSAL_PERCENT_SCALED_RATIO


def test_resolve_null_value_agrees_with_null_value_for_on_unit_scale():
    # where the scale is not in question, the row-level answer must equal the name-level
    # one, or the two code paths have diverged
    for param_type, lo, hi in (("Hazard Ratio", 1.1, 1.9),
                               ("Mean Difference", -2.0, 5.0)):
        assert resolve_null_value(param_type, lo, hi)[0] == null_value_for(param_type)


# ---- the regression, at the tier boundary ---------------------------------
def test_percent_scaled_ratio_interval_is_tier_d_not_a_positive():
    # the exact live shape: a bioequivalence interval of [85.08, 104.21] read as met
    tier, met, reason = classify_analysis(
        {"param_type": "Ratio of the T/R geometric mean x 100", "p_value": "",
         "ci_lower_limit": "85.08", "ci_upper_limit": "104.21"})
    assert (tier, met) == (TIER_D, None)
    assert REFUSAL_PERCENT_SCALED_RATIO in reason      # the refusal is NAMED, not silent


def test_unnamed_percent_scaled_ratio_also_refused():
    # 1,868 rows say only this, so a param_type regex could never reach them
    tier, met, _ = classify_analysis(
        {"param_type": "Geometric mean ratio", "p_value": "",
         "ci_lower_limit": "96.2", "ci_upper_limit": "103.0"})
    assert (tier, met) == (TIER_D, None)


def test_unit_scaled_ratio_interval_still_tier_c():
    # the refusal must not cost the 7,518 rows where the rule scores kappa 0.849
    tier, met, _ = classify_analysis({"param_type": "Geometric Mean Ratio", "p_value": "",
                                      "ci_lower_limit": "1.10", "ci_upper_limit": "1.90",
                                      "ci_percent": "95.0"})
    assert (tier, met) == (TIER_C, 1)


def test_large_difference_interval_still_tier_c():
    tier, met, _ = classify_analysis({"param_type": "Mean Difference", "p_value": "",
                                      "ci_lower_limit": "120", "ci_upper_limit": "180",
                                      "ci_percent": "95.0"})
    assert (tier, met) == (TIER_C, 1)


def test_p_value_precedence_survives_the_refusal():
    # a percent-scaled ratio row that ALSO posts a p-value is still decided on the
    # p-value: the scale question never arises
    tier, met, _ = classify_analysis(
        {"non_inferiority_type": "Superiority", "p_value": "0.30",
         "param_type": "Geometric mean ratio",
         "ci_lower_limit": "96.2", "ci_upper_limit": "103.0"})
    assert (tier, met) == (TIER_A, 0)


# ---- the refusals are counted, not buried in tier D -----------------------
def test_outcome_counts_scale_refusals():
    floor = DEFAULT_RATIO_SCALE_FLOOR
    v = aggregate_outcome([
        {"param_type": "Geometric mean ratio", "ci_lower_limit": floor * 9.6,
         "ci_upper_limit": floor * 10.3},
        {"param_type": "Hazard Ratio", "ci_lower_limit": 1.2, "ci_upper_limit": 1.8},
    ])
    assert v["n_scale_refused"] == 1
    assert v["n_analyses"] == 2


def test_trial_with_only_refused_rows_states_why_rather_than_blank():
    floor = DEFAULT_RATIO_SCALE_FLOOR
    agg = aggregate_trial({"o1": [
        {"param_type": "Geometric mean ratio", "ci_lower_limit": floor * 8.5,
         "ci_upper_limit": floor * 10.4}]})
    assert agg["any_primary_met"] is None                 # still unknown, not zero
    assert agg["n_analyses_scale_refused"] == 1
    assert agg["endpoint_na_reason"] == NA_REASON_PERCENT_SCALED
    assert NA_REASON_PERCENT_SCALED in NA_REASON_DOC      # the reason is documented


def test_na_reason_empty_when_a_decidable_analysis_exists():
    # a refused row alongside a real verdict is not a not-applicable trial
    floor = DEFAULT_RATIO_SCALE_FLOOR
    agg = aggregate_trial({
        "o1": [{"non_inferiority_type": "Superiority", "p_value": "0.01"}],
        "o2": [{"param_type": "Geometric mean ratio", "ci_lower_limit": floor * 9.6,
                "ci_upper_limit": floor * 10.3}],
    })
    assert agg["endpoint_na_reason"] == NA_REASON_NONE
    assert agg["n_analyses_scale_refused"] == 1


def test_na_reason_empty_for_an_ordinary_unlabelled_trial():
    # no refused rows at all: the trial is unknown, which is a different claim
    agg = aggregate_trial({"o1": [{"param_type": "Mean", "p_value": ""}]})
    assert agg["endpoint_na_reason"] == NA_REASON_NONE
    assert agg["n_analyses_scale_refused"] == 0


def test_label_row_carries_the_threshold_it_used():
    # same discipline as alpha: an offline re-derive with a different floor must be
    # detectable from the row rather than inferred
    floor = DEFAULT_RATIO_SCALE_FLOOR * 2
    rec = label_row({"nct_id": "NCT1"}, {"o1": [{"p_value": "0.01"}]},
                    ratio_scale_floor=floor)
    assert rec["ratio_scale_floor_used"] == floor


def test_floor_change_flips_the_verdict_end_to_end():
    # structural: raising the floor above the interval makes the same row unit-scaled
    # again, which proves the flag reaches the label and is not read from a constant
    row = {"param_type": "Geometric mean ratio", "p_value": "",
           "ci_lower_limit": "85.08", "ci_upper_limit": "104.21", "ci_percent": "95.0"}
    assert classify_analysis(row)[0] == TIER_D
    assert classify_analysis(row, ratio_scale_floor=200.0)[0] == TIER_C


# ===========================================================================
# CI COVERAGE -- an interval test is the alpha test only at matching coverage
# ===========================================================================
# Second bug of the same family as the ratio scale: `analysis_ci_percent` sits in the dump
# and the engine never read it. Measured on the p-value validation set, the error
# direction FLIPS with the band -- tighter intervals under-call (2 over-calls in 555),
# looser ones over-call (151 over, 45 under) -- which is what licenses the one-way
# implication rule rather than a blanket refusal.
def test_required_coverage_is_derived_from_alpha_not_hardcoded():
    # the relation, not the number: a two-sided interval at 100*(1-alpha)
    for alpha in (0.05, 0.01, 0.10):
        assert required_ci_percent(alpha) == 100.0 * (1.0 - alpha)


def test_default_alpha_requires_the_conventional_coverage():
    # derived from the constant, so changing DEFAULT_ALPHA cannot leave this asserting 95
    assert required_ci_percent(DEFAULT_ALPHA) == 100.0 * (1.0 - DEFAULT_ALPHA)


def test_ci_percent_given_as_a_proportion_is_refused_not_rescaled():
    # AACT carries 30 rows reading '0.95'. Multiplying by 100 would be a guess about what
    # the sponsor meant -- the same class of assumption that produced the ratio-scale bug.
    assert normalize_ci_percent("0.95") is None
    assert normalize_ci_percent(str(CI_PERCENT_PROPORTION_MAX)) is None
    assert normalize_ci_percent(str(CI_PERCENT_PROPORTION_MAX + 0.01)) is not None


def test_ci_percent_blank_and_unparseable_are_unknown():
    for raw in ("", None, "NA", "ninety-five"):
        assert normalize_ci_percent(raw) is None


def test_coverage_band_is_relative_to_alpha():
    required = required_ci_percent(DEFAULT_ALPHA)
    tol = DEFAULT_COVERAGE_TOLERANCE_POINTS
    assert coverage_band(required) == COVERAGE_MATCHED
    assert coverage_band(required + tol) == COVERAGE_MATCHED          # inclusive
    assert coverage_band(required + tol + 0.01) == COVERAGE_TIGHTER
    assert coverage_band(required - tol - 0.01) == COVERAGE_LOOSER
    assert coverage_band(None) == COVERAGE_UNKNOWN


def test_the_same_coverage_changes_band_when_alpha_changes():
    # structural: 90 is looser than the 0.05 requirement and matched at 0.10. If this
    # failed, the band would be reading a hardcoded 95 rather than alpha.
    ninety = required_ci_percent(0.10)
    assert coverage_band(ninety, alpha=0.05) == COVERAGE_LOOSER
    assert coverage_band(ninety, alpha=0.10) == COVERAGE_MATCHED


def test_one_way_implication_per_band():
    # matched: this IS the alpha test, so both verdicts stand
    assert coverage_supports(COVERAGE_MATCHED, 1) and coverage_supports(COVERAGE_MATCHED, 0)
    # tighter: excluding the null at higher confidence implies it at the required one
    assert coverage_supports(COVERAGE_TIGHTER, 1)
    assert not coverage_supports(COVERAGE_TIGHTER, 0)
    # looser: failing to exclude at lower confidence implies failing at the required one
    assert coverage_supports(COVERAGE_LOOSER, 0)
    assert not coverage_supports(COVERAGE_LOOSER, 1)
    # unknown supports nothing in either direction
    assert not coverage_supports(COVERAGE_UNKNOWN, 1)
    assert not coverage_supports(COVERAGE_UNKNOWN, 0)


def test_no_band_supports_a_missing_verdict():
    for band in (COVERAGE_MATCHED, COVERAGE_TIGHTER, COVERAGE_LOOSER, COVERAGE_UNKNOWN):
        assert not coverage_supports(band, None)


def test_loose_interval_met_verdict_is_refused_to_tier_d():
    # a 90% interval excluding the null is two-sided p < 0.10, not < 0.05
    loose = required_ci_percent(0.10)
    tier, met, reason = classify_analysis(
        {"param_type": "Hazard Ratio", "p_value": "",
         "ci_lower_limit": "1.10", "ci_upper_limit": "1.90", "ci_percent": str(loose)})
    assert (tier, met) == (TIER_D, None)
    assert REFUSAL_COVERAGE_MISMATCH in reason


def test_loose_interval_not_met_verdict_is_KEPT():
    # failing to exclude at 90% implies failing at 95%, so this verdict is sound and
    # refusing it would have discarded thousands of usable negatives
    loose = required_ci_percent(0.10)
    tier, met, _ = classify_analysis(
        {"param_type": "Hazard Ratio", "p_value": "",
         "ci_lower_limit": "0.80", "ci_upper_limit": "1.30", "ci_percent": str(loose)})
    assert (tier, met) == (TIER_C, 0)


def test_tight_interval_met_verdict_is_KEPT():
    tight = required_ci_percent(0.01)
    tier, met, _ = classify_analysis(
        {"param_type": "Hazard Ratio", "p_value": "",
         "ci_lower_limit": "1.10", "ci_upper_limit": "1.90", "ci_percent": str(tight)})
    assert (tier, met) == (TIER_C, 1)


def test_tight_interval_not_met_verdict_is_refused():
    # a 99% interval containing the null does NOT imply a 95% one would
    tight = required_ci_percent(0.01)
    tier, met, _ = classify_analysis(
        {"param_type": "Hazard Ratio", "p_value": "",
         "ci_lower_limit": "0.80", "ci_upper_limit": "1.30", "ci_percent": str(tight)})
    assert (tier, met) == (TIER_D, None)


def test_unknown_coverage_refuses_and_this_is_deliberate():
    # pinned explicitly rather than left to fixtures that omit the field: no ci_percent
    # means no implication to lean on, and assuming 95 would repeat the scale bug
    tier, met, _ = classify_analysis({"param_type": "Hazard Ratio", "p_value": "",
                                      "ci_lower_limit": "1.10", "ci_upper_limit": "1.90"})
    assert (tier, met) == (TIER_D, None)


def test_coverage_tolerance_flag_reaches_the_verdict():
    # structural: widening the tolerance until it admits the posted coverage flips the
    # refusal, which proves the flag is threaded through rather than read from a constant
    off_by = DEFAULT_COVERAGE_TOLERANCE_POINTS + 1.0
    row = {"param_type": "Hazard Ratio", "p_value": "", "ci_lower_limit": "1.10",
           "ci_upper_limit": "1.90",
           "ci_percent": str(required_ci_percent(DEFAULT_ALPHA) - off_by)}
    assert classify_analysis(row)[0] == TIER_D
    assert classify_analysis(row, coverage_tolerance=off_by)[0] == TIER_C


def test_label_row_carries_the_coverage_settings_it_used():
    tol = DEFAULT_COVERAGE_TOLERANCE_POINTS * 3
    rec = label_row({"nct_id": "NCT1"}, {"o1": [{"p_value": "0.01"}]},
                    coverage_tolerance=tol)
    assert rec["coverage_tolerance_used"] == tol
    assert rec["required_ci_percent_used"] == required_ci_percent(DEFAULT_ALPHA)


# ===========================================================================
# TIER E -- non-inferiority and equivalence rely on a different test
# ===========================================================================
# Third bug of the family. At matching coverage the interval rule scores kappa 0.942 on
# superiority rows and 0.434 on NI/equivalence rows, under-calling the latter 12.5 to 1,
# because an NI trial succeeds when the interval sits inside the margin -- which routinely
# INCLUDES the null. The margin is not a structured field (only 16.3% of NI descriptions
# put a number next to the word "margin"), so tier E produces no verdict and stays
# countable instead of dissolving into tier D.
def test_ni_interval_goes_to_tier_e_not_tier_c():
    tier, met, reason = classify_analysis(
        {"non_inferiority_type": "NON_INFERIORITY", "p_value": "",
         "param_type": "Hazard Ratio", "ci_lower_limit": "0.80",
         "ci_upper_limit": "1.10", "ci_percent": "95.0"})
    assert (tier, met) == (TIER_E, None)
    assert REFUSAL_NI_MARGIN_UNAVAILABLE in reason


def test_equivalence_interval_also_tier_e():
    tier, met, _ = classify_analysis(
        {"non_inferiority_type": "EQUIVALENCE", "p_value": "",
         "param_type": "Mean Difference", "ci_lower_limit": "-1.0",
         "ci_upper_limit": "2.0", "ci_percent": "95.0"})
    assert (tier, met) == (TIER_E, None)


def test_ni_with_a_p_value_is_still_tier_b():
    # p-value precedence is untouched: tier E only catches the interval branch
    tier, met, _ = classify_analysis(
        {"non_inferiority_type": "NON_INFERIORITY", "p_value": "0.001"})
    assert (tier, met) == (TIER_B, 1)


def test_tier_e_is_ordered_and_documented_like_every_other_tier():
    assert TIER_E in TIER_ORDER
    assert TIER_E in TIER_DOC and TIER_DOC[TIER_E].strip()
    # weaker than C, stronger than D: it has an interval but no applicable rule
    assert TIER_ORDER.index(TIER_C) < TIER_ORDER.index(TIER_E) < TIER_ORDER.index(TIER_D)


def test_tier_e_is_not_a_headline_tier():
    assert TIER_E not in HEADLINE_TIERS


def test_tier_e_never_produces_a_label():
    agg = aggregate_trial({"o1": [
        {"non_inferiority_type": "NON_INFERIORITY", "p_value": "",
         "param_type": "Hazard Ratio", "ci_lower_limit": "0.80",
         "ci_upper_limit": "1.10", "ci_percent": "95.0"}]})
    assert agg["any_primary_met"] is None
    assert agg["n_primary_analyzed"] == 0
    assert agg["n_analyses_ni_design"] == 1
    assert agg["endpoint_na_reason"] == NA_REASON_NI_DESIGN


# ---- the finer design reading, carried not decided ------------------------
def test_design_detail_separates_the_three_tests():
    assert analysis_design_detail("NON_INFERIORITY") == DESIGN_DETAIL_NON_INFERIORITY
    assert analysis_design_detail("EQUIVALENCE") == DESIGN_DETAIL_EQUIVALENCE
    assert analysis_design_detail("SUPERIORITY") == DESIGN_DETAIL_SUPERIORITY
    assert analysis_design_detail("") == DESIGN_DETAIL_UNSTATED


def test_combined_value_is_not_resolved_to_whichever_is_commoner():
    # 'NON_INFERIORITY_OR_EQUIVALENCE' names two different tests and says which is unknown
    assert (analysis_design_detail("NON_INFERIORITY_OR_EQUIVALENCE")
            == DESIGN_DETAIL_NI_OR_EQUIVALENCE)
    assert (analysis_design_detail("NON_INFERIORITY_OR_EQUIVALENCE_LEGACY")
            == DESIGN_DETAIL_NI_OR_EQUIVALENCE)


def test_design_detail_refines_design_without_contradicting_it():
    # structural: every value the coarse function calls 'ni' must map to one of the three
    # NI-family details, and nothing else may. Guards the two from drifting apart.
    ni_details = {DESIGN_DETAIL_NON_INFERIORITY, DESIGN_DETAIL_EQUIVALENCE,
                  DESIGN_DETAIL_NI_OR_EQUIVALENCE}
    for value in ("NON_INFERIORITY", "EQUIVALENCE", "NON_INFERIORITY_OR_EQUIVALENCE",
                  "NON_INFERIORITY_OR_EQUIVALENCE_LEGACY", "SUPERIORITY",
                  "SUPERIORITY_OR_OTHER", "SUPERIORITY_OR_OTHER_LEGACY", "OTHER", ""):
        coarse = analysis_design(value)
        detail = analysis_design_detail(value)
        assert (coarse == DESIGN_NI) == (detail in ni_details), value


def test_design_detail_handles_every_spelling_the_coarse_one_does():
    for value in ("Non-Inferiority", "non inferiority", "NON_INFERIORITY"):
        assert analysis_design_detail(value) == DESIGN_DETAIL_NON_INFERIORITY


# ---- refusal accounting ---------------------------------------------------
def test_refusal_kind_names_each_refusal():
    floor = DEFAULT_RATIO_SCALE_FLOOR
    assert refusal_kind({"non_inferiority_type": "NON_INFERIORITY", "p_value": "",
                         "param_type": "Hazard Ratio", "ci_lower_limit": "0.8",
                         "ci_upper_limit": "1.1", "ci_percent": "95.0"}) == REFUSAL_NI_DESIGN
    assert refusal_kind({"p_value": "", "param_type": "Geometric mean ratio",
                         "ci_lower_limit": floor * 8.5, "ci_upper_limit": floor * 10.4,
                         "ci_percent": "95.0"}) == REFUSAL_PERCENT_SCALED
    assert refusal_kind({"p_value": "", "param_type": "Hazard Ratio",
                         "ci_lower_limit": "1.1", "ci_upper_limit": "1.9",
                         "ci_percent": str(required_ci_percent(0.10))}) == REFUSAL_COVERAGE


def test_refusal_kind_is_none_where_nothing_was_refused():
    # decided on its p-value: the interval was never consulted
    assert refusal_kind({"p_value": "0.01", "param_type": "Hazard Ratio",
                         "ci_lower_limit": "1.1", "ci_upper_limit": "1.9"}) is None
    # no interval at all: tier D on its own ground, not a refusal
    assert refusal_kind({"p_value": "", "param_type": "Mean"}) is None
    # a clean tier-C row
    assert refusal_kind({"p_value": "", "param_type": "Hazard Ratio",
                         "ci_lower_limit": "1.1", "ci_upper_limit": "1.9",
                         "ci_percent": "95.0"}) is None


def test_refusal_kind_agrees_with_the_tier_it_produces():
    # structural: the counter and the classifier must not disagree, since they are the
    # two places a refusal is observed
    rows = [
        {"non_inferiority_type": "EQUIVALENCE", "p_value": "", "param_type": "Hazard Ratio",
         "ci_lower_limit": "0.8", "ci_upper_limit": "1.1", "ci_percent": "95.0"},
        {"p_value": "", "param_type": "Geometric mean ratio", "ci_lower_limit": "85.1",
         "ci_upper_limit": "104.2", "ci_percent": "95.0"},
        {"p_value": "", "param_type": "Hazard Ratio", "ci_lower_limit": "1.1",
         "ci_upper_limit": "1.9", "ci_percent": str(required_ci_percent(0.10))},
        {"p_value": "", "param_type": "Hazard Ratio", "ci_lower_limit": "1.1",
         "ci_upper_limit": "1.9", "ci_percent": "95.0"},
    ]
    for row in rows:
        tier, met, _ = classify_analysis(row)
        refused = refusal_kind(row) is not None
        assert refused == (met is None and tier in (TIER_D, TIER_E)), row


def test_outcome_tallies_every_refusal_kind():
    floor = DEFAULT_RATIO_SCALE_FLOOR
    v = aggregate_outcome([
        {"non_inferiority_type": "NON_INFERIORITY", "p_value": "",
         "param_type": "Hazard Ratio", "ci_lower_limit": "0.8", "ci_upper_limit": "1.1",
         "ci_percent": "95.0"},
        {"p_value": "", "param_type": "Geometric mean ratio",
         "ci_lower_limit": floor * 8.5, "ci_upper_limit": floor * 10.4,
         "ci_percent": "95.0"},
        {"p_value": "", "param_type": "Hazard Ratio", "ci_lower_limit": "1.1",
         "ci_upper_limit": "1.9", "ci_percent": str(required_ci_percent(0.10))},
    ])
    assert v["refusals"][REFUSAL_NI_DESIGN] == 1
    assert v["refusals"][REFUSAL_PERCENT_SCALED] == 1
    assert v["refusals"][REFUSAL_COVERAGE] == 1
    assert set(v["refusals"]) == set(REFUSAL_KINDS)      # every kind present, even at zero


def test_na_reason_follows_the_declared_precedence():
    # all three refusals at once: the declared order decides, not dict insertion order
    floor = DEFAULT_RATIO_SCALE_FLOOR
    agg = aggregate_trial({"o1": [
        {"p_value": "", "param_type": "Hazard Ratio", "ci_lower_limit": "1.1",
         "ci_upper_limit": "1.9", "ci_percent": str(required_ci_percent(0.10))},
        {"p_value": "", "param_type": "Geometric mean ratio",
         "ci_lower_limit": floor * 8.5, "ci_upper_limit": floor * 10.4,
         "ci_percent": "95.0"},
        {"non_inferiority_type": "NON_INFERIORITY", "p_value": "",
         "param_type": "Hazard Ratio", "ci_lower_limit": "0.8", "ci_upper_limit": "1.1",
         "ci_percent": "95.0"},
    ]})
    expected = NA_REASON_FOR_REFUSAL[REFUSAL_KINDS[0]]
    assert agg["endpoint_na_reason"] == expected


def test_every_refusal_kind_maps_to_a_documented_na_reason():
    for kind in REFUSAL_KINDS:
        reason = NA_REASON_FOR_REFUSAL[kind]
        assert reason in NA_REASON_DOC and NA_REASON_DOC[reason].strip()


def test_na_reason_empty_when_a_verdict_survived_alongside_refusals():
    agg = aggregate_trial({
        "o1": [{"non_inferiority_type": "Superiority", "p_value": "0.01"}],
        "o2": [{"non_inferiority_type": "NON_INFERIORITY", "p_value": "",
                "param_type": "Hazard Ratio", "ci_lower_limit": "0.8",
                "ci_upper_limit": "1.1", "ci_percent": "95.0"}],
    })
    assert agg["endpoint_na_reason"] == NA_REASON_NONE
    assert agg["n_analyses_ni_design"] == 1


# ---- tier E must be VISIBLE at trial level --------------------------------
# Found in the first widened pull: the tier distribution printed E_ni_interval as 0
# trials while the refusal counts on the same page said 1,820. tier_min was derived only
# from COUNTED outcomes, and tier E never produces a verdict, so a trial whose every
# primary analysis was an NI interval reported as D_no_analysis -- indistinguishable from
# a single-arm descriptive posting, which is the opposite of why tier E exists.
def test_trial_with_only_ni_intervals_reports_tier_e_not_tier_d():
    agg = aggregate_trial({"o1": [
        {"non_inferiority_type": "NON_INFERIORITY", "p_value": "",
         "param_type": "Hazard Ratio", "ci_lower_limit": "0.80",
         "ci_upper_limit": "1.10", "ci_percent": "95.0"}]})
    assert agg["tier_min"] == TIER_E
    assert agg["tier_max"] == TIER_E
    assert agg["any_primary_met"] is None          # still no verdict


def test_tier_min_still_describes_what_the_label_rests_on():
    # an A outcome plus an E outcome keeps tier_min = A: the label rests only on the
    # counted outcome, and tier_min is a statement about the label, not about the trial
    agg = aggregate_trial({
        "o1": [{"non_inferiority_type": "Superiority", "p_value": "0.01"}],
        "o2": [{"non_inferiority_type": "NON_INFERIORITY", "p_value": "",
                "param_type": "Hazard Ratio", "ci_lower_limit": "0.80",
                "ci_upper_limit": "1.10", "ci_percent": "95.0"}],
    })
    assert agg["tier_min"] == TIER_A
    assert agg["any_primary_met"] == 1


def test_trial_with_no_analyses_at_all_is_still_tier_d():
    # the fallback must not promote an ordinary unlabelled trial out of D
    agg = aggregate_trial({"o1": [{"param_type": "Mean", "p_value": ""}]})
    assert agg["tier_min"] == TIER_D
    assert agg["tier_max"] == TIER_D


def test_fallback_picks_the_weakest_uncounted_tier():
    # structural: with both an E outcome and a D outcome and no verdict anywhere,
    # tier_max is the stronger of the two and tier_min the weaker, per TIER_ORDER
    agg = aggregate_trial({
        "o1": [{"non_inferiority_type": "EQUIVALENCE", "p_value": "",
                "param_type": "Mean Difference", "ci_lower_limit": "-1",
                "ci_upper_limit": "2", "ci_percent": "95.0"}],
        "o2": [{"param_type": "Mean", "p_value": ""}],
    })
    assert agg["any_primary_met"] is None
    assert TIER_ORDER.index(agg["tier_max"]) < TIER_ORDER.index(agg["tier_min"])
    assert agg["tier_max"] == TIER_E and agg["tier_min"] == TIER_D