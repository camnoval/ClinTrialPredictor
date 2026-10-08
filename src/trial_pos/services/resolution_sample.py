"""The hand-labelled sample that measures trial-to-drug resolution. Pure.

Decided 2026-10-07 (rev 10 section 12.30, gate ratified 2026-10-06):

  frame      market-eligible pivotal drug trials at the headline window
  strata     the resolver's match class (D-24): MATCHED (a tested agent fully resolved),
             PARTIAL (only partly resolved agents; excluded from the market label, sampled
             to measure how often the resolved part is the tested drug), UNMATCHED (none,
             and undeterminable: an approved drug there is a miss all the same)
  order      each stratum's members in salted-hash order, fixed at draw time. Tranche 1 is
             the first `n` of each; tranche 2 continues down the SAME order, so the second
             draw is a function of the first, never a re-draw
  stopping   on COUNTS, never on an observed rate. The targets below are the smallest
             sample whose Wilson half-width at the gate value is within the target
  blind      the sheet carries no resolver output and no stratum; rows are presented in a
             separate salted order so position does not reveal the stratum
  intervals  Wilson for precision (one stratum). Recall is a stratified ratio estimate,
             scored once labels exist; its interval is Wilson on the effective sample size

The gate decides only whether the market target proceeds. What is carried forward is the
measured truth: both rates with their 95% intervals, as the market label's error rate.
"""
from __future__ import annotations

from math import sqrt
from statistics import NormalDist
from typing import Iterable, Optional

from trial_pos.services.sampling import ordered_by_hash, stable_hash

from trial_pos.services.drug_resolution import (  # noqa: E402
    MATCH_CLASSES, MATCH_FULL, MATCH_NONE, MATCH_PARTIAL_ONLY, MATCH_UNDETERMINABLE,
)

STRATUM_MATCHED = "matched"
STRATUM_PARTIAL = "partial_only"
STRATUM_UNMATCHED = "unmatched"
STRATA = (STRATUM_MATCHED, STRATUM_PARTIAL, STRATUM_UNMATCHED)
STRATUM_OF_CLASS = {MATCH_FULL: STRATUM_MATCHED, MATCH_PARTIAL_ONLY: STRATUM_PARTIAL,
                    MATCH_NONE: STRATUM_UNMATCHED, MATCH_UNDETERMINABLE: STRATUM_UNMATCHED}

RECALL_GATE = 0.90          # ratified 2026-10-06
PRECISION_GATE = 0.95       # ratified 2026-10-06
TARGET_HALF_WIDTH = 0.05
CONFIDENCE = 0.95

DEFAULT_SALT = "trial-drug-resolution-v1"
SALT_ORDER = "order"
SALT_PRESENT = "present"
SAMPLE_ID_PREFIX = "R"
CTGOV_URL = "https://clinicaltrials.gov/study/{nct}"

ANSWER_YES = "yes"
ANSWER_NO = "no"
ANSWER_UNCLEAR = "unclear"
ANSWERS = (ANSWER_YES, ANSWER_NO, ANSWER_UNCLEAR)
NOT_IN_DRUGCENTRAL = "none"

NO_FORM_STATED = "none stated"

SHEET_COLUMNS = ("sample_id", "nct_id", "url", "tested_agents", "tested_form",
                 "drugcentral_ids", "any_fda_approved", "approval_cber_only", "notes")
LABEL_COLUMNS = SHEET_COLUMNS[3:]
KEY_COLUMNS = ("sample_id", "nct_id", "stratum", "stratum_rank", "tranche",
               "selection_outcome", "match_class", "matched", "drugs", "partial_drugs",
               "any_form_stated", "tested_moiety_in_comparator")
RESOLUTION_COLUMNS = ("nct_id", "selection_outcome", "match_class", "matched", "drugs",
                      "partial_drugs", "any_form_stated", "tested_moiety_in_comparator")


def z_value(confidence: float = CONFIDENCE) -> float:
    return NormalDist().inv_cdf(1.0 - (1.0 - confidence) / 2.0)


