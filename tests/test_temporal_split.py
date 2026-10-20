"""Tests for temporal_split. Plain asserts so tests/_run_stdlib.py works."""
from __future__ import annotations

from datetime import date, timedelta

from trial_pos.services.eligibility import years_after
from trial_pos.services.population import DATE_TYPE_ACTUAL, DATE_TYPE_ESTIMATED
from trial_pos.services.temporal_split import (
    REASON_INCONSISTENT, REASON_READOUT_NOT_ACTUAL, REASON_START_ABSENT,
    REASON_START_NOT_ACTUAL, SIDE_STRADDLER,
    SIDE_TEST, SIDE_TRAIN, SIDE_UNDETERMINABLE, SIDES, START_ACTUAL, START_ANY,
    TRAIN_BY_LABEL_KNOWN, TRAIN_BY_READOUT, outcome_known_on, split_counts, split_side,
)

BOUNDARY = date(2016, 1, 1)
LAG = 3
DAY = timedelta(days=1)
ACTUAL = DATE_TYPE_ACTUAL


def _side(readout, start, start_type=ACTUAL, lag=LAG, train=TRAIN_BY_LABEL_KNOWN,
          start_rule=START_ACTUAL):
    return split_side(readout, start, start_type, BOUNDARY, lag, train, start_rule)


def test_the_three_sides_under_the_readout_reading():
    before, after = BOUNDARY - DAY, BOUNDARY + DAY
    assert _side(before, years_after(before, -LAG), train=TRAIN_BY_READOUT) == (SIDE_TRAIN, "")
    assert _side(years_after(after, LAG), after, train=TRAIN_BY_READOUT) == (SIDE_TEST, "")
    assert _side(after, before, train=TRAIN_BY_READOUT) == (SIDE_STRADDLER, "")


def test_label_known_moves_a_recent_readout_from_train_to_straddler():
    readout = BOUNDARY - DAY                       # before the boundary...
    start = years_after(readout, -LAG)
    assert _side(readout, start, train=TRAIN_BY_READOUT)[0] == SIDE_TRAIN
    # ...but its window closes after it, so the label was not known at the boundary
    assert years_after(readout, LAG) >= BOUNDARY
    assert _side(readout, start, train=TRAIN_BY_LABEL_KNOWN)[0] == SIDE_STRADDLER


def test_with_no_label_lag_the_two_readings_coincide():
    for offset in (-400, -1, 0, 1, 400):
        readout = BOUNDARY + timedelta(days=offset)
        start = readout - timedelta(days=700)
        assert (_side(readout, start, lag=0, train=TRAIN_BY_READOUT)
                == _side(readout, start, lag=0, train=TRAIN_BY_LABEL_KNOWN)), offset


def test_boundary_edges_completed_strictly_before_started_on_or_after():
    assert _side(BOUNDARY, BOUNDARY - DAY * 500, lag=0)[0] == SIDE_STRADDLER
    assert _side(BOUNDARY + DAY * 500, BOUNDARY, lag=0)[0] == SIDE_TEST


def test_an_unconfirmed_start_reaches_the_test_side_only_under_any():
    readout, start = years_after(BOUNDARY, LAG + 1), BOUNDARY + DAY
    assert _side(readout, start, DATE_TYPE_ESTIMATED, start_rule=START_ACTUAL) == \
        (SIDE_UNDETERMINABLE, REASON_START_NOT_ACTUAL)
    assert _side(readout, start, DATE_TYPE_ESTIMATED, start_rule=START_ANY) == (SIDE_TEST, "")
    assert _side(readout, start, "", start_rule=START_ACTUAL)[1] == REASON_START_NOT_ACTUAL


def test_dates_placing_a_trial_on_both_sides_are_undeterminable():
    readout = years_after(BOUNDARY, -LAG - 1)
    assert _side(readout, BOUNDARY + DAY) == (SIDE_UNDETERMINABLE, REASON_INCONSISTENT)


def test_an_absent_start_blocks_only_the_test_side():
    old = years_after(BOUNDARY, -LAG - 1)
    assert _side(old, None)[0] == SIDE_TRAIN
    assert _side(BOUNDARY + DAY, None) == (SIDE_UNDETERMINABLE, REASON_START_ABSENT)
    assert outcome_known_on(None, LAG, TRAIN_BY_LABEL_KNOWN) is None


def test_split_counts_reports_every_side_and_the_minority_class():
    items = [(SIDE_TRAIN, 1)] * 5 + [(SIDE_TRAIN, 0)] * 2 + [(SIDE_TEST, 0)] * 3 + \
            [(SIDE_STRADDLER, 1)]
    got = split_counts(items)
    assert set(got["counts"]) == set(SIDES)
    assert got["counts"][SIDE_TRAIN] == {0: 2, 1: 5}
    assert got["events"] == {SIDE_TRAIN: 2, SIDE_TEST: 0}


def test_bad_inputs_raise():
    for call in (lambda: split_counts([(SIDE_TRAIN, 2)]),
                 lambda: split_counts([("holdout", 1)]),
                 lambda: outcome_known_on(BOUNDARY, LAG, "neither"),
                 lambda: outcome_known_on(BOUNDARY, -1, TRAIN_BY_READOUT),
                 lambda: split_side(BOUNDARY, BOUNDARY, ACTUAL, BOUNDARY, 0,
                                    TRAIN_BY_READOUT, "sometimes")):
        try:
            call()
        except ValueError:
            continue
        raise AssertionError("expected ValueError")


def test_no_actual_readout_is_undeterminable_unless_the_start_places_it_in_test():
    assert _side(None, BOUNDARY - DAY) == (SIDE_UNDETERMINABLE, REASON_READOUT_NOT_ACTUAL)
    assert _side(None, BOUNDARY + DAY) == (SIDE_TEST, "")
