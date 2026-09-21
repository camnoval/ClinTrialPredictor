"""Which trials are eligible for WHICH target. The denominator, decided before any rate.

WHY THIS IS ITS OWN MODULE
==========================
Step 6.3 has to report posting rate by phase, era, sponsor class and FDAAA component. Every
one of those is a fraction, and a fraction needs a denominator that was chosen on purpose.
The three targets do NOT share one:

  endpoint met  a question for any drug trial that posted a primary analysis, at any phase
  advancement   meaningless for a trial with no next phase
  market        meaningless for a trial whose drug is already on the market

So eligibility is per target, it is decided here rather than inside whichever script
happens to need it first, and it is TRI-STATE: eligible, ineligible, or undeterminable.
Undeterminable is not ineligible. A trial with no actual completion date cannot be placed
in or out of a closed window, and recording that as "out" would silently shrink the
population by the amount of missing data rather than by a decision.

THE PHASE 4 DECISION, AND THE PRINCIPLE BEHIND IT
=================================================
Phase 4 is post-approval: the drug is already marketed. So `market_fda_any` is 1 by
construction, and advancement has no next rung to reach. 30,899 of the 221,887 drug trials
are phase 4 — a stratum that size at a ~100% positive rate would dominate the base rate and
let a model score well by learning "is this phase 4" from the phase column. That is the
same failure as a decision rule that answers the same thing on every row, one level up:
**a stratum where an AVAILABLE FEATURE DETERMINES THE LABEL must be excluded, or the model
learns the feature instead of the question.**

Under `market_fda_indication` a phase 4 label-expansion study is not trivially 1 — the new
indication can be refused. But it reaches market through a SUPPLEMENTAL approval, a
different evidence bar and process from the original NDA/BLA, so pooling it would put two
regulatory mechanisms under one label. Excluded for both reasons, and the flag is retained
so the decision is reversible without a re-pull.

Endpoint-met keeps phase 4. Whether a post-approval trial cleared its own primary analysis
is a real question with a real answer.

THE PIVOTAL RESTRICTION FOR MARKET
==================================
Market is restricted to pivotal phases and a 3-year window, together rather than
separately. The two are not independent choices: a short window is only interpretable
BECAUSE the population is pivotal. Over all phases, 3 years from readout mostly measures
whether a trial sat late in its program; for a phase 3 trial, readout to approval inside 3
years is the normal path for a successful pivotal study. Measured pools, drug trials with
an ACTUAL primary completion and the window closed against a 2026 snapshot:

    window   phase 3 / 2-3   other phases   all phases
      W=10          16,301         59,358       75,659
      W=5           22,984         89,260      112,244
      W=3           25,735        102,157      127,892

W=3 on pivotal phases is 58% LARGER than W=10 on the same phases, because it stops
discarding seven years of readouts, and it admits readouts through 2023 — so checkpoint
inhibitors, cell and gene therapy and heavy accelerated-approval use sit inside the
training window instead of outside it. Transportability was the argument against a long
window and no amount of data fixes it.

WHAT IS NOT DECIDED HERE
========================
No approval data exists yet, so nothing here assigns a market LABEL. This module answers
only "could this trial carry one", which is the question the audit's denominators need.
The approval-precedes-readout rule (§1.2.3) is a labelling concern and belongs with the
label, not here.
"""
from __future__ import annotations

from datetime import date
from typing import Optional

from trial_pos.services.endpoint_label import HEADLINE_TIERS
from trial_pos.services.population import (
    NOT_APPLICABLE, UNKNOWN, is_actual_date, parse_date, tribool,
)

# ---- verdicts -------------------------------------------------------------
ELIGIBLE = "eligible"
INELIGIBLE = "ineligible"
UNDETERMINABLE = "undeterminable"

ELIGIBILITY_VERDICTS = (ELIGIBLE, INELIGIBLE, UNDETERMINABLE)

ELIGIBILITY_DOC = {
    ELIGIBLE: "this trial could carry this target's label",
    INELIGIBLE: "this trial could not, and we know why -- a DECISION, countable by reason",
    UNDETERMINABLE: ("the inputs needed to decide are missing. NOT the same as ineligible: "
                     "counting it as out would shrink the population by the amount of "
                     "missing data rather than by a judgment"),
}

# ---- targets --------------------------------------------------------------
TARGET_ENDPOINT_MET = "endpoint_met"
TARGET_ADVANCEMENT = "advancement"
TARGET_MARKET = "market"

