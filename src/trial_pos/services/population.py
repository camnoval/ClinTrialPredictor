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