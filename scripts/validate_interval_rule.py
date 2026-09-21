#!/usr/bin/env python3
"""Validate the tier-C interval rule against the sponsors' own posted p-values. OFFLINE.

WHAT THIS ANSWERS
=================
Tier C labels a trial from a confidence interval when no p-value is posted: met := the
interval excludes the null. That rule was never measured. It can be, exactly, without
re-querying AACT.

Analysis rows carrying BOTH a decidable p-value AND a full interval are a LABELLED
VALIDATION SET for it. The p-value is the sponsor's own verdict on the same comparison,
and the label engine discards those intervals anyway under p-value precedence, so using
them here leaks nothing.

The rule turned out to be wrong in three independent ways, each found by a stratification
this script now performs:
  1. RATIO SCALE     -- a percent-scaled ratio interval cannot contain 1, so the rule
                        answers "met" on 100% of such rows. kappa 0.000.
  2. CI COVERAGE     -- `ci_percent` was never read. An interval only tests at alpha when
                        its coverage is 100*(1-alpha); off-coverage intervals err, and the
                        DIRECTION of the error flips with the band.
  3. ANALYSIS DESIGN -- non-inferiority and equivalence trials succeed against a MARGIN,
                        not against no effect, so "excludes the null" is the wrong
                        question and under-calls them heavily.

WHY --alpha IS NOT A FREE PARAMETER
===================================
An earlier version of this script let `--alpha` move only the REFERENCE side. The
candidate side tests whether the interval excludes the null, and the interval's coverage
is whatever the sponsor posted -- so at alpha 0.01 it compared a 99%-strength verdict
against a 95% interval and reported one degraded number that looked like fragility in the
rule. It was an artefact of the flag.

`--alpha` now moves the REQUIRED COVERAGE with it, and section 4 prints the full
alpha x coverage grid so the relation is visible rather than asserted. Agreement should
peak where coverage == 100*(1-alpha); where it does not, the cell is usually thin and the
n is printed beside it.

WHAT THIS CANNOT ANSWER
=======================
The validation rows POSTED a p-value; production tier-C rows did not. So this measures an
ADJACENT population, not the labelled one -- lesson 16's hazard, a diagnostic whose
denominator excludes part of what it diagnoses. Bioequivalence analyses in particular
often post an interval with no p-value and are under-represented here. A favourable number
in any stratum is a ceiling, never a clean estimate.

No derivations live in this file. Everything is in `trial_pos.services.endpoint_label`
and `trial_pos.services.agreement`.
"""
from __future__ import annotations

import argparse
import csv
import sys
from collections import Counter, defaultdict
from pathlib import Path

# Scripts self-bootstrap src/ because run_checks.py sets PYTHONPATH itself, so the gate
# can be green while a hand-run script fails on import (lesson 9).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from trial_pos.services.agreement import (  # noqa: E402
    KAPPA_DOC, KAPPA_UNINFORMATIVE_MAX, summarize,
)
from trial_pos.services.endpoint_label import (  # noqa: E402
    COVERAGE_LOOSER, COVERAGE_MATCHED, COVERAGE_TIGHTER, COVERAGE_UNKNOWN,
    CONTRAST_DIFFERENCE, CONTRAST_RATIO, DEFAULT_ALPHA,
    DEFAULT_COVERAGE_TOLERANCE_POINTS, DEFAULT_RATIO_SCALE_FLOOR, DESIGN_NI,
    HEADLINE_TIERS, RATIO_SCALE_PERCENT, RATIO_SCALE_UNIT, REFUSAL_KINDS,
    TIER_C, TIER_E, analysis_design, analysis_design_detail, contrast_family,
    coverage_band, coverage_supports, is_percent_scaled_ratio, met_from_ci, met_from_p,
    normalize_ci_percent, null_value_for, parse_p_value, ratio_scale, refusal_kind,
    required_ci_percent,
)

# AACT's outcome_type value for a primary outcome, lowercased.
PRIMARY_OUTCOME_TYPE = "primary"

# Alpha values for the grid in section 4. Chosen to bracket the conventional 0.05 on both
# sides so the peak is visible rather than sitting at an edge.
GRID_ALPHAS = (0.01, 0.025, 0.05, 0.10, 0.20)

