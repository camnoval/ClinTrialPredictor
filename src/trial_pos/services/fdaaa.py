"""FDAAA applicability: was this trial REQUIRED to post results? Written once, explicitly.

WHY THIS IS A SEPARATE MODULE AND NOT PART OF THE PULL
=====================================================
Applicability is a derivation whose inputs are partly sponsor self-reported on a form that
postdates the 2017 Final Rule. Deciding it inside a pull would bury a judgment where nobody
could audit it, which is why `population.py` carries the components and refuses the verdict,
and why a test there fails if an applicability function appears in it.

This is that verdict, in one place, with every branch stated. The pull must not import it.

WHAT THE STATUTE SAYS, AND WHAT AACT CAN SEE
============================================
An "applicable clinical trial" of a drug or biologic is, in outline: an interventional study
of an FDA-regulated product, OTHER THAN a phase-1-only study, that satisfies at least one
jurisdictional hook. The hooks are three:

  1. conducted under an IND or IDE
  2. at least one study site in the United States or its territories
  3. the product is manufactured in the US and exported for study

**AACT can see hooks 2 and 3. It cannot see hook 1.** There is no IND field. That single
gap decides the shape of this entire module, because it makes the rule ONE-DIRECTIONAL:

  - a visible hook means the jurisdictional condition IS satisfied -> a positive verdict is
    available
  - NEITHER visible hook does NOT mean the condition fails, because the trial may have run
    under an IND with no US site -> the verdict is UNDETERMINABLE, never "not applicable"

So confident NOT-APPLICABLE verdicts can only come from the conditions AACT observes fully:
the era (a trial completing before the statute took effect), the phase-1 exclusion, and the
absence of any FDA-regulated product. Everything else is either applicable or unknown. A
rule that returned "not applicable" on a missing US facility would be asserting the absence
of an IND it cannot observe, and the share of the population it would mislabel is large:
`has_us_facility` is false for 55.4% of trials.

WHY THE VERDICT MATTERS FOR THE MODEL, NOT JUST FOR COMPLIANCE
==============================================================
Non-posting means two different things either side of this line. Among trials that were
REQUIRED to post, silence is non-compliance and is plausibly correlated with the result --
sponsors with bad news post late or not at all, which is precisely the selection that makes
the endpoint-met slice non-random. Among trials under no obligation, silence is a free
choice and carries much weaker information about the outcome.

The phase-1 exemption is the clearest case and it is already visible in the disclosure
rates: phase 1 drug trials post at 16.9% against phase 3's 52.2%. A large part of that gap
is not reticence, it is that the statute never asked.

PHASE 1/PHASE 2 IS A JUDGMENT, AND IT IS FLAGGED AS ONE
=======================================================
The exclusion is for a study that is phase 1 ONLY. A trial registered as PHASE1/PHASE2 is
not phase-1-only on its face, so it is treated as in scope. That is a reading, not a fact,
and it moves 14,740 drug trials. `PHASE1_ONLY_PHASES` is the named set so the reading can be
changed in one place and the count of affected trials reported.
"""
from __future__ import annotations

from datetime import date
from typing import Optional

from trial_pos.services.population import (
    ERA_FINAL_RULE, ERA_PRE_FDAAA, UNKNOWN, era_for_row, tribool,
)

# ---- verdicts -------------------------------------------------------------
APPLICABLE = "applicable"
NOT_APPLICABLE = "not_applicable"
UNDETERMINABLE = "undeterminable"

APPLICABILITY_VERDICTS = (APPLICABLE, NOT_APPLICABLE, UNDETERMINABLE)

VERDICT_DOC = {
    APPLICABLE: ("every condition the statute imposes is satisfied on the evidence AACT "
                 "carries, so non-posting here is non-compliance"),
    NOT_APPLICABLE: ("a condition AACT can observe FULLY is not met -- the era, the "
                     "phase-1 exclusion, or the absence of any FDA-regulated product. "
                     "Non-posting here is a free choice, not a breach"),
    UNDETERMINABLE: ("the inputs are missing. Overwhelmingly this is the IND hook, which "
                     "AACT does not record at all, so a trial with no US site cannot be "
                     "ruled out -- it may have run under an IND"),
}

# ---- the reasons, countable ----------------------------------------------
REASON_PRE_STATUTE = "completed_before_the_statute"
REASON_PHASE_1_ONLY = "phase_1_only_study"
REASON_NO_REGULATED_PRODUCT = "no_fda_regulated_product"
REASON_HOOK_US_FACILITY = "us_study_site"
REASON_HOOK_US_EXPORT = "us_manufactured_export"
REASON_PRODUCT_UNKNOWN = "regulated_product_status_unknown"
REASON_HOOKS_UNOBSERVABLE = "no_visible_hook_and_ind_status_unrecorded"
REASON_PHASE_UNKNOWN = "phase_absent_or_not_applicable"
REASON_ERA_UNKNOWN = "no_usable_completion_date"