TARGETS = (TARGET_ENDPOINT_MET, TARGET_ADVANCEMENT, TARGET_MARKET)

# ---- phase vocabulary, as AACT actually spells it -------------------------
# Read off the pull rather than assumed: PHASE1, PHASE2, PHASE3, PHASE4, PHASE1/PHASE2,
# PHASE2/PHASE3, EARLY_PHASE1, NA, and a handful of blanks.
PHASE_NOT_APPLICABLE = "NA"

# Phases that can support a marketing application. PHASE2/PHASE3 is included because such
# a trial is pivotal-capable by design; it is a judgment rather than an obvious fact, which
# is why it is a named constant and not an inline literal.
PIVOTAL_PHASES = frozenset({"PHASE3", "PHASE2/PHASE3"})

# Post-approval. Excluded from market and advancement; see the module docstring.
POST_APPROVAL_PHASES = frozenset({"PHASE4"})

# Everything a trial can be that is neither pivotal nor post-approval. Listed rather than
# derived by subtraction so an unrecognised phase value is UNDETERMINABLE instead of
# quietly landing in this bucket.
EARLY_PHASES = frozenset({"EARLY_PHASE1", "PHASE1", "PHASE1/PHASE2", "PHASE2"})

KNOWN_PHASES = PIVOTAL_PHASES | POST_APPROVAL_PHASES | EARLY_PHASES

# ---- ineligibility reasons, countable ------------------------------------
REASON_NOT_DRUG_TRIAL = "not_a_drug_trial"
REASON_POST_APPROVAL = "post_approval_phase"
REASON_NOT_PIVOTAL = "not_a_pivotal_phase"
REASON_NO_ANALYSIS_POSTED = "no_primary_analysis_posted"
REASON_NOT_HEADLINE_TIER = "label_rests_on_a_non_headline_tier"
REASON_WINDOW_OPEN = "window_has_not_closed"

# Why the answer is not knowable, as distinct from the answer being no.
REASON_PHASE_UNKNOWN = "phase_absent_or_unrecognised"
REASON_READOUT_NOT_ACTUAL = "readout_date_is_planned_or_absent"

INELIGIBILITY_REASONS = (REASON_NOT_DRUG_TRIAL, REASON_POST_APPROVAL,
                         REASON_NOT_PIVOTAL, REASON_NO_ANALYSIS_POSTED,
                         REASON_NOT_HEADLINE_TIER, REASON_WINDOW_OPEN)
UNDETERMINABLE_REASONS = (REASON_PHASE_UNKNOWN, REASON_READOUT_NOT_ACTUAL)

REASON_DOC = {
    REASON_NOT_DRUG_TRIAL: ("scoping is drug-only: intervention_type carries neither drug "
                            "nor biological"),
    REASON_POST_APPROVAL: ("phase 4 -- the drug is already marketed, so market is 1 by "
                           "construction and advancement has no next rung. A stratum whose "
                           "label an available feature determines teaches the model the "
                           "feature"),
    REASON_NOT_PIVOTAL: ("market is restricted to pivotal phases, because a 3-year window "
                         "is only interpretable for a trial that is itself the last rung "
                         "before filing"),
    REASON_NO_ANALYSIS_POSTED: ("endpoint-met is taken from the trial's own posted primary "
                                "analysis; without one there is nothing to read"),
    REASON_NOT_HEADLINE_TIER: ("the label rests on a tier the headline excludes -- an "
                               "interval-only verdict (tier C), which is a sensitivity "
                               "stratum rather than part of the modelling population. "
                               "Tier C systematically under-calls positives relative to "
                               "A/B, so pooling them would shift the base rate"),
    REASON_WINDOW_OPEN: ("readout + W is later than the snapshot, so this trial has not had "
                         "the same time to succeed as the rest of the training set. "
                         "EXCLUDED, never labelled 0"),
    REASON_PHASE_UNKNOWN: ("phase is absent, 'NA', or a value this module does not know. "
                           "Not assumed early, because that would silently widen the "
                           "market population"),
    REASON_READOUT_NOT_ACTUAL: ("no ACTUAL primary completion date, so the window clock has "
                                "no event to start from. A clock cannot start from a plan"),
}

# Default market window, in years. A flag everywhere it is used: 3 versus 5 changes the
# eligible sample, the positive rate and the claim made to the user, so it produces
# verdicts. 3 is the headline and 5 the sensitivity check; they are comparable because they
# run on the SAME pivotal population, which 3-versus-10 did not.
DEFAULT_MARKET_WINDOW_YEARS = 3
MARKET_WINDOW_YEARS_REPORTED = (3, 5, 10)

