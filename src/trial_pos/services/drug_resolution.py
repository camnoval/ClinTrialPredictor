"""Trial-to-drug resolution: each tested agent of a drug trial -> DrugCentral drugs. Pure.

Decided 2026-10-07 (with the tested-agent rule in tested_agent.py):

  NAME     one registry name: the whole name first, through every variant step
           (drug_names.variants), every source (drug_dictionary.SOURCES), then the
           dictionary-side salt step; the first hit decides, and an AMBIGUOUS hit is final.
           Only when the whole name has no hit is it split into active components, each
           resolved the same way. Only when that resolves nothing is it split on commas
           too (drug_names.comma_components): last resort, its own route. No fuzzy
           matching.
  AGENT    one intervention: its own name, then each of its other names in byte order.
           The first name that fully resolves wins; otherwise the best of partial,
           ambiguous, unresolved, in that order.
  TRIAL    (D-6 as amended by D-24) four classes, MATCH_CLASSES:
             full           at least one tested agent resolves FULLY
             partial_only   none fully, at least one partly ('torcetrapib/atorvastatin'
                            -> atorvastatin only). Neither matched nor unmatched: excluded
                            from the market label and sampled as its own stratum, because
                            the part that resolves is often a background drug or excipient
             none           tested agents selected, none resolves
             undeterminable the tested agent could not be selected; nothing was looked up
           `matched` is True only for full, False only for none, None otherwise. The trial's
           `drugs` are the FULLY resolved agents' drugs only; partly resolved agents' drugs
           are kept apart in `partial_drugs`, so an excipient never becomes the identity.
  FORM     (D-12 to D-15, docs/Handoff_rev11.md) the drug ids are MOIETIES; the form is
           carried beside them: the salt and formulation/route classes stated in the
           intervention's name and other names, the dictionary entry that matched (a brand
           or ingredient entry pins the product), and how the match treated the stated form
           (FORM_HANDLINGS). Stripping a stated form to find the moiety is recorded, never
           silent.
  COMPARATOR  comparator-arm drugs are resolved the same way. A trial whose tested moiety
           also appears in a comparator arm tests a form, dose or regimen of one drug, and
           a moiety-level approval date says nothing about it.
  MESH     corroboration only. A trial's indexed (mesh-list) terms are resolved by name;
           a matched trial is corroborated when they share a drug. MeSH never creates a
           match: it cannot say which arm a term belongs to (51.4% of indexed terms match
           no intervention name, audit/probe_intervention_arms.py).
"""
from __future__ import annotations

from collections import Counter
from typing import Iterable, NamedTuple, Optional

from trial_pos.services.drug_dictionary import (
    HIT_AMBIGUOUS, HIT_RESOLVED, STEP_DICTIONARY_NO_SALT, DrugDictionary,
)
from trial_pos.services.drug_names import (
    NO_FORM, SALT_CANONICAL, tokens, active_components, comma_components, key, stated_form,
)
from trial_pos.services.tested_agent import ROUTE_ARM_RULE, Selection

AGENT_RESOLVED = "resolved"
AGENT_PARTIAL = "partial"
AGENT_AMBIGUOUS = "ambiguous"
AGENT_UNRESOLVED = "unresolved"
AGENT_STATUSES = (AGENT_RESOLVED, AGENT_PARTIAL, AGENT_AMBIGUOUS, AGENT_UNRESOLVED)
_RANK = {s: i for i, s in enumerate(AGENT_STATUSES)}
MATCHING_STATUSES = frozenset({AGENT_RESOLVED, AGENT_PARTIAL})

HOW_WHOLE = "whole_name"
HOW_COMPONENTS = "components"
HOW_COMMA_COMPONENTS = "comma_components"
HOW_NONE = "none"

NAME_PRIMARY = "intervention_name"
NAME_OTHER = "other_name"

DRUG_SEP = "|"
STEP_SEP = "+"
KEY_SEP = " + "

MATCH_FULL = "full"
MATCH_PARTIAL_ONLY = "partial_only"
MATCH_NONE = "none"
MATCH_UNDETERMINABLE = "undeterminable"
MATCH_CLASSES = (MATCH_FULL, MATCH_PARTIAL_ONLY, MATCH_NONE, MATCH_UNDETERMINABLE)
MATCHED_BY_CLASS = {MATCH_FULL: True, MATCH_NONE: False, MATCH_PARTIAL_ONLY: None,
                    MATCH_UNDETERMINABLE: None}

