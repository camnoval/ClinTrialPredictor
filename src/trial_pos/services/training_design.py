"""The decided training design, as constants (Handoff D-55 to D-62). Pure.

One place so every training script reads the same split. Each constant names its decision.
"""
from __future__ import annotations

from datetime import date

from trial_pos.services.temporal_split import START_ANY, TRAIN_BY_LABEL_KNOWN

# D-59: one boundary for all three targets. Train = outcome known before it; test = started
# on or after it.
SPLIT_BOUNDARY = date(2017, 1, 1)
TRAIN_RULE = TRAIN_BY_LABEL_KNOWN
# D-58: a labelled trial's unconfirmed start date still places it (its start happened).
START_RULE = START_ANY
# D-57: advancement trains only on readouts from this date (the registry-start artefact).
ADVANCEMENT_LEFT_CUT = date(2007, 1, 1)
# D-60: drug, disease and entity history counts look back this many years before the
# trial's own registration. Lifetime counts grow with calendar time (the median prior
# trials of a trial's lead sponsor rose from 85 to 545 across readout bands,
# probe_feature_candidates_20261001.txt), so they would encode the era. Three years: with
# ClinicalTrials.gov registration required by ICMJE from September 2005, windows are
# complete for trials registered from late 2008; earlier ones are truncated and the
# builder prints coverage by band.
HISTORY_LOOKBACK_YEARS = 3
# D-67: diagnostics printed beside the headline, never instead of it.
# Extra cut-off dates, scored with exactly the headline's rules: does the skill hold in other
# eras? Two years either side of the headline boundary.
ROLLING_BOUNDARIES = (date(2015, 1, 1), date(2017, 1, 1), date(2019, 1, 1))
# The mixed-era check: grouped folds over every labelled trial (grouped_split.py).
GROUPED_FOLDS = 5

# ---- the split, per target ------------------------------------------------------------------
from trial_pos.services.eligibility import DEFAULT_ADVANCEMENT_WINDOW_YEARS  # noqa: E402
from trial_pos.services.temporal_split import SIDE_TRAIN, split_side  # noqa: E402
from trial_pos.services.trial_features import (  # noqa: E402
    TARGET_ADVANCEMENT, TARGET_ENDPOINT_MET, TARGET_MARKET, TARGETS,
)

# D-36: market's headline window. D-51: advancement's (eligibility's default).
MARKET_HEADLINE_WINDOW_YEARS = 5
ADVANCEMENT_HEADLINE_WINDOW_YEARS = DEFAULT_ADVANCEMENT_WINDOW_YEARS
# How long after the readout each label is known: the split's label lag (D-59).
LABEL_LAG_YEARS = {TARGET_ENDPOINT_MET: 0,
                   TARGET_ADVANCEMENT: ADVANCEMENT_HEADLINE_WINDOW_YEARS,
                   TARGET_MARKET: MARKET_HEADLINE_WINDOW_YEARS}
SIDE_BEFORE_LEFT_CUT = "before_left_cut"
REASON_BEFORE_LEFT_CUT = "advancement_readout_before_left_cut"


def split_side_for(target: str, readout, start, start_type, boundary: date = SPLIT_BOUNDARY
                   ) -> tuple:
    """(side, reason) under the decided split: D-59's boundary and train rule, D-58's start
    rule, and for advancement D-57's left cut (a training trial read out before it is set
    aside, never trained on). `boundary` is changed only by D-67's rolling diagnostic."""
    if target not in TARGETS:
        raise ValueError(f"target {target!r} not in {TARGETS}")
    side, why = split_side(readout, start, start_type, boundary,
                           LABEL_LAG_YEARS[target], TRAIN_RULE, START_RULE)
    if (target == TARGET_ADVANCEMENT and side == SIDE_TRAIN
            and readout < ADVANCEMENT_LEFT_CUT):
        return SIDE_BEFORE_LEFT_CUT, REASON_BEFORE_LEFT_CUT
    return side, why


def in_left_cut(target: str, readout) -> bool:
    """True when D-57 removes this trial from every fit: an advancement trial read out
    before the left cut. The mixed-era check and the final model honour it too."""
    return target == TARGET_ADVANCEMENT and (readout is None or readout < ADVANCEMENT_LEFT_CUT)