# Advancement windows. Separate constant because advancement asks a different question and
# its clock is program-to-program rather than readout-to-approval.
DEFAULT_ADVANCEMENT_WINDOW_YEARS = 3
ADVANCEMENT_WINDOW_YEARS_REPORTED = (2, 3, 5)


def normalize_phase(raw) -> Optional[str]:
    """AACT phase -> an uppercased token, NOT_APPLICABLE, or None when absent.

    'NA' is AACT's explicit not-applicable and is preserved as such rather than collapsed
    into missing: for phase the distinction is load-bearing, since 'NA' is how a device or
    behavioural trial says the concept does not apply to it.
    """
    text = "" if raw is None else str(raw).strip()
    if not text:
        return None
    token = text.upper()
    return NOT_APPLICABLE if token == PHASE_NOT_APPLICABLE else token


def phase_class(raw) -> str:
    """phase -> 'pivotal' | 'post_approval' | 'early' | UNKNOWN.

    UNKNOWN covers absent, AACT's 'NA', and any value not in KNOWN_PHASES. An unrecognised
    phase is deliberately NOT treated as early: doing so would widen the market population
    on a guess, and the same passthrough discipline that exposed the 'Estimated' vocabulary
    gap applies here.
    """
    token = normalize_phase(raw)
    if token is None or token == NOT_APPLICABLE or token not in KNOWN_PHASES:
        return UNKNOWN
    if token in PIVOTAL_PHASES:
        return "pivotal"
    if token in POST_APPROVAL_PHASES:
        return "post_approval"
    return "early"


def readout_date(row: dict) -> Optional[date]:
    """The window clock's start: primary completion, and ONLY when it is ACTUAL.

    None when the date is absent, unparseable, or planned. A planned completion is a
    statement of intent; starting a censoring clock from it would put unfinished trials in
    the training set, which is the leak the temporal split exists to prevent.
    """
    if not is_actual_date(row.get("primary_completion_date_type")):
        return None
    return parse_date(row.get("primary_completion_date"))


def window_closed(row: dict, snapshot: date, window_years: int) -> Optional[bool]:
    """Has readout + window_years elapsed by the snapshot? None when readout is unknown.

    `snapshot` is REQUIRED and injected. It is never read from the clock: the same trial's
    eligibility would otherwise change with the day the pipeline ran, and that cannot be
    tested. Years are added by calendar year arithmetic on the date, not by multiplying a
    nominal day count, so a leap year cannot move a trial across the boundary.
    """
    readout = readout_date(row)
    if readout is None:
        return None
    try:
        closes = readout.replace(year=readout.year + window_years)
    except ValueError:
        # 29 February plus N years in a non-leap year. Falls back to 28 February, which is
        # the conservative direction: the window closes a day EARLIER, so no trial is
        # admitted that has not had its full term.
        closes = readout.replace(month=2, day=28, year=readout.year + window_years)
    return closes <= snapshot


def _verdict(eligible: bool, reason: str) -> dict:
    return {"verdict": ELIGIBLE if eligible else INELIGIBLE,
            "reason": "" if eligible else reason}


def _undeterminable(reason: str) -> dict:
    return {"verdict": UNDETERMINABLE, "reason": reason}


# Whether endpoint-met eligibility requires a HEADLINE tier. True by default because the
# headline is the modelling population: tier C is an interval-only verdict kept as a
# sensitivity stratum, and it systematically under-calls positives relative to A/B (427
# under-calls against 180 over-calls at matched coverage), so pooling would shift the base
# rate. A flag rather than a constant so the tier-C stratum can still be reported.
#
# The first version of this predicate had no tier condition at all, and reported 15,504
# eligible against a headline population of 14,368 -- 1,136 tier-C drug trials counted into
# a denominator the model will not be fit on. Exactly the denominator confusion this module
# exists to prevent.
DEFAULT_ENDPOINT_HEADLINE_ONLY = True


def eligible_for_endpoint_met(row: dict,
                              headline_only: bool = DEFAULT_ENDPOINT_HEADLINE_ONLY) -> dict:
    """Drug trial, any phase, with a decidable primary analysis at a headline tier.

    Phase 4 is KEPT. Whether a post-approval trial cleared its own primary analysis is a
    real question; it is only market and advancement that the drug's existing approval
    answers in advance.
    """
    if tribool(row.get("is_drug_trial")) is not True:
        return _verdict(False, REASON_NOT_DRUG_TRIAL)
    strict = row.get("endpoint_met_strict")
    if strict is None or str(strict).strip() == "":
        return _verdict(False, REASON_NO_ANALYSIS_POSTED)
    if headline_only and (row.get("tier_min") or "") not in HEADLINE_TIERS:
        return _verdict(False, REASON_NOT_HEADLINE_TIER)
    return _verdict(True, "")


