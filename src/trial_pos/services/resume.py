"""Pure logic for resuming an interrupted pull safely.

WHY THIS EXISTS
===============
The full-population pull takes 15-30 minutes against a shared server, and it was
interrupted twice by the editor's conda auto-activation sending SIGINT to the foreground
process. Losing 20 minutes of work to an external event is worth a resume path.

WHY IT IS MOSTLY GUARDS
=======================
Resume is a correctness hazard, not a convenience feature. Appending to a file written by
a run that used DIFFERENT settings produces one CSV containing two definitions, silently
-- half the rows labelled at alpha=0.05 and half at 0.01, or half with the era fallback on
and half off. Nothing downstream could detect that, and the file would look perfect.

So the rule here is: resume only when the new run is configured EXACTLY as the old one,
and refuse loudly otherwise. The manifest is what makes that checkable. An operator who
really wants to mix settings has to say so explicitly, and the mixed file is then their
stated intent rather than an accident.

Everything in this module is pure. Reading and writing the manifest is the script's job.
"""
from __future__ import annotations

from typing import Iterable, Optional

# Settings that change what a row MEANS. A resume across any difference in these would
# produce a file whose rows are not comparable to each other.
#
# `population` and `schema` are here because they change WHICH rows exist; alpha,
# broad_includes_safety and era_fallback because they change how a row is labelled or
# binned. `max_trials` is deliberately absent -- it caps the id list rather than altering
# any row's meaning, and a capped run followed by an uncapped resume is a coherent thing
# to want.
MANIFEST_FIELDS = (
    "population",
    "schema",
    "alpha",
    "broad_includes_safety",
    "era_fallback",
    # Added after rev 7 section 0.1 bug 3. All three PRODUCE VERDICTS, so a
    # resume that ignored them would append rows labelled under different
    # decision rules to rows already in the file -- the exact failure the
    # manifest exists to prevent, reached by omission rather than disagreement.
    "ratio_scale_floor",
    "coverage_tolerance",
    "as_of",
)

MANIFEST_FIELD_DOC = {
    "population": "which trials are in scope; a different scope means different rows",
    "schema": "the AACT schema queried",
    "alpha": "the p-value threshold that decides met/not-met",
    "broad_includes_safety": "whether safety terminations enter the broad label as 0",
    "era_fallback": "whether the era may be binned on completion_date",
    "ratio_scale_floor": ("the bound above which a ratio interval is read as "
                          "percent-scaled and REFUSED rather than tested against 1"),
    "coverage_tolerance": ("how far a posted ci_percent may sit from the required "
                          "coverage and still support an interval verdict"),
    "as_of": ("the date the run treated as today. Moves the date-plausibility "
              "horizon, so two runs at different as-of dates can disagree about "
              "which rows are admissible at all"),
}

# Fields added to MANIFEST_FIELDS after manifests were already being written. A
# manifest saved before they existed carries no value for them, so
# `manifest_conflicts` reports a conflict with None on the saved side. That is correct
# and is NOT a false alarm: the run that produced those rows really did not record the
# setting, so whether it matched is genuinely unknown, and section 0.1 bug 3 is the
# observation that such a run could have used any value. Named so a script can say WHY
# an existing file suddenly conflicts, instead of the operator reaching for
# --force-resume to make the message go away.
MANIFEST_FIELDS_ADDED_LATER = (
    "ratio_scale_floor",
    "coverage_tolerance",
    "as_of",
)


def build_manifest(settings: dict) -> dict:
    """-> the subset of settings that must match for a resume to be sound.

    Raises on a missing field rather than defaulting: a manifest with a silently
    defaulted value would compare equal to a run that set it explicitly, which defeats
    the entire point of the comparison.
    """
    missing = [f for f in MANIFEST_FIELDS if f not in settings]
    if missing:
        raise KeyError(f"manifest is missing required settings: {missing}")
    return {f: settings[f] for f in MANIFEST_FIELDS}


def manifest_conflicts(saved: Optional[dict], current: dict) -> list:
    """-> [(field, saved_value, current_value)] for every field that differs.

    A missing saved manifest is reported as a conflict on every field, with None for the
    saved side. That is deliberate: "we do not know how the existing rows were made" is
    strictly worse than a known mismatch and must not read as agreement.
    """
    if saved is None:
        return [(f, None, current.get(f)) for f in MANIFEST_FIELDS]
    out = []
    for field in MANIFEST_FIELDS:
        was, now = saved.get(field, None), current.get(field, None)
        if not _same(was, now):
            out.append((field, was, now))
    return out


def _same(a, b) -> bool:
    """Equality that survives a JSON round-trip.

    JSON turns a float into a float and a bool into a bool, but a manifest written by an
    older version may hold "0.05" as a string, and 0.05 == "0.05" is False in Python. A
    spurious conflict would block a legitimate resume, so numeric strings are compared
    numerically. Booleans are compared by identity of truthiness AFTER a type check, so
    that 1 and True do not silently agree when one came from a checkbox and the other
    from a count.
    """
    if isinstance(a, bool) or isinstance(b, bool):
        return isinstance(a, bool) and isinstance(b, bool) and a == b
    if a is None or b is None:
        return a is None and b is None
    try:
        return float(a) == float(b)
    except (TypeError, ValueError):
        return str(a) == str(b)


def ids_remaining(all_ids: Iterable[str], done_ids: Iterable[str]) -> list:
    """-> ids from `all_ids` not present in `done_ids`, ORDER PRESERVED.

    Order matters: the pull walks ids in nct_id order and the operator reads chunk
    numbers against that. Returning a set would make the resumed run's progress output
    incomparable to the original's.

    Comparison is case-normalised because nct_ids are upper-cased on the way in, and a
    manifest or CSV hand-edited to lowercase should not cause 400,000 trials to be
    re-pulled.
    """
    done = {str(i).strip().upper() for i in done_ids if str(i).strip()}
    return [i for i in all_ids if str(i).strip().upper() not in done]


def header_problems(existing: Optional[list], expected: list) -> list:
    """-> human-readable reasons the existing CSV cannot be appended to. Empty = safe.

    Appending rows with a different column order to a CSV is silent data corruption: the
    values land under the wrong headers and every downstream read is wrong in a way no
    schema check would catch. So the check is exact ORDER, not set membership.
    """
    if existing is None:
        return ["the existing file has no header row"]
    if existing == expected:
        return []
    problems = []
    missing = [c for c in expected if c not in existing]
    extra = [c for c in existing if c not in expected]
    if missing:
        problems.append(f"columns expected but absent: {', '.join(missing)}")
    if extra:
        problems.append(f"columns present but not expected: {', '.join(extra)}")
    if not problems:
        problems.append(
            "same columns in a DIFFERENT ORDER; appending would put values under the "
            "wrong headers"
        )
    return problems


def describe_conflicts(conflicts: list) -> list:
    """-> one explanatory line per conflict, for the script to print.

    The reason each field matters lives next to the field list, so the operator sees why
    a resume is being refused rather than just that it was.
    """
    lines = []
    for field, was, now in conflicts:
        why = MANIFEST_FIELD_DOC.get(field, "")
        was_txt = "unknown (no manifest)" if was is None else repr(was)
        lines.append(f"{field}: existing={was_txt} current={now!r} -- {why}")
    return lines