# Coverage levels for the same grid, as percentage points. These are the four AACT
# actually carries in quantity; everything else is a long tail of 100-plus values.
GRID_COVERAGES = (99.0, 97.5, 95.0, 90.0, 80.0)

# A stratum below this many rows is printed with its n but should not be read as
# evidence, and the one-directional warning is suppressed for it. Named because it
# produces a reading, even if not a verdict: on a 21-row stratum two same-direction
# disagreements trip a "DISQUALIFYING" banner that means nothing, and a warning that
# cries wolf is a warning that gets ignored.
MIN_STRATUM_FOR_VERDICT = 40

# Free-text analysis descriptions can exceed the default CSV field limit.
csv.field_size_limit(10 ** 9)


def _rule(title: str) -> None:
    print()
    print("=" * 78)
    print(title)
    print("=" * 78)


def _pct(n: int, d: int) -> str:
    return "n/a" if not d else f"{100.0 * n / d:5.1f}%"


def _fmt(value, spec: str = ".3f") -> str:
    """None prints as 'undefined', never 0 or nan: the distinction is the point here."""
    return "undefined" if value is None else format(value, spec)


def read_rows(path: Path):
    """Stream the raw joined outcomes/analyses dump.

    Uses the csv module rather than pandas deliberately: pandas' default na_values
    contains "NA" and turns empty cells into float NaN, which is truthy. Both have already
    produced silent disagreements between the live pull and the offline re-derive in this
    project (lessons 5 and 6).
    """
    required = ("outcome_outcome_type", "analysis_param_type", "analysis_p_value",
                "analysis_ci_lower_limit", "analysis_ci_upper_limit",
                "analysis_ci_percent", "analysis_non_inferiority_type")
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        missing = [c for c in required if c not in (reader.fieldnames or [])]
        if missing:
            raise SystemExit(f"!! {path} is missing expected columns: {missing}\n"
                             f"   columns present: {reader.fieldnames}")
        for row in reader:
            yield row


def analysis_view(row: dict) -> dict:
    """Raw dump row -> the analysis fields under the names the engine expects.

    A rename, not a derivation: the engine reads `param_type`, the dump writes
    `analysis_param_type`.
    """
    return {
        "nct_id": row.get("analysis_nct_id") or row.get("outcome_nct_id"),
        "non_inferiority_type": row.get("analysis_non_inferiority_type"),
        "non_inferiority_description": row.get("analysis_non_inferiority_description"),
        "param_type": row.get("analysis_param_type"),
        "p_value": row.get("analysis_p_value"),
        "p_value_modifier": row.get("analysis_p_value_modifier"),
        "ci_lower_limit": row.get("analysis_ci_lower_limit"),
        "ci_upper_limit": row.get("analysis_ci_upper_limit"),
        "ci_percent": row.get("analysis_ci_percent"),
    }


