from datetime import date
from trial_pos.domain.records import TrialRecord
from trial_pos.foundation.identity import NCTId
from trial_pos.services import features_pure


def _trial(**kw):
    base = dict(nct_id=NCTId("NCT00000009"), phase="phase 2", status="completed",
                start_date=date(2012, 1, 1), completion_date=date(2013, 1, 1))
    base.update(kw)
    return TrialRecord(**base)


def test_phase_ordinal():
    assert features_pure.phase_ordinal(_trial(phase="Phase 2")) == 2
    assert features_pure.phase_ordinal(_trial(phase="nonsense")) is None
    assert features_pure.phase_ordinal(_trial(phase=None)) is None


def test_duration():
    # derived from the fixture dates, not a remembered 366 (2012 is a leap year, which
    # is exactly the kind of detail a transcribed constant gets wrong)
    expected = (_trial().completion_date - _trial().start_date).days
    assert features_pure.duration_days(_trial()) == expected
    assert features_pure.duration_days(_trial(completion_date=None)) is None


def test_terminated():
    assert features_pure.was_terminated(_trial(status="Terminated")) == 1
    assert features_pure.was_terminated(_trial(status="completed")) == 0
    assert features_pure.was_terminated(_trial(status=None)) is None


def test_build():
    assert set(features_pure.build_trial_features(_trial())) == {
        "phase_ordinal", "duration_days", "was_terminated"}