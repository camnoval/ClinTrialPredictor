"""The market label and the readout-to-first-approval lag. Pure.

THE TARGET (owner, D-32, 2026-10-13): `market_fda_any_pivotal_wW` -- a tested agent that was
NOT approved before the readout is approved by FDA (NDA/BLA, D-9) for ANY indication within
W years after the readout. It is not indication-specific: Drugs@FDA carries no indication,
and rev 10 measured the indication crosswalk at 36.8% of DrugCentral's indication concepts.
Rev 7 section 1.2's `market_fda_indication` is not built.

Approval is form-level (D-12 to D-14), combinations count (D-32), a brand pins its family
(D-33): all of that lives in services/form_approval.py, read here through its output file.

WHICH TRIALS CARRY A LABEL, in this order, each a countable reason:
  1. eligibility.eligible_for_market: pivotal phase, actual readout, window closed,
     prospectively registered. Its verdict and reason are kept as they are.
  2. the trial's tested agents fully resolved (D-24). Partial-only, unmatched and
     undeterminable selections are UNDETERMINABLE, never 0: an unidentified drug is not a
     drug that failed.
  3. the floor (rev 7 section 1.2.3): a trial whose every tested agent was approved BEFORE
     its readout is INELIGIBLE. Its label would be a fact that existed before the trial
     reported, and a model would learn the prior approval instead of the question -- the
     same principle that excludes phase 4.
  4. the label, over the agents not approved before the readout:
       1  one of them is approved within W (proved by a strict product)
       0  none is, and no tested agent's prior status or window answer is unknown
     otherwise UNDETERMINABLE.

THE LAG (D-10): per agent identity (moiety set, stated salts and form words, pinned brand),
the earliest pivotal readout at which it was not yet approved, among readouts with at least
H years of follow-up before the snapshot. The lag is readout to first approval at or after
it. Reported at both bounds: loose (earliest possible) and strict (earliest proved).
"""
from __future__ import annotations

import math
from datetime import date
from typing import Iterable, NamedTuple, Optional

from trial_pos.services.drug_resolution import (
    MATCH_FULL, MATCH_NONE, MATCH_PARTIAL_ONLY, MATCH_UNDETERMINABLE,
)
from trial_pos.services.eligibility import ELIGIBLE, INELIGIBLE, UNDETERMINABLE, years_after
from trial_pos.services.form_approval import (
    STATUS_BEFORE, STATUS_NOT, AgentApproval, approved_within, before_status, first_after,
)

TARGET_NAME = "market_fda_any_pivotal_w{w}"

REASON_PARTIAL_ONLY = "tested_agents_only_partly_resolved"
REASON_UNRESOLVED = "no_tested_agent_resolved"
REASON_SELECTION_UNDETERMINABLE = "tested_agent_undeterminable"
REASON_PRIOR_APPROVAL = "every_tested_agent_approved_before_readout"
REASON_PRIOR_UNKNOWN = "a_tested_agent_prior_approval_undeterminable"
REASON_WINDOW_UNKNOWN = "approval_within_window_undeterminable"
REASON_LOOSE_REGIMEN = "tested_agents_approved_or_a_loose_regimen_of_approved_drugs"
MARKET_LABEL_REASONS = (REASON_PARTIAL_ONLY, REASON_UNRESOLVED, REASON_SELECTION_UNDETERMINABLE,
                        REASON_PRIOR_APPROVAL, REASON_PRIOR_UNKNOWN, REASON_WINDOW_UNKNOWN,
                        REASON_LOOSE_REGIMEN)
_MATCH_REASON = {MATCH_PARTIAL_ONLY: REASON_PARTIAL_ONLY, MATCH_NONE: REASON_UNRESOLVED,
                 MATCH_UNDETERMINABLE: REASON_SELECTION_UNDETERMINABLE}

LABEL_POSITIVE, LABEL_NEGATIVE = 1, 0


def _record(verdict: str, reason: str, label: Optional[int]) -> dict:
    return {"verdict": verdict, "reason": reason, "label": label}


def is_loose_regimen(a: AgentApproval, moieties: Iterable[int], readout: date,
                     fda_first: dict) -> bool:
    """D-45: a multi-drug agent not approved before the readout, whose every moiety
    DrugCentral dates as FDA-approved before it, and for which no product containing them
    all was ever approved (cisplatin + gemcitabine). Its drugs are on the market; no
    combination product can make it a success, so a 0 would be true by construction."""
    ms = list(moieties)
    return (len(ms) > 1 and a.n_identity == 0
            and before_status(a, readout) == STATUS_NOT
            and all(m in fda_first and fda_first[m] < readout for m in ms))