def collect(path: Path, alpha: float, floor: float, tolerance: float) -> dict:
    """Walk the dump once. Counting and bucketing only; no verdict logic lives here."""
    coverage = Counter()
    by_design: dict = defaultdict(list)
    by_detail: dict = defaultdict(list)
    by_band: dict = defaultdict(list)
    by_scale: dict = defaultdict(list)
    grid: dict = defaultdict(list)
    refused_trials: dict = defaultdict(set)
    refused_param_types: dict = defaultdict(Counter)
    ci_vocab = Counter()

    for raw in read_rows(path):
        if (raw.get("outcome_outcome_type") or "").strip().lower() != PRIMARY_OUTCOME_TYPE:
            continue
        coverage["primary_analysis_rows"] += 1
        row = analysis_view(raw)
        family = contrast_family(row["param_type"])
        ci_vocab[(row["ci_percent"] or "").strip() or "<blank>"] += 1

        # PRODUCTION side: which rows a refusal withholds, and which kind. Independent of
        # whether the row carries a p-value, so this is the real impact tally.
        kind = refusal_kind(row, alpha, floor, tolerance)
        if kind is not None:
            coverage[f"refused_{kind}"] += 1
            refused_param_types[kind][(row["param_type"] or "").strip()] += 1
            if row["nct_id"]:
                refused_trials[kind].add(row["nct_id"])

        # VALIDATION side: needs both a decidable p-value and a readable interval.
        operator, value = parse_p_value(row["p_value"], row["p_value_modifier"])
        reference, _ = met_from_p(operator, value, alpha)
        if reference is None:
            coverage["no_decidable_p_value"] += 1
            continue
        if family is None:
            coverage["p_value_but_no_contrast_named"] += 1
            continue
        candidate, _ = met_from_ci(row["ci_lower_limit"], row["ci_upper_limit"],
                                   null_value_for(row["param_type"]))
        if candidate is None:
            coverage["p_value_but_no_readable_interval"] += 1
            continue
        coverage["validation_rows"] += 1

        # The candidate verdict is computed the way tier C computed it BEFORE the fixes,
        # from the name-only null. The audit exists to show where that fails, so it must
        # not be pre-corrected by the rules being validated.
        design = analysis_design(row["non_inferiority_type"])
        pair = (reference, candidate)
        by_design[design].append(pair)
        by_detail[analysis_design_detail(row["non_inferiority_type"])].append(pair)

        if design == DESIGN_NI:
            continue        # the NI stratum is reported by design; it has its own section
        if is_percent_scaled_ratio(row["param_type"], row["ci_lower_limit"],
                                   row["ci_upper_limit"], floor):
            by_scale[(CONTRAST_RATIO, RATIO_SCALE_PERCENT)].append(pair)
            continue
        if family == CONTRAST_RATIO:
            by_scale[(CONTRAST_RATIO, RATIO_SCALE_UNIT)].append(pair)
        else:
            by_scale[(CONTRAST_DIFFERENCE, "-")].append(pair)

        band = coverage_band(row["ci_percent"], alpha, tolerance)
        by_band[band].append(pair)
        by_band["RETAINED" if coverage_supports(band, candidate)
                else "REFUSED"].append(pair)

        # The grid re-reads both sides at each alpha, which is the only way the flag can
        # be honest: the reference verdict AND the required coverage move together.
        posted = normalize_ci_percent(row["ci_percent"])
        if posted is not None:
            for grid_alpha in GRID_ALPHAS:
                grid_reference, _ = met_from_p(operator, value, grid_alpha)
                if grid_reference is None:
                    continue
                for grid_coverage in GRID_COVERAGES:
                    if abs(posted - grid_coverage) <= tolerance:
                        grid[(grid_alpha, grid_coverage)].append(
                            (grid_reference, candidate))

    return {"coverage": coverage, "by_design": by_design, "by_detail": by_detail,
            "by_band": by_band, "by_scale": by_scale, "grid": grid,
            "refused_trials": refused_trials,
            "refused_param_types": refused_param_types, "ci_vocab": ci_vocab}


# Strata where one-directional disagreement is the PREDICTED signal rather than a fault.
# In the off-matched coverage bands the whole point of the one-way implication rule is
# that the errors run one way, so a "disqualifying" banner there is not just noise, it
# inverts the finding. It stays live for the scale and design strata, where one-sidedness
# means the rule is a constant or is answering a different question.
EXPECTED_ONE_SIDED = (COVERAGE_TIGHTER, COVERAGE_LOOSER)