APPLICABILITY_REASONS = (
    REASON_PRE_STATUTE, REASON_PHASE_1_ONLY, REASON_NO_REGULATED_PRODUCT,
    REASON_HOOK_US_FACILITY, REASON_HOOK_US_EXPORT, REASON_PRODUCT_UNKNOWN,
    REASON_HOOKS_UNOBSERVABLE, REASON_PHASE_UNKNOWN, REASON_ERA_UNKNOWN,
)

REASON_DOC = {
    REASON_PRE_STATUTE: ("primary completion predates the FDAAA results-submission "
                         "obligation, so no obligation existed to breach"),
    REASON_PHASE_1_ONLY: ("a phase-1-only study of a drug is excluded from the definition "
                          "of an applicable clinical trial. This is the single largest "
                          "source of confident NOT-APPLICABLE and it explains much of the "
                          "16.9% phase 1 posting rate"),
    REASON_NO_REGULATED_PRODUCT: ("the sponsor declared neither an FDA-regulated drug nor "
                                  "device, so the trial is outside the product scope"),
    REASON_HOOK_US_FACILITY: "at least one recorded study site in the United States",
    REASON_HOOK_US_EXPORT: "product manufactured in the US and exported for study",
    REASON_PRODUCT_UNKNOWN: ("both regulated-product declarations are absent. The form that "
                             "collects them postdates 2017, so this is mostly an artefact "
                             "of age rather than a refusal to answer"),
    REASON_HOOKS_UNOBSERVABLE: ("no US site and no export, and AACT does not record IND "
                                "status, so the jurisdictional condition cannot be ruled "
                                "out. NOT a negative verdict"),
    REASON_PHASE_UNKNOWN: ("phase is absent or 'NA', so the phase-1 exclusion cannot be "
                           "applied. Not assumed to be non-phase-1, because that would "
                           "manufacture applicable trials"),
    REASON_ERA_UNKNOWN: "no primary or overall completion date, so the era is unknown",
}

# Phases the statute excludes. The exclusion is for a study that is phase 1 ONLY, so a
# PHASE1/PHASE2 registration is NOT here -- it is not phase-1-only on its face. That is a
# READING rather than a fact, it moves 14,740 drug trials, and it lives in one named place
# so it can be changed and the effect counted.
PHASE1_ONLY_PHASES = frozenset({"PHASE1", "EARLY_PHASE1"})

# Phases whose registration spans the phase-1 boundary. Treated as in scope, and carried
# separately so the sensitivity of the whole rule to that reading is measurable.
PHASE_SPANNING_PHASE1 = frozenset({"PHASE1/PHASE2"})


def _phase_token(raw) -> Optional[str]:
    text = "" if raw is None else str(raw).strip().upper()
    if not text or text == "NA":
        return None
    return text


def product_in_scope(row: dict) -> Optional[bool]:
    """Is an FDA-regulated drug or device declared? None when both declarations are absent.

    True if either is true. False only when BOTH are explicitly false -- one false and one
    unknown leaves the question open, because a trial can be either or both and an absent
    declaration is not a denial.
    """
    drug = tribool(row.get("is_fda_regulated_drug"))
    device = tribool(row.get("is_fda_regulated_device"))
    if drug is True or device is True:
        return True
    if drug is False and device is False:
        return False
    return None


def visible_hook(row: dict) -> Optional[str]:
    """Which observable jurisdictional hook is satisfied, if any. None when none is.

    None does NOT mean the jurisdictional condition fails. The IND hook is unobservable in
    AACT, so the only honest readings of None are "undeterminable", never "not applicable".
    `has_us_facility` is preferred as the reason because it derives from facility records
    rather than a sponsor declaration and therefore exists for pre-2017 trials.
    """
    if tribool(row.get("has_us_facility")) is True:
        return REASON_HOOK_US_FACILITY
    if tribool(row.get("is_us_export")) is True:
        return REASON_HOOK_US_EXPORT
    return None


