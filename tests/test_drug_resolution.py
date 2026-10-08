"""Tests for drug_resolution. Plain asserts so tests/_run_stdlib.py works."""
from __future__ import annotations

from trial_pos.services.drug_dictionary import build_dictionary
from trial_pos.services.drug_resolution import (
    AGENT_AMBIGUOUS, AGENT_COLUMNS, AGENT_PARTIAL, AGENT_RESOLVED, AGENT_STATUSES,
    AGENT_UNRESOLVED, HOW_COMPONENTS, HOW_WHOLE, NAME_OTHER, NAME_PRIMARY, TRIAL_COLUMNS,
    agent_rows, resolve_agent, resolve_one_name, resolve_trial, tally, trial_row,
)
from trial_pos.services.tested_agent import (
    OUTCOMES, ROUTE_ARM_RULE, UNDET_NO_EXPERIMENTAL_ARM, Selection,
)

A, B, C1, C2 = 1, 2, 3, 4


def _d():
    return build_dictionary(
        synonyms=[{"id": str(A), "lname": "alpha"}, {"id": str(B), "lname": "beta"},
                  {"id": str(C1), "lname": "gamma"}, {"id": str(C2), "lname": "gamma"}],
        structures=[], struct2parent=[], ob_product=[], struct2obprod=[], fda_products=[],
        fda_applications=[])


def test_whole_name_first_then_components():
    d = _d()
    r = resolve_one_name(d, "Alpha 10 mg")
    assert r.status == AGENT_RESOLVED and r.how == HOW_WHOLE and r.drugs == {A}
    r = resolve_one_name(d, "alpha + beta")
    assert r.status == AGENT_RESOLVED and r.how == HOW_COMPONENTS and r.drugs == {A, B}
    r = resolve_one_name(d, "alpha + unknownium")
    assert r.status == AGENT_PARTIAL and r.n_components == 2 and r.n_components_resolved == 1


def test_ambiguity_is_final_and_unknown_is_unresolved():
    d = _d()
    assert resolve_one_name(d, "gamma").status == AGENT_AMBIGUOUS
    assert resolve_one_name(d, "unknownium").status == AGENT_UNRESOLVED
    assert resolve_one_name(d, "placebo").status == AGENT_UNRESOLVED


def test_other_names_are_tried_in_byte_order_after_the_name():
    d = _d()
    a = resolve_agent(d, 7, "code-123", ["beta", "Alpha"])
    assert a.status == AGENT_RESOLVED and a.name_kind == NAME_OTHER and a.name_used == "Alpha"
    a = resolve_agent(d, 7, "alpha", ["beta"])
    assert a.name_kind == NAME_PRIMARY and a.drugs == {A}


def test_the_best_status_wins_when_nothing_fully_resolves():
    d = _d()
    a = resolve_agent(d, 7, "unknownium", ["gamma", "beta + unknownium"])
    assert a.status == AGENT_PARTIAL


def test_a_trial_matches_when_at_least_one_agent_does():
    d = _d()
    sel = Selection(ROUTE_ARM_RULE, (1, 2), ())
    t = resolve_trial(d, "NCT1", sel, {1: "alpha", 2: "unknownium"}, {}, ["Alpha"])
    assert t.matched is True and t.drugs == {A} and t.mesh_corroborated is True
    t = resolve_trial(d, "NCT1", sel, {1: "alpha", 2: "unknownium"}, {}, ["Beta"])
    assert t.mesh_corroborated is False
    t = resolve_trial(d, "NCT1", sel, {1: "alpha", 2: "unknownium"}, {}, [])
    assert t.mesh_corroborated is None


def test_mesh_never_creates_a_match_but_is_recorded_when_unmatched():
    d = _d()
    sel = Selection(ROUTE_ARM_RULE, (1,), ())
    t = resolve_trial(d, "NCT1", sel, {1: "unknownium"}, {}, ["Beta"])
    assert t.matched is False and not t.drugs and t.mesh_drugs_when_unmatched == {B}


def test_an_undetermined_selection_is_none_not_unmatched():
    t = resolve_trial(_d(), "NCT1", Selection(UNDET_NO_EXPERIMENTAL_ARM, (), ()), {}, {})
    assert t.matched is None and t.agents == ()


