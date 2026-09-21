"""Posting bias: WHO discloses, measured inside each target's own denominator.

WHY THIS IS THE PROJECT'S CREDIBILITY
=====================================
The endpoint-met label exists for 14,368 drug trials out of 221,887 — 6.5%. That slice is
not a random sample: it is the set of trials whose sponsors chose, or were required, to
post a statistical analysis. A model fit on it describes disclosed trials. Whether it
describes anything else is an empirical question, and this module is how it gets asked.

If the disclosure rate is flat across phase, era and sponsor class, the slice is close to a
random sample of drug trials and the model can claim to transport. If it is graded, the
model speaks for a sub-population and the applicability domain has to say which one. The
point of measuring is that either answer is usable; what is not usable is asserting the
first without looking.

TWO STAGES, NOT ONE
===================
The label needs a posted ANALYSIS, not merely posted results, and the gap between those is
the coverage cliff: 75,434 trials posted results and only 24,228 posted a primary analysis.
So disclosure is a THREE-state ladder, and the two steps can have different mechanisms.
An industry sponsor filing to a regulator has reason to post the statistics; an academic
group meeting a registry obligation may post measurements and stop. If the cliff is
sponsor-graded and the first step is not, the selection acting on the label is not the
selection most published disclosure research measures.

THE DENOMINATOR IS TRIALS WHOSE DEADLINE HAS PASSED
===================================================
A trial that read out last month has not failed to post; its clock is still running. FDAAA
allows twelve months from primary completion. Dividing disclosures by all completed trials
mixes non-disclosure with not-yet-due, and the mixture is era-correlated — recent trials are
disproportionately not-yet-due — which would manufacture exactly the era gradient this
module exists to detect. So the denominator is trials with an ACTUAL primary completion
whose posting deadline has elapsed by the snapshot, and everything else is counted as
undeterminable rather than as a failure to post.

RATES NEED THEIR n, AND A SPREAD NEEDS A FLOOR
==============================================
A stratum of four trials at 100% is not a finding. Every rate is returned with its
denominator, and `rate_spread` ignores strata below a named floor rather than reporting a
range driven by a handful of rows.

`results_posted` and `n_primary_analyzed` are LABEL INPUTS under R7 and must never become
features. Here they are the DEPENDENT VARIABLE, which is the one legitimate use: this
module measures disclosure, it does not predict outcomes.
"""
from __future__ import annotations

from datetime import date
from typing import Callable, Optional

from trial_pos.services.population import UNKNOWN, is_actual_date, parse_date, tribool

# FDAAA 801 allows twelve months from primary completion to submit results. Named because
# it defines the denominator of every rate below: a trial inside this window has not
# failed to post, and counting it as a non-poster would mix non-disclosure with not-yet-due.
FDAAA_POSTING_DEADLINE_MONTHS = 12

# The disclosure ladder. Three states, not two, because the gap between posting
# measurements and posting an analysis is the coverage cliff and may be driven by something
# different from the decision to post at all.
DISCLOSURE_NONE = "no_results"
DISCLOSURE_RESULTS_ONLY = "results_without_analysis"
DISCLOSURE_ANALYSIS = "analysis_posted"

DISCLOSURE_STAGES = (DISCLOSURE_NONE, DISCLOSURE_RESULTS_ONLY, DISCLOSURE_ANALYSIS)

DISCLOSURE_DOC = {
    DISCLOSURE_NONE: "nothing posted",
    DISCLOSURE_RESULTS_ONLY: ("outcome measurements posted but no statistical analysis. "
                              "This is the coverage cliff: the trial disclosed, and the "
                              "label still cannot read a verdict from it"),
    DISCLOSURE_ANALYSIS: "a primary analysis posted; the only stage the label can use",
}

# A stratum below this many trials is reported with its n but excluded from the spread: a
# range driven by four trials at 100% is arithmetic, not evidence.
MIN_STRATUM_FOR_RATE = 50

# Deliberately not a verdict threshold. A spread this wide means the disclosed slice is
# graded on that variable and the applicability domain must name it; below it, the variable
# is not the one doing the selecting. Named so the two callers quote the same number.
NOTABLE_SPREAD = 0.10


def posting_deadline_elapsed(row: dict, snapshot: date,
                             months: int = FDAAA_POSTING_DEADLINE_MONTHS
                             ) -> Optional[bool]:
    """Has the posting deadline passed by `snapshot`? None when readout is unknown.

    Requires an ACTUAL primary completion date. A planned completion cannot start a
    statutory clock, and treating it as one would put trials that have not read out into
    the denominator of a disclosure rate.

    `snapshot` is injected, never read from the clock: the same trial's inclusion would
    otherwise change with the day the audit ran (lesson 15).
    """
    if not is_actual_date(row.get("primary_completion_date_type")):
        return None
    readout = parse_date(row.get("primary_completion_date"))
    if readout is None:
        return None
    total = readout.month - 1 + months
    year = readout.year + total // 12
    month = total % 12 + 1
    # Clamp the day: 31 January plus one month has no 31st. Clamping DOWN moves the
    # deadline earlier, which is the conservative direction -- it can only admit a trial
    # that has had its full term, never one that has not.
    day = min(readout.day, _days_in_month(year, month))
    return date(year, month, day) <= snapshot


def _days_in_month(year: int, month: int) -> int:
    if month == 12:
        return 31
    return (date(year, month + 1, 1) - date(year, month, 1)).days


