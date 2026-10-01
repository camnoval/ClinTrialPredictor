#!/usr/bin/env python3
"""How many labels are INVERTED by a bioequivalence analysis read as a superiority test?

A scratch probe, not a shipped rule. Self-contained on purpose: the predicate below is a
hypothesis about a defect, and promoting it into `services/` before the defect is confirmed
would be building a rule to describe a bug that might not exist.

THE HYPOTHESIS
==============
A bioequivalence study succeeds by CONTAINMENT: the interval around the test/reference
ratio has to sit inside roughly 0.80-1.25. `met_from_ci` asks the opposite question --
does the interval EXCLUDE the null -- and for a ratio the null resolves to 1.0. A
successful BE study's interval contains 1.0. So such a row is read as "did not meet".

That is not a refusal, it is an inversion: tier C, a verdict, confidently backwards, and
nothing downstream can detect it.

Three things have to fail together for a trial to reach that state:

  1. `non_inferiority_type` is NOT set to the NI/equivalence enum. When it is set,
     `analysis_design` returns DESIGN_NI, the row refuses as REFUSAL_NI_DESIGN and lands
     in tier E with no verdict. That is the machinery working correctly.
  2. The interval is UNIT-scaled. A percent-scaled one (90-125) is caught by
     `is_percent_scaled_ratio` and refuses.
  3. The endpoint TITLE does not match the bioequivalence pattern, so the endpoint-type
     gate does not refuse the trial either.

Condition 3 is the interesting one. The gate reads REGISTERED TEXT and the analysis shape
lives in the POSTED STATISTICS -- two different fields that need not agree -- so the gate
was only ever catching the clearly-worded subset. This probe counts the rest.

WHAT A CONTAINMENT WINDOW CANNOT DO
===================================
The 0.80-1.25 window is not in AACT as a structured field. Matching an interval against it
is a GUESS that a BE rule was in force, and a narrow difference interval on a percentage
outcome can sit inside 0.80-1.25 by coincidence. So this probe over-counts by construction
and the window is a flag, printed, not a constant. Read the sensitivity table in section 3
before believing any single figure: if the count moves sharply with the window, the count
is measuring the window rather than the defect.
"""
from __future__ import annotations

import argparse
import csv
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from trial_pos.services.endpoint_label import (  # noqa: E402
    CONTRAST_RATIO, DEFAULT_ALPHA, DEFAULT_COVERAGE_TOLERANCE_POINTS,
    DEFAULT_RATIO_SCALE_FLOOR, DESIGN_NI, HEADLINE_TIERS, TIER_C, analysis_design,
    classify_analysis, contrast_family, is_percent_scaled_ratio,
)
from trial_pos.services.endpoint_type import (  # noqa: E402
    CLASS_BIOEQUIVALENCE, classify_title, matched_classes,
)
from trial_pos.services.sampling import phase_group  # noqa: E402

# The conventional bioequivalence acceptance window. A FLAG, not a constant, because it is
# not a field in the dump and matching against it is an inference.
DEFAULT_BE_LOW = 0.80
DEFAULT_BE_HIGH = 1.25

# Windows for the sensitivity table. If the count moves sharply across these, the count is
# a property of the window rather than of the data.
SENSITIVITY_WINDOWS = ((0.80, 1.25), (0.70, 1.43), (0.90, 1.11), (0.50, 2.00))

RATIO_NULL = 1.0


def pct(value, spec: str = ".1f") -> str:
    return "undefined" if value is None else format(100.0 * value, spec) + "%"


def share(part: int, whole: int):
    """None rather than 0.0 when there is no denominator."""
    return None if not whole else part / whole


def as_float(raw):
    try:
        return float(str(raw).strip())
    except (TypeError, ValueError):
        return None


def analysis_view(row: dict) -> dict:
    """A rename. Same mapping as validate_interval_rule.py."""
    return {
        "non_inferiority_type": row.get("analysis_non_inferiority_type"),
        "param_type": row.get("analysis_param_type"),
        "p_value": row.get("analysis_p_value"),
        "p_value_modifier": row.get("analysis_p_value_modifier"),
        "ci_lower_limit": row.get("analysis_ci_lower_limit"),
        "ci_upper_limit": row.get("analysis_ci_upper_limit"),
        "ci_percent": row.get("analysis_ci_percent"),
    }


