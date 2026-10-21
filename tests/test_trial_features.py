"""Tests for trial_features and training_design. Plain asserts so tests/_run_stdlib.py works."""
from __future__ import annotations

from datetime import date, timedelta

from trial_pos.services import trial_features as F
from trial_pos.services import training_design as D
from trial_pos.services.aact_fields import output_name
from trial_pos.services.eligibility import years_after
from trial_pos.services.endpoint_type import GATE_ONLY_FIELDS
from trial_pos.services.temporal_split import SIDE_TEST, SIDE_TRAIN

CODES = sorted(F.top_codes(["C04.588", "C14.280", "C04"]))
SPECS = F.build_specs(CODES)
DAY = timedelta(days=1)
T0 = date(2015, 6, 1)


def _raises(call, kinds=(ValueError, KeyError)):
    try:
        call()
    except kinds:
        return True
    return False


# ---- registry ------------------------------------------------------------------------------
def test_the_real_specs_pass_the_registry_for_every_target():
    for t in F.TARGETS:
        assert F.leakage_violations(SPECS, t) == [], t


def test_names_are_unique_and_every_source_is_classified():
    names = [s.name for s in SPECS]
    assert len(names) == len(set(names))
    for s in SPECS:
        assert s.sources and s.group in F.GROUPS and s.kind in F.KINDS, s.name
        F.leakage_violations([s], F.TARGET_MARKET)        # raises on an unclassified source


def test_a_post_hoc_or_label_source_is_refused_for_every_target():
    for col in ("enrollment", "overall_status", "why_stopped"):
        bad = F.FeatureSpec("x", F.GROUP_DESIGN, F.KIND_NUMERIC, (("studies", col),), "")
        for t in F.TARGETS:
            assert F.leakage_violations([bad], t), (col, t)


def test_a_gate_only_name_is_refused_for_endpoint_met_only():
    bad = F.FeatureSpec(GATE_ONLY_FIELDS[0], F.GROUP_DESIGN, F.KIND_CATEGORY,
                        (("studies", "phase"),), "")
    assert F.leakage_violations([bad], F.TARGET_ENDPOINT_MET)
    assert not F.leakage_violations([bad], F.TARGET_ADVANCEMENT)


def test_a_source_outside_aact_cannot_be_declared():
    bad = F.FeatureSpec("x", F.GROUP_DESIGN, F.KIND_NUMERIC, (("drugcentral", "approval"),), "")
    assert _raises(lambda: F.leakage_violations([bad], F.TARGET_MARKET))
    assert _raises(lambda: F.leakage_violations(SPECS, "approval_anywhere"))


def test_toggles_are_never_served_and_registration_only_is_a_strict_subset():
    served = F.served(SPECS)
    assert served and not any(s.group in F.TOGGLE_GROUPS for s in served)
    assert {s.group for s in SPECS} >= set(F.TOGGLE_GROUPS)
    reg = [s for s in served if F.is_registration_only(s)]
    assert 0 < len(reg) < len(served)
    by = {s.name: s for s in SPECS}
    assert F.is_registration_only(by["phase"]) and not F.is_registration_only(by["n_sites"])


def test_every_fields_column_read_has_a_registry_provenance():
    cols = F.fields_columns_read()
    assert F.START in cols and F.SUBMITTED in cols
    for c in cols:
        F.field_provenance(c)


# ---- per-trial values ------------------------------------------------------------------------
def test_age_bounds_convert_through_the_unit_table_and_unknowns_stay_unknown():
    assert F.age_years("18", "Years") == 18 * F.AGE_UNIT_YEARS["year"]
    assert F.age_years("6", "months") == 6 * F.AGE_UNIT_YEARS["month"]
    assert F.age_years("2", "Week") == 2 * F.AGE_UNIT_YEARS["week"]
    assert F.age_years("", "Years") is None and F.age_years("3", "") is None
    assert F.age_years("3", "fortnights") is None


