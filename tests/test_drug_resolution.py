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