def be_shaped(view: dict, floor: float, low: float, high: float) -> bool:
    """Does this analysis row look like a bioequivalence test read as a superiority one?

    Deliberately does NOT check the tier. Whether the row got a verdict is a separate
    question, asked by the caller, so that "BE-shaped and refused" can be counted next to
    "BE-shaped and labelled" rather than one hiding the other.
    """
    if analysis_design(view.get("non_inferiority_type")) == DESIGN_NI:
        return False                      # already refuses correctly, tier E
    if contrast_family(view.get("param_type")) != CONTRAST_RATIO:
        return False
    lower = as_float(view.get("ci_lower_limit"))
    upper = as_float(view.get("ci_upper_limit"))
    if lower is None or upper is None:
        return False
    if lower > upper:
        lower, upper = upper, lower
    if is_percent_scaled_ratio(view.get("param_type"), view.get("ci_lower_limit"),
                               view.get("ci_upper_limit"), floor):
        return False                      # caught by the scale refusal
    # The inversion needs the interval to CONTAIN the null -- that is what makes the
    # exclusion test answer "did not meet" -- and to sit inside the acceptance window,
    # which is what makes a BE reading plausible at all.
    return lower <= RATIO_NULL <= upper and low <= lower and upper <= high


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--outcomes", default="data/aact/results_raw_outcomes.csv", type=Path)
    ap.add_argument("--labels", default="data/aact/trial_labels.csv", type=Path)
    ap.add_argument("--alpha", type=float, default=DEFAULT_ALPHA)
    ap.add_argument("--ratio-scale-floor", type=float, default=DEFAULT_RATIO_SCALE_FLOOR)
    ap.add_argument("--coverage-tolerance", type=float,
                    default=DEFAULT_COVERAGE_TOLERANCE_POINTS)
    ap.add_argument("--be-low", type=float, default=DEFAULT_BE_LOW)
    ap.add_argument("--be-high", type=float, default=DEFAULT_BE_HIGH)
    ap.add_argument("--examples", type=int, default=10)
    args = ap.parse_args()

    print("=" * 78)
    print("BIOEQUIVALENCE INVERSION PROBE -- scratch diagnostic")
    print("=" * 78)
    print(f"  alpha {args.alpha}   ratio scale floor {args.ratio_scale_floor}   "
          f"coverage tolerance {args.coverage_tolerance}")
    print(f"  acceptance window    {args.be_low} to {args.be_high}   <- AN INFERENCE, "
          f"not a field in the dump")
    print("\n  This counts LABELS THAT MAY BE BACKWARDS, not labels that are missing.")
    print("  A refusal is honest; an inverted verdict is not, and nothing downstream")
    print("  can detect one. Over-counts by construction -- see section 3.")

    for path in (args.labels, args.outcomes):
        if not path.exists():
            raise SystemExit(f"!! {path} not found.")

    trials = {}
    with args.labels.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            if (row.get("is_drug_trial") or "").strip().lower() in ("true", "1", "t"):
                trials[row["nct_id"]] = {
                    "tier_min": (row.get("tier_min") or "").strip(),
                    "phase_group": phase_group(row.get("phase")),
                }

    titles: dict = {}
    rows: dict = defaultdict(list)
    with args.outcomes.open(newline="", encoding="utf-8") as handle:
        for raw in csv.DictReader(handle):
            if (raw.get("outcome_outcome_type") or "").strip().lower() != "primary":
                continue
            nct = (raw.get("outcome_nct_id") or "").strip()
            if nct not in trials or not (raw.get("analysis_id") or "").strip():
                continue
            key = (nct, (raw.get("outcome_id") or "").strip())
            titles[key] = raw.get("outcome_title")
            rows[key].append(analysis_view(raw))

    # ---- 1. the population ------------------------------------------------
    counts = Counter()
    hits: dict = defaultdict(list)
    by_class = Counter()
    by_phase = Counter()
    gate_would_catch = Counter()
    for key, views in rows.items():
        title = titles[key]
        title_is_be = CLASS_BIOEQUIVALENCE in matched_classes(title)
        for view in views:
            counts["analysis_rows"] += 1
            if not be_shaped(view, args.ratio_scale_floor, args.be_low, args.be_high):
                continue
            counts["be_shaped"] += 1
            tier, met, _ = classify_analysis(view, args.alpha, args.ratio_scale_floor,
                                             args.coverage_tolerance)
            if met is None:
                counts["be_shaped_but_refused_anyway"] += 1
                continue
            counts["be_shaped_with_a_verdict"] += 1
            if tier == TIER_C:
                counts["verdict_came_from_the_interval"] += 1
            elif tier in HEADLINE_TIERS:
                counts["verdict_came_from_a_p_value"] += 1
            if title_is_be:
                gate_would_catch["gate_refuses_this_trial_today"] += 1
                continue
            counts["INVERSION_CANDIDATE"] += 1
            if tier == TIER_C:
                counts["inversion_candidate_tier_c"] += 1
                hits[classify_title(title)].append((key[0], title, view))
            by_class[classify_title(title)] += 1
            by_phase[trials[key[0]]["phase_group"]] += 1

    print("\n" + "-" * 78)
    print("1. THE FUNNEL")
    print("-" * 78)
    order = ("analysis_rows", "be_shaped", "be_shaped_but_refused_anyway",
             "be_shaped_with_a_verdict", "verdict_came_from_the_interval",
             "verdict_came_from_a_p_value", "INVERSION_CANDIDATE",
             "inversion_candidate_tier_c")
    for key in order:
        print(f"  {key:<38}{counts[key]:>10,}")
    for key, value in gate_would_catch.items():
        print(f"  {key:<38}{value:>10,}   <- the gate's BE class earns its keep here")
    print(f"\n  tier C is the inverting tier: its verdict came FROM the interval, so a")
    print("  containment rule read as exclusion flips it. A tier A/B row got its")
    print("  verdict from a posted p-value and is not inverted by this mechanism,")
    print("  which is why the two are counted separately rather than summed.")

    # ---- 2. where they sit -------------------------------------------------
    print("\n" + "-" * 78)
    print("2. WHERE THE CANDIDATES SIT")
    print("-" * 78)
    print("  by the class the endpoint-type rule assigned to the TITLE:")
    for endpoint_class, n in by_class.most_common():
        print(f"    {endpoint_class:<24}{n:>8,}")
    print("\n  by phase group:")
    for group, n in by_phase.most_common():
        print(f"    {group:<24}{n:>8,}")

    if args.examples and hits:
        print(f"\n  up to {args.examples} tier-C candidates per class, for reading:")
        for endpoint_class, found in sorted(hits.items(), key=lambda kv: -len(kv[1])):
            print(f"    [{endpoint_class}]")
            for nct, title, view in found[:args.examples]:
                print(f"      {nct}  {view.get('param_type')}  "
                      f"[{view.get('ci_lower_limit')}, {view.get('ci_upper_limit')}]")
                print(f"        {str(title)[:80]}")

    # ---- 3. sensitivity ----------------------------------------------------
    print("\n" + "-" * 78)
    print("3. SENSITIVITY TO THE WINDOW -- read this before believing section 1")
    print("-" * 78)
    print(f"  {'window':<18}{'be_shaped':>12}{'with verdict':>14}{'tier C cands':>14}")
    for low, high in SENSITIVITY_WINDOWS:
        shaped = verdicts = tier_c = 0
        for key, views in rows.items():
            title_is_be = CLASS_BIOEQUIVALENCE in matched_classes(titles[key])
            for view in views:
                if not be_shaped(view, args.ratio_scale_floor, low, high):
                    continue
                shaped += 1
                tier, met, _ = classify_analysis(view, args.alpha,
                                                 args.ratio_scale_floor,
                                                 args.coverage_tolerance)
                if met is None:
                    continue
                verdicts += 1
                if tier == TIER_C and not title_is_be:
                    tier_c += 1
        marker = "  <- default" if (low, high) == (args.be_low, args.be_high) else ""
        print(f"  {f'{low} - {high}':<18}{shaped:>12,}{verdicts:>14,}"
              f"{tier_c:>14,}{marker}")
    print("\n  A count that roughly tracks the window's WIDTH is measuring the window.")
    print("  A count that plateaus is measuring a real cluster of BE-shaped rows.")
    print("  The widest window here is not a BE window at all -- any ratio interval")
    print("  straddling 1.0 fits it -- so its column is the ceiling, not a finding.")
    return 0


if __name__ == "__main__":
    sys.exit(main())