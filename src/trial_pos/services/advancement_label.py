"""The advancement label (D-37, D-38). Pure.

THE TARGET: an early-phase trial's drug reaches a strictly later phase IN THE SAME DISEASE
within W years after the trial's readout. Owner, 2026-10-13: "psoriatic for psoriatic".

LINKAGE (D-37): the registry's own tested-agent names and codes, never the DrugCentral
moiety. The moiety key had no key for 53% of early trials and an 81% link rate where it
did: it kept the later-approved (audit/probe_advancement_links.py). A later trial counts
when it shares a NAME KEY, has a strictly higher phase rank, matches the disease, and was
first submitted to the registry between this trial's own first submission and readout + W.
The registration date is used because it is always recorded and never a plan.

SAME DISEASE (D-37): by MeSH condition terms (browse_conditions).
  exact   B, the owner's choice and the headline: a shared mesh-list term.
  family  C, the sensitivity: that, or one trial's own mesh-list term is among the other's
          mesh-ancestor terms ('Carcinoma, Non-Small-Cell Lung' under 'Lung Neoplasms').
          Siblings never match: rheumatoid and psoriatic arthritis share only 'Arthritis'.

WHICH TRIALS CARRY A LABEL, in order, each a countable reason:
  1. eligibility.eligible_for_advancement (drug trial, closed window, prospective).
  2. an EARLY phase (eligibility.EARLY_PHASES). Phase 3 is out: its next step, approval, is
     the market label (proposed 2026-10-13, owner did not object).
  3. a tested-agent name key, condition terms and a registration date; else
     UNDETERMINABLE.
  4. the floor (D-38): a same-disease, later-phase trial of the drug registered BEFORE this
     one makes it INELIGIBLE. Its label would be known in advance.
  5. 1 when a linked trial exists, else 0.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date
from typing import Iterable, NamedTuple, Optional

from trial_pos.services.drug_names import compact, drop_dose_and_form, drop_salt, key
from trial_pos.services.eligibility import (
    EARLY_PHASES, ELIGIBLE, INELIGIBLE, UNDETERMINABLE, years_after,
)

# Strictly higher rank = a later rung. Combined phases sit between their two halves.
PHASE_RANK = {"EARLY_PHASE1": 0, "PHASE1": 1, "PHASE1/PHASE2": 2, "PHASE2": 3,
              "PHASE2/PHASE3": 4, "PHASE3": 5, "PHASE4": 6}
MIN_NAME_KEY = 4                  # D-20: shorter compact keys are abbreviations
DISEASE_EXACT, DISEASE_FAMILY = "exact", "family"
DISEASE_RULES = (DISEASE_EXACT, DISEASE_FAMILY)
TARGET_NAME = "advancement_same_disease_w{w}_{rule}"

REASON_NOT_EARLY = "not_an_early_phase"
REASON_NO_NAME = "no_tested_agent_name_key"
REASON_NO_CONDITION = "no_condition_terms"
REASON_NO_REGISTRATION = "registration_date_unknown"
REASON_FLOOR = "a_later_phase_trial_in_this_disease_registered_first"
ADVANCEMENT_REASONS = (REASON_NOT_EARLY, REASON_NO_NAME, REASON_NO_CONDITION,
                       REASON_NO_REGISTRATION, REASON_FLOOR)
LABEL_POSITIVE, LABEL_NEGATIVE = 1, 0


class TrialFacts(NamedTuple):
    nct_id: str
    phase: Optional[str]          # eligibility.normalize_phase
    submitted: Optional[date]
    readout: Optional[date]
    names: frozenset              # name keys of the tested agents
    mesh: frozenset               # mesh-list condition terms
    ancestors: frozenset          # mesh-ancestor condition terms


def name_keys(name: str) -> set:
    """Compact name; without dose and form words; and without trailing salts. Several,
    because dose stripping also takes a spaced code's number ('xyz 123' -> 'xyz') and a
    salt hides the same drug ('cediranib maleate')."""
    base = key(name)
    no_form = drop_dose_and_form(base)
    return {k for k in (compact(base), compact(no_form), compact(drop_salt(no_form)))
            if len(k) >= MIN_NAME_KEY}


def same_disease(a: TrialFacts, b: TrialFacts, rule: str) -> bool:
    if a.mesh & b.mesh:
        return True
    if rule == DISEASE_EXACT:
        return False
    if rule == DISEASE_FAMILY:
        return bool(a.mesh & b.ancestors or b.mesh & a.ancestors)
    raise ValueError(f"disease rule {rule!r} not in {DISEASE_RULES}")


class AdvancementIndex:
    """Trials that can be a later rung: a known phase, a registration date, a name key."""

    def __init__(self, trials: Iterable[TrialFacts]):
        self.by_key = defaultdict(list)
        for t in trials:
            if t.phase in PHASE_RANK and t.submitted is not None:
                for k in t.names:
                    self.by_key[k].append(t)


def _record(verdict: str, reason: str, label: Optional[int]) -> dict:
    return {"verdict": verdict, "reason": reason, "label": label}


def trial_advancement_label(eligibility: dict, t: TrialFacts, index: AdvancementIndex,
                            window_years: int, rule: str) -> dict:
    if eligibility["verdict"] != ELIGIBLE:
        return _record(eligibility["verdict"], eligibility["reason"], None)
    if t.phase not in EARLY_PHASES:
        return _record(INELIGIBLE, REASON_NOT_EARLY, None)
    if not t.names:
        return _record(UNDETERMINABLE, REASON_NO_NAME, None)
    if not t.mesh:
        return _record(UNDETERMINABLE, REASON_NO_CONDITION, None)
    if t.submitted is None or t.readout is None:
        return _record(UNDETERMINABLE, REASON_NO_REGISTRATION, None)
    end = years_after(t.readout, window_years)
    rank, linked = PHASE_RANK[t.phase], False
    for k in sorted(t.names):
        for o in index.by_key.get(k, ()):
            if o.nct_id == t.nct_id or PHASE_RANK[o.phase] <= rank:
                continue
            if not same_disease(t, o, rule):
                continue
            if o.submitted < t.submitted:
                return _record(INELIGIBLE, REASON_FLOOR, None)
            if o.submitted <= end:
                linked = True
    return _record(ELIGIBLE, "", LABEL_POSITIVE if linked else LABEL_NEGATIVE)
