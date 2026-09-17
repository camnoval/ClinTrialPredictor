"""Wire the two flows: build records, and make a leakage-checked split."""
from __future__ import annotations
from datetime import date

from trial_pos.domain.records import TrialRecord
from trial_pos.foundation.contracts import Source
from trial_pos.services import features_pure
from trial_pos.services.temporal_split import assert_no_leakage, leakage_report, split_by_date


def build_records(sources: list[Source]) -> list[TrialRecord]:
    records: list[TrialRecord] = []
    for src in sources:
        for rec in src.run():
            source_features = dict(rec.features)  # e.g. TOP list-column counts
            rec.features = {**source_features, **features_pure.build_trial_features(rec)}
            records.append(rec)
    return records


def make_split(records: list[TrialRecord], boundary: date):
    train, test = split_by_date(records, boundary)
    assert_no_leakage(train, test, boundary)
    return train, test, leakage_report(records, boundary)