def trial_market_label(eligibility: dict, match_class: str, agents: list,
                       readout: Optional[date], window_years: int,
                       loose: Optional[list] = None) -> dict:
    """One trial -> {verdict, reason, label}. `eligibility` is eligible_for_market's record;
    `agents` the AgentApproval of every fully resolved tested agent; `loose`, aligned with
    `agents`, which of them are D-45 loose regimens (treated like agents approved before
    the readout)."""
    if eligibility["verdict"] != ELIGIBLE:
        return _record(eligibility["verdict"], eligibility["reason"], None)
    if match_class != MATCH_FULL:
        if match_class not in _MATCH_REASON:
            raise ValueError(f"match class {match_class!r}")
        return _record(UNDETERMINABLE, _MATCH_REASON[match_class], None)
    if not agents:
        raise ValueError("a full-match trial with no resolved agent: inputs disagree")
    if readout is None:
        raise ValueError("an eligible market trial without an actual readout")
    end = years_after(readout, window_years)
    loose = list(loose) if loose is not None else [False] * len(agents)
    if len(loose) != len(agents):
        raise ValueError("`loose` must align with `agents`")
    statuses = [before_status(a, readout) for a in agents]
    new = [a for a, s, lo in zip(agents, statuses, loose) if s == STATUS_NOT and not lo]
    unknown = any(s not in (STATUS_BEFORE, STATUS_NOT) for s in statuses)
    within = [approved_within(a, readout, end) for a in new]
    if any(w is True for w in within):
        return _record(ELIGIBLE, "", LABEL_POSITIVE)
    if unknown:
        return _record(UNDETERMINABLE, REASON_PRIOR_UNKNOWN, None)
    if not new:
        return _record(INELIGIBLE, REASON_LOOSE_REGIMEN if any(loose) else REASON_PRIOR_APPROVAL,
                       None)
    if any(w is None for w in within):
        return _record(UNDETERMINABLE, REASON_WINDOW_UNKNOWN, None)
    return _record(ELIGIBLE, "", LABEL_NEGATIVE)


# ---- the lag -------------------------------------------------------------------------------
DAYS_PER_YEAR = 365.25          # lags are printed in years; window membership uses calendars
PERCENTILES = (50, 75, 90)
LAG_DECISION_PERCENTILE = 90    # D-10
D10_LOWER_YEARS, D10_UPPER_YEARS = 3, 5
BOUND_LOOSE, BOUND_STRICT = "loose", "strict"
BOUNDS = (BOUND_LOOSE, BOUND_STRICT)


class LagCase(NamedTuple):
    identity: tuple
    nct_id: str
    readout: date
    first_loose: Optional[date]
    first_strict: Optional[date]


def identity_key(drugs: str, salts: str, words: str, brand: str) -> tuple:
    """Agent identity for the decisive figure (D-10 per drug, D-13 identity, D-33 brand)."""
    return (drugs, salts, words, brand)


def decisive_cases(records: Iterable[tuple], snapshot: date, follow_up_years: int) -> list:
    """records: (identity, nct_id, readout, AgentApproval) for PIVOTAL agents with an actual
    readout. Keeps, per identity, its earliest readout at which it was not approved before
    and that has `follow_up_years` of follow-up; ties broken by nct_id."""
    best = {}
    for identity, nct, readout, a in records:
        if readout is None:
            raise ValueError("lag records need an actual readout")
        if years_after(readout, follow_up_years) > snapshot:
            continue
        if before_status(a, readout) != STATUS_NOT:
            continue
        key = (readout, nct)
        if identity not in best or key < best[identity][0]:
            lo, st = first_after(a, readout)
            best[identity] = (key, LagCase(identity, nct, readout, lo, st))
    return [case for _k, case in sorted(best.values(), key=lambda kv: (kv[0], kv[1].identity))]


def nearest_rank(values: list, pct: float) -> float:
    """The nearest-rank percentile: the smallest value with at least pct% at or below it."""
    if not values:
        raise ValueError("percentile of nothing")
    ordered = sorted(values)
    rank = max(1, math.ceil(pct / 100.0 * len(ordered)))
    return ordered[rank - 1]


def _first(case: LagCase, bound: str) -> Optional[date]:
    return case.first_loose if bound == BOUND_LOOSE else case.first_strict


def lag_summary(cases: list, follow_up_years: int, windows: Iterable[int]) -> dict:
    """Per bound: identities, approved within the follow-up, lag percentiles in years over
    those, and the share of them approved within each window."""
    out = {"identities": len(cases), "follow_up_years": follow_up_years}
    for bound in BOUNDS:
        approved = [c for c in cases if _first(c, bound) is not None
                    and _first(c, bound) <= years_after(c.readout, follow_up_years)]
        lags = [(_first(c, bound) - c.readout).days / DAYS_PER_YEAR for c in approved]
        out[bound] = {
            "approved_within_follow_up": len(approved),
            "percentiles": {p: nearest_rank(lags, p) for p in PERCENTILES} if lags else {},
            "within_window": {w: sum(1 for c in approved
                                     if _first(c, bound) <= years_after(c.readout, w))
                              for w in windows},
        }
    return out


DECISION_W3 = "W=3 stands (p90 at or under 3 years)"
DECISION_W5 = "W=5 becomes the headline, W=3 the sensitivity (p90 between 3 and 5 years)"
DECISION_BEYOND = "beyond D-10's decision line (p90 over 5 years): the owner decides"
DECISION_NONE = "no approved identity: nothing to decide on"


def decision_line(p90_years: Optional[float]) -> str:
    if p90_years is None:
        return DECISION_NONE
    if p90_years <= D10_LOWER_YEARS:
        return DECISION_W3
    if p90_years <= D10_UPPER_YEARS:
        return DECISION_W5
    return DECISION_BEYOND
