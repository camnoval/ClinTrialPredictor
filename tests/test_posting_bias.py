"""Tests for the posting-bias measurement. Plain asserts so tests/_run_stdlib.py works.

No transcribed expected values. Rates are recomputed from the cell counts, dates are
derived from the snapshot and the deadline constant, and the rest are structural properties
that must hold whatever the constants are set to.
"""
from __future__ import annotations

from datetime import date, timedelta

from trial_pos.services.posting_bias import (
    DISCLOSURE_ANALYSIS, DISCLOSURE_DOC, DISCLOSURE_NONE, DISCLOSURE_RESULTS_ONLY,
    DISCLOSURE_STAGES, FDAAA_POSTING_DEADLINE_MONTHS, MIN_STRATUM_FOR_RATE,
    NOTABLE_SPREAD, cliff_spread, disclosure_stage, posting_deadline_elapsed,
    rate_spread, stage_rate, stratified_disclosure,
)
from trial_pos.services.population import UNKNOWN

SNAPSHOT = date(2026, 9, 20)


def _row(readout=None, actual=True, posted=False, analysed=0, stratum="a"):
    row = {"results_posted": 1 if posted else 0, "n_primary_analyzed": analysed,
           "stratum": stratum}
    if readout is not None:
        row["primary_completion_date"] = readout.isoformat()
        row["primary_completion_date_type"] = "Actual" if actual else "Estimated"
    return row


def _long_ago():
    """A readout comfortably past the deadline, derived from the constant not written."""
    years = FDAAA_POSTING_DEADLINE_MONTHS // 12 + 2
    return SNAPSHOT.replace(year=SNAPSHOT.year - years)


# ---- the deadline denominator ---------------------------------------------
def test_deadline_requires_an_actual_readout():
    # a planned completion cannot start a statutory clock
    assert posting_deadline_elapsed(_row(_long_ago(), actual=True), SNAPSHOT) is True
    assert posting_deadline_elapsed(_row(_long_ago(), actual=False), SNAPSHOT) is None
    assert posting_deadline_elapsed(_row(), SNAPSHOT) is None