def test_rows_carry_exactly_the_declared_columns_and_tally_zeros_every_kind():
    d = _d()
    t = resolve_trial(d, "NCT1", Selection(ROUTE_ARM_RULE, (1,), ()), {1: "alpha"}, {})
    assert set(trial_row(t)) == set(TRIAL_COLUMNS)
    assert all(set(r) == set(AGENT_COLUMNS) for r in agent_rows(t))
    counts = tally([])
    assert set(counts["outcomes"]) == set(OUTCOMES) and set(counts["statuses"]) == set(
        AGENT_STATUSES)
    assert all(v == 0 for v in counts["outcomes"].values())


# ---- comma last resort and salt step through the resolver, 2026-10-07 ----------------------
from trial_pos.services.drug_resolution import HOW_COMMA_COMPONENTS  # noqa: E402

EZE, FLU2 = 5, 6


def _d2():
    return build_dictionary(
        synonyms=[{"id": str(EZE), "lname": "ezetimibe"}, {"id": str(A), "lname": "alpha"},
                  {"id": str(B), "lname": "beta"},
                  {"id": str(FLU2), "lname": "fludarabine phosphate"}],
        structures=[], struct2parent=[], ob_product=[], struct2obprod=[], fda_products=[],
        fda_applications=[])


def test_a_code_plus_name_resolves_partly_through_the_comma_route():
    r = resolve_one_name(_d2(), "mk0653, ezetimibe")
    assert r.status == AGENT_PARTIAL and r.how == HOW_COMMA_COMPONENTS and r.drugs == {EZE}


def test_the_comma_route_is_never_used_when_the_normal_split_finds_something():
    r = resolve_one_name(_d2(), "alpha + beta, unknownium")
    assert r.how == HOW_COMPONENTS and r.drugs == {A}


def test_the_salt_step_works_inside_a_combination():
    r = resolve_one_name(_d2(), "Fludarabine + Alpha")
    assert r.status == AGENT_RESOLVED and r.drugs == {FLU2, A}


# ---- form handling and comparators, D-12 to D-15 ---------------------------------------------
from trial_pos.services.drug_resolution import (  # noqa: E402
    FORM_HANDLINGS, FORM_INFERRED, FORM_KEPT, FORM_NONE_STATED, FORM_STRIPPED,
    form_handling,
)

MET = 7


def _d3():
    return build_dictionary(
        synonyms=[{"id": str(MET), "lname": "metoprolol"},
                  {"id": str(MET), "lname": "metoprolol succinate"},
                  {"id": str(MET), "lname": "metoprolol tartrate"},
                  {"id": str(A), "lname": "alpha"},
                  {"id": str(FLU2), "lname": "fludarabine phosphate"}],
        structures=[], struct2parent=[], ob_product=[], struct2obprod=[], fda_products=[],
        fda_applications=[])


def test_form_handling_records_kept_stripped_inferred_and_none():
    d = _d3()
    kept = resolve_agent(d, 1, "Metoprolol succinate", [])
    assert kept.form_handling == FORM_KEPT and kept.matched_key == "metoprolol succinate"
    stripped = resolve_agent(d, 1, "Metoprolol succinate ER", [])
    assert stripped.form_handling == FORM_STRIPPED and stripped.drugs == {MET}
    assert stripped.form.salts == ("succinate",)
    assert stripped.form.classes == ("extended_release",)
    assert resolve_agent(d, 1, "Fludarabine", []).form_handling == FORM_INFERRED
    assert resolve_agent(d, 1, "Alpha", []).form_handling == FORM_NONE_STATED
    assert resolve_agent(d, 1, "Unknownium", []).form_handling == ""
    assert set(FORM_HANDLINGS) == {FORM_KEPT, FORM_STRIPPED, FORM_INFERRED, FORM_NONE_STATED}


def test_hcl_in_the_entry_counts_as_keeping_hydrochloride():
    assert form_handling("Drug hydrochloride", "drug hcl", "exact", AGENT_RESOLVED) == FORM_KEPT


def test_one_moiety_in_two_forms_is_flagged_and_other_cases_are_not():
    d = _d3()
    sel = Selection(ROUTE_ARM_RULE, (1,), (), (2,))
    names = {1: "Metoprolol succinate ER", 2: "Metoprolol tartrate"}
    t = resolve_trial(d, "N", sel, names, {})
    assert t.tested_moiety_in_comparator is True and t.comparator_drugs == {MET}
    t = resolve_trial(d, "N", sel, {1: "Metoprolol succinate", 2: "Alpha"}, {})
    assert t.tested_moiety_in_comparator is False
    t = resolve_trial(d, "N", sel, {1: "Metoprolol succinate", 2: "Unknownium"}, {})
    assert t.tested_moiety_in_comparator is None
    t = resolve_trial(d, "N", Selection(ROUTE_ARM_RULE, (1,), ()), {1: "Alpha"}, {})
    assert t.tested_moiety_in_comparator is False
    row = trial_row(resolve_trial(d, "N", sel, names, {}))
    assert row["tested_moiety_in_comparator"] == "True" and row["any_form_stated"] == "True"


