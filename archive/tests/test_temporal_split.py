from datetime import date
from trial_pos.domain.records import TrialRecord
from trial_pos.foundation.identity import NCTId
from trial_pos.services.temporal_split import (
    TemporalLeakageError, assert_no_leakage, leakage_report, split_by_date,
)

BOUNDARY = date(2014, 1, 1)


def _trial(nct, start, completion):
    return TrialRecord(NCTId(nct), "phase 2", "completed", start, completion)


def test_clean_split():
    past = _trial("NCT00000001", date(2010, 1, 1), date(2013, 6, 1))
    future = _trial("NCT00000002", date(2015, 1, 1), date(2018, 1, 1))
    train, test = split_by_date([past, future], BOUNDARY)
    assert [t.nct_id.value for t in train] == ["NCT00000001"]
    assert [t.nct_id.value for t in test] == ["NCT00000002"]
    assert_no_leakage(train, test, BOUNDARY)


def test_straddler_dropped():
    s = _trial("NCT00000003", date(2013, 1, 1), date(2016, 1, 1))
    train, test = split_by_date([s], BOUNDARY)
    assert train == [] and test == []


def test_leaky_train_caught():
    leaky = _trial("NCT00000004", date(2013, 1, 1), date(2015, 1, 1))
    try:
        assert_no_leakage([leaky], [], BOUNDARY)
    except TemporalLeakageError:
        return
    raise AssertionError("expected leak error")


def test_leaky_test_caught():
    leaky = _trial("NCT00000005", date(2010, 1, 1), date(2012, 1, 1))
    try:
        assert_no_leakage([], [leaky], BOUNDARY)
    except TemporalLeakageError:
        return
    raise AssertionError("expected leak error")


def test_report():
    recs = [
        _trial("NCT00000001", date(2010, 1, 1), date(2013, 6, 1)),
        _trial("NCT00000002", date(2015, 1, 1), date(2018, 1, 1)),
        _trial("NCT00000003", date(2013, 1, 1), date(2016, 1, 1)),
    ]
    r = leakage_report(recs, BOUNDARY)
    assert (r["train"], r["test"], r["dropped_straddling_or_undated"]) == (1, 1, 1)
