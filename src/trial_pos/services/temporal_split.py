"""Which side of a temporal split a labelled trial falls on (rev 7 section 4.3). Pure.

THE RULE, as settled in rev 7:
  train      the trial's outcome was determined BEFORE the boundary;
  test       the trial STARTED on or after the boundary, so nothing can leak backward;
  straddler  neither: started before, outcome determined on or after. DROPPED,
             deliberately costing sample size, so a disappointing result cannot be explained
             away as a split artefact.

"OUTCOME DETERMINED" HAS TWO READINGS, and the probe prints both before one is chosen:
  readout       the actual primary completion is before the boundary (rev 7's wording,
                "completed before the boundary");
  label_known   readout + the target's label lag is before the boundary. For market and
                advancement a 0 is only known when the window closes, so under `readout` a
                training trial's label can rest on an approval or a later trial that
                happened after the test trials started.
For endpoint met the label lag is 0 and the two readings coincide.

The test side needs a start date. AACT's start_date_type is unknown on about a third of
trials (rev 7 section 6.1), so whether an unconfirmed start may place a trial in the test
set is a parameter (`START_ANY` or `START_ACTUAL`), not an assumption.

Never split on results_first_posted_date: it is a label input (LABEL_DERIVED_FIELDS),
because sponsors with bad results post late.
"""
from __future__ import annotations

from datetime import date
from typing import Iterable, Optional

from trial_pos.services.eligibility import years_after
from trial_pos.services.population import is_actual_date

SIDE_TRAIN = "train"
SIDE_TEST = "test"
SIDE_STRADDLER = "straddler"
SIDE_UNDETERMINABLE = "undeterminable"
SIDES = (SIDE_TRAIN, SIDE_TEST, SIDE_STRADDLER, SIDE_UNDETERMINABLE)

TRAIN_BY_READOUT = "readout"
TRAIN_BY_LABEL_KNOWN = "label_known"
TRAIN_RULES = (TRAIN_BY_READOUT, TRAIN_BY_LABEL_KNOWN)

START_ANY = "any_recorded_start"
START_ACTUAL = "actual_start_only"
START_RULES = (START_ANY, START_ACTUAL)

REASON_INCONSISTENT = "starts_on_or_after_boundary_but_outcome_known_before"
REASON_START_ABSENT = "start_date_absent"
REASON_START_NOT_ACTUAL = "start_date_not_actual"
REASON_READOUT_NOT_ACTUAL = "readout_not_actual"
SPLIT_REASONS = (REASON_INCONSISTENT, REASON_READOUT_NOT_ACTUAL, REASON_START_ABSENT,
                 REASON_START_NOT_ACTUAL)


def outcome_known_on(readout: Optional[date], label_lag_years: int,
                     train_rule: str) -> Optional[date]:
    """The date the trial's label is determined, under `train_rule`. None without a readout."""
    if train_rule not in TRAIN_RULES:
        raise ValueError(f"train rule {train_rule!r} not in {TRAIN_RULES}")
    if label_lag_years < 0:
        raise ValueError(f"label lag {label_lag_years} is negative")
    if readout is None:
        return None
    return readout if train_rule == TRAIN_BY_READOUT else years_after(readout, label_lag_years)


def split_side(readout: Optional[date], start: Optional[date], start_type,
               boundary: date, label_lag_years: int, train_rule: str,
               start_rule: str) -> tuple:
    """-> (side, reason). The reason is '' for train, test and straddler.

    A trial that would be on both sides (starts on or after the boundary yet its outcome is
    known before it) has inconsistent dates and is UNDETERMINABLE, never forced to a side.
    """
    if start_rule not in START_RULES:
        raise ValueError(f"start rule {start_rule!r} not in {START_RULES}")
    known = outcome_known_on(readout, label_lag_years, train_rule)
    train_ok = known is not None and known < boundary
    started_after = start is not None and start >= boundary
    start_ok = start_rule == START_ANY or is_actual_date(start_type)
    test_ok = started_after and start_ok
    if train_ok and started_after:
        return SIDE_UNDETERMINABLE, REASON_INCONSISTENT
    if train_ok:
        return SIDE_TRAIN, ""
    if test_ok:
        return SIDE_TEST, ""
    if known is None:            # not test, and when its outcome was settled is unknown
        return SIDE_UNDETERMINABLE, REASON_READOUT_NOT_ACTUAL
    if start is None:
        return SIDE_UNDETERMINABLE, REASON_START_ABSENT
    if started_after:
        return SIDE_UNDETERMINABLE, REASON_START_NOT_ACTUAL
    return SIDE_STRADDLER, ""


def split_counts(items: Iterable[tuple]) -> dict:
    """items: (side, label) with label 0 or 1 -> {side: {0: n, 1: n}} over every side, plus
    the minority-class count of the train and test sides (the events count that bounds a
    model, rev 10 section 12.26). Raises on a label other than 0 or 1."""
    out = {s: {0: 0, 1: 0} for s in SIDES}
    for side, label in items:
        if side not in out:
            raise ValueError(f"side {side!r} not in {SIDES}")
        if label not in (0, 1):
            raise ValueError(f"label {label!r} is not 0 or 1")
        out[side][label] += 1
    events = {s: min(out[s][0], out[s][1]) for s in (SIDE_TRAIN, SIDE_TEST)}
    return {"counts": out, "events": events}
