"""Tests for per-target eligibility. Plain asserts so tests/_run_stdlib.py works.

No transcribed expected values. Dates are derived from the snapshot and the window
constant, phase memberships from the phase sets, and the rest from structural properties
that must hold whatever the constants are set to.
"""
from __future__ import annotations

from datetime import date, timedelta

from trial_pos.services.eligibility import (
    ADVANCEMENT_WINDOW_YEARS_REPORTED, DEFAULT_ADVANCEMENT_WINDOW_YEARS,
    DEFAULT_MARKET_WINDOW_YEARS, EARLY_PHASES, ELIGIBLE, ELIGIBILITY_DOC,
    ELIGIBILITY_VERDICTS, INELIGIBLE, INELIGIBILITY_REASONS, KNOWN_PHASES,
    MARKET_WINDOW_YEARS_REPORTED, PIVOTAL_PHASES, POST_APPROVAL_PHASES, REASON_DOC,
    REASON_NOT_DRUG_TRIAL, REASON_NOT_HEADLINE_TIER, REASON_NOT_PIVOTAL,
    REASON_NO_ANALYSIS_POSTED,
    REASON_PHASE_UNKNOWN, REASON_POST_APPROVAL, REASON_READOUT_NOT_ACTUAL,
    REASON_WINDOW_OPEN, TARGETS, TARGET_ADVANCEMENT, TARGET_ENDPOINT_MET, TARGET_MARKET,
    UNDETERMINABLE, UNDETERMINABLE_REASONS, eligibility_coverage, eligibility_for_row,
    eligible_for_advancement, eligible_for_endpoint_met, eligible_for_market,
    normalize_phase, phase_class, readout_date, window_closed,
)
from trial_pos.services.endpoint_label import HEADLINE_TIERS
from trial_pos.services.population import NOT_APPLICABLE, UNKNOWN

SNAPSHOT = date(2026, 9, 20)


def _row(phase="PHASE3", years_ago=None, actual=True, drug=True, strict="1", tier=None):
    """A trial row, with its readout placed relative to the snapshot.

    `years_ago` is derived from the snapshot rather than written as a literal date, so
    moving SNAPSHOT cannot leave a test asserting against a stale calendar.

    `tier` defaults to a HEADLINE tier taken from the constant rather than spelled out, so
    the fixture is a realistic labelled trial. A fixture that omitted tier_min is what let
    the missing headline condition go unnoticed in the first place.
    """
    row = {"phase": phase, "is_drug_trial": drug, "endpoint_met_strict": strict,
           "tier_min": tier if tier is not None else HEADLINE_TIERS[0]}
    if years_ago is not None:
        readout = SNAPSHOT.replace(year=SNAPSHOT.year - years_ago)
        row["primary_completion_date"] = readout.isoformat()
        row["primary_completion_date_type"] = "Actual" if actual else "Estimated"
    return row


# ---- phase vocabulary -----------------------------------------------------
def test_phase_sets_are_disjoint():
    # a phase in two classes would make phase_class order-dependent
    assert not (PIVOTAL_PHASES & POST_APPROVAL_PHASES)
    assert not (PIVOTAL_PHASES & EARLY_PHASES)
    assert not (POST_APPROVAL_PHASES & EARLY_PHASES)


def test_known_phases_is_exactly_the_union():
    assert KNOWN_PHASES == PIVOTAL_PHASES | POST_APPROVAL_PHASES | EARLY_PHASES


def test_every_known_phase_classifies_into_a_real_class():
    for phase in KNOWN_PHASES:
        assert phase_class(phase) in ("pivotal", "post_approval", "early"), phase


def test_aact_not_applicable_is_preserved_not_collapsed():
    # 'NA' is how a device or behavioural trial says the concept does not apply; that is a
    # different claim from the field being empty
    assert normalize_phase("NA") == NOT_APPLICABLE
    assert normalize_phase("") is None
    assert normalize_phase(None) is None


def test_unrecognised_phase_is_unknown_not_early():
    # treating it as early would widen the market population on a guess
    assert phase_class("PHASE7") == UNKNOWN
    assert phase_class("NA") == UNKNOWN
    assert phase_class(None) == UNKNOWN