# ---- D-20 through the resolver; D-19 parent groups in the comparator flag ---------------------
ISO, DIM, SAL, IRON, CHL = 11, 12, 13, 14, 15


def _d4():
    return build_dictionary(
        synonyms=[{"id": str(ISO), "lname": "inh"}, {"id": str(ISO), "lname": "isoniazid"},
                  {"id": str(DIM), "lname": "bal"}, {"id": str(SAL), "lname": "albuterol"},
                  {"id": str(IRON), "lname": "iron"}, {"id": str(CHL), "lname": "tace"},
                  {"id": "90", "lname": "testosterone undecanoate"},
                  {"id": "91", "lname": "testosterone cypionate"}],
        structures=[], struct2parent=[{"struct_id": "90", "parent_id": "5"},
                                      {"struct_id": "91", "parent_id": "5"}],
        ob_product=[], struct2obprod=[], fda_products=[], fda_applications=[])


def test_abbreviations_never_match_inside_a_longer_name_or_as_an_other_name():
    d = _d4()
    r = resolve_one_name(d, "albuterol dpi 25 mcg/inh", whole_intervention_name=True)
    assert r.drugs == {SAL}
    assert resolve_agent(d, 1, "Balstilimab (BAL)", []).status == AGENT_UNRESOLVED
    assert resolve_agent(d, 1, "Code-7", ["ATS"]).status == AGENT_UNRESOLVED
    assert resolve_agent(d, 1, "ADCC & TACE", []).status == AGENT_UNRESOLVED
    assert resolve_agent(d, 1, "INH", []).drugs == {ISO}
    assert resolve_agent(d, 1, "Ferumoxytol or oral iron", []).drugs == {IRON}


def test_two_salts_of_one_parent_count_as_the_same_moiety_for_the_comparator_flag():
    d = _d4()
    sel = Selection(ROUTE_ARM_RULE, (1,), (), (2,))
    t = resolve_trial(d, "N", sel, {1: "Testosterone undecanoate", 2: "Testosterone cypionate"},
                      {})
    assert t.drugs == {90} and t.comparator_drugs == {91}
    assert t.tested_moiety_in_comparator is True


# ---- D-24: partial-only is its own class, and partial drugs never become the identity -------
from trial_pos.services.drug_resolution import (  # noqa: E402
    MATCH_CLASSES, MATCH_FULL, MATCH_NONE, MATCH_PARTIAL_ONLY, MATCH_UNDETERMINABLE,
    MATCHED_BY_CLASS,
)


def test_a_trial_matched_only_through_partial_agents_is_its_own_class():
    d = _d()
    sel = Selection(ROUTE_ARM_RULE, (1,), ())
    t = resolve_trial(d, "N", sel, {1: "torcetrapib/alpha"}, {}, ["Alpha"])
    assert t.match_class == MATCH_PARTIAL_ONLY and t.matched is None
    assert t.drugs == frozenset() and t.partial_drugs == {A}
    assert t.mesh_corroborated is None and t.tested_moiety_in_comparator is None


def test_partial_agents_in_a_full_trial_never_add_to_its_drugs():
    d = _d()
    sel = Selection(ROUTE_ARM_RULE, (1, 2), ())
    t = resolve_trial(d, "N", sel, {1: "beta", 2: "gelatinum/alpha"}, {})
    assert t.match_class == MATCH_FULL and t.matched is True
    assert t.drugs == {B} and t.partial_drugs == {A}


def test_every_class_maps_to_a_matched_value_and_rows_carry_the_class():
    d = _d()
    assert set(MATCHED_BY_CLASS) == set(MATCH_CLASSES)
    sel = Selection(ROUTE_ARM_RULE, (1,), ())
    assert resolve_trial(d, "N", sel, {1: "unknownium"}, {}).match_class == MATCH_NONE
    t = resolve_trial(d, "N", Selection(UNDET_NO_EXPERIMENTAL_ARM, (), ()), {}, {})
    assert t.match_class == MATCH_UNDETERMINABLE
    row = trial_row(resolve_trial(d, "N", sel, {1: "alpha"}, {}))
    assert row["match_class"] == MATCH_FULL and row["matched"] == "True"
    counts = tally([])
    assert set(counts["match_classes"]) == set(MATCH_CLASSES)