def eligible_for_advancement(row: dict, snapshot: date,
                             window_years: int = DEFAULT_ADVANCEMENT_WINDOW_YEARS) -> dict:
    """Drug trial with a next phase to reach and a closed lookahead window."""
    if tribool(row.get("is_drug_trial")) is not True:
        return _verdict(False, REASON_NOT_DRUG_TRIAL)
    klass = phase_class(row.get("phase"))
    if klass == UNKNOWN:
        return _undeterminable(REASON_PHASE_UNKNOWN)
    if klass == "post_approval":
        return _verdict(False, REASON_POST_APPROVAL)
    closed = window_closed(row, snapshot, window_years)
    if closed is None:
        return _undeterminable(REASON_READOUT_NOT_ACTUAL)
    if not closed:
        return _verdict(False, REASON_WINDOW_OPEN)
    return _verdict(True, "")


def eligible_for_market(row: dict, snapshot: date,
                        window_years: int = DEFAULT_MARKET_WINDOW_YEARS) -> dict:
    """Drug trial, PIVOTAL phase, with a closed window measured from an ACTUAL readout.

    The phase restriction and the short window travel together and neither is defensible
    alone -- see the module docstring.
    """
    if tribool(row.get("is_drug_trial")) is not True:
        return _verdict(False, REASON_NOT_DRUG_TRIAL)
    klass = phase_class(row.get("phase"))
    if klass == UNKNOWN:
        return _undeterminable(REASON_PHASE_UNKNOWN)
    if klass == "post_approval":
        return _verdict(False, REASON_POST_APPROVAL)
    if klass != "pivotal":
        return _verdict(False, REASON_NOT_PIVOTAL)
    closed = window_closed(row, snapshot, window_years)
    if closed is None:
        return _undeterminable(REASON_READOUT_NOT_ACTUAL)
    if not closed:
        return _verdict(False, REASON_WINDOW_OPEN)
    return _verdict(True, "")


def eligibility_for_row(row: dict, snapshot: date,
                        market_window: int = DEFAULT_MARKET_WINDOW_YEARS,
                        advancement_window: int = DEFAULT_ADVANCEMENT_WINDOW_YEARS,
                        headline_only: bool = DEFAULT_ENDPOINT_HEADLINE_ONLY) -> dict:
    """One trial -> {target: verdict record}. Every target, every time."""
    return {
        TARGET_ENDPOINT_MET: eligible_for_endpoint_met(row, headline_only),
        TARGET_ADVANCEMENT: eligible_for_advancement(row, snapshot, advancement_window),
        TARGET_MARKET: eligible_for_market(row, snapshot, market_window),
    }


def eligibility_coverage(rows, snapshot: date,
                         market_window: int = DEFAULT_MARKET_WINDOW_YEARS,
                         advancement_window: int = DEFAULT_ADVANCEMENT_WINDOW_YEARS,
                         headline_only: bool = DEFAULT_ENDPOINT_HEADLINE_ONLY) -> dict:
    """Per-target verdict counts and ineligibility reasons over a row iterable.

    Reasons are counted rather than summarised, because "ineligible" on its own is not a
    finding. 100,000 trials excluded for being device trials and 100,000 excluded for an
    open window are different facts about the project: one is a scoping decision that will
    not change, the other shrinks every year the snapshot advances.
    """
    out = {t: {"verdicts": {v: 0 for v in ELIGIBILITY_VERDICTS}, "reasons": {}}
           for t in TARGETS}
    total = 0
    for row in rows:
        total += 1
        for target, record in eligibility_for_row(row, snapshot, market_window,
                                                  advancement_window,
                                                  headline_only).items():
            bucket = out[target]
            bucket["verdicts"][record["verdict"]] += 1
            if record["reason"]:
                bucket["reasons"][record["reason"]] = (
                    bucket["reasons"].get(record["reason"], 0) + 1)
    return {"total": total, "targets": out,
            "snapshot": snapshot.isoformat(),
            "market_window_years": market_window,
            "advancement_window_years": advancement_window,
            "endpoint_headline_only": headline_only}