"""Tests for the endpoint-met label engine. Plain asserts so tests/_run_stdlib.py works."""
from __future__ import annotations

from trial_pos.services.endpoint_label import (
    TIER_A, TIER_B, TIER_C, TIER_D, TIER_DOC, TIER_ORDER, LABEL_DERIVED_FIELDS,
    aggregate_outcome, aggregate_trial, analysis_design, broad_label, classify_analysis,
    classify_stop_reason, label_row, met_from_ci, met_from_p, null_value_for,
    parse_p_value,
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
                                      "ci_lower_limit": "1.10", "ci_upper_limit": "1.90"})
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
                "ci_upper_limit": "1.8"}],
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