def report_stratum(label: str, pairs: list, expect_one_sided: bool = False) -> dict:
    out = summarize(pairs)
    cells = out["cells"]
    print(f"\n  {label}")
    print(f"    n                         : {out['n']}")
    print(f"    raw agreement             : {_fmt(out['raw_agreement'], '.1%')}")
    print(f"    chance agreement          : {_fmt(out['chance_agreement'], '.1%')}")
    print(f"    Cohen's kappa             : {_fmt(out['kappa'])}")
    print(f"    2x2 (sponsor x interval)  : "
          f"[not-met,not-met]={cells[(0, 0)]}  [not-met,MET]={cells[(0, 1)]}  "
          f"[MET,not-met]={cells[(1, 0)]}  [MET,MET]={cells[(1, 1)]}")
    print(f"    interval rule says 'met'  : "
          f"{_fmt(out['candidate_positive_rate'], '.1%')} of rows")
    print(f"    sponsor says 'met'        : "
          f"{_fmt(out['reference_positive_rate'], '.1%')} of rows")
    over, under = cells[(0, 1)], cells[(1, 0)]
    if over or under:
        print(f"    error direction           : over-calls {over}, under-calls {under}"
              f"  (ratio {over / under:.2f})" if under else
              f"    error direction           : over-calls {over}, under-calls {under}")
    one_sided = out["one_sided_disagreement"]
    if out["n"] < MIN_STRATUM_FOR_VERDICT:
        print(f"    (n below {MIN_STRATUM_FOR_VERDICT}: reported, not evidence)")
    elif one_sided is True and expect_one_sided:
        print("    one-directional, AS PREDICTED for this band: that asymmetry is what")
        print("    the one-way implication rule relies on, not a fault in it.")
    elif one_sided is True:
        print("    !! DISAGREEMENT IS ONE-DIRECTIONAL -- the rule is biased, not noisy.")
        print("       Disqualifying at any level of raw agreement.")
    kappa = out["kappa"]
    if (kappa is not None and kappa <= KAPPA_UNINFORMATIVE_MAX
            and out["n"] >= MIN_STRATUM_FOR_VERDICT):
        print(f"    !! kappa <= {KAPPA_UNINFORMATIVE_MAX} -- no usable information here.")
    return out


def report_grid(grid: dict, alpha: float, tolerance: float) -> None:
    print("  Agreement should peak where coverage == 100*(1-alpha). This is the check")
    print("  that makes --alpha meaningful: both the reference verdict AND the required")
    print("  coverage move with it. Cells below the minimum row count are marked thin.")
    print()
    print("  READ THIS GRID WITH ITS n. The candidate side uses whatever interval the")
    print("  sponsor posted, and 95% coverage dominates the dump -- every other column")
    print("  is one to two orders of magnitude thinner. So the relation is genuinely")
    print("  TESTABLE only on the ci=95 column, where alpha 0.05 should win and does.")
    print("  Off-diagonal peaks in thin columns are not evidence against the relation,")
    print("  and are not evidence for it either.")
    header = "".join(f"  ci={c:<6.4g}" for c in GRID_COVERAGES)
    print(f"\n    {'alpha':<8}{header}")
    for grid_alpha in GRID_ALPHAS:
        required = required_ci_percent(grid_alpha)
        line = f"    {grid_alpha:<8}"
        for grid_coverage in GRID_COVERAGES:
            pairs = grid.get((grid_alpha, grid_coverage), [])
            if len(pairs) < MIN_STRATUM_FOR_VERDICT:
                line += f"  {'thin':>9} "
            else:
                kappa = summarize(pairs)["kappa"]
                marker = "*" if abs(grid_coverage - required) <= tolerance else " "
                line += f"  {_fmt(kappa):>8}{marker} "
        print(line)
    print(f"\n    * marks the cell where coverage matches the alpha on that row.")
    print("    Where a thin column beats the marked cell, treat it as unexplained rather")
    print("    than as a finding: the 97.5% column in particular is plausibly")
    print("    multiplicity-adjusted alpha splitting, which this audit cannot confirm.")
    print(f"    n per cell:")
    for grid_alpha in GRID_ALPHAS:
        counts = " ".join(f"{grid_coverage:g}:{len(grid.get((grid_alpha, grid_coverage), []))}"
                          for grid_coverage in GRID_COVERAGES)
        print(f"      alpha {grid_alpha:<6} {counts}")
    print(f"\n  The configured alpha for every other section is {alpha}, requiring "
          f"{required_ci_percent(alpha)}% coverage.")