def test_phase_matching_is_case_insensitive():
    for phase in KNOWN_PHASES:
        assert phase_class(phase.lower()) == phase_class(phase), phase


# ---- the window clock -----------------------------------------------------
def test_readout_requires_an_actual_date():
    # a clock cannot start from a plan
    assert readout_date(_row(years_ago=10, actual=True)) is not None
    assert readout_date(_row(years_ago=10, actual=False)) is None
    assert readout_date(_row()) is None                     # no date at all


def test_window_closes_exactly_at_the_boundary():
    window = DEFAULT_MARKET_WINDOW_YEARS
    readout = SNAPSHOT.replace(year=SNAPSHOT.year - window)
    row = {"primary_completion_date": readout.isoformat(),
           "primary_completion_date_type": "Actual"}
    assert window_closed(row, SNAPSHOT, window) is True     # inclusive
    one_day_later = {"primary_completion_date": (readout + timedelta(days=1)).isoformat(),
                     "primary_completion_date_type": "Actual"}
    assert window_closed(one_day_later, SNAPSHOT, window) is False


def test_window_unknown_when_readout_unknown():
    # None, not False: "cannot tell" is not "has not closed"
    assert window_closed(_row(years_ago=10, actual=False), SNAPSHOT,
                         DEFAULT_MARKET_WINDOW_YEARS) is None


def test_leap_day_readout_closes_no_later_than_its_term():
    # 29 Feb + N years in a non-leap year must not extend the window
    row = {"primary_completion_date": "2016-02-29", "primary_completion_date_type": "Actual"}
    for window in MARKET_WINDOW_YEARS_REPORTED:
        closed = window_closed(row, SNAPSHOT, window)
        expected = date(2016 + window, 3, 1) <= SNAPSHOT
        assert closed == expected or closed is True, (window, closed)


def test_a_longer_window_is_never_more_permissive():
    # structural: widening the window can only close FEWER trials' windows
    row = _row(years_ago=6)
    closures = [window_closed(row, SNAPSHOT, w) for w in sorted(MARKET_WINDOW_YEARS_REPORTED)]
    assert closures == sorted(closures, reverse=True)


# ---- endpoint met ---------------------------------------------------------
def test_endpoint_met_keeps_phase_4():
    # whether a post-approval trial cleared its OWN primary analysis is a real question;
    # only market and advancement are answered in advance by the existing approval
    for phase in POST_APPROVAL_PHASES:
        assert eligible_for_endpoint_met(_row(phase=phase))["verdict"] == ELIGIBLE


def test_endpoint_met_requires_a_posted_analysis():
    for strict in ("", None):
        out = eligible_for_endpoint_met(_row(strict=strict))
        assert out["verdict"] == INELIGIBLE
        assert out["reason"] == REASON_NO_ANALYSIS_POSTED


def test_endpoint_met_respects_drug_only_scoping():
    out = eligible_for_endpoint_met(_row(drug=False))
    assert (out["verdict"], out["reason"]) == (INELIGIBLE, REASON_NOT_DRUG_TRIAL)


def test_unknown_is_drug_trial_is_not_treated_as_a_drug_trial():
    out = eligible_for_endpoint_met(_row(drug=None))
    assert out["verdict"] == INELIGIBLE


# ---- market: the phase 4 decision ----------------------------------------
def test_market_excludes_post_approval_phases():
    # the drug is already marketed, so the label is 1 by construction and a model would
    # learn "is this phase 4" from the phase column
    for phase in POST_APPROVAL_PHASES:
        out = eligible_for_market(_row(phase=phase, years_ago=10), SNAPSHOT)
        assert (out["verdict"], out["reason"]) == (INELIGIBLE, REASON_POST_APPROVAL)


def test_advancement_also_excludes_post_approval_phases():
    # there is no next rung to reach
    for phase in POST_APPROVAL_PHASES:
        out = eligible_for_advancement(_row(phase=phase, years_ago=10), SNAPSHOT)
        assert (out["verdict"], out["reason"]) == (INELIGIBLE, REASON_POST_APPROVAL)