FORM_NONE_STATED = "no_form_stated"
FORM_KEPT = "stated_form_kept"
FORM_STRIPPED = "stated_form_stripped"
FORM_INFERRED = "salt_inferred"
FORM_HANDLINGS = (FORM_NONE_STATED, FORM_KEPT, FORM_STRIPPED, FORM_INFERRED)
FORM_HANDLING_DOC = {
    FORM_NONE_STATED: "the name used states no salt and no formulation or route",
    FORM_KEPT: "every stated salt and formulation word is in the dictionary entry matched",
    FORM_STRIPPED: "matching removed a stated salt or formulation word to find the moiety",
    FORM_INFERRED: ("the name states no salt; the dictionary-side salt step matched the only "
                    "salt DrugCentral holds"),
}


class NameResult(NamedTuple):
    status: str
    drugs: frozenset
    how: str
    step: str
    source: str
    n_components: int
    n_components_resolved: int
    matched_key: str = ""


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
    matched_key: str = ""
    form: tuple = NO_FORM            # drug_names.StatedForm over every name of the agent
    form_handling: str = ""          # FORM_HANDLINGS, blank when nothing matched


class TrialResult(NamedTuple):
    nct_id: str
    selection: Selection
    agents: tuple
    matched: Optional[bool]
    drugs: frozenset
    n_mesh_terms: int
    mesh_corroborated: Optional[bool]
    mesh_drugs_when_unmatched: frozenset
    comparators: tuple = ()
    comparator_drugs: frozenset = frozenset()
    tested_moiety_in_comparator: Optional[bool] = None
    parent_groups: frozenset = frozenset()     # DrugDictionary.parent_groups(drugs)
    match_class: str = MATCH_UNDETERMINABLE
    partial_drugs: frozenset = frozenset()     # drugs of partly resolved agents only


def _resolve_parts(d: DrugDictionary, parts: list, how: str, original: str = "") -> NameResult:
    drugs, steps, sources, keys, n_ok, any_ambiguous = set(), set(), set(), [], 0, False
    for part in parts:
        h, v = d.resolve(part, original=original)
        if h.status == HIT_RESOLVED:
            drugs |= h.drugs
            steps.add(v.step)
            sources.add(h.source)
            keys.append(v.key)
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
    return NameResult(status, frozenset(drugs), how if n_ok else HOW_NONE,
                      STEP_SEP.join(sorted(steps)), STEP_SEP.join(sorted(sources)),
                      len(parts), n_ok, KEY_SEP.join(keys))


def resolve_one_name(d: DrugDictionary, name, whole_intervention_name: bool = False) -> NameResult:
    """`whole_intervention_name`: the name is an intervention's own name, so an
    abbreviation-length key may match it exactly (D-20). Components never may."""
    hit, variant = d.resolve(name, allow_short=whole_intervention_name)
    if hit.status == HIT_RESOLVED:
        return NameResult(AGENT_RESOLVED, hit.drugs, HOW_WHOLE, variant.step, hit.source, 1, 1,
                          variant.key)
    if hit.status == HIT_AMBIGUOUS:
        return NameResult(AGENT_AMBIGUOUS, frozenset(), HOW_WHOLE, variant.step, hit.source,
                          1, 0, variant.key)
    parts = active_components(name)
    if not parts:
        return NameResult(AGENT_UNRESOLVED, frozenset(), HOW_NONE, "", "", 0, 0)
    result = _resolve_parts(d, parts, HOW_COMPONENTS, str(name or ""))
    if result.status != AGENT_UNRESOLVED:
        return result
    comma_parts = comma_components(name)
    if len(comma_parts) > len(parts):
        comma = _resolve_parts(d, comma_parts, HOW_COMMA_COMPONENTS, str(name or ""))
        if comma.status != AGENT_UNRESOLVED:
            return comma
    return result


def _byte_order(values: Iterable) -> list:
    return sorted({str(v) for v in values if key(v)}, key=lambda s: s.encode("utf-8"))


def form_handling(name_used: str, matched_key: str, step: str, status: str) -> str:
    """How a match treated the form stated in the name it used. Blank when nothing
    matched."""
    if status not in MATCHING_STATUSES:
        return ""
    used = stated_form([name_used])
    if STEP_DICTIONARY_NO_SALT in step.split(STEP_SEP) and not used.salts:
        return FORM_INFERRED
    if not used.stated:
        return FORM_NONE_STATED
    present = {SALT_CANONICAL.get(t, t) for t in tokens(key(matched_key))}
    return FORM_KEPT if set(used.salts) | set(used.words) <= present else FORM_STRIPPED