def report_production_impact(labels_path: Path, refused_trials: dict,
                             refused_param_types: dict, show: int) -> None:
    if not labels_path.exists():
        print(f"\n  (skipped: {labels_path} not found -- run the pull first)")
        return
    all_refused = set().union(*refused_trials.values()) if refused_trials else set()
    by_tier = Counter()
    with labels_path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            if row.get("nct_id") not in all_refused:
                continue
            tier_min = row.get("tier_min") or "?"
            by_tier["headline" if tier_min in HEADLINE_TIERS else tier_min] += 1

    print("  trials touched by each refusal (a trial can appear in more than one):")
    for kind in REFUSAL_KINDS:
        print(f"    {kind:22s} {len(refused_trials.get(kind, ())):6d} trials")
    total = sum(by_tier.values())
    print(f"\n  union of all refusals            : {len(all_refused)} trials")
    print(f"  of those, found in the labels     : {total}")
    print("\n  by tier_min as labelled BEFORE the fixes:")
    for tier, n in by_tier.most_common():
        print(f"    {tier:22s} {n:6d}  {_pct(n, total)}")
    print("\n  A trial whose label came from an A/B p-value is untouched -- the interval")
    print("  was never consulted. Only trials resting on an interval lose anything.")

    for kind in REFUSAL_KINDS:
        types = refused_param_types.get(kind)
        if not types:
            continue
        print(f"\n  {kind}: refused rows by param_type (top {show}):")
        for param_type, n in types.most_common(show):
            print(f"    {n:6d}  {param_type!r}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Measure the tier-C interval rule against sponsor-posted p-values.")
    parser.add_argument("--raw-outcomes",
                        default=Path("data/aact/results_raw_outcomes.csv"), type=Path,
                        help="the joined outcomes/analyses dump written by the pull")
    parser.add_argument("--labels", default=Path("data/aact/trial_labels.csv"), type=Path,
                        help="label CSV, read only to report production impact")
    parser.add_argument("--alpha", type=float, default=DEFAULT_ALPHA,
                        help="significance threshold. Moves BOTH the reference verdict "
                             "and the required interval coverage (100*(1-alpha)), so the "
                             "two sides stay comparable.")
    parser.add_argument("--ratio-scale-floor", type=float,
                        default=DEFAULT_RATIO_SCALE_FLOOR,
                        help="a ratio interval lying entirely at or above this is treated "
                             "as percent-scaled. Produces verdicts, so it is a flag.")
    parser.add_argument("--coverage-tolerance", type=float,
                        default=DEFAULT_COVERAGE_TOLERANCE_POINTS,
                        help="coverage POINTS within which a posted ci_percent counts as "
                             "matching the required coverage. Produces verdicts.")
    parser.add_argument("--show", type=int, default=12,
                        help="param_types listed per refusal kind")
    args = parser.parse_args()

    _rule("SETTINGS -- every threshold that produced a verdict below")
    print(f"  raw outcomes dump     : {args.raw_outcomes}")
    print(f"  labels                : {args.labels}")
    print(f"  alpha                 : {args.alpha}")
    print(f"  required ci coverage  : {required_ci_percent(args.alpha)}%  "
          f"(derived as 100*(1-alpha), not hardcoded)")
    print(f"  coverage tolerance    : +/- {args.coverage_tolerance} points")
    print(f"  ratio scale floor     : {args.ratio_scale_floor}")
    print(f"\n  {KAPPA_DOC}")

    if not args.raw_outcomes.exists():
        raise SystemExit(f"!! not found: {args.raw_outcomes}")

    got = collect(args.raw_outcomes, args.alpha, args.ratio_scale_floor,
                  args.coverage_tolerance)
    cov = got["coverage"]

    _rule("1. COVERAGE -- how big is the validation set, and what does it exclude?")
    primary = cov["primary_analysis_rows"]
    print(f"  primary analysis rows scanned        : {primary}")
    for key in ("no_decidable_p_value", "p_value_but_no_contrast_named",
                "p_value_but_no_readable_interval"):
        print(f"  excluded: {key:36s}: {cov[key]:7d}  {_pct(cov[key], primary)}")
    print(f"  VALIDATION ROWS (p-value AND interval) : {cov['validation_rows']:7d}"
          f"  {_pct(cov['validation_rows'], primary)}")
    print("\n  rows withheld in PRODUCTION, by refusal kind:")
    for kind in REFUSAL_KINDS:
        n = cov[f"refused_{kind}"]
        print(f"    {kind:22s} {n:7d}  {_pct(n, primary)}")
    print("\n  ci_percent vocabulary (top 8 of "
          f"{len(got['ci_vocab'])} distinct values):")
    for value, n in got["ci_vocab"].most_common(8):
        print(f"    {value!r:12s} {n:7d}")
    print("\n  The validation rows POSTED a p-value; production tier-C rows did not.")
    print("  This measures an ADJACENT population. A good number is a ceiling.")

    _rule("2. BY ANALYSIS DESIGN -- is the rule even asking the right question?")
    print("  An NI or equivalence trial succeeds when the interval sits inside the")
    print("  MARGIN, which routinely includes the null. If the NI stratum under-calls")
    print(f"  heavily, the rule is answering a different question and belongs in "
          f"{TIER_E}.")
    for design in ("superiority", "unstated", DESIGN_NI):
        if got["by_design"].get(design):
            report_stratum(f"design = {design}", got["by_design"][design])
    print("\n  finer reading -- non-inferiority and equivalence are different tests:")
    for detail, pairs in sorted(got["by_detail"].items(), key=lambda kv: -len(kv[1])):
        if len(pairs) < MIN_STRATUM_FOR_VERDICT:
            continue
        out = summarize(pairs)
        print(f"    {detail:32s} n={out['n']:6d}  kappa {_fmt(out['kappa'])}")

    _rule("3. BY RATIO SCALE, then BY COVERAGE -- superiority/unstated rows only")
    print("  Scale first: a ratio's null depends on the scale the sponsor used, which the")
    print("  param_type does not carry.")
    for key, label in (((CONTRAST_DIFFERENCE, "-"), "difference"),
                       ((CONTRAST_RATIO, RATIO_SCALE_UNIT), "ratio / unit-scaled"),
                       ((CONTRAST_RATIO, RATIO_SCALE_PERCENT), "ratio / percent-scaled")):
        if got["by_scale"].get(key):
            report_stratum(label, got["by_scale"][key])

    print("\n  Then coverage. The ONE-WAY IMPLICATION: at matched coverage both verdicts")
    print("  stand; at tighter coverage only 'met' is implied; at looser coverage only")
    print("  'not met' is. Watch the error DIRECTION flip between the bands.")
    for band in (COVERAGE_MATCHED, COVERAGE_TIGHTER, COVERAGE_LOOSER, COVERAGE_UNKNOWN):
        if got["by_band"].get(band):
            report_stratum(f"coverage {band}", got["by_band"][band],
                           expect_one_sided=band in EXPECTED_ONE_SIDED)
    print("\n  What the implication rule keeps versus drops. NOTE: this comparison")
    print("  CONDITIONS ON THE CANDIDATE VERDICT, so kappa on the retained subset is not")
    print("  a clean validation number -- the selection distorts the marginals. Read the")
    print("  raw agreement gap and the per-band directions above instead.")
    for key in ("RETAINED", "REFUSED"):
        if got["by_band"].get(key):
            out = summarize(got["by_band"][key])
            cells = out["cells"]
            print(f"    {key:9s} n={out['n']:6d}  raw {_fmt(out['raw_agreement'], '.1%')}"
                  f"  over-calls {cells[(0, 1)]:5d}  under-calls {cells[(1, 0)]:5d}")

    _rule("4. ALPHA x COVERAGE -- the relation, measured rather than assumed")
    report_grid(got["grid"], args.alpha, args.coverage_tolerance)

    _rule("5. PRODUCTION IMPACT -- what the refusals cost, in trials")
    report_production_impact(args.labels, got["refused_trials"],
                             got["refused_param_types"], args.show)

    _rule("VERDICT -- read it yourself")
    print("  Each refusal is justified only by its own evidence:")
    print(f"  - the percent-scaled stratum in section 3 must be DEGENERATE (candidate")
    print("    positive rate at or near 100%, kappa at or near 0, one-directional);")
    print("  - the coverage bands must show the error direction FLIPPING, or a single")
    print("    null would serve every coverage and the implication rule is dead weight;")
    print(f"  - the NI stratum in section 2 must under-call heavily, or {TIER_C} was")
    print(f"    answering its question adequately and {TIER_E} is unnecessary cost.")
    print("  If any of those fails, say so rather than keeping a refusal because it is")
    print("  already written.")
    return 0


if __name__ == "__main__":
    sys.exit(main())