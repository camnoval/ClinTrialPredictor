"""Define the trial population, and carry FDAAA inputs WITHOUT deciding applicability.

WHAT THIS MODULE IS FOR
=======================
Step 6.2 drops the TOP x ChEMBL cohort restriction: the endpoint-met label needs only
AACT's own `outcomes` / `outcome_analyses` tables, so the cohort was pure cost. The
population becomes every interventional trial in AACT, and this module is the pure half of
that change -- what counts as in scope, and which raw fields travel with each row so the
posting-bias audit can later decide FDAAA applicability.

=============================================================================
IT DECIDES NOTHING ABOUT FDAAA. THAT IS THE POINT.
=============================================================================
FDAAA applicability is not a field in AACT. It is a derivation from intervention type,
phase, US jurisdiction, product approval status at the time, and the trial's own dates --
and several of its inputs are sponsor-self-reported on a form that only existed after the
2017 Final Rule, so their COVERAGE is itself unknown until measured.

Committing to an applicability rule before measuring the coverage of its inputs would bury
a judgment call inside a pull. So this module:

  - NAMES the component fields (`FDAAA_COMPONENTS`) and why each one matters,
  - extracts them as TRI-STATE values (`components_for_row`), so "sponsor said no" stays
    distinguishable from "sponsor was never asked",
  - counts their coverage (`component_coverage`),
  - and provides descriptive era bins derived from the statutory dates (`era_for_date`).

No function here returns "this trial was required to post." The audit script reads the
coverage, and the rule gets written once -- explicitly, with its inputs' coverage known.

=============================================================================
WHY TRI-STATE, EVERYWHERE
=============================================================================
`is_fda_regulated_drug` has three meaningful states: true, false, and never-collected. A
pull that flattens the third into false would manufacture a finding -- it would look like
sponsors declaring their trials unregulated, when in fact the question postdates the trial.
`tribool` is therefore the only way booleans enter this pipeline, and it handles the
Postgres-native form AND the string form a CSV round-trip produces.

=============================================================================
ERA BINS ARE DESCRIPTIVE, AND DERIVED FROM STATUTE
=============================================================================
The boundary years are not chosen. They are read off the named statutory dates below, so
the bins cannot drift from the law they are supposed to represent, and the tests assert the
relationship rather than transcribing "2008". The eras are contiguous and exhaustive by
construction -- `era_for_date` cannot return None for a valid date.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Iterable, Optional

# ---- study type / population scope ---------------------------------------
# AACT `study_type` free-ish values seen in the wild: 'Interventional',
# 'Observational', 'Observational [Patient Registry]', 'Expanded Access', 'N/A'.
# Matching is on a normalised prefix so a bracketed qualifier or a case change does not
# silently drop a whole class of trial.
STUDY_TYPE_INTERVENTIONAL = "interventional"
STUDY_TYPE_OBSERVATIONAL = "observational"
STUDY_TYPE_EXPANDED_ACCESS = "expanded access"

# Expanded access is NOT interventional for our purposes: there is no randomised
# comparison and no primary-endpoint analysis to label. It is named rather than lumped
# into "other" so its count is visible in the audit.
KNOWN_STUDY_TYPES = (
    STUDY_TYPE_INTERVENTIONAL,
    STUDY_TYPE_OBSERVATIONAL,
    STUDY_TYPE_EXPANDED_ACCESS,
)

# 'N/A' in `study_type` means the registry does not state a type -- unknown. This is
# handled HERE rather than in `_text` on purpose: in `phase`, 'N/A' is meaningful
# ("no phase applies", e.g. a device trial), and folding it into None there would destroy
# exactly the unknown-vs-not-applicable distinction this module exists to preserve. So the
# not-applicable tokens are field-specific, never global.
_STUDY_TYPE_NOT_APPLICABLE = frozenset({"n/a", "na", "not applicable", "unknown"})


def normalize_study_type(raw) -> Optional[str]:
    """AACT study_type -> a canonical lowercase form, or None if blank/unknown.

    Returns the canonical string for a recognised type, the lowercased original for an
    unrecognised non-blank value (so it shows up in the audit instead of vanishing), and
    None when the field is absent or explicitly not-applicable.
    """
    s = _text(raw)
    if s is None:
        return None
    low = s.lower()
    if low in _STUDY_TYPE_NOT_APPLICABLE:
        return None
    for known in KNOWN_STUDY_TYPES:
        if low.startswith(known):
            return known
    return low


def is_interventional(raw) -> Optional[bool]:
    """True / False / None(unknown). None is NOT False -- see the module docstring."""
    norm = normalize_study_type(raw)
    if norm is None:
        return None
    return norm == STUDY_TYPE_INTERVENTIONAL


# ---- tri-state boolean parsing -------------------------------------------
# Postgres hands psycopg2 real bools; a CSV round-trip hands back 't'/'f' or
# 'True'/'False' or ''. pandas turns the blank into float NaN, which is TRUTHY -- the same
# trap that once made every missing results-posting date read as posted.
_TRUE_TOKENS = frozenset({"t", "true", "y", "yes", "1"})
_FALSE_TOKENS = frozenset({"f", "false", "n", "no", "0"})


def tribool(raw) -> Optional[bool]:
    """Parse an AACT boolean to True / False / None(unknown). Never guesses."""
    if raw is None:
        return None
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, float) and raw != raw:      # NaN
        return None
    if isinstance(raw, (int, float)):
        if raw == 1:
            return True
        if raw == 0:
            return False
        return None
    s = str(raw).strip().lower()
    if s in _TRUE_TOKENS:
        return True
    if s in _FALSE_TOKENS:
        return False
    return None


def _text(raw) -> Optional[str]:
    """Blank-ish -> None, guarding the pandas-NaN-is-truthy trap.

    'NA' is deliberately NOT treated as blank. AACT ships `phase` as 'NA' for trials
    where no phase applies (device trials, behavioural interventions), and that is a
    STATEMENT, not a gap -- it is one of the strongest signals that a trial falls outside
    a drug results obligation. An earlier version of this function listed 'na' among the
    blank tokens, which collapsed explicit not-applicable into absent and produced a
    single 'unknown' bucket mixing the two. The module docstring claimed to preserve that
    distinction while the code destroyed it; the live smoke run is what exposed it.

    Fields where 'N/A' really does mean unknown handle it themselves --
    `normalize_study_type` is the one such case, and it checks after this function runs.
    """
    if raw is None:
        return None
    if isinstance(raw, float) and raw != raw:
        return None
    s = str(raw).strip()
    if not s or s.lower() in ("nan", "none", "null", "nat"):
        return None
    return s


# Tokens that mean "the question does not apply here", as opposed to "nobody answered".
# Kept separate from the blank tokens above so the two can be counted separately.
NOT_APPLICABLE_TOKENS = frozenset({"na", "n/a", "not applicable", "none/na"})


def is_not_applicable(raw) -> bool:
    """True when a value explicitly states the question does not apply."""
    s = _text(raw)
    return s is not None and s.lower() in NOT_APPLICABLE_TOKENS


# ---- statutory dates -----------------------------------------------------
# These are dates in law, not tuning parameters, which is why they are constants here and
# not CLI flags. Every era boundary below is derived from them, so the bins cannot drift
# from the statute and the tests can assert the relationship instead of a literal year.
#
# FDAAA: Food and Drug Administration Amendments Act of 2007, Section 801. Signed
# 2007-09-27; the results-submission obligation ran from one year after enactment.
FDAAA_ENACTED = date(2007, 9, 27)
FDAAA_RESULTS_EFFECTIVE = date(2008, 9, 27)

# Final Rule (42 CFR Part 11), which broadened the obligation and tightened enforcement:
# effective 2017-01-18, with compliance required from 2017-04-18. The compliance date is
# the operative one for "was this sponsor actually on the hook", so it is the boundary
# used below; the effective date is kept because it is the date the obligation existed.
FINAL_RULE_EFFECTIVE = date(2017, 1, 18)
FINAL_RULE_COMPLIANCE = date(2017, 4, 18)

ERA_PRE_FDAAA = "pre_fdaaa"
ERA_FDAAA_801 = "fdaaa_801"
ERA_FINAL_RULE = "final_rule"

# (era name, inclusive start or None for open, exclusive end or None for open).
# Derived from the statutory constants -- contiguous and exhaustive by construction.
ERAS = (
    (ERA_PRE_FDAAA, None, FDAAA_RESULTS_EFFECTIVE),
    (ERA_FDAAA_801, FDAAA_RESULTS_EFFECTIVE, FINAL_RULE_COMPLIANCE),
    (ERA_FINAL_RULE, FINAL_RULE_COMPLIANCE, None),
)

ERA_DOC = {
    ERA_PRE_FDAAA: (
        f"before {FDAAA_RESULTS_EFFECTIVE.isoformat()}: no results-submission obligation. "
        "Absence of posted results here says nothing about disclosure behaviour."
    ),
    ERA_FDAAA_801: (
        f"{FDAAA_RESULTS_EFFECTIVE.isoformat()} to {FINAL_RULE_COMPLIANCE.isoformat()}: "
        "FDAAA 801 in force, narrower scope, weak enforcement."
    ),
    ERA_FINAL_RULE: (
        f"from {FINAL_RULE_COMPLIANCE.isoformat()}: Final Rule compliance date. Broader "
        "scope, and the form that collects several FDAAA component fields dates from here."
    ),
}


def parse_date(raw) -> Optional[date]:
    """AACT date -> date. Accepts date/datetime and partial strings.

    AACT date columns are real dates from Postgres but strings after a CSV round-trip, and
    ClinicalTrials.gov permits month-precision dates ('2015-03') and year-only ones. A
    partial date is resolved to the FIRST day of the stated period, which is the
    conservative choice for era binning: it never moves a trial later than the registry
    can justify.
    """
    if isinstance(raw, datetime):
        return raw.date()
    if isinstance(raw, date):
        return raw
    s = _text(raw)
    if s is None:
        return None
    s = s.split("T")[0].split(" ")[0]
    for fmt, pad in (("%Y-%m-%d", None), ("%Y-%m", "month"), ("%Y", "year")):
        try:
            parsed = datetime.strptime(s, fmt)
        except ValueError:
            continue
        if pad == "year":
            return date(parsed.year, 1, 1)
        if pad == "month":
            return date(parsed.year, parsed.month, 1)
        return parsed.date()
    return None


def parse_year(raw) -> Optional[int]:
    d = parse_date(raw)
    return d.year if d is not None else None


def era_for_date(raw) -> Optional[str]:
    """Which regulatory era a date falls in. None ONLY when the date is unparseable.

    The eras partition the timeline, so a valid date always lands somewhere -- there is no
    "other" bucket to hide a binning bug in.
    """
    d = parse_date(raw)
    if d is None:
        return None
    for name, start, end in ERAS:
        if (start is None or d >= start) and (end is None or d < end):
            return name
    # Unreachable while ERAS stays contiguous and open at both ends; a structural test
    # pins that, so reaching here means ERAS was edited wrongly.
    raise AssertionError(f"ERAS do not cover {d!r} -- the era table is no longer a partition")


# ---- which date the era is binned on -------------------------------------
# `primary_completion_date` is the date a posting deadline actually runs from: when the
# last participant was measured for the primary outcome. `completion_date` can be years
# later, after long-term follow-up.
#
# The first live run found primary_completion_date absent for 74% of the oldest trials
# while completion_date was often present, so a fallback recovers real coverage. But the
# two are NOT the same quantity, and substituting one for the other pushes some trials
# into a LATER era than the statute would -- which biases an era analysis toward
# overstating how many trials were subject to the tightened rules.
#
# So the fallback is optional, and every row records WHICH date it used. That turns the
# question empirical: the audit reports era rates both ways, and if they differ materially
# the fallback is doing work that needs justifying rather than quietly shifting the bins.
ERA_DATE_PRIMARY = "primary_completion_date"
ERA_DATE_FALLBACK = "completion_date"
ERA_DATE_ABSENT = "absent"
ERA_DATE_SOURCES = (ERA_DATE_PRIMARY, ERA_DATE_FALLBACK, ERA_DATE_ABSENT)

ERA_DATE_SOURCE_DOC = {
    ERA_DATE_PRIMARY: "primary completion: the date a posting deadline runs from",
    ERA_DATE_FALLBACK: ("overall completion, used because primary completion was absent. "
                        "Can be later than the primary readout, so this row's era may be "
                        "too late rather than too early"),
    ERA_DATE_ABSENT: "neither date available; era is unknown",
}


def era_date_for_row(row: dict, allow_fallback: bool = True):
    """-> (date | None, source). Source is always one of ERA_DATE_SOURCES.

    Reads output/AACT field names, which are identical for these two columns, so this
    works on a raw pulled row and on a derived record alike.
    """
    primary = parse_date(row.get(ERA_DATE_PRIMARY))
    if primary is not None:
        return primary, ERA_DATE_PRIMARY
    if allow_fallback:
        fallback = parse_date(row.get(ERA_DATE_FALLBACK))
        if fallback is not None:
            return fallback, ERA_DATE_FALLBACK
    return None, ERA_DATE_ABSENT


def era_for_row(row: dict, allow_fallback: bool = True):
    """-> (era | None, date_source). The pair travels together on purpose: an era read
    without knowing which date produced it cannot be audited."""
    d, source = era_date_for_row(row, allow_fallback)
    return (era_for_date(d) if d is not None else None), source


def era_coverage_by_source(rows: Iterable[dict], allow_fallback: bool = True) -> dict:
    """-> {date_source: {era_or_unknown: count}}, every key present even at zero.

    This is the table that makes the fallback decision checkable: if the fallback rows'
    era distribution looks nothing like the primary rows', the fallback is not a neutral
    coverage gain.
    """
    out = {src: {name: 0 for name, _s, _e in ERAS} for src in ERA_DATE_SOURCES}
    for src in out:
        out[src][UNKNOWN] = 0
    for row in rows:
        era, source = era_for_row(row, allow_fallback)
        out[source][era if era is not None else UNKNOWN] += 1
    return out


def completion_gap_days(row: dict) -> Optional[int]:
    """Days from primary completion to overall completion. None if either is missing.

    Negative values are returned rather than clamped: overall completion earlier than
    primary completion is a registry data error, and the audit should be able to count
    them instead of having them silently folded into zero.
    """
    primary = parse_date(row.get(ERA_DATE_PRIMARY))
    overall = parse_date(row.get(ERA_DATE_FALLBACK))
    if primary is None or overall is None:
        return None
    return (overall - primary).days


def quantile(sorted_values: list, q: float):
    """Nearest-rank quantile of an already-sorted list. None if empty.

    Nearest-rank rather than interpolated so the result is always an observed value --
    for a day count, an interpolated 'half a day' would be an artefact of the method.
    """
    if not sorted_values:
        return None
    if not 0.0 <= q <= 1.0:
        raise ValueError(f"quantile q must be in [0,1], got {q}")
    idx = int(q * (len(sorted_values) - 1) + 0.5)
    return sorted_values[idx]


def gap_summary(rows: Iterable[dict], quantiles=(0.5, 0.9)) -> dict:
    """Distribution of primary-to-overall completion gaps.

    Reported so the fallback's risk is quantified rather than asserted: if the typical gap
    is days, substituting overall completion is nearly harmless; if it is years, the
    fallback moves trials across era boundaries and the flagged rows must be read
    separately.
    """
    gaps = sorted(g for g in (completion_gap_days(r) for r in rows) if g is not None)
    out = {
        "n_comparable": len(gaps),
        "n_negative": sum(1 for g in gaps if g < 0),
        "min": gaps[0] if gaps else None,
        "max": gaps[-1] if gaps else None,
    }
    for q in quantiles:
        out[f"q{int(q * 100)}"] = quantile(gaps, q)
    return out


# ---- date TYPE: actual event vs stated plan ------------------------------
# AACT carries a `*_date_type` beside each date: 'Actual' or 'Anticipated'.
# This was missed in the first full pull and it matters twice over.
#
# For ERA binning it is a nuance: an anticipated date still says roughly when.
# For the TEMPORAL SPLIT it is decisive. The split is "train on trials that COMPLETED
# before the boundary, so their outcome was determined by then". An anticipated
# completion is a plan, not an event -- splitting on it would put unfinished trials in
# the training set, which is the exact leak the split was designed to prevent.
#
# Consistent with the FDAAA handling: the type is CARRIED and REPORTED, never used to
# filter here. The split will filter on it; the pull only has to preserve it.
# VOCABULARY NOTE, learned from real data: the live AACT server reports 'Estimated',
# NOT 'Anticipated' -- 153,288 primary completion dates were 'Estimated' and ZERO were
# 'Anticipated'. 'Anticipated' is the older registry wording and is kept because a
# historical dump may still carry it.
#
# This was caught only because `normalize_date_type` passes an unrecognised value through
# as itself instead of returning None, so 'estimated' appeared in the audit as its own
# bucket rather than vanishing into 'unknown'. `is_actual_date` was also right by
# construction -- anything that is not 'actual' is not actual -- so nothing was
# mislabelled. The vocabulary was merely incomplete, which is the cheap version of this
# mistake.
DATE_TYPE_ACTUAL = "actual"
DATE_TYPE_ANTICIPATED = "anticipated"
DATE_TYPE_ESTIMATED = "estimated"
DATE_TYPES = (DATE_TYPE_ACTUAL, DATE_TYPE_ANTICIPATED, DATE_TYPE_ESTIMATED)

# Types that describe a PLAN rather than an event. The temporal split must exclude these:
# "train on trials that completed before the boundary" is meaningless if the completion
# has not happened. Grouped rather than merged, because the two words are different
# registry vocabularies and collapsing them would lose which era a record came from.
PLANNED_DATE_TYPES = frozenset({DATE_TYPE_ANTICIPATED, DATE_TYPE_ESTIMATED})

DATE_TYPE_DOC = {
    DATE_TYPE_ACTUAL: "the event happened; safe for a temporal split",
    DATE_TYPE_ANTICIPATED: ("a stated plan, not an event; NEVER safe for a temporal "
                            "split. Older registry wording, absent from the current "
                            "server but retained for historical dumps"),
    DATE_TYPE_ESTIMATED: ("a stated plan, not an event; NEVER safe for a temporal split. "
                          "This is what the live server actually reports"),
}


def normalize_date_type(raw) -> Optional[str]:
    """AACT *_date_type -> 'actual' | 'anticipated' | None(unknown).

    None is not 'anticipated'. An unknown type on an old record is a gap in the registry,
    while 'anticipated' is a positive statement that the date has not happened yet, and a
    split that treated the two alike would either leak or discard needlessly.
    """
    s = _text(raw)
    if s is None:
        return None
    low = s.lower()
    for known in DATE_TYPES:
        if low.startswith(known):
            return known
    return low


def is_actual_date(raw) -> Optional[bool]:
    """True / False / None(unknown) for whether a date describes a completed event.

    Defined as "is 'actual'", not "is not planned", deliberately: an unrecognised type is
    not actual, so a vocabulary gap can never promote a plan into an event. That is what
    kept 'Estimated' harmless before it was a named constant.
    """
    norm = normalize_date_type(raw)
    if norm is None:
        return None
    return norm == DATE_TYPE_ACTUAL


def is_planned_date(raw) -> Optional[bool]:
    """True when the type explicitly says the date has not happened yet.

    NOT the negation of `is_actual_date`: an unrecognised or absent type is neither
    confirmed-actual nor confirmed-planned, so this returns None there. The temporal split
    needs the positive statement -- excluding merely-unknown types would discard 32% of
    start dates on this server.
    """
    norm = normalize_date_type(raw)
    if norm is None:
        return None
    if norm == DATE_TYPE_ACTUAL:
        return False
    if norm in PLANNED_DATE_TYPES:
        return True
    return None          # a type we do not recognise makes no claim either way


# ---- date plausibility ---------------------------------------------------
# The first full pull reported a primary-to-overall completion gap with a MAXIMUM of
# 31,777 days -- 87 years -- and 484 negative gaps. Those are registry errors and absurd
# anticipated dates, and an era binned on them is binned on noise.
#
# Bounds are parameters, not constants: the floor depends on how far back retrospective
# registration goes, and the future horizon depends on what counts as a credible plan.
# Both are exposed as CLI flags and printed, per the rule that a threshold producing a
# verdict must be visible.
DEFAULT_MIN_TRIAL_DATE = date(1900, 1, 1)
DEFAULT_MAX_FUTURE_YEARS = 20

# A nominal year, used only to turn a years-flag into a days bound. Exactness is
# irrelevant at a twenty-year horizon and leap-year handling would be false precision.
DAYS_PER_YEAR_NOMINAL = 365

DATE_OK = "ok"
DATE_UNPARSEABLE = "unparseable"
DATE_TOO_EARLY = "implausible_past"
DATE_TOO_LATE = "implausible_future"
DATE_QUALITIES = (DATE_OK, DATE_TOO_EARLY, DATE_TOO_LATE, DATE_UNPARSEABLE)

DATE_QUALITY_DOC = {
    DATE_OK: "parses and falls inside the plausible window",
    DATE_TOO_EARLY: "before the floor; a typo rather than a retrospective registration",
    DATE_TOO_LATE: "further ahead than any credible plan; a data-entry error",
    DATE_UNPARSEABLE: "absent or not a date at all",
}


def date_quality(raw, as_of: date, min_date: date = DEFAULT_MIN_TRIAL_DATE,
                 max_future_days: int = DEFAULT_MAX_FUTURE_YEARS * DAYS_PER_YEAR_NOMINAL
                 ) -> str:
    """Classify one date. `as_of` is INJECTED, never read from the clock.

    A function that called date.today() would give different answers on different days and
    could not be tested deterministically, which for something that decides what enters a
    training set is unacceptable.
    """
    d = parse_date(raw)
    if d is None:
        return DATE_UNPARSEABLE
    if d < min_date:
        return DATE_TOO_EARLY
    if (d - as_of).days > max_future_days:
        return DATE_TOO_LATE
    return DATE_OK


def date_quality_coverage(rows: Iterable[dict], fields: Iterable[str], as_of: date,
                          min_date: date = DEFAULT_MIN_TRIAL_DATE,
                          max_future_days: int = (DEFAULT_MAX_FUTURE_YEARS
                                                  * DAYS_PER_YEAR_NOMINAL)) -> dict:
    """-> {field: {quality: count}}, every quality present even at zero."""
    fields = list(fields)
    out = {f: {q: 0 for q in DATE_QUALITIES} for f in fields}
    for row in rows:
        for f in fields:
            out[f][date_quality(row.get(f), as_of, min_date, max_future_days)] += 1
    return out


# ---- is this a drug trial? ----------------------------------------------
# The first full pull found `phase` explicitly not-applicable for 51.1% of interventional
# trials: over half of ClinicalTrials.gov's interventional population is device,
# behavioural, surgical or dietary. Those trials have no mechanism, so they cannot carry
# the mechanism attribution the product promises.
#
# The scoping decision -- whether to train on them -- is DEFERRED. Three signals are
# carried instead, because they disagree and collapsing them is the judgment being
# deferred:
#   intervention_type   authoritative; what the trial actually administers
#   phase               a drug concept; 'NA' is strong evidence of a non-drug trial
#   is_fda_regulated_drug  sponsor's declaration, but mostly absent before 2017
#
# `is_drug_trial` is defined on the authoritative signal ALONE, so it has one stated
# meaning rather than being a blend. The other two travel beside it and the audit reports
# how often they agree.
DRUG_INTERVENTION_TYPES = frozenset({"drug", "biological"})

INTERVENTION_TYPE_SEP = "|"


def parse_intervention_types(raw) -> tuple:
    """Pipe-joined aggregate -> a tuple of normalised type names.

    A trial may administer several types at once (a drug AND a device), so this is a set
    rather than a single value, and any drug-like member makes it a drug trial.
    """
    s = _text(raw)
    if s is None:
        return ()
    parts = (p.strip().lower() for p in s.split(INTERVENTION_TYPE_SEP))
    return tuple(sorted({p for p in parts if p}))


def has_drug_intervention(raw) -> Optional[bool]:
    """True / False / None(unknown). None means no interventions were recorded at all.

    That third state is real and must not read as False: a trial with no intervention rows
    is an incomplete registration, not a declared non-drug trial.
    """
    types = parse_intervention_types(raw)
    if not types:
        return None
    return any(t in DRUG_INTERVENTION_TYPES for t in types)


DRUG_SIGNALS = ("is_drug_trial", "phase_is_drug_like", "is_fda_regulated_drug")

DRUG_SIGNAL_DOC = {
    "is_drug_trial": "intervention_type includes drug or biological -- authoritative",
    "phase_is_drug_like": "phase is stated rather than 'NA'; phases are a drug concept",
    "is_fda_regulated_drug": "sponsor declaration; mostly absent before the 2017 form",
}


def drug_trial_signals(row: dict) -> dict:
    """-> the three tri-state drug signals for one row. No verdict is blended."""
    return {
        "is_drug_trial": has_drug_intervention(row.get("intervention_types")),
        "phase_is_drug_like": (None if _text(row.get("phase")) is None
                               else not is_not_applicable(row.get("phase"))),
        "is_fda_regulated_drug": tribool(row.get("is_fda_regulated_drug")),
    }


def drug_signal_agreement(rows: Iterable[dict]) -> dict:
    """-> {signal: {'true'|'false'|'unknown': n}} plus pairwise agreement counts.

    The cross-tab that the scoping decision needs. If the authoritative signal and the
    phase proxy agree almost always, the proxy is usable where interventions are missing;
    if they diverge, the scoping question is harder than it looks and must be decided on
    the authoritative signal alone.
    """
    counts = {s: {"true": 0, "false": 0, UNKNOWN: 0} for s in DRUG_SIGNALS}
    pairs: dict = {}
    for i, a in enumerate(DRUG_SIGNALS):
        for b in DRUG_SIGNALS[i + 1:]:
            pairs[f"{a}__vs__{b}"] = {"agree": 0, "disagree": 0, "not_comparable": 0}
    for row in rows:
        sig = {s: row.get(s) for s in DRUG_SIGNALS}
        for s in DRUG_SIGNALS:
            v = sig[s]
            counts[s]["true" if v is True else "false" if v is False else UNKNOWN] += 1
        for key in pairs:
            a, b = key.split("__vs__")
            va, vb = sig[a], sig[b]
            if va is None or vb is None:
                pairs[key]["not_comparable"] += 1
            elif bool(va) == bool(vb):
                pairs[key]["agree"] += 1
            else:
                pairs[key]["disagree"] += 1
    return {"counts": counts, "pairs": pairs}


# ---- FDAAA component carriage --------------------------------------------
# Each entry: (output field name, AACT table, AACT column, kind, why it matters).
# `kind` drives parsing only: 'tribool' | 'text' | 'date'.
#
# NOTHING here is combined into a verdict. The audit reads coverage and the rule is
# written once, afterwards, with its inputs' coverage known.
FDAAA_COMPONENTS = (
    ("is_fda_regulated_drug", "studies", "is_fda_regulated_drug", "tribool",
     "sponsor's own declaration that a regulated drug is involved. Collected on the "
     "post-Final-Rule form, so expect it missing for older trials -- which is exactly "
     "why it is carried tri-state rather than defaulted."),
    ("is_fda_regulated_device", "studies", "is_fda_regulated_device", "tribool",
     "device counterpart of the above; a trial can be either or both."),
    ("is_us_export", "studies", "is_us_export", "tribool",
     "product exported from the US, one of the jurisdictional hooks."),
    ("has_us_facility", "calculated_values", "has_us_facility", "tribool",
     "AACT-derived flag for a US study location. The other jurisdictional hook, and the "
     "one that exists for old trials, since it comes from facility records rather than a "
     "sponsor declaration."),
    ("has_expanded_access", "studies", "has_expanded_access", "tribool",
     "expanded-access programmes are out of scope for an endpoint label; carried so they "
     "can be counted rather than assumed absent."),
    ("phase", "studies", "phase", "text",
     "phase-1-only drug trials fall outside the FDAAA results obligation, so phase is a "
     "component of applicability and not merely a feature."),
    ("primary_completion_date", "studies", "primary_completion_date", "date",
     "the date the posting deadline runs from. Determines WHICH era's rule applies, so it "
     "matters more than completion_date for applicability."),
)

# Output field -> the sentence explaining why it is carried. Printed by the audit so the
# reasoning travels with the numbers instead of living only here.
FDAAA_COMPONENT_DOC = {name: why for name, _t, _c, _k, why in FDAAA_COMPONENTS}

# Fields that are FDAAA components AND label inputs. `were_results_reported` is AACT's own
# results-posted flag, i.e. a synonym for the label input `results_posted`; it is pulled
# because the audit needs it as the OUTCOME of the posting-bias analysis, and it must
# never cross into a feature matrix. endpoint_label.LABEL_DERIVED_FIELDS is the registry.
POSTING_OUTCOME_FIELDS = ("were_results_reported",)


def components_for_row(row: dict) -> dict:
    """One joined AACT row -> the FDAAA component fields, parsed, tri-state preserved.

    Reads by AACT column name, writes the output field name. A column absent from the pull
    yields None (unknown) rather than a missing key, so every row has the same shape and
    "unknown" is representable in the output CSV.
    """
    out: dict = {}
    for name, _table, column, kind, _why in FDAAA_COMPONENTS:
        raw = row.get(column, None)
        if kind == "tribool":
            out[name] = tribool(raw)
        elif kind == "date":
            d = parse_date(raw)
            out[name] = d.isoformat() if d is not None else None
        else:
            out[name] = _text(raw)
    return out


UNKNOWN = "unknown"
NOT_APPLICABLE = "not_applicable"


def component_coverage(rows: Iterable[dict]) -> dict:
    """-> {output_field: {state: count}}.

    Tri-state fields report true / false / unknown.

    Text and date fields report present / not_applicable / unknown, because those are
    three different claims and the applicability rule will read them differently. A phase
    of 'NA' says no phase applies, which is evidence ABOUT the trial; an absent phase says
    nobody recorded one, which is evidence about the record. 'true'/'false' are omitted
    for these fields rather than reported as zero, since a zero would imply the question
    was asked and answered negatively.
    """
    kinds = {name: kind for name, _t, _c, kind, _w in FDAAA_COMPONENTS}
    out: dict = {}
    for name, kind in kinds.items():
        out[name] = ({"true": 0, "false": 0, UNKNOWN: 0} if kind == "tribool"
                     else {"present": 0, NOT_APPLICABLE: 0, UNKNOWN: 0})
    for row in rows:
        for name, kind in kinds.items():
            value = row.get(name, None)
            if kind == "tribool":
                if value is True:
                    out[name]["true"] += 1
                elif value is False:
                    out[name]["false"] += 1
                else:
                    out[name][UNKNOWN] += 1
            elif is_not_applicable(value):
                out[name][NOT_APPLICABLE] += 1
            elif _text(value) is not None:
                out[name]["present"] += 1
            else:
                out[name][UNKNOWN] += 1
    return out


def era_coverage(rows: Iterable[dict], date_field: str = "primary_completion_date") -> dict:
    """-> {era_name_or_unknown: count}, binned on `date_field`.

    Defaults to primary_completion_date because that is the date a posting deadline runs
    from. Every era key is present even at zero, so a missing era is visible as a zero
    rather than as an absent line.
    """
    out = {name: 0 for name, _s, _e in ERAS}
    out[UNKNOWN] = 0
    for row in rows:
        era = era_for_date(row.get(date_field))
        out[era if era is not None else UNKNOWN] += 1
    return out

# ---- MODALITY signals: carried beside is_drug_trial, never folded into it -------------
# 3,629 trials carry `genetic` or `combination_product` with NO `drug` or `biological`
# (71 of them headline-labelled). Under `is_drug_trial` they read False and drop out of the
# drug-only population entirely -- yet cell and gene therapies receive BLAs, and they are
# exactly the modality a 10-year market window cannot see.
#
# DRUG_INTERVENTION_TYPES is deliberately NOT widened to include them. `is_drug_trial` has
# one stated meaning and everything else rests on it; changing that definition to fix a
# 0.8% edge case would move the scoping decision, the agreement cross-tabs and the
# population count all at once. These signals travel alongside instead, so the 3,629 stay
# countable and re-scopable. Same carry-don't-blend discipline as the three drug signals.
ADVANCED_THERAPY_TYPES = frozenset({"genetic"})
COMBINATION_PRODUCT_TYPES = frozenset({"combination_product"})

MODALITY_SIGNALS = ("has_advanced_therapy", "has_combination_product",
                    "is_drug_like_modality")


def has_advanced_therapy(raw) -> Optional[bool]:
    """Does this trial administer a gene or cell therapy intervention?

    None when no intervention rows were recorded at all -- absent is not false. Tri-state
    by default, because collapsing never-collected into False manufactures findings.
    """
    types = parse_intervention_types(raw)
    if not types:
        return None
    return bool(set(types) & ADVANCED_THERAPY_TYPES)


def has_combination_product(raw) -> Optional[bool]:
    """Does this trial administer a combination product (drug-device, drug-biologic)?"""
    types = parse_intervention_types(raw)
    if not types:
        return None
    return bool(set(types) & COMBINATION_PRODUCT_TYPES)


def is_drug_like_modality(raw) -> Optional[bool]:
    """The WIDER reading: drug, biological, genetic or combination product.

    Offered as a named alternative scope, NOT as the definition. The market target has a
    live argument for using this reading (a gene therapy gets a BLA) while endpoint-met
    has a weaker one. Carrying both means that decision can be made on measured coverage
    instead of being baked into `is_drug_trial` now.
    """
    types = parse_intervention_types(raw)
    if not types:
        return None
    wider = DRUG_INTERVENTION_TYPES | ADVANCED_THERAPY_TYPES | COMBINATION_PRODUCT_TYPES
    return bool(set(types) & wider)


def modality_signals(row: dict) -> dict:
    """One studies row -> the modality signal columns. Reads `intervention_types` only."""
    raw = row.get("intervention_types")
    return {"has_advanced_therapy": has_advanced_therapy(raw),
            "has_combination_product": has_combination_product(raw),
            "is_drug_like_modality": is_drug_like_modality(raw)}


# ---- SPONSOR CLASS and RESPONSIBLE PARTY ---------------------------------------------
# §8.2 requires posting rate by sponsor class and no pulled column supported it. Two
# entities can bear the obligation and they do not always agree: convention (and most
# published posting-rate work) bins by LEAD SPONSOR, while the FDAAA obligation falls on
# the RESPONSIBLE PARTY. Both are carried so the disagreement is measurable rather than
# assumed away.
#
# The vocabularies are normalised but NOT validated against these sets: an unrecognised
# value passes through as itself, lowercased, so a registry vocabulary change shows up in
# the audit as its own bucket instead of disappearing into 'unknown'. That is lesson 17,
# and it is the reason the 'Estimated' / 'Anticipated' gap was visible within one run.
AGENCY_CLASSES = frozenset({"nih", "industry", "other", "fed", "other_gov", "indiv",
                            "network", "ambig", "unknown"})

RESPONSIBLE_PARTY_TYPES = frozenset({"sponsor", "principal investigator",
                                     "sponsor-investigator"})

SPONSOR_COLS = ("lead_sponsor_class", "collaborator_classes", "responsible_party_type",
                "lead_sponsor_class_known", "responsible_party_type_known")


def normalize_agency_class(raw) -> Optional[str]:
    """AACT agency_class -> a lowercased token, or None when absent.

    Passes unrecognised values through as themselves. AACT's 'N/A' is handled the same way
    `study_type` handles it -- locally, not by a global blank-token list -- because whether
    'N/A' means not-applicable or unknown is field-specific (lesson 7).
    """
    text = _text(raw)
    if text is None:
        return None
    token = text.strip().lower().replace(" ", "_")
    return token or None


def parse_agency_classes(raw) -> tuple:
    """Pipe-joined aggregate of agency classes -> a sorted tuple of tokens.

    A trial can have several collaborators of different classes, so this is a set. Empty
    tuple means the aggregate was absent, which for collaborators legitimately means "no
    collaborators" and for the lead means "not recorded" -- the two are distinguished by
    WHICH column is empty, not by this function.
    """
    text = _text(raw)
    if text is None:
        return ()
    parts = (normalize_agency_class(p) for p in text.split(INTERVENTION_TYPE_SEP))
    return tuple(sorted({p for p in parts if p}))


def normalize_responsible_party_type(raw) -> Optional[str]:
    """AACT responsible_party_type -> a lowercased token, or None when absent.

    Same passthrough discipline as `normalize_agency_class`: an unrecognised party type is
    a finding about the registry, not a value to discard.
    """
    text = _text(raw)
    if text is None:
        return None
    return text.strip().lower() or None


def is_known_agency_class(raw) -> Optional[bool]:
    """Is this value in the vocabulary we expected? None when absent.

    Carried as its own column so the audit can report vocabulary drift as a COUNT rather
    than requiring someone to eyeball the class distribution. False does not mean bad
    data; it means this run saw something the constant does not list.
    """
    token = normalize_agency_class(raw)
    if token is None:
        return None
    return token in AGENCY_CLASSES


def is_known_responsible_party_type(raw) -> Optional[bool]:
    token = normalize_responsible_party_type(raw)
    if token is None:
        return None
    return token in RESPONSIBLE_PARTY_TYPES


def sponsor_signals(row: dict) -> dict:
    """One studies row -> the sponsor and responsible-party columns.

    `lead_sponsor_class` is joined back to a single string because the lead is normally
    one entity; when a trial records several, all of them are kept pipe-joined rather than
    one being picked, so the multiplicity is visible in the audit.
    """
    lead = parse_agency_classes(row.get("lead_sponsor_class"))
    collaborators = parse_agency_classes(row.get("collaborator_classes"))
    return {
        "lead_sponsor_class": INTERVENTION_TYPE_SEP.join(lead) or None,
        "collaborator_classes": INTERVENTION_TYPE_SEP.join(collaborators) or None,
        "responsible_party_type":
            normalize_responsible_party_type(row.get("responsible_party_type")),
        "lead_sponsor_class_known":
            all(c in AGENCY_CLASSES for c in lead) if lead else None,
        "responsible_party_type_known":
            is_known_responsible_party_type(row.get("responsible_party_type")),
    }


def sponsor_agreement(rows) -> dict:
    """Cross-tab lead sponsor class against responsible party type. PAIRED, not marginal.

    Lesson 19: matching marginal totals do not mean two fields agree. `is_drug_trial` and
    `phase_is_drug_like` had totals within 1% and disagreed on 49,759 trials because the
    errors ran both ways and cancelled. The only way to see that is the paired cross-tab,
    so this returns the joint distribution rather than two summaries.
    """
    joint: dict = {}
    coverage = {"both": 0, "lead_only": 0, "party_only": 0, "neither": 0}
    for row in rows:
        lead = row.get("lead_sponsor_class") or None
        party = row.get("responsible_party_type") or None
        if lead and party:
            coverage["both"] += 1
        elif lead:
            coverage["lead_only"] += 1
        elif party:
            coverage["party_only"] += 1
        else:
            coverage["neither"] += 1
        key = (lead or UNKNOWN, party or UNKNOWN)
        joint[key] = joint.get(key, 0) + 1
    return {"joint": joint, "coverage": coverage}


# The name sources, declared once so `entity_coverage` and the union count cannot drift
# apart -- the union was hand-listed at first and would have silently stopped matching the
# per-source list as soon as a fourth source was added.
DRUG_NAME_FIELDS = ("intervention_names", "intervention_other_names",
                    "intervention_mesh_terms")
CONDITION_FIELDS = ("condition_mesh_terms", "condition_mesh_ancestors")
ENTITY_COVERAGE_FIELDS = DRUG_NAME_FIELDS + CONDITION_FIELDS


# The two MeSH fields whose INTERSECTION is the ceiling on code-to-code indication
# matching: a curated drug term on one side, a curated condition term on the other. Either
# alone overstates what the join can reach, which is why `both_mesh` is counted rather than
# left to be inferred from two percentages.
JOINT_MESH_FIELDS = ("intervention_mesh_terms", "condition_mesh_terms")


def entity_coverage(rows) -> dict:
    """Coverage of each drug-name and condition source, reported SEPARATELY.

    Three name sources are pulled rather than one, because picking one and discovering
    later that it was the weak source is the expensive mistake. Resolution rate has to be
    reported per source, so coverage is too: a source present on 90% of trials and a source
    present on 12% are not interchangeable inputs to the same match step.

    Counts trials with a NON-EMPTY aggregate. It says nothing about whether the value
    resolves to a drug -- that is §8.1c's job and needs DrugCentral.
    """
    fields = ENTITY_COVERAGE_FIELDS
    out = {"total": 0, "present": {f: 0 for f in fields}, "any_drug_name_source": 0,
           "both_mesh": 0,
           # The same four quantities restricted to trials where is_drug_trial is TRUE.
           # Overall coverage is nearly useless for the market target: the population is
           # half device and behavioural trials, so a percentage over all 460,569 answers
           # a question nobody asked. Scoping is drug-only, so the drug-only stratum is
           # the denominator every market figure has to use.
           "drug_total": 0, "drug_present": {f: 0 for f in fields},
           "drug_any_drug_name_source": 0, "drug_both_mesh": 0}
    for row in rows:
        is_drug = tribool(row.get("is_drug_trial")) is True
        out["total"] += 1
        if is_drug:
            out["drug_total"] += 1
        for field in fields:
            if _text(row.get(field)) is not None:
                out["present"][field] += 1
                if is_drug:
                    out["drug_present"][field] += 1
        if any(_text(row.get(f)) is not None for f in DRUG_NAME_FIELDS):
            out["any_drug_name_source"] += 1
            if is_drug:
                out["drug_any_drug_name_source"] += 1
        if all(_text(row.get(f)) is not None for f in JOINT_MESH_FIELDS):
            out["both_mesh"] += 1
            if is_drug:
                out["drug_both_mesh"] += 1
    return out


def merge_entity_coverage(left: dict, right: dict) -> dict:
    """Add two `entity_coverage` results. Lets the pull accumulate per CHUNK.

    The pull streams to bound memory and only keeps the small label records in RAM; the
    entity aggregates are free text and holding 460k of them would undo that. So coverage
    is tallied per chunk and merged, which needs addition to be defined somewhere pure
    rather than done inline in the script.

    Field sets must match. A silent union would let a chunk that happened to lack a column
    shrink the denominator for that column alone, which is the kind of per-column
    denominator difference that makes two percentages in the same table incomparable.
    """
    if set(left["present"]) != set(right["present"]):
        raise ValueError("entity coverage field sets differ: "
                         f"{sorted(left['present'])} vs {sorted(right['present'])}")
    merged = {}
    for key in ("total", "any_drug_name_source", "both_mesh",
                "drug_total", "drug_any_drug_name_source", "drug_both_mesh"):
        merged[key] = left[key] + right[key]
    for key in ("present", "drug_present"):
        merged[key] = {f: left[key][f] + right[key][f] for f in left[key]}
    return merged


def empty_entity_coverage() -> dict:
    """The additive identity for `merge_entity_coverage`.

    Explicit rather than relying on `entity_coverage([])`, so the accumulator starts from
    a value whose field set is fixed by the declaration and not by whatever the first
    chunk happened to contain.
    """
    return {"total": 0, "present": {f: 0 for f in ENTITY_COVERAGE_FIELDS},
            "any_drug_name_source": 0, "both_mesh": 0,
            "drug_total": 0, "drug_present": {f: 0 for f in ENTITY_COVERAGE_FIELDS},
            "drug_any_drug_name_source": 0, "drug_both_mesh": 0}