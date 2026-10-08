"""Trial-to-drug resolution: each tested agent of a drug trial -> DrugCentral drugs. Pure.

Decided 2026-10-07 (with the tested-agent rule in tested_agent.py):

  NAME     one registry name: the whole name first, through every variant step
           (drug_names.variants) and every source (drug_dictionary.SOURCES); the first hit
           decides, and an AMBIGUOUS hit is final. Only when the whole name has no hit is
           it split into active components, each resolved the same way. No fuzzy matching.
  AGENT    one intervention: its own name, then each of its other names in byte order.
           The first name that fully resolves wins; otherwise the best of partial,
           ambiguous, unresolved, in that order.
  TRIAL    matched when AT LEAST ONE tested agent resolves fully or partly. Undeterminable
           (None) when the tested agent could not be selected: never counted as unmatched,
           because nothing was looked up.
  MESH     corroboration only. A trial's indexed (mesh-list) terms are resolved by name;
           a matched trial is corroborated when they share a drug. MeSH never creates a
           match: it cannot say which arm a term belongs to (51.4% of indexed terms match
           no intervention name, audit/probe_intervention_arms.py).
"""
from __future__ import annotations

from collections import Counter
from typing import Iterable, NamedTuple, Optional

from trial_pos.services.drug_dictionary import (
    HIT_AMBIGUOUS, HIT_RESOLVED, DrugDictionary,
)
from trial_pos.services.drug_names import active_components, key
from trial_pos.services.tested_agent import Selection

AGENT_RESOLVED = "resolved"
AGENT_PARTIAL = "partial"
AGENT_AMBIGUOUS = "ambiguous"
AGENT_UNRESOLVED = "unresolved"
AGENT_STATUSES = (AGENT_RESOLVED, AGENT_PARTIAL, AGENT_AMBIGUOUS, AGENT_UNRESOLVED)
_RANK = {s: i for i, s in enumerate(AGENT_STATUSES)}
MATCHING_STATUSES = frozenset({AGENT_RESOLVED, AGENT_PARTIAL})

HOW_WHOLE = "whole_name"
HOW_COMPONENTS = "components"
HOW_NONE = "none"

NAME_PRIMARY = "intervention_name"
NAME_OTHER = "other_name"

DRUG_SEP = "|"
STEP_SEP = "+"


class NameResult(NamedTuple):
    status: str
    drugs: frozenset
    how: str
    step: str
    source: str
    n_components: int
    n_components_resolved: int


class AgentResult(NamedTuple):
    intervention_id: int
    status: str
    drugs: frozenset
    name_used: str
    name_kind: str
    how: str
    step: str
    source: str
    n_components: int
    n_components_resolved: int


class TrialResult(NamedTuple):
    nct_id: str
    selection: Selection
    agents: tuple
    matched: Optional[bool]
    drugs: frozenset
    n_mesh_terms: int
    mesh_corroborated: Optional[bool]
    mesh_drugs_when_unmatched: frozenset


def resolve_one_name(d: DrugDictionary, name) -> NameResult:
    hit, variant = d.resolve_name(name)
    if hit.status == HIT_RESOLVED:
        return NameResult(AGENT_RESOLVED, hit.drugs, HOW_WHOLE, variant.step, hit.source, 1, 1)
    if hit.status == HIT_AMBIGUOUS:
        return NameResult(AGENT_AMBIGUOUS, frozenset(), HOW_WHOLE, variant.step, hit.source,
                          1, 0)
    parts = active_components(name)
    if not parts:
        return NameResult(AGENT_UNRESOLVED, frozenset(), HOW_NONE, "", "", 0, 0)
    drugs, steps, sources, n_ok, any_ambiguous = set(), set(), set(), 0, False
    for part in parts:
        h, v = d.resolve_name(part)
        if h.status == HIT_RESOLVED:
            drugs |= h.drugs
            steps.add(v.step)
            sources.add(h.source)
            n_ok += 1
        elif h.status == HIT_AMBIGUOUS:
            any_ambiguous = True
    if n_ok == len(parts):
        status = AGENT_RESOLVED
    elif n_ok:
        status = AGENT_PARTIAL
    elif any_ambiguous:
        status = AGENT_AMBIGUOUS
    else:
        status = AGENT_UNRESOLVED
    return NameResult(status, frozenset(drugs), HOW_COMPONENTS if n_ok else HOW_NONE,
                      STEP_SEP.join(sorted(steps)), STEP_SEP.join(sorted(sources)),
                      len(parts), n_ok)


def _byte_order(values: Iterable) -> list:
    return sorted({str(v) for v in values if key(v)}, key=lambda s: s.encode("utf-8"))


