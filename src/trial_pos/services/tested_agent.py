"""Which interventions of a drug trial are its TESTED agents. Pure.

Decided 2026-10-07, from audit/probe_intervention_arms.py on the pinned restore:

  arm rule          a drug/biological intervention in at least one EXPERIMENTAL arm and in
                    no comparator arm (active, placebo or sham) is a tested agent
  no-arms fallback  a trial with no arms at all: every drug/biological intervention is a
                    candidate. A separate ROUTE, so the hand sample measures it separately
  undeterminable    no experimental arm (head-to-head trials); every experimental drug also
                    in a comparator arm (add-on designs, dose comparisons); experimental
                    arms carrying no drug; no drug linked to any arm; arms untyped
  placebo           a pure placebo-type intervention is dropped from the candidates; if
                    nothing is left the trial is undeterminable. "X or placebo" stays: the
                    resolver keeps X (drug_names.active_components)

Inputs are one trial's rows, keyed on the CHILD's id. A link to an intervention or arm that
is not the trial's own raises: the pull already refuses those, so one arriving here means
the inputs were joined wrongly.
"""
from __future__ import annotations

import re
from typing import Iterable, NamedTuple

from trial_pos.services.drug_names import is_pure_placebo, key
from trial_pos.services.population import DRUG_INTERVENTION_TYPES

GROUP_EXPERIMENTAL = "EXPERIMENTAL"
GROUP_COMPARATOR = frozenset({"ACTIVE_COMPARATOR", "PLACEBO_COMPARATOR", "SHAM_COMPARATOR"})
GROUP_NEUTRAL = frozenset({"OTHER", "NO_INTERVENTION"})
GROUP_TYPES = frozenset({GROUP_EXPERIMENTAL}) | GROUP_COMPARATOR | GROUP_NEUTRAL

ROUTE_ARM_RULE = "arm_rule"
ROUTE_NO_ARMS_FALLBACK = "no_arms_fallback"
ROUTES = (ROUTE_ARM_RULE, ROUTE_NO_ARMS_FALLBACK)

UNDET_NO_DRUG_INTERVENTION = "no_drug_intervention"
UNDET_NO_DRUG_LINKED = "no_drug_intervention_linked_to_an_arm"
UNDET_ARMS_UNTYPED = "no_experimental_arm_and_some_arms_untyped"
UNDET_NO_EXPERIMENTAL_ARM = "no_experimental_arm"
UNDET_ALL_BACKGROUND = "every_experimental_drug_also_in_a_comparator_arm"
UNDET_EXPERIMENTAL_NO_DRUG = "experimental_arms_carry_no_drug"
UNDET_ONLY_PLACEBO = "every_candidate_is_a_placebo"
UNDETERMINABLE = (UNDET_NO_DRUG_INTERVENTION, UNDET_NO_DRUG_LINKED, UNDET_ARMS_UNTYPED,
                  UNDET_NO_EXPERIMENTAL_ARM, UNDET_ALL_BACKGROUND,
                  UNDET_EXPERIMENTAL_NO_DRUG, UNDET_ONLY_PLACEBO)
OUTCOMES = ROUTES + UNDETERMINABLE


class Selection(NamedTuple):
    outcome: str              # a ROUTE when agents were found, else an UNDETERMINABLE reason
    agents: tuple             # intervention ids, ascending
    dropped_placebo: tuple    # candidate intervention ids dropped as pure placebo
    # Drug interventions in a comparator arm that are not tested agents and not placebo,
    # ascending. Only under the arm rule: with no arms there is no comparator to name.
    comparators: tuple = ()

    @property
    def determined(self) -> bool:
        return self.outcome in ROUTES


def normalize_group_type(raw):
    """-> a GROUP_TYPES token, or None when blank. An unknown spelling raises."""
    s = str(raw or "").strip()
    if not s:
        return None
    token = re.sub(r"[\s\-]+", "_", s.upper())
    if token not in GROUP_TYPES:
        raise ValueError(f"arm type {raw!r} is not one of {sorted(GROUP_TYPES)}")
    return token


def is_drug_type(raw) -> bool:
    return str(raw or "").strip().lower() in DRUG_INTERVENTION_TYPES


def _is_placebo_intervention(name) -> bool:
    """A blank name is NOT a placebo: it stays a candidate and shows up as unresolved."""
    return bool(key(name)) and is_pure_placebo(name)


def _finish(candidates: list, names: dict, route: str, comparators=()) -> Selection:
    kept = sorted(i for i in candidates if not _is_placebo_intervention(names[i]))
    dropped = sorted(i for i in candidates if _is_placebo_intervention(names[i]))
    if not kept:
        return Selection(UNDET_ONLY_PLACEBO, (), tuple(dropped))
    comps = sorted(i for i in comparators
                   if i not in kept and not _is_placebo_intervention(names[i]))
    return Selection(route, tuple(kept), tuple(dropped), tuple(comps))


def select_tested_agents(interventions: Iterable, groups: dict, links: Iterable) -> Selection:
    """interventions: (intervention_id, intervention_type, name); groups: {arm id: raw
    group_type}; links: (arm id, intervention_id). All for ONE trial."""
    ivs = {iid: (itype, name) for iid, itype, name in interventions}
    arms = {gid: normalize_group_type(t) for gid, t in groups.items()}
    names = {iid: name for iid, (_t, name) in ivs.items()}
    drugs = [iid for iid, (t, _n) in ivs.items() if is_drug_type(t)]
    if not drugs:
        return Selection(UNDET_NO_DRUG_INTERVENTION, (), ())
    if not arms:
        return _finish(drugs, names, ROUTE_NO_ARMS_FALLBACK)

    kinds = {iid: set() for iid in ivs}
    for gid, iid in links:
        if iid not in ivs:
            raise ValueError(f"link to intervention {iid}, which is not this trial's")
        if gid not in arms:
            raise ValueError(f"link to arm {gid}, which is not this trial's")
        kinds[iid].add(arms[gid])
    linked = [i for i in drugs if kinds[i]]
    if not linked:
        return Selection(UNDET_NO_DRUG_LINKED, (), ())
    in_exp = [i for i in drugs if GROUP_EXPERIMENTAL in kinds[i]]
    candidates = [i for i in in_exp if not (kinds[i] & GROUP_COMPARATOR)]
    if candidates:
        in_comp = [i for i in drugs if kinds[i] & GROUP_COMPARATOR]
        return _finish(candidates, names, ROUTE_ARM_RULE, in_comp)
    if GROUP_EXPERIMENTAL not in arms.values():
        if None in arms.values():
            return Selection(UNDET_ARMS_UNTYPED, (), ())
        return Selection(UNDET_NO_EXPERIMENTAL_ARM, (), ())
    if in_exp:
        return Selection(UNDET_ALL_BACKGROUND, (), ())
    return Selection(UNDET_EXPERIMENTAL_NO_DRUG, (), ())