def wilson_interval(successes: int, n: int, z: Optional[float] = None):
    """(low, high), or None when n == 0: a rate with no denominator is undefined."""
    if n < 0 or successes < 0 or successes > n:
        raise ValueError(f"impossible counts {successes}/{n}")
    if n == 0:
        return None
    z = z_value() if z is None else z
    p = successes / n
    denom = 1.0 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return max(0.0, centre - half), min(1.0, centre + half)


def wilson_half_width(p: float, n: int, z: Optional[float] = None) -> float:
    """Half-width of the Wilson interval at an observed proportion p over n."""
    if n <= 0:
        raise ValueError("n must be positive")
    z = z_value() if z is None else z
    return z * sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1.0 + z * z / n)


def n_for_half_width(p: float, half_width: float = TARGET_HALF_WIDTH,
                     z: Optional[float] = None) -> int:
    """Smallest n whose Wilson half-width at p is within half_width."""
    if not 0 < half_width < 1:
        raise ValueError("half_width must be in (0, 1)")
    n = 1
    while wilson_half_width(p, n, z) > half_width:
        n += 1
    return n


def stratum_of(match_class: str) -> str:
    """A resolver match class -> its stratum. An unknown class raises."""
    if match_class not in STRATUM_OF_CLASS:
        raise ValueError(f"match class {match_class!r} is not one of {MATCH_CLASSES}")
    return STRATUM_OF_CLASS[match_class]


def strata(matched_by_nct: dict, frame: Iterable[str]) -> dict:
    """`matched_by_nct`: {nct: match class}."""
    """{stratum: sorted members}. A frame trial absent from the resolution raises: it means
    the resolver was not run on the population the sample is drawn from."""
    out = {s: [] for s in STRATA}
    for nct in sorted(set(frame)):
        if nct not in matched_by_nct:
            raise KeyError(f"{nct} is in the frame but has no resolution row")
        out[stratum_of(matched_by_nct[nct])].append(nct)
    return out


def ordered_strata(members: dict, salt: str = DEFAULT_SALT) -> dict:
    return {s: ordered_by_hash(members.get(s, ()), f"{salt}:{SALT_ORDER}:{s}")
            for s in STRATA}


def tranche(ordered: dict, start: int, size: int) -> dict:
    """{stratum: [(rank, nct)]} for ranks [start, start + size) in each stratum."""
    if start < 0 or size < 0:
        raise ValueError("start and size must be non-negative")
    return {s: list(enumerate(ordered[s]))[start:start + size] for s in STRATA}


def presentation(selected: dict, salt: str = DEFAULT_SALT) -> list:
    """[(sample_id, stratum, rank, nct)] in a salted order independent of the stratum."""
    flat = [(s, rank, nct) for s in STRATA for rank, nct in selected.get(s, ())]
    flat.sort(key=lambda x: (stable_hash(x[2], f"{salt}:{SALT_PRESENT}"), x[2]))
    width = len(str(len(flat))) if flat else 1
    return [(f"{SAMPLE_ID_PREFIX}{i + 1:0{width}d}", s, rank, nct)
            for i, (s, rank, nct) in enumerate(flat)]


def sheet_row(sample_id: str, nct: str) -> dict:
    row = {c: "" for c in SHEET_COLUMNS}
    row.update(sample_id=sample_id, nct_id=nct, url=CTGOV_URL.format(nct=nct))
    return row


def key_row(sample_id: str, stratum: str, rank: int, tranche_no: int, resolution: dict) -> dict:
    missing = [c for c in RESOLUTION_COLUMNS if c not in resolution]
    if missing:
        raise KeyError(f"resolution row lacks {missing}: rerun resolve_trial_drugs.py")
    return {"sample_id": sample_id, "nct_id": resolution["nct_id"], "stratum": stratum,
            "stratum_rank": rank, "tranche": tranche_no,
            "selection_outcome": resolution["selection_outcome"],
            "match_class": resolution["match_class"],
            "matched": resolution["matched"], "drugs": resolution["drugs"],
            "partial_drugs": resolution["partial_drugs"],
            "any_form_stated": resolution["any_form_stated"],
            "tested_moiety_in_comparator": resolution["tested_moiety_in_comparator"]}