def test_post_approval_is_excluded_from_exactly_two_of_three_targets():
    # structural statement of the decision: phase 4 poses a real question for endpoint-met
    # and a predetermined one for the other two
    row = _row(phase=sorted(POST_APPROVAL_PHASES)[0], years_ago=10)
    verdicts = eligibility_for_row(row, SNAPSHOT)
    excluded = [t for t, v in verdicts.items()
                if v["reason"] == REASON_POST_APPROVAL]
    assert sorted(excluded) == sorted([TARGET_ADVANCEMENT, TARGET_MARKET])
    assert verdicts[TARGET_ENDPOINT_MET]["verdict"] == ELIGIBLE


# ---- market: the pivotal restriction --------------------------------------
def test_market_admits_only_pivotal_phases():
    for phase in PIVOTAL_PHASES:
        out = eligible_for_market(_row(phase=phase, years_ago=10), SNAPSHOT)
        assert out["verdict"] == ELIGIBLE, phase
    for phase in EARLY_PHASES:
        out = eligible_for_market(_row(phase=phase, years_ago=10), SNAPSHOT)
        assert (out["verdict"], out["reason"]) == (INELIGIBLE, REASON_NOT_PIVOTAL), phase


def test_advancement_admits_early_phases_that_market_refuses():
    # the two targets differ precisely here: an early-phase trial has a next rung but no
    # interpretable 3-year path to approval
    for phase in EARLY_PHASES:
        row = _row(phase=phase, years_ago=10)
        assert eligible_for_advancement(row, SNAPSHOT)["verdict"] == ELIGIBLE, phase
        assert eligible_for_market(row, SNAPSHOT)["verdict"] == INELIGIBLE, phase


def test_open_window_is_ineligible_and_never_a_zero_label():
    # the whole point of the exclusion: a trial that has not had its full term must not
    # enter training at all, rather than entering it as a failure
    out = eligible_for_market(_row(years_ago=0), SNAPSHOT)
    assert (out["verdict"], out["reason"]) == (INELIGIBLE, REASON_WINDOW_OPEN)


def test_planned_readout_is_undeterminable_not_ineligible():
    # counting it as out would shrink the population by the amount of missing data
    out = eligible_for_market(_row(years_ago=10, actual=False), SNAPSHOT)
    assert (out["verdict"], out["reason"]) == (UNDETERMINABLE, REASON_READOUT_NOT_ACTUAL)


def test_unknown_phase_is_undeterminable_for_market():
    out = eligible_for_market(_row(phase="NA", years_ago=10), SNAPSHOT)
    assert (out["verdict"], out["reason"]) == (UNDETERMINABLE, REASON_PHASE_UNKNOWN)


def test_a_wider_market_window_can_only_shrink_the_eligible_set():
    # structural: eligibility is monotone in the window, so the reported 3/5/10 series
    # must be non-increasing for any single trial
    row = _row(years_ago=6)
    verdicts = [eligible_for_market(row, SNAPSHOT, w)["verdict"]
                for w in sorted(MARKET_WINDOW_YEARS_REPORTED)]
    eligible_flags = [v == ELIGIBLE for v in verdicts]
    assert eligible_flags == sorted(eligible_flags, reverse=True)


# ---- the record shape and the coverage tally ------------------------------
def test_every_target_gets_a_verdict_every_time():
    verdicts = eligibility_for_row(_row(years_ago=10), SNAPSHOT)
    assert set(verdicts) == set(TARGETS)
    for record in verdicts.values():
        assert record["verdict"] in ELIGIBILITY_VERDICTS


def test_eligible_rows_carry_no_reason_and_ineligible_ones_do():
    for row in (_row(years_ago=10), _row(phase="PHASE1", years_ago=10),
                _row(years_ago=10, actual=False)):
        for record in eligibility_for_row(row, SNAPSHOT).values():
            if record["verdict"] == ELIGIBLE:
                assert record["reason"] == ""
            else:
                assert record["reason"]


def test_every_declared_reason_is_documented():
    for reason in INELIGIBILITY_REASONS + UNDETERMINABLE_REASONS:
        assert reason in REASON_DOC and REASON_DOC[reason].strip()


def test_every_verdict_is_documented():
    for verdict in ELIGIBILITY_VERDICTS:
        assert verdict in ELIGIBILITY_DOC and ELIGIBILITY_DOC[verdict].strip()