def applicability_for_row(row: dict, era_fallback: bool = True) -> dict:
    """One trial -> {verdict, reason}. The FDAAA results obligation, decided once.

    Order is deliberate: the conditions AACT observes FULLY are tested first, because they
    are the only ones that can produce a confident NOT_APPLICABLE. Jurisdiction is tested
    LAST, since its negative branch is unobservable and can only ever return
    UNDETERMINABLE. Reversing the order would turn every phase 1 trial with no US site into
    an undeterminable rather than the clear exemption it is.
    """
    era, _source = era_for_row(row, era_fallback)
    if era is None or era == UNKNOWN:
        return {"verdict": UNDETERMINABLE, "reason": REASON_ERA_UNKNOWN}
    if era == ERA_PRE_FDAAA:
        return {"verdict": NOT_APPLICABLE, "reason": REASON_PRE_STATUTE}

    scope = product_in_scope(row)
    if scope is False:
        return {"verdict": NOT_APPLICABLE, "reason": REASON_NO_REGULATED_PRODUCT}

    phase = _phase_token(row.get("phase"))
    if phase is None:
        return {"verdict": UNDETERMINABLE, "reason": REASON_PHASE_UNKNOWN}
    if phase in PHASE1_ONLY_PHASES:
        return {"verdict": NOT_APPLICABLE, "reason": REASON_PHASE_1_ONLY}

    if scope is None:
        return {"verdict": UNDETERMINABLE, "reason": REASON_PRODUCT_UNKNOWN}

    hook = visible_hook(row)
    if hook is None:
        return {"verdict": UNDETERMINABLE, "reason": REASON_HOOKS_UNOBSERVABLE}
    return {"verdict": APPLICABLE, "reason": hook}


def applicability_coverage(rows, era_fallback: bool = True) -> dict:
    """Verdict and reason counts, plus the PHASE1/PHASE2 sensitivity.

    The spanning-phase count is returned because the rule's treatment of PHASE1/PHASE2 is a
    reading rather than a fact. Reporting how many trials the reading moves is what makes it
    reviewable instead of buried.
    """
    verdicts = {v: 0 for v in APPLICABILITY_VERDICTS}
    reasons: dict = {}
    spanning = {"total": 0, "verdict": {v: 0 for v in APPLICABILITY_VERDICTS}}
    total = 0
    for row in rows:
        total += 1
        record = applicability_for_row(row, era_fallback)
        verdicts[record["verdict"]] += 1
        reasons[record["reason"]] = reasons.get(record["reason"], 0) + 1
        if _phase_token(row.get("phase")) in PHASE_SPANNING_PHASE1:
            spanning["total"] += 1
            spanning["verdict"][record["verdict"]] += 1
    return {"total": total, "verdicts": verdicts, "reasons": reasons,
            "phase_spanning_phase1": spanning, "era_fallback": era_fallback}


# ---- user-facing flag text ------------------------------------------------
# The product shows a probability with a flag rather than withholding it (owner's
# decision). A flag that says only "few trials like yours in the training data" reads as
# generic hedging. Naming the MECHANISM is what makes it credible, and there are two
# distinct mechanisms which need distinct words:
#
#   1. the statute never asked. A phase-1-only drug trial is excluded from the FDAAA
#      results-submission requirement, so its non-posting is lawful and the labelled slice
#      is thin here for a reason that has nothing to do with the trial's quality.
#   2. the question does not apply. 63% of phase 1 primary endpoints are pharmacokinetic or
#      dose-finding measurements -- AUC, Cmax, MTD -- which are reported as values rather
#      than tested against a threshold, so "did it meet its primary endpoint" has no
#      answer to estimate.
#
# The second needs the endpoint-type classifier, which is not built yet. Until it is, only
# FLAG_FDAAA_EXEMPT can be attached from data on hand; `compose_flag` takes the endpoint
# clause as an argument rather than inventing one, so the missing half is visibly missing
# instead of silently omitted.
FLAG_FDAAA_EXEMPT = (
    "Phase 1 trials are exempt from the FDAAA results-reporting requirement, so few of "
    "them post the statistical analysis this estimate is trained on."
)

FLAG_THIN_TRAINING = (
    "Trials like this one make up a small share of the training data, so this estimate is "
    "less reliable than it would be for a later-phase trial."
)

FLAG_NOT_REQUIRED_TO_POST = (
    "This trial was not required to post results under FDAAA, so its absence from the "
    "training data carries less information than it would otherwise."
)


def compose_flag(applicability: dict, endpoint_clause: Optional[str] = None) -> str:
    """Build the user-facing caveat from the applicability verdict and, when available, the
    endpoint-type clause.

    Returns "" when there is nothing to flag. `endpoint_clause` is a parameter rather than
    something this module derives, because the endpoint-type classifier does not exist yet
    and a flag that invented the clause would be asserting a measurement nobody made.
    """
    parts = []
    if applicability.get("reason") == REASON_PHASE_1_ONLY:
        parts.append(FLAG_FDAAA_EXEMPT)
    elif applicability.get("verdict") == NOT_APPLICABLE:
        parts.append(FLAG_NOT_REQUIRED_TO_POST)
    if endpoint_clause:
        parts.append(endpoint_clause)
    if parts:
        parts.append(FLAG_THIN_TRAINING)
    return " ".join(parts)