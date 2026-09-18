"""Pure trial-level feature functions. Add one function per feature."""
from __future__ import annotations
from trial_pos.domain.records import TrialRecord

_PHASE_ORDINAL = {
    "early phase 1": 0, "phase 1": 1, "phase 1/phase 2": 1,
    "phase 2": 2, "phase 2/phase 3": 2, "phase 3": 3, "phase 4": 4,
}


def phase_ordinal(rec: TrialRecord):
    return None if rec.phase is None else _PHASE_ORDINAL.get(rec.phase.strip().lower())


def duration_days(rec: TrialRecord):
    if rec.start_date is None or rec.completion_date is None:
        return None
    return (rec.completion_date - rec.start_date).days


def was_terminated(rec: TrialRecord):
    if rec.status is None:
        return None
    return int(rec.status.strip().lower() in {"terminated", "withdrawn", "suspended"})


def build_trial_features(rec: TrialRecord) -> dict:
    return {
        "phase_ordinal": phase_ordinal(rec),
        "duration_days": duration_days(rec),
        "was_terminated": was_terminated(rec),
    }