def resolve_agent(d: DrugDictionary, intervention_id: int, name,
                  other_names: Iterable = ()) -> AgentResult:
    tries = [(NAME_PRIMARY, str(name or ""))] + [(NAME_OTHER, o) for o in _byte_order(
        o for o in other_names if key(o) != key(name))]
    best = None
    for kind, candidate in tries:
        r = resolve_one_name(d, candidate)
        if best is None or _RANK[r.status] < _RANK[best[2].status]:
            best = (kind, candidate, r)
        if r.status == AGENT_RESOLVED:
            break
    kind, used, r = best
    return AgentResult(intervention_id, r.status, r.drugs, used, kind, r.how, r.step,
                       r.source, r.n_components, r.n_components_resolved)


def resolve_mesh(d: DrugDictionary, terms: Iterable) -> frozenset:
    """Drugs named by a trial's indexed MeSH terms, whole-term resolution only."""
    out = set()
    for t in terms:
        hit, _v = d.resolve_name(t)
        if hit.status == HIT_RESOLVED:
            out |= hit.drugs
    return frozenset(out)


def resolve_trial(d: DrugDictionary, nct_id: str, selection: Selection, names: dict,
                  other_names: dict, mesh_terms: Iterable = ()) -> TrialResult:
    """names: {intervention id: name}; other_names: {intervention id: [names]}."""
    terms = [t for t in mesh_terms if key(t)]
    mesh_drugs = resolve_mesh(d, terms)
    if not selection.determined:
        return TrialResult(nct_id, selection, (), None, frozenset(), len(terms), None,
                           frozenset())
    agents = tuple(resolve_agent(d, iid, names[iid], other_names.get(iid, ()))
                   for iid in selection.agents)
    matched = any(a.status in MATCHING_STATUSES for a in agents)
    drugs = frozenset().union(*(a.drugs for a in agents)) if agents else frozenset()
    if not terms or not matched:
        corroborated = None
    else:
        corroborated = bool(drugs & mesh_drugs)
    return TrialResult(nct_id, selection, agents, matched, drugs, len(terms), corroborated,
                       frozenset() if matched else mesh_drugs)


# ---- serialisation and tallies --------------------------------------------------------------
def drug_text(drugs: Iterable[int]) -> str:
    return DRUG_SEP.join(str(x) for x in sorted(drugs))


def tribool_text(v: Optional[bool]) -> str:
    return "" if v is None else ("True" if v else "False")


AGENT_COLUMNS = ("nct_id", "intervention_id", "selection_route", "agent_status", "drugs",
                 "name_used", "name_kind", "how", "step", "source", "n_components",
                 "n_components_resolved")
TRIAL_COLUMNS = ("nct_id", "selection_outcome", "n_agents", "n_agents_matching", "matched",
                 "drugs", "n_mesh_terms", "mesh_corroborated", "mesh_drugs_when_unmatched",
                 "n_placebo_dropped")


def agent_rows(t: TrialResult) -> list:
    return [{"nct_id": t.nct_id, "intervention_id": a.intervention_id,
             "selection_route": t.selection.outcome, "agent_status": a.status,
             "drugs": drug_text(a.drugs), "name_used": a.name_used, "name_kind": a.name_kind,
             "how": a.how, "step": a.step, "source": a.source,
             "n_components": a.n_components,
             "n_components_resolved": a.n_components_resolved} for a in t.agents]


def trial_row(t: TrialResult) -> dict:
    return {"nct_id": t.nct_id, "selection_outcome": t.selection.outcome,
            "n_agents": len(t.agents),
            "n_agents_matching": sum(a.status in MATCHING_STATUSES for a in t.agents),
            "matched": tribool_text(t.matched), "drugs": drug_text(t.drugs),
            "n_mesh_terms": t.n_mesh_terms,
            "mesh_corroborated": tribool_text(t.mesh_corroborated),
            "mesh_drugs_when_unmatched": drug_text(t.mesh_drugs_when_unmatched),
            "n_placebo_dropped": len(t.selection.dropped_placebo)}


def tally(results: Iterable[TrialResult]) -> dict:
    """Every count the audit prints, with a zero for every vocabulary entry."""
    from trial_pos.services.tested_agent import OUTCOMES
    outcomes = Counter({o: 0 for o in OUTCOMES})
    statuses = Counter({s: 0 for s in AGENT_STATUSES})
    matched = Counter({True: 0, False: 0, None: 0})
    corroborated = Counter({True: 0, False: 0, None: 0})
    routes, sources = Counter(), Counter()
    unmatched_with_mesh_drug = 0
    for t in results:
        outcomes[t.selection.outcome] += 1
        matched[t.matched] += 1
        corroborated[t.mesh_corroborated] += 1
        if t.matched is False and t.mesh_drugs_when_unmatched:
            unmatched_with_mesh_drug += 1
        for a in t.agents:
            statuses[a.status] += 1
            if a.status in MATCHING_STATUSES:
                routes[(a.name_kind, a.how, a.step)] += 1
                sources[a.source] += 1
    return {"outcomes": outcomes, "statuses": statuses, "matched": matched,
            "corroborated": corroborated, "routes": routes, "sources": sources,
            "unmatched_with_mesh_drug": unmatched_with_mesh_drug}