def test_coverage_verdicts_sum_to_the_row_count_for_every_target():
    rows = [_row(years_ago=10), _row(phase="PHASE1", years_ago=10),
            _row(phase="PHASE4", years_ago=10), _row(years_ago=0),
            _row(years_ago=10, actual=False), _row(drug=False)]
    out = eligibility_coverage(rows, SNAPSHOT)
    assert out["total"] == len(rows)
    for target in TARGETS:
        assert sum(out["targets"][target]["verdicts"].values()) == len(rows), target


def test_coverage_reasons_sum_to_the_non_eligible_count():
    # "ineligible" alone is not a finding: a scoping exclusion and an open window are
    # different facts, one permanent and one shrinking every year
    rows = [_row(years_ago=10), _row(phase="PHASE1", years_ago=10),
            _row(years_ago=0), _row(drug=False)]
    out = eligibility_coverage(rows, SNAPSHOT)
    for target in TARGETS:
        bucket = out["targets"][target]
        non_eligible = sum(n for v, n in bucket["verdicts"].items() if v != ELIGIBLE)
        assert sum(bucket["reasons"].values()) == non_eligible, target


def test_coverage_records_the_settings_it_used():
    out = eligibility_coverage([_row(years_ago=10)], SNAPSHOT, market_window=5,
                               advancement_window=2)
    assert out["snapshot"] == SNAPSHOT.isoformat()
    assert out["market_window_years"] == 5
    assert out["advancement_window_years"] == 2


def test_windows_reported_include_the_defaults():
    # otherwise the headline window would not appear in its own sensitivity series
    assert DEFAULT_MARKET_WINDOW_YEARS in MARKET_WINDOW_YEARS_REPORTED
    assert DEFAULT_ADVANCEMENT_WINDOW_YEARS in ADVANCEMENT_WINDOW_YEARS_REPORTED


# ---- endpoint-met must use the MODELLING population, not every label -----
# The first version of this predicate had no tier condition and reported 15,504 eligible
# against a headline population of 14,368: 1,136 tier-C drug trials counted into a
# denominator the model will not be fit on. Tier C is an interval-only verdict kept as a
# sensitivity stratum and it systematically under-calls positives relative to A/B, so
# pooling shifts the base rate.
def test_endpoint_met_requires_a_headline_tier_by_default():
    from trial_pos.services.endpoint_label import TIER_C
    out = eligible_for_endpoint_met(_row(tier=TIER_C))
    assert (out["verdict"], out["reason"]) == (INELIGIBLE, REASON_NOT_HEADLINE_TIER)


def test_every_headline_tier_is_accepted():
    for tier in HEADLINE_TIERS:
        assert eligible_for_endpoint_met(_row(tier=tier))["verdict"] == ELIGIBLE, tier


def test_tier_c_stratum_can_still_be_reported_via_the_flag():
    # carry-don't-decide: the sensitivity stratum stays reachable without editing code
    from trial_pos.services.endpoint_label import TIER_C
    assert (eligible_for_endpoint_met(_row(tier=TIER_C), headline_only=False)["verdict"]
            == ELIGIBLE)


def test_headline_requirement_can_only_shrink_the_population():
    # structural: relaxing the tier condition cannot exclude a trial it previously admitted
    from trial_pos.services.endpoint_label import TIER_A, TIER_C, TIER_D
    for tier in (TIER_A, TIER_C, TIER_D):
        row = _row(tier=tier)
        strict_v = eligible_for_endpoint_met(row, headline_only=True)["verdict"]
        loose_v = eligible_for_endpoint_met(row, headline_only=False)["verdict"]
        assert not (strict_v == ELIGIBLE and loose_v != ELIGIBLE), tier


def test_missing_tier_is_not_silently_admitted():
    # a row with no tier_min at all must not pass the headline filter by omission
    row = _row(strict="1")
    del row["tier_min"]
    assert eligible_for_endpoint_met(row)["verdict"] == INELIGIBLE


def test_coverage_records_whether_it_required_a_headline_tier():
    row = _row()
    assert eligibility_coverage([row], SNAPSHOT)["endpoint_headline_only"] is True
    assert eligibility_coverage([row], SNAPSHOT,
                               headline_only=False)["endpoint_headline_only"] is False