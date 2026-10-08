"""Tests for tested_agent. Plain asserts so tests/_run_stdlib.py works."""
from __future__ import annotations

from trial_pos.services.aact_fields import GROUP_TYPES as FIELDS_GROUP_TYPES
from trial_pos.services.tested_agent import (
    GROUP_TYPES, OUTCOMES, ROUTE_ARM_RULE, ROUTE_NO_ARMS_FALLBACK, ROUTES,
    UNDET_ALL_BACKGROUND, UNDET_ARMS_UNTYPED, UNDET_EXPERIMENTAL_NO_DRUG,
    UNDET_NO_DRUG_INTERVENTION, UNDET_NO_DRUG_LINKED, UNDET_NO_EXPERIMENTAL_ARM,
    UNDET_ONLY_PLACEBO, normalize_group_type, select_tested_agents,
)


def _raises(fn, *args, exc=ValueError):
    try:
        fn(*args)
    except exc:
        return True
    return False


EXP, ACT, PBO, OTH = "EXPERIMENTAL", "ACTIVE_COMPARATOR", "PLACEBO_COMPARATOR", "OTHER"
CASES = {
    ROUTE_ARM_RULE: ([(1, "DRUG", "X"), (2, "DRUG", "Placebo"), (3, "DEVICE", "Pump")],
                     {10: EXP, 11: PBO}, [(10, 1), (10, 3), (11, 2)]),
    ROUTE_NO_ARMS_FALLBACK: ([(1, "DRUG", "X"), (2, "DRUG", "Placebo")], {}, []),
    UNDET_NO_DRUG_INTERVENTION: ([(1, "DEVICE", "Pump")], {10: EXP}, [(10, 1)]),
    UNDET_NO_DRUG_LINKED: ([(1, "DRUG", "X")], {10: EXP}, []),
    UNDET_ARMS_UNTYPED: ([(1, "DRUG", "X")], {10: None, 11: ACT}, [(10, 1)]),
    UNDET_NO_EXPERIMENTAL_ARM: ([(1, "DRUG", "X"), (2, "DRUG", "Y")], {10: ACT, 11: ACT},
                                [(10, 1), (11, 2)]),
    UNDET_ALL_BACKGROUND: ([(1, "DRUG", "X")], {10: EXP, 11: ACT}, [(10, 1), (11, 1)]),
    UNDET_EXPERIMENTAL_NO_DRUG: ([(1, "DRUG", "X"), (2, "DEVICE", "Pump")],
                                 {10: EXP, 11: ACT}, [(10, 2), (11, 1)]),
    UNDET_ONLY_PLACEBO: ([(1, "DRUG", "Placebo")], {10: EXP}, [(10, 1)]),
}


def test_every_outcome_is_reachable_and_routes_are_the_determined_ones():
    seen = {o: select_tested_agents(*c).outcome for o, c in CASES.items()}
    assert seen == {o: o for o in CASES}
    assert set(CASES) == set(OUTCOMES)
    for o, c in CASES.items():
        assert select_tested_agents(*c).determined == (o in ROUTES)


def test_arm_rule_keeps_experimental_only_drugs_and_drops_comparator_placebo():
    s = select_tested_agents(*CASES[ROUTE_ARM_RULE])
    assert s.agents == (1,) and s.dropped_placebo == ()


def test_fallback_drops_pure_placebo_and_records_it():
    s = select_tested_agents(*CASES[ROUTE_NO_ARMS_FALLBACK])
    assert s.agents == (1,) and s.dropped_placebo == (2,)


def test_x_or_placebo_and_blank_names_stay_candidates():
    s = select_tested_agents([(1, "DRUG", "X or placebo"), (2, "DRUG", "")], {10: EXP},
                             [(10, 1), (10, 2)])
    assert s.agents == (1, 2) and s.dropped_placebo == ()


def test_a_drug_in_experimental_and_other_arms_is_still_tested():
    s = select_tested_agents([(1, "BIOLOGICAL", "X")], {10: EXP, 11: OTH},
                             [(10, 1), (11, 1)])
    assert s.outcome == ROUTE_ARM_RULE and s.agents == (1,)


def test_links_outside_the_trial_and_unknown_arm_types_raise():
    assert _raises(select_tested_agents, [(1, "DRUG", "X")], {10: EXP}, [(10, 2)])
    assert _raises(select_tested_agents, [(1, "DRUG", "X")], {10: EXP}, [(11, 1)])
    assert _raises(normalize_group_type, "Experimental-ish")


def test_arm_vocabulary_matches_the_fields_pull_and_spellings_normalise():
    assert GROUP_TYPES == set(FIELDS_GROUP_TYPES)
    assert normalize_group_type("Active Comparator") == ACT
    assert normalize_group_type("  ") is None