def _row(**kv):
    return {output_name(*k.split("__", 1)): v for k, v in kv.items()}


def test_blank_fields_are_unknown_never_false_or_zero():
    v = F.fields_features({})
    for name in ("has_dmc", "placebo_arm", "industry_collaborator", "multi_country",
                 "intervention_biological", "number_of_arms", "us_site_share"):
        assert v[name] is None, name


def test_arm_and_collaborator_flags_follow_the_counts():
    v = F.fields_features(_row(design_groups__n_placebo_comparator="1",
                               design_groups__n_active_comparator="0",
                               sponsors__n_collaborators="2",
                               sponsors__collaborator_agency_classes="INDUSTRY|OTHER",
                               interventions__types="DRUG|BIOLOGICAL",
                               facilities__n_distinct="4", facilities__n_us_distinct="1",
                               facilities__n_countries="1"))
    assert v["placebo_arm"] is True and v["active_comparator_arm"] is False
    assert v["industry_collaborator"] is True and v["nih_collaborator"] is False
    assert v["intervention_biological"] is True and v["intervention_device"] is False
    assert v["us_site_share"] == 1 / 4 and v["multi_country"] is False
    none = F.fields_features(_row(sponsors__n_collaborators="0"))
    assert none["industry_collaborator"] is False


def test_start_minus_registration_is_signed():
    v = F.fields_features(_row(studies__start_date="2015-01-01",
                               studies__study_first_submitted_date="2015-03-01"))
    assert v["start_minus_registration_days"] == (date(2015, 1, 1) - date(2015, 3, 1)).days < 0


def test_biomarker_terms_need_a_word_boundary():
    yes = ("EGFR mutation", "ALK-positive NSCLC", "HER2+ disease", "known BRAF V600E",
           "KRAS mutated")
    no = ("alkaline phosphatase <= 2.5 x ULN", "diabetic retinopathy", "return of symptoms",
          "prior infusion reaction", "retreatment allowed")
    for t in yes:
        assert F.text_features(t)["selection_biomarker"] is True, t
    for t in no:
        assert F.text_features(t)["selection_biomarker"] is False, t
    assert F.text_features("") == F.text_features(None)
    assert all(v is None for v in F.text_features(None).values())


def test_list_items_are_counted_by_their_markers():
    text = "Inclusion:\n- a\n* b\n1. c\n2) d\nnot an item\n"
    assert F.text_features(text)["eligibility_items"] == 4


def test_disease_flags_unknown_without_terms_false_without_a_category():
    assert all(v is None for v in F.disease_features(0, set(), CODES).values())
    v = F.disease_features(2, set(), CODES)
    assert all(v[F.disease_feature_name(c)] is False for c in CODES)
    v = F.disease_features(1, {CODES[0]}, CODES)
    assert v[F.disease_feature_name(CODES[0])] is True and v["n_disease_categories"] == 1


def test_entity_keys_fold_accents_case_and_corporate_suffixes():
    assert F.entity_key("Assistance Publique - Hôpitaux de Paris") == \
        F.entity_key("ASSISTANCE PUBLIQUE HOPITAUX DE PARIS")
    assert F.entity_key("Pfizer Inc.") == F.entity_key("PFIZER") == F.entity_key("Pfizer, Ltd")
    assert F.entity_key("Inc") == "inc"            # never emptied by suffix removal
    assert F.site_key("", "Boston", "United States") == ""


# ---- history ----------------------------------------------------------------------------------
def test_history_counts_the_half_open_lookback_and_never_the_trial_itself():
    ix = F.HistoryIndex()
    start = years_after(T0, -D.HISTORY_LOOKBACK_YEARS)
    for n, d in (("in1", start), ("in2", T0 - DAY), ("same_day", T0), ("too_old", start - DAY),
                 ("later", T0 + DAY)):
        ix.add("k", n, d)
    ix.add("k", "in1", start)                      # one trial counted once
    assert _raises(lambda: ix.count("k", T0, D.HISTORY_LOOKBACK_YEARS), (RuntimeError,))
    ix.freeze()
    assert ix.count("k", T0, D.HISTORY_LOOKBACK_YEARS) == 2
    assert ix.count("absent", T0, D.HISTORY_LOOKBACK_YEARS) == 0
    assert _raises(lambda: ix.add("k", "x", T0), (RuntimeError,))