def disclosure_stage(row: dict) -> str:
    """One label row -> where it sits on the disclosure ladder.

    Reads `results_posted` and `n_primary_analyzed`, both of which are LABEL INPUTS and
    must never be features. Using them as the dependent variable of a disclosure
    measurement is the one legitimate use.

    A trial with an analysis but no posting flag is still counted as having disclosed an
    analysis: the analysis IS the disclosure, and the flag disagreeing would be a data
    problem worth seeing rather than a reason to drop the row. The pull reports those two
    definitions agreeing on 460,569 of 460,569, so this branch should stay empty.
    """
    analysed = str(row.get("n_primary_analyzed") or "0").strip()
    if analysed not in ("", "0"):
        return DISCLOSURE_ANALYSIS
    posted = tribool(row.get("results_posted"))
    if posted is True:
        return DISCLOSURE_RESULTS_ONLY
    return DISCLOSURE_NONE


def stratified_disclosure(rows, key: Callable[[dict], str], snapshot: date,
                          months: int = FDAAA_POSTING_DEADLINE_MONTHS) -> dict:
    """Disclosure stage counts per stratum, over rows whose deadline has elapsed.

    `key` maps a row to its stratum name and is supplied by the caller, so this function
    knows nothing about phases or sponsors -- the same code measures every cross-tab and
    they cannot drift apart.

    Rows whose deadline has not passed, or whose readout is not an actual date, are counted
    in `excluded` and never enter a rate. Both are returned so a suspiciously small
    denominator can be traced to the reason it is small.
    """
    strata: dict = {}
    excluded = {"deadline_not_elapsed": 0, "readout_not_actual": 0}
    for row in rows:
        elapsed = posting_deadline_elapsed(row, snapshot, months)
        if elapsed is None:
            excluded["readout_not_actual"] += 1
            continue
        if not elapsed:
            excluded["deadline_not_elapsed"] += 1
            continue
        name = key(row) or UNKNOWN
        bucket = strata.setdefault(name, {s: 0 for s in DISCLOSURE_STAGES})
        bucket[disclosure_stage(row)] += 1
    return {"strata": strata, "excluded": excluded,
            "n_in_denominator": sum(sum(b.values()) for b in strata.values())}


def stage_rate(bucket: dict, stage: str) -> Optional[float]:
    """Share of a stratum at or above a disclosure stage. None when the stratum is empty.

    "At or above" because the ladder is cumulative: a trial that posted an analysis also
    posted results. Asking for DISCLOSURE_ANALYSIS gives the rate the LABEL depends on;
    asking for DISCLOSURE_RESULTS_ONLY gives the rate most published disclosure work
    measures. The two are different questions and the gap between them is the cliff.
    """
    n = sum(bucket.values())
    if n == 0:
        return None
    index = DISCLOSURE_STAGES.index(stage)
    at_or_above = sum(bucket[s] for s in DISCLOSURE_STAGES[index:])
    return at_or_above / n


def rate_spread(strata: dict, stage: str,
                min_stratum: int = MIN_STRATUM_FOR_RATE) -> dict:
    """max - min of a stage's rate across strata large enough to count.

    The single number that answers "does disclosure depend on this variable". Strata below
    `min_stratum` are excluded and counted, because a four-trial stratum at 100% would
    otherwise set the maximum and turn arithmetic into a finding.

    Returns None spread when fewer than two strata qualify: a range needs two points, and
    reporting 0.0 there would read as "no variation" when the truth is "not measurable".
    """
    usable = {name: bucket for name, bucket in strata.items()
              if sum(bucket.values()) >= min_stratum}
    rates = {name: stage_rate(bucket, stage) for name, bucket in usable.items()}
    rates = {name: rate for name, rate in rates.items() if rate is not None}
    if len(rates) < 2:
        return {"spread": None, "lowest": None, "highest": None,
                "n_strata_used": len(rates),
                "n_strata_too_small": len(strata) - len(usable), "rates": rates}
    lowest = min(rates, key=lambda name: rates[name])
    highest = max(rates, key=lambda name: rates[name])
    return {"spread": rates[highest] - rates[lowest],
            "lowest": (lowest, rates[lowest]),
            "highest": (highest, rates[highest]),
            "n_strata_used": len(rates),
            "n_strata_too_small": len(strata) - len(usable),
            "rates": rates}


def cliff_spread(strata: dict, min_stratum: int = MIN_STRATUM_FOR_RATE) -> dict:
    """Spread of the CONDITIONAL analysis rate: given results were posted, was an analysis?

    This isolates the coverage cliff from the decision to post at all. A variable can leave
    the posting rate flat and still decide the label's availability entirely, by acting only
    on the second step. Measuring the unconditional analysis rate would blend the two and
    attribute the whole effect to whichever step is larger.
    """
    conditional = {}
    for name, bucket in strata.items():
        disclosed = bucket[DISCLOSURE_RESULTS_ONLY] + bucket[DISCLOSURE_ANALYSIS]
        if disclosed >= min_stratum:
            conditional[name] = bucket[DISCLOSURE_ANALYSIS] / disclosed
    if len(conditional) < 2:
        return {"spread": None, "lowest": None, "highest": None, "rates": conditional}
    lowest = min(conditional, key=lambda name: conditional[name])
    highest = max(conditional, key=lambda name: conditional[name])
    return {"spread": conditional[highest] - conditional[lowest],
            "lowest": (lowest, conditional[lowest]),
            "highest": (highest, conditional[highest]),
            "rates": conditional}