def test_deadline_is_exactly_the_stated_number_of_months():
    months = FDAAA_POSTING_DEADLINE_MONTHS
    # a readout whose deadline lands exactly on the snapshot has elapsed (inclusive)
    total = SNAPSHOT.month - 1 - months
    year = SNAPSHOT.year + (total // 12)
    month = total % 12 + 1
    on_boundary = date(year, month, SNAPSHOT.day)
    assert posting_deadline_elapsed(_row(on_boundary), SNAPSHOT) is True
    one_day_short = on_boundary + timedelta(days=1)
    assert posting_deadline_elapsed(_row(one_day_short), SNAPSHOT) is False


def test_a_recent_readout_has_not_failed_to_post():
    # the distinction the denominator exists for: not-yet-due is not non-disclosure
    assert posting_deadline_elapsed(_row(SNAPSHOT), SNAPSHOT) is False


def test_month_end_readout_clamps_the_day_downward():
    # 31 January plus one month has no 31st; clamping DOWN moves the deadline earlier,
    # which can only admit a trial that has had its full term.
    #
    # The first version of this test wrote 28 February as the clamped deadline and failed:
    # 2020 is a leap year, so the clamp lands on the 29th. The month length is derived
    # here rather than assumed, which is the same mistake the clamp itself exists to avoid.
    readout = date(2020, 1, 31)
    last_day_of_next_month = (date(2020, 3, 1) - timedelta(days=1)).day
    deadline = date(2020, 2, last_day_of_next_month)
    row = _row(readout)
    assert posting_deadline_elapsed(row, SNAPSHOT, months=1) is True
    assert posting_deadline_elapsed(row, deadline, months=1) is True
    assert posting_deadline_elapsed(row, deadline - timedelta(days=1),
                                    months=1) is False
    # and the clamp is strictly earlier than the readout's own day-of-month
    assert last_day_of_next_month < readout.day


def test_a_longer_deadline_is_never_more_inclusive():
    # structural: extending the allowance can only leave FEWER deadlines elapsed
    row = _row(SNAPSHOT.replace(year=SNAPSHOT.year - 2))
    elapsed = [posting_deadline_elapsed(row, SNAPSHOT, months=m)
               for m in (6, 12, 24, 48)]
    assert elapsed == sorted(elapsed, reverse=True)


# ---- the disclosure ladder ------------------------------------------------
def test_three_stages_not_two():
    assert disclosure_stage(_row()) == DISCLOSURE_NONE
    assert disclosure_stage(_row(posted=True)) == DISCLOSURE_RESULTS_ONLY
    assert disclosure_stage(_row(posted=True, analysed=2)) == DISCLOSURE_ANALYSIS


def test_an_analysis_counts_even_without_the_posting_flag():
    # the analysis IS the disclosure; a disagreeing flag is a data problem worth seeing
    # rather than a reason to drop the row
    assert disclosure_stage(_row(posted=False, analysed=1)) == DISCLOSURE_ANALYSIS


def test_blank_and_zero_analysis_counts_are_both_not_an_analysis():
    for value in ("", "0", 0, None):
        assert disclosure_stage(_row(posted=True, analysed=value)) \
            == DISCLOSURE_RESULTS_ONLY, value


def test_every_stage_is_documented():
    for stage in DISCLOSURE_STAGES:
        assert stage in DISCLOSURE_DOC and DISCLOSURE_DOC[stage].strip()


def test_stages_are_ordered_from_least_to_most_disclosure():
    # stage_rate reads DISCLOSURE_STAGES as a cumulative ladder, so the order is load-bearing
    assert DISCLOSURE_STAGES.index(DISCLOSURE_NONE) < \
        DISCLOSURE_STAGES.index(DISCLOSURE_RESULTS_ONLY) < \
        DISCLOSURE_STAGES.index(DISCLOSURE_ANALYSIS)


# ---- cumulative rates -----------------------------------------------------
def test_rate_is_cumulative_up_the_ladder():
    # a trial that posted an analysis also posted results, so the results rate can never
    # be below the analysis rate
    bucket = {DISCLOSURE_NONE: 5, DISCLOSURE_RESULTS_ONLY: 3, DISCLOSURE_ANALYSIS: 2}
    assert stage_rate(bucket, DISCLOSURE_RESULTS_ONLY) >= \
        stage_rate(bucket, DISCLOSURE_ANALYSIS)


def test_rate_matches_an_independent_recomputation():
    bucket = {DISCLOSURE_NONE: 7, DISCLOSURE_RESULTS_ONLY: 11, DISCLOSURE_ANALYSIS: 13}
    n = sum(bucket.values())
    assert abs(stage_rate(bucket, DISCLOSURE_ANALYSIS) - 13 / n) < 1e-12
    assert abs(stage_rate(bucket, DISCLOSURE_RESULTS_ONLY) - (11 + 13) / n) < 1e-12
    assert stage_rate(bucket, DISCLOSURE_NONE) == 1.0


def test_rate_of_an_empty_stratum_is_none_not_zero():
    assert stage_rate({s: 0 for s in DISCLOSURE_STAGES}, DISCLOSURE_ANALYSIS) is None


# ---- stratification -------------------------------------------------------
def test_stratum_counts_sum_to_the_denominator():
    rows = [_row(_long_ago(), posted=True, analysed=1, stratum="x"),
            _row(_long_ago(), posted=True, stratum="x"),
            _row(_long_ago(), stratum="y")]
    out = stratified_disclosure(rows, lambda r: r["stratum"], SNAPSHOT)
    assert out["n_in_denominator"] == len(rows)
    assert sum(sum(b.values()) for b in out["strata"].values()) == len(rows)


def test_not_yet_due_and_no_actual_date_are_excluded_separately():
    # the two reasons a trial is out of the denominator are different facts: one resolves
    # with time, the other is missing data
    rows = [_row(SNAPSHOT), _row(_long_ago(), actual=False), _row(_long_ago())]
    out = stratified_disclosure(rows, lambda r: "s", SNAPSHOT)
    assert out["excluded"]["deadline_not_elapsed"] == 1
    assert out["excluded"]["readout_not_actual"] == 1
    assert out["n_in_denominator"] == 1


def test_missing_stratum_key_becomes_unknown_not_dropped():
    out = stratified_disclosure([_row(_long_ago(), stratum="")],
                                lambda r: r["stratum"], SNAPSHOT)
    assert UNKNOWN in out["strata"]


def test_every_stratum_carries_every_stage_even_at_zero():
    # an absent key would hide a stage that never occurred, which is itself informative
    out = stratified_disclosure([_row(_long_ago())], lambda r: "s", SNAPSHOT)
    assert set(out["strata"]["s"]) == set(DISCLOSURE_STAGES)


# ---- the spread statistic -------------------------------------------------
def _stratum(none, results, analysis):
    return {DISCLOSURE_NONE: none, DISCLOSURE_RESULTS_ONLY: results,
            DISCLOSURE_ANALYSIS: analysis}


def test_spread_is_max_minus_min_over_qualifying_strata():
    floor = MIN_STRATUM_FOR_RATE
    strata = {"low": _stratum(floor, 0, 0), "high": _stratum(0, 0, floor)}
    out = rate_spread(strata, DISCLOSURE_ANALYSIS)
    assert out["spread"] == 1.0
    assert out["lowest"][0] == "low" and out["highest"][0] == "high"


def test_small_strata_are_excluded_and_counted():
    # a four-trial stratum at 100% would otherwise set the maximum
    floor = MIN_STRATUM_FOR_RATE
    strata = {"big_a": _stratum(floor, 0, 0), "big_b": _stratum(0, 0, floor),
              "tiny": _stratum(0, 0, 1)}
    out = rate_spread(strata, DISCLOSURE_ANALYSIS)
    assert out["n_strata_used"] == 2
    assert out["n_strata_too_small"] == 1
    assert "tiny" not in out["rates"]


def test_spread_is_none_rather_than_zero_when_unmeasurable():
    # zero would read as "no variation"; the truth is "not enough strata to compare"
    out = rate_spread({"only": _stratum(MIN_STRATUM_FOR_RATE, 0, 0)},
                      DISCLOSURE_ANALYSIS)
    assert out["spread"] is None
    out_empty = rate_spread({}, DISCLOSURE_ANALYSIS)
    assert out_empty["spread"] is None


def test_spread_is_never_negative_and_bounded_by_one():
    floor = MIN_STRATUM_FOR_RATE
    strata = {"a": _stratum(floor // 2, floor // 2, floor),
              "b": _stratum(floor, floor, floor),
              "c": _stratum(0, floor, floor)}
    out = rate_spread(strata, DISCLOSURE_ANALYSIS)
    assert 0.0 <= out["spread"] <= 1.0


def test_notable_spread_is_a_named_reporting_threshold():
    # not a verdict: it decides what gets flagged in prose, and both callers must use the
    # same number rather than each picking one
    assert 0.0 < NOTABLE_SPREAD < 1.0


# ---- the cliff, isolated from the decision to post at all -----------------
def test_cliff_is_conditional_on_having_disclosed():
    # a variable can leave the POSTING rate flat and still decide the label's availability
    # entirely, by acting only on the second step
    floor = MIN_STRATUM_FOR_RATE
    strata = {
        # identical posting rates, opposite analysis rates among those who posted
        "industry": _stratum(floor, 0, floor),
        "academic": _stratum(floor, floor, 0),
    }
    posting = rate_spread(strata, DISCLOSURE_RESULTS_ONLY)
    cliff = cliff_spread(strata)
    assert posting["spread"] == 0.0            # posting rate identical
    assert cliff["spread"] == 1.0              # the cliff explains everything


def test_cliff_denominator_is_disclosures_not_all_trials():
    floor = MIN_STRATUM_FOR_RATE
    strata = {"a": _stratum(1000, floor, floor), "b": _stratum(0, floor, floor)}
    out = cliff_spread(strata)
    # the two strata have very different posting rates but the same conditional rate
    assert out["spread"] == 0.0


def test_cliff_ignores_strata_with_too_few_disclosures():
    floor = MIN_STRATUM_FOR_RATE
    strata = {"a": _stratum(0, floor, floor), "b": _stratum(10000, 1, 0)}
    out = cliff_spread(strata)
    assert "b" not in out["rates"]
    assert out["spread"] is None               # only one qualifying stratum left