def test_drug_history_dedupes_across_keys_and_reads_phase_and_disease():
    L = D.HISTORY_LOOKBACK_YEARS
    h = F.DrugHistory([("a", T0 - DAY, "p1", "PHASE3", {"asthma"}),
                       ("b", T0 - DAY, "p1", "PHASE3", {"asthma"}),
                       ("a", T0 - 2 * DAY, "p2", "PHASE1", {"copd"}),
                       ("a", T0 + DAY, "later", "PHASE4", {"asthma"}),
                       ("a", T0 - DAY, "nophase", "not_applicable", {"asthma"})])
    v = h.features(["a", "b"], T0, {"asthma"}, L)
    assert v["drug_prior_trials"] == 2 and v["drug_prior_same_disease"] == 1
    from trial_pos.services.advancement_label import PHASE_RANK
    assert v["drug_prior_max_phase"] == PHASE_RANK["PHASE3"]
    assert h.features([], T0, set(), L)["drug_prior_trials"] is None
    assert h.features(["zzz"], T0, set(), L) == {"drug_prior_trials": 0,
                                                 "drug_prior_max_phase": 0,
                                                 "drug_prior_same_disease": 0}


def test_crowding_and_site_features():
    L = D.HISTORY_LOOKBACK_YEARS
    every, terms, sites = F.HistoryIndex(), F.HistoryIndex(), F.HistoryIndex()
    for i in range(4):
        every.add(F.ALL_TRIALS_KEY, f"n{i}", T0 - DAY)
    terms.add("asthma", "n0", T0 - DAY)
    sites.add("s1", "n0", T0 - DAY)
    sites.add("s1", "n1", T0 - DAY)
    for ix in (every, terms, sites):
        ix.freeze()
    assert F.disease_crowding(terms, every, ["asthma", "copd"], T0, L) == 1 / 4
    assert F.disease_crowding(terms, every, [], T0, L) is None
    v = F.site_features(sites, ["s1", "s2", ""], T0, L)
    assert v == {"site_prior_median": 1.0, "site_prior_max": 2}


def test_csv_cells_are_deterministic():
    assert [F.csv_cell(x) for x in (None, True, False, 3, 0.125)] == ["", "1", "0", "3", "0.125"]


# ---- the decided split -------------------------------------------------------------------------
def test_the_split_constants_are_coherent():
    assert D.ADVANCEMENT_LEFT_CUT < D.SPLIT_BOUNDARY
    assert D.HISTORY_LOOKBACK_YEARS > 0
    assert set(D.LABEL_LAG_YEARS) == set(F.TARGETS)


def test_advancement_sets_aside_training_trials_read_out_before_the_left_cut():
    old = D.ADVANCEMENT_LEFT_CUT - DAY
    start = years_after(old, -1)
    assert D.split_side_for(F.TARGET_ENDPOINT_MET, old, start, "")[0] == SIDE_TRAIN
    assert D.split_side_for(F.TARGET_ADVANCEMENT, old, start, "")[0] == D.SIDE_BEFORE_LEFT_CUT
    new = D.ADVANCEMENT_LEFT_CUT + DAY
    assert D.split_side_for(F.TARGET_ADVANCEMENT, new, years_after(new, -1), "")[0] == SIDE_TRAIN
    late = D.SPLIT_BOUNDARY + DAY
    assert D.split_side_for(F.TARGET_ADVANCEMENT, years_after(late, 1), late, "")[0] == SIDE_TEST
    assert _raises(lambda: D.split_side_for("approval_anywhere", old, start, ""))


def test_the_post_2017_fields_are_a_toggle_and_keep_their_place_in_the_file():
    by = {sp.name: sp for sp in SPECS}
    assert all(by[n].group == F.GROUP_LATE_FIELDS for n in F.LATE_FIELD_FEATURES)
    assert not set(F.LATE_FIELD_FEATURES) & {sp.name for sp in F.served(SPECS)}
    order = [sp.name for sp in SPECS]
    assert order == [sp.name for sp in F.build_specs(CODES)]   # stable column order


def test_disease_codes_round_trip_through_the_feature_names():
    names = [sp.name for sp in SPECS]
    assert "disease_crowding" in names
    assert F.disease_codes_from_names(names) == CODES
    assert [sp.name for sp in F.build_specs(F.disease_codes_from_names(names))] == names


def test_the_d68_outcome_counts_are_served_editable_features():
    by = {sp.name: sp for sp in SPECS}
    for n in F.REGISTERED_OUTCOME_COUNT_FEATURES:
        assert by[n].group == F.GROUP_EDITABLE and by[n] in F.served(SPECS)
        assert not F.is_registration_only(by[n])


# ---- D-69 bundle -------------------------------------------------------------------------------
def test_prior_approval_counts_only_dates_strictly_before_registration():
    assert F.prior_approval([T0 - DAY], T0) is True
    assert F.prior_approval([T0], T0) is False                   # same day is not before
    assert F.prior_approval([T0 + DAY], T0) is False             # approved later
    assert F.prior_approval([], T0) is False                     # unresolved reads as not known
    assert F.prior_approval([T0 - DAY], None) is None


def test_laplace_share_follows_the_rule_of_succession():
    assert F.laplace_share(0, 0) is None
    assert F.laplace_share(1, 1) == (1 + 1) / (1 + 2)
    assert F.laplace_share(0, 3) == 1 / 5
    for bad in ((2, 1), (-1, 3), (0, -1)):
        assert _raises(lambda: F.laplace_share(*bad))


def test_outcome_history_reads_only_earlier_public_outcomes_never_its_own():
    h = F.OutcomeHistory([("k", "a", T0 - 2 * DAY, 1), ("k", "b", T0 - DAY, 0),
                          ("j", "a", T0 - 2 * DAY, 1),            # same trial under two keys
                          ("k", "same_day", T0, 1), ("k", "later", T0 + DAY, 1),
                          ("k", "self", T0 - DAY, 1), ("k", "undated", None, 1)])
    assert h.track_record(["k", "j"], T0, "self") == F.laplace_share(1, 2)
    assert h.track_record(["absent"], T0, "x") is None
    assert h.track_record([], T0, "x") is None and h.track_record(["k"], None, "x") is None
    assert _raises(lambda: F.OutcomeHistory([("k", "z", T0, 2)]))


def test_as_of_sources_are_refused_outside_the_bundle():
    bad = F.FeatureSpec("x", F.GROUP_DESIGN, F.KIND_NUMERIC, (F.AS_OF_OUTCOMES,), "")
    for t in F.TARGETS:
        assert F.leakage_violations([bad], t), t
    bundle = [sp for sp in SPECS if sp.group in F.BUNDLE_GROUPS]
    assert {sp.name for sp in bundle} == {"prior_fda_approval", "drug_track_record",
                                          "sponsor_track_record"}
    for t in F.TARGETS:
        assert F.leakage_violations(bundle, t) == [], t
    assert not any(F.is_registration_only(sp) for sp in bundle)
    assert not set(bundle) & set(F.served(SPECS))
    assert set(bundle) <= set(F.served(SPECS, with_bundle=True))


def test_every_declared_source_has_a_provenance_for_the_manifest():
    for sp in SPECS:
        for table, column in sp.sources:
            assert F.source_provenance(table, column), (sp.name, table, column)
    assert F.source_provenance(*F.AS_OF_OUTCOMES) == F.PROVENANCE_AS_OF