def resolve_agent(d: DrugDictionary, intervention_id: int, name,
                  other_names: Iterable = ()) -> AgentResult:
    other_names = list(other_names)
    tries = [(NAME_PRIMARY, str(name or ""))] + [(NAME_OTHER, o) for o in _byte_order(
        o for o in other_names if key(o) != key(name))]
    best = None
    for kind, candidate in tries:
        r = resolve_one_name(d, candidate, whole_intervention_name=kind == NAME_PRIMARY)
        if best is None or _RANK[r.status] < _RANK[best[2].status]:
            best = (kind, candidate, r)
        if r.status == AGENT_RESOLVED:
            break
    kind, used, r = best
    return AgentResult(intervention_id, r.status, r.drugs, used, kind, r.how, r.step,
                       r.source, r.n_components, r.n_components_resolved, r.matched_key,
                       stated_form([name] + other_names),
                       form_handling(used, r.matched_key, r.step, r.status))


def resolve_mesh(d: DrugDictionary, terms: Iterable) -> frozenset:
    """Drugs named by a trial's indexed MeSH terms, whole-term resolution only."""
    out = set()
    for t in terms:
        hit, _v = d.resolve(t)
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
                           frozenset(), match_class=MATCH_UNDETERMINABLE)
    agents = tuple(resolve_agent(d, iid, names[iid], other_names.get(iid, ()))
                   for iid in selection.agents)
    comps = tuple(resolve_agent(d, iid, names[iid], other_names.get(iid, ()))
                  for iid in selection.comparators)
    klass = match_class(agents)
    matched = MATCHED_BY_CLASS[klass]
    full = [a for a in agents if a.status == AGENT_RESOLVED]
    part = [a for a in agents if a.status == AGENT_PARTIAL]
    drugs = frozenset().union(*(a.drugs for a in full)) if full else frozenset()
    partial_drugs = frozenset().union(*(a.drugs for a in part)) if part else frozenset()
    comp_drugs = frozenset().union(*(c.drugs for c in comps)) if comps else frozenset()
    corroborated = bool(drugs & mesh_drugs) if terms and matched else None
    return TrialResult(nct_id, selection, agents, matched, drugs, len(terms), corroborated,
                       frozenset() if matched else mesh_drugs, comps, comp_drugs,
                       moiety_in_comparator(selection, bool(matched), drugs, comps, d),
                       d.parent_groups(drugs), klass, partial_drugs)


def match_class(agents: tuple) -> str:
    """D-24: full / partial_only / none for agents that were selected."""
    if any(a.status == AGENT_RESOLVED for a in agents):
        return MATCH_FULL
    if any(a.status == AGENT_PARTIAL for a in agents):
        return MATCH_PARTIAL_ONLY
    return MATCH_NONE


def moiety_in_comparator(selection: Selection, matched: bool, drugs: frozenset,
                         comps: tuple, d: Optional[DrugDictionary] = None) -> Optional[bool]:
    """True: a tested moiety is also a comparator moiety, by structure id or, when `d` is
    given, by DrugCentral parent group (two salts of one parent). False: no comparator drug,
    or every comparator resolved and none shares a moiety. None: nothing to compare (no arms,
    tested agent unmatched) or an unresolved comparator could still be the same moiety."""
    if selection.outcome != ROUTE_ARM_RULE or not matched:
        return None
    comp_drugs = frozenset().union(*(c.drugs for c in comps)) if comps else frozenset()
    if drugs & comp_drugs:
        return True
    if d is not None and d.parent_groups(drugs) & d.parent_groups(comp_drugs):
        return True
    if all(c.status == AGENT_RESOLVED for c in comps):
        return False
    return None


# ---- serialisation and tallies --------------------------------------------------------------
def drug_text(drugs: Iterable[int]) -> str:
    return DRUG_SEP.join(str(x) for x in sorted(drugs))


def tribool_text(v: Optional[bool]) -> str:
    return "" if v is None else ("True" if v else "False")


AGENT_COLUMNS = ("nct_id", "intervention_id", "selection_route", "agent_status", "drugs",
                 "name_used", "name_kind", "how", "step", "source", "n_components",
                 "n_components_resolved", "matched_key", "stated_salts",
                 "stated_form_classes", "stated_form_words", "salt_conflict",
                 "form_handling")
