"""Split records by date. Guard against future leakage (R1)."""
from __future__ import annotations
from datetime import date
from typing import Iterable

from trial_pos.domain.records import TrialRecord


class TemporalLeakageError(AssertionError):
    pass


def split_by_date(records: Iterable[TrialRecord], boundary: date):
    """Train = completed before boundary. Test = started on/after. Straddlers dropped."""
    train, test = [], []
    for r in records:
        if r.completion_date is not None and r.completion_date < boundary:
            train.append(r)
        elif r.start_date is not None and r.start_date >= boundary:
            test.append(r)
    return train, test


def assert_no_leakage(train, test, boundary: date) -> None:
    for r in train:
        if r.completion_date is None or r.completion_date >= boundary:
            raise TemporalLeakageError(f"{r.nct_id} completes on/after boundary in train")
    for r in test:
        if r.start_date is None or r.start_date < boundary:
            raise TemporalLeakageError(f"{r.nct_id} starts before boundary in test")


def leakage_report(records: Iterable[TrialRecord], boundary: date) -> dict:
    records = list(records)
    train, test = split_by_date(records, boundary)
    return {
        "total": len(records),
        "train": len(train),
        "test": len(test),
        "dropped_straddling_or_undated": len(records) - len(train) - len(test),
        "boundary": boundary.isoformat(),
    }