TRIAL_COLUMNS = ("nct_id", "selection_outcome", "n_agents", "n_agents_matching",
                 "match_class", "matched", "drugs", "partial_drugs", "parent_groups", "any_form_stated", "n_mesh_terms",
                 "mesh_corroborated",
                 "mesh_drugs_when_unmatched", "n_placebo_dropped", "n_comparators",
                 "comparator_drugs", "tested_moiety_in_comparator")
LIST_SEP = "|"


def agent_rows(t: TrialResult) -> list:
    return [{"nct_id": t.nct_id, "intervention_id": a.intervention_id,
             "selection_route": t.selection.outcome, "agent_status": a.status,
             "drugs": drug_text(a.drugs), "name_used": a.name_used, "name_kind": a.name_kind,
             "how": a.how, "step": a.step, "source": a.source,
             "n_components": a.n_components,
             "n_components_resolved": a.n_components_resolved,
             "matched_key": a.matched_key, "stated_salts": LIST_SEP.join(a.form.salts),
             "stated_form_classes": LIST_SEP.join(a.form.classes),
             "stated_form_words": LIST_SEP.join(a.form.words),
             "salt_conflict": tribool_text(len(a.form.salts) > 1),
             "form_handling": a.form_handling} for a in t.agents]


def trial_row(t: TrialResult) -> dict:
    return {"nct_id": t.nct_id, "selection_outcome": t.selection.outcome,
            "n_agents": len(t.agents),
            "n_agents_matching": sum(a.status in MATCHING_STATUSES for a in t.agents),
            "match_class": t.match_class,
            "matched": tribool_text(t.matched), "drugs": drug_text(t.drugs),
            "partial_drugs": drug_text(t.partial_drugs),
            "parent_groups": LIST_SEP.join(sorted(t.parent_groups)),
            "any_form_stated": tribool_text(any(a.form.stated for a in t.agents)
                                            if t.agents else None),
            "n_mesh_terms": t.n_mesh_terms,
            "mesh_corroborated": tribool_text(t.mesh_corroborated),
            "mesh_drugs_when_unmatched": drug_text(t.mesh_drugs_when_unmatched),
            "n_placebo_dropped": len(t.selection.dropped_placebo),
            "n_comparators": len(t.comparators),
            "comparator_drugs": drug_text(t.comparator_drugs),
            "tested_moiety_in_comparator": tribool_text(t.tested_moiety_in_comparator)}


def tally(results: Iterable[TrialResult]) -> dict:
    """Every count the audit prints, with a zero for every vocabulary entry."""
    from trial_pos.services.tested_agent import OUTCOMES
    outcomes = Counter({o: 0 for o in OUTCOMES})
    statuses = Counter({s: 0 for s in AGENT_STATUSES})
    matched = Counter({True: 0, False: 0, None: 0})
    classes_by_trial = Counter({c: 0 for c in MATCH_CLASSES})
    corroborated = Counter({True: 0, False: 0, None: 0})
    routes, sources = Counter(), Counter()
    handling = Counter({h: 0 for h in FORM_HANDLINGS})
    classes, salt_conflicts = Counter(), 0
    in_comparator = Counter({True: 0, False: 0, None: 0})
    unmatched_with_mesh_drug = 0
    for t in results:
        outcomes[t.selection.outcome] += 1
        matched[t.matched] += 1
        classes_by_trial[t.match_class] += 1
        corroborated[t.mesh_corroborated] += 1
        if t.match_class in (MATCH_NONE, MATCH_PARTIAL_ONLY) and t.mesh_drugs_when_unmatched:
            unmatched_with_mesh_drug += 1
        in_comparator[t.tested_moiety_in_comparator] += 1
        for a in t.agents:
            statuses[a.status] += 1
            classes.update(a.form.classes)
            salt_conflicts += len(a.form.salts) > 1
            if a.form_handling:
                handling[a.form_handling] += 1
            if a.status in MATCHING_STATUSES:
                routes[(a.name_kind, a.how, a.step)] += 1
                sources[a.source] += 1
    return {"outcomes": outcomes, "statuses": statuses, "matched": matched,
            "match_classes": classes_by_trial,
            "corroborated": corroborated, "routes": routes, "sources": sources,
            "unmatched_with_mesh_drug": unmatched_with_mesh_drug,
            "form_handling": handling, "form_classes": classes,
            "salt_conflicts": salt_conflicts, "tested_moiety_in_comparator": in_comparator}
