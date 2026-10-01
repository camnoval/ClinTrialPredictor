#!/usr/bin/env python3
"""The falsification cross-tab: the keyword gate against the sponsor's own analysis. OFFLINE.

WHAT THIS ANSWERS, AND WHAT IT CAN REFUSE TO LET YOU BUILD
==========================================================
`endpoint_type.py` decides whether "did this trial meet its primary endpoint" has an
answer, by reading the endpoint text with a keyword rule and six classes. Four of those
classes mean REFUSE. That carving is a judgment nobody has checked.

For the trials that posted, the sponsor's own primary analysis says whether a threshold was
applied, judged independently of the keyword rule. Crossing the two produces four decided
cells, and two of them are the ones that matter:

  OVER-REFUSAL   the rule refuses an endpoint the sponsor threshold-tested
  UNDER-REFUSAL  the rule allows an endpoint the sponsor did not test

**If the over-refusal cell is large, STOP.** The six-class scheme is carving endpoints
wrongly, nothing downstream is worth building, and no amount of hand labelling rescues it
(section 12.9 decision 4). This script is section 14 item 0 and it runs before the
`design_outcomes` pull on purpose: it can kill the whole line of work for the cost of one
pass over a file already on disk.

THE ENDPOINT TEXT HERE IS THE RESULTS-SIDE TITLE, NOT THE REGISTERED MEASURE
============================================================================
Section 12.2 is explicit that the classifier's input is `design_outcomes.measure`, the
REGISTERED text, and that a gate validated on results-side titles could not be deployed
against a trial with no posted results. That still holds. This script nevertheless reads
`outcomes.title`, for two reasons, and the substitution is printed in the output rather
than buried here:

  1. the cross-tab's population is the POSTING trials by construction -- a sponsor verdict
     requires posted analyses -- so on this population the registered measure adds
     coverage the cross-tab cannot use anyway;
  2. section 14 orders this script BEFORE the `design_outcomes` pull, so the registered
     text does not exist yet. That ordering is deliberate: a cheap falsification test
     should not be gated behind a pull it might make unnecessary.

**What that costs, stated so it is not discovered later.** The titles are edited at posting
time, and the direction of the edit is unmeasured. So a FAILING result here is strong --
the patterns miscarve text of this general kind, and registered text is not obviously
easier -- while a PASSING result is weaker than it looks, and does not discharge the
section 12.6 hand-labelling job on registered text. `build_endpoint_type_sample.py
--titles` can compare the two fields on the overlap once the pull has run, which is the
measurement that would close this gap.

WHAT ELSE THIS CANNOT ANSWER
============================
The reference is tier-derived (section 12.9 decision 3), so it is independent of the
keyword rule and NOT of the label pipeline. It never validates the label machinery.

Posting is selected: 16.4% overall, phase 3 over-represented, phase 1 far under. The
reference is therefore strongest exactly where endpoint type matters least, and section 2
prints that as a table because it is the single largest limitation on every figure below.

And no automated reference can ratify the class SCHEME. If endpoints do not in fact divide
into these six kinds, every automated reference will agree with the rule and report a clean
result. Section 12.7's irreducible ~25 hand adjudications are what test that, and this
script does not replace them.

No derivations live in this file. The tri-state mapping, the roll-ups, the cross-tab and
the selection report are all in `trial_pos.services.sponsor_threshold`, with tests.
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

from trial_pos.services.endpoint_label import (  # noqa: E402
    DEFAULT_ALPHA, DEFAULT_COVERAGE_TOLERANCE_POINTS, DEFAULT_RATIO_SCALE_FLOOR,
    HEADLINE_TIERS,
)
from trial_pos.services.endpoint_type import (  # noqa: E402
    CLASS_PHARMACOKINETIC, GATE_NOT_APPLICABLE, GATE_VERDICTS, classify_title,
    trial_gate,
)
from trial_pos.services.sampling import PHASE_GROUPS, phase_group  # noqa: E402
from trial_pos.services.sponsor_threshold import (  # noqa: E402
    AGREEMENT_CELLS, CELL_OVER_REFUSAL, CELL_UNDER_REFUSAL,
    DECISIVE_CELLS, SPONSOR_REASONS, SPONSOR_VERDICT_DOC, SPONSOR_VERDICTS,
    THRESHOLD_NOT_APPLIED, UNREADABLE_CELLS, allowance_precision, cell_for, cell_rate,
    crosstab, decisive_total, disagreement_ratio, false_refusal_share, one_directional,
    outcome_threshold, over_refusal_rate, resolve_verdicts, selection_report,
    under_refusal_rate,
)

PRIMARY_OUTCOME_TYPE = "primary"

# Section 12.5 pre-registers this for the hand-label asymmetry check. Reused here on the
# same shape of evidence one level earlier, so the two cannot drift.
DEFAULT_MIN_PAIR = 5

# NOT PRE-REGISTERED, and the output says so. Section 12.9 decision 4 says to stop if the
# over-refusal cell is "large" without fixing a number, so this is a flag with a stated
# default rather than a constant: the value has to be ratified before the verdict it
# produces means anything, and a hardcoded one would get rationalised after the fact,
# which is the exact failure GATE_KAPPA_MINIMUM was pre-registered to avoid.
DEFAULT_OVER_REFUSAL_MAX = 0.20

# The gating threshold, and also NOT pre-registered. If the six classes are drawn
# correctly then a refusal should usually be agreed with, so a majority of refusals being
# contradicted is the scheme failing rather than the rule being noisy. 0.35 is a stated
# default for a quantity the owner has to ratify, not a finding.
DEFAULT_MAX_FALSE_REFUSAL = 0.35

# Phase 3 all-pharmacokinetic-titled trials carrying a headline A/B label. Section 13
# records 152 of them, found while verifying rev 6, and they are the concrete reason this
# script exists. Recomputed here rather than asserted, so the anchor is checked against
# the current tree instead of quoted.
ANCHOR_PHASE_GROUP = "pivotal"


def pct(value, spec: str = ".1f") -> str:
    """None prints as 'undefined', never 0 or nan: the distinction is the whole point."""
    return "undefined" if value is None else format(100.0 * value, spec) + "%"


def num(value, spec: str = ".3f") -> str:
    return "undefined" if value is None else format(value, spec)


def read_rows(path: Path, required: tuple):
    """Stream a CSV with the csv module, not pandas.

    pandas' default na_values contains "NA" and turns empty cells into float NaN, which is
    truthy; both have already produced silent disagreements in this project between the
    live pull and the offline re-derive (lessons 5 and 6).
    """
    if not path.exists():
        raise SystemExit(f"!! {path} not found. This script reads files already on disk "
                         f"and pulls nothing.")
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
    `analysis_param_type`. Same mapping as `validate_interval_rule.py`.
    """
    return {
        "non_inferiority_type": row.get("analysis_non_inferiority_type"),
        "param_type": row.get("analysis_param_type"),
        "p_value": row.get("analysis_p_value"),
        "p_value_modifier": row.get("analysis_p_value_modifier"),
        "ci_lower_limit": row.get("analysis_ci_lower_limit"),
        "ci_upper_limit": row.get("analysis_ci_upper_limit"),
        "ci_percent": row.get("analysis_ci_percent"),
    }


def load_trials(path: Path) -> dict:
    """nct_id -> the trial fields this script conditions on. One pass, drug trials only.

    Drug-only per section 5, which is settled and not re-litigated here. `tier_min` comes
    along for the section-13 anchor check and is NEVER used as the sponsor verdict -- see
    `sponsor_threshold`'s note on why a tier-keyed mapping would be wrong.
    """
    required = ("nct_id", "phase", "is_drug_trial", "results_posted", "tier_min",
                "n_primary_outcomes")
    trials = {}
    counts = Counter()
    for row in read_rows(path, required):
        counts["rows"] += 1
        if (row.get("is_drug_trial") or "").strip().lower() not in ("true", "1", "t"):
            continue
        counts["drug"] += 1
        trials[row["nct_id"]] = {
            "phase_group": phase_group(row.get("phase")),
            "tier_min": (row.get("tier_min") or "").strip(),
            "posted": (row.get("results_posted") or "").strip().lower()
            in ("true", "1", "t"),
        }
    return {"trials": trials, "counts": counts}


def load_outcomes(path: Path, trials: dict, alpha: float, floor: float,
                  tolerance: float) -> dict:
    """Walk the joined dump once, grouping primary outcomes and grading each one.

    Bucketing and counting only. Every verdict comes from the services.
    """
    analyses: dict = defaultdict(list)
    titles: dict = {}
    counts = Counter()
    required = ("outcome_nct_id", "outcome_outcome_type", "outcome_title", "outcome_id",
                "analysis_id", "analysis_param_type", "analysis_p_value",
                "analysis_ci_lower_limit", "analysis_ci_upper_limit",
                "analysis_ci_percent", "analysis_non_inferiority_type")
    for raw in read_rows(path, required):
        counts["rows"] += 1
        if (raw.get("outcome_outcome_type") or "").strip().lower() != PRIMARY_OUTCOME_TYPE:
            continue
        counts["primary_rows"] += 1
        nct = (raw.get("outcome_nct_id") or "").strip()
        if nct not in trials:
            counts["primary_rows_non_drug_or_unknown"] += 1
            continue
        counts["primary_rows_drug"] += 1
        key = (nct, (raw.get("outcome_id") or "").strip())
        titles[key] = raw.get("outcome_title")
        # A left join puts outcomes with no analyses in the file with blank analysis
        # fields. Counting one of those as an analysis row would inflate n_analyses and
        # report a posted-but-silent analysis where there is in fact no analysis at all.
        if (raw.get("analysis_id") or "").strip():
            analyses[key].append(analysis_view(raw))
        else:
            counts["primary_outcomes_with_no_analysis_row"] += 1
    graded = {key: outcome_threshold(analyses.get(key, []), alpha, floor, tolerance)
              for key in titles}
    return {"titles": titles, "sponsor": graded, "counts": counts}


def build(trials: dict, titles: dict, sponsor: dict) -> dict:
    """Pair each primary outcome's gate with its sponsor verdict, then roll up to trials.

    Per-outcome is the primary unit because that is the unit section 12.6 labels and the
    unit the classifier classifies. Per-trial is reported beside it because the trial is
    what ships, and because the section-13 anchor is stated at trial level.
    """
    outcome_pairs = []
    trial_classes: dict = defaultdict(list)
    trial_sponsor: dict = defaultdict(list)
    by_group: dict = defaultdict(list)
    over_reasons: dict = defaultdict(Counter)
    over_titles: dict = defaultdict(list)
    under_titles: dict = defaultdict(list)

    for key, title in titles.items():
        nct = key[0]
        endpoint_class = classify_title(title)
        record = trial_gate([endpoint_class])
        gate = record["gate"]
        verdict = sponsor[key]["verdict"]
        cell = cell_for(gate, verdict)
        outcome_pairs.append((gate, verdict))
        trial_classes[nct].append(endpoint_class)
        trial_sponsor[nct].append(verdict)
        group = trials[nct]["phase_group"]
        by_group[group].append((gate, verdict))
        if cell == CELL_OVER_REFUSAL:
            for reason, count in sponsor[key]["reasons"].items():
                if count:
                    over_reasons[reason][endpoint_class] += 1
            over_titles[endpoint_class].append((nct, title))
        elif cell == CELL_UNDER_REFUSAL:
            under_titles[endpoint_class].append((nct, title))

    trial_pairs = []
    trial_cells: dict = defaultdict(list)
    for nct, classes in trial_classes.items():
        gate = trial_gate(classes)["gate"]
        # ANY on the tested state, matching DEFAULT_GATE_ROLLUP and the label's
        # any_primary_met. Same roll-up on both sides or the comparison is not one, and
        # the precedence comes from the service so it cannot drift from the declared one.
        verdict = resolve_verdicts(trial_sponsor[nct])
        trial_pairs.append((gate, verdict))
        trial_cells[cell_for(gate, verdict)].append(nct)

    return {"outcome_pairs": outcome_pairs, "trial_pairs": trial_pairs,
            "by_group": by_group, "over_reasons": over_reasons,
            "over_titles": over_titles, "under_titles": under_titles,
            "trial_cells": trial_cells, "trial_classes": trial_classes}


def print_table(table: dict, label: str) -> None:
    print(f"\n{label}: n={table['total']:,}   decided by both sides="
          f"{decisive_total(table):,}")
    print(f"  {'gate \\ sponsor':<20}" + "".join(f"{s:>24}" for s in SPONSOR_VERDICTS))
    for gate in GATE_VERDICTS:
        row = "".join(f"{table['grid'][(gate, s)]:>24,}" for s in SPONSOR_VERDICTS)
        print(f"  {gate:<20}{row}")
    print(f"\n  {'cell':<28}{'n':>10}{'of decided':>14}")
    for cell in DECISIVE_CELLS + AGREEMENT_CELLS:
        print(f"  {cell:<28}{table['cells'][cell]:>10,}"
              f"{pct(cell_rate(table, cell)):>14}")
    for cell in UNREADABLE_CELLS:
        print(f"  {cell:<28}{table['cells'][cell]:>10,}{'excluded':>14}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--outcomes", default="data/aact/results_raw_outcomes.csv",
                    type=Path)
    ap.add_argument("--labels", default="data/aact/trial_labels.csv", type=Path)
    ap.add_argument("--alpha", type=float, default=DEFAULT_ALPHA)
    ap.add_argument("--ratio-scale-floor", type=float,
                    default=DEFAULT_RATIO_SCALE_FLOOR)
    ap.add_argument("--coverage-tolerance", type=float,
                    default=DEFAULT_COVERAGE_TOLERANCE_POINTS)
    ap.add_argument("--over-refusal-max", type=float, default=DEFAULT_OVER_REFUSAL_MAX,
                    help="stop threshold on the over-refusal rate. NOT pre-registered; "
                         "ratify it before trusting the verdict it produces")
    ap.add_argument("--max-false-refusal", type=float,
                    default=DEFAULT_MAX_FALSE_REFUSAL,
                    help="THE GATING THRESHOLD: stop when this share of the rule's "
                         "decided refusals is contradicted by the sponsor. NOT "
                         "pre-registered; ratify it before trusting the verdict")
    ap.add_argument("--min-pair", type=int, default=DEFAULT_MIN_PAIR)
    ap.add_argument("--examples", type=int, default=8,
                    help="disagreeing endpoint titles to print per class")
    args = ap.parse_args()

    print("=" * 78)
    print("FALSIFICATION CROSS-TAB: keyword gate x tier-derived sponsor verdict")
    print("=" * 78)
    print(f"  alpha                {args.alpha}")
    print(f"  ratio scale floor    {args.ratio_scale_floor}")
    print(f"  coverage tolerance   {args.coverage_tolerance}")
    print(f"  max false-refusal    {args.max_false_refusal}   <- GATING, NOT "
          f"PRE-REGISTERED")
    print(f"  over-refusal max     {args.over_refusal_max}   <- NOT PRE-REGISTERED")
    print(f"  min pair             {args.min_pair}")
    print("\n  ENDPOINT TEXT SOURCE: outcomes.title (results-side), NOT")
    print("  design_outcomes.measure (registered). Section 12.2 says the deployed field")
    print("  and the validated field must be the same field, and here they are not:")
    print("  the registered pull is section 14 item 2 and has not run. Consequence, and")
    print("  it is asymmetric: a FAILING result below is strong, a PASSING result does")
    print("  NOT discharge the hand-labelling job on registered text.")
    print("\n  The reference is tier-derived, so it is independent of the KEYWORD RULE")
    print("  and not of the label pipeline. It never validates the label machinery.")

    loaded = load_trials(args.labels)
    trials = loaded["trials"]
    print(f"\n  trial rows read       {loaded['counts']['rows']:,}")
    print(f"  drug trials kept      {loaded['counts']['drug']:,}")

    outcomes = load_outcomes(args.outcomes, trials, args.alpha,
                             args.ratio_scale_floor, args.coverage_tolerance)
    for key, value in outcomes["counts"].most_common():
        print(f"  {key:<38}{value:>12,}")

    built = build(trials, outcomes["titles"], outcomes["sponsor"])

    # ---- 1. the cross-tab -------------------------------------------------
    print("\n" + "-" * 78)
    print("1. THE CROSS-TAB")
    print("-" * 78)
    for verdict in SPONSOR_VERDICTS:
        print(f"  {verdict:<24}{SPONSOR_VERDICT_DOC[verdict][:52]}")
    outcome_table = crosstab(built["outcome_pairs"])
    trial_table = crosstab(built["trial_pairs"])
    print_table(outcome_table, "PER PRIMARY OUTCOME (the labelling unit)")
    print_table(trial_table, "PER TRIAL (the shipping unit, ANY roll-up both sides)")

    # ---- 2. the selection bias, as a table --------------------------------
    print("\n" + "-" * 78)
    print("2. WHAT THIS REFERENCE DOES NOT COVER")
    print("-" * 78)
    population = Counter(t["phase_group"] for t in trials.values())
    covered = Counter(trials[n]["phase_group"] for n in built["trial_classes"])
    report = selection_report(covered, population)
    print(f"  overall coverage of drug trials: {pct(report['posting_rate'])}")
    print(f"  {'phase group':<18}{'covered':>10}{'drug trials':>14}"
          f"{'coverage':>11}{'ratio':>9}")
    for group in PHASE_GROUPS:
        row = report["groups"].get(group)
        if row is None:
            continue
        print(f"  {group:<18}{row['posted']:>10,}{row['population']:>14,}"
              f"{pct(row['posting_rate']):>11}{num(row['ratio'], '.2f'):>9}")
    print("\n  ratio 1.00 = proportionally represented. The groups below 1.00 are where")
    print("  the gate does most of its work and where this reference is weakest.")

    print(f"\n  {'phase group':<18}{'decided':>10}{'over-refusal':>14}"
          f"{'under-refusal':>15}")
    for group in PHASE_GROUPS:
        if group not in built["by_group"]:
            continue
        sub = crosstab(built["by_group"][group])
        decided = decisive_total(sub)
        verdictable = decided >= args.min_pair
        print(f"  {group:<18}{decided:>10,}"
              f"{(pct(over_refusal_rate(sub)) if verdictable else 'thin'):>14}"
              f"{(pct(under_refusal_rate(sub)) if verdictable else 'thin'):>15}")

    # ---- 3. the over-refusal cell, decomposed -----------------------------
    print("\n" + "-" * 78)
    print("3. THE OVER-REFUSAL CELL, DECOMPOSED")
    print("-" * 78)
    print("  A count of this cell cannot be acted on. An over-refusal resting on")
    print("  contrast_interval on geometric-mean ratios is this REFERENCE's artefact;")
    print("  one resting on p_value_posted is the rule refusing an endpoint the sponsor")
    print("  tested against an alpha, which is the rule's error. Opposite responses.")
    print(f"\n  {'sponsor reason':<44}{'n':>9}  top refused class")
    for reason in SPONSOR_REASONS:
        classes = built["over_reasons"].get(reason)
        if not classes:
            continue
        top = classes.most_common(1)[0]
        print(f"  {reason:<44}{sum(classes.values()):>9,}  {top[0]} ({top[1]:,})")

    print("\n  over-refusals by the class the rule assigned:")
    for endpoint_class, rows in sorted(built["over_titles"].items(),
                                       key=lambda kv: -len(kv[1])):
        print(f"    {endpoint_class:<24}{len(rows):>9,}")
    print("\n  under-refusals by the class the rule assigned:")
    for endpoint_class, rows in sorted(built["under_titles"].items(),
                                       key=lambda kv: -len(kv[1])):
        print(f"    {endpoint_class:<24}{len(rows):>9,}")

    if args.examples:
        print(f"\n  up to {args.examples} over-refused titles per class, for reading:")
        for endpoint_class, rows in sorted(built["over_titles"].items(),
                                           key=lambda kv: -len(kv[1])):
            print(f"    [{endpoint_class}]")
            for nct, title in rows[:args.examples]:
                print(f"      {nct}  {str(title)[:88]}")

    # ---- 4. the section-13 anchor, recomputed -----------------------------
    print("\n" + "-" * 78)
    print("4. THE SECTION-13 ANCHOR, RECOMPUTED NOT QUOTED")
    print("-" * 78)
    anchor = [nct for nct, classes in built["trial_classes"].items()
              if trials[nct]["phase_group"] == ANCHOR_PHASE_GROUP
              and classes and all(c == CLASS_PHARMACOKINETIC for c in classes)
              and trials[nct]["tier_min"] in HEADLINE_TIERS]
    print(f"  {ANCHOR_PHASE_GROUP} trials, every primary title classified "
          f"{CLASS_PHARMACOKINETIC},")
    print(f"  carrying a headline tier label: {len(anchor):,}")
    print("  Section 13 records 152 on the registered-text question. This figure is")
    print("  computed on results-side titles and on the pivotal phase GROUP (phase 3")
    print("  plus phase 2/3), so it is an adjacent quantity and a mismatch is not a bug.")

    # ---- 5. the verdict --------------------------------------------------
    print("\n" + "-" * 78)
    print("5. VERDICT")
    print("-" * 78)
    over = over_refusal_rate(outcome_table)
    under = under_refusal_rate(outcome_table)
    false_refusals = false_refusal_share(outcome_table)
    allow = allowance_precision(outcome_table)
    ratio = disagreement_ratio(outcome_table)
    directional = one_directional(outcome_table, args.min_pair)
    refused = (outcome_table["cells"][CELL_OVER_REFUSAL]
               + outcome_table["cells"][cell_for(GATE_NOT_APPLICABLE,
                                                 THRESHOLD_NOT_APPLIED)])
    print("  GATING STATISTIC -- the denominator is the refusals, because section 12.9")
    print("  decision 4 asks whether the four REFUSING classes are drawn wrongly:")
    print(f"  false-refusal share  {pct(false_refusals)}   "
          f"({outcome_table['cells'][CELL_OVER_REFUSAL]:,} of {refused:,} decided "
          f"refusals contradicted)")
    print("\n  Reported beside it, and NOT gating:")
    print(f"  over-refusal rate    {pct(over)}   "
          f"(same cell over all {decisive_total(outcome_table):,} decided units -- falls "
          f"when the rule refuses less, not when it refuses better)")
    print(f"  under-refusal rate   {pct(under)}   "
          f"({outcome_table['cells'][CELL_UNDER_REFUSAL]:,})")
    print(f"  allowance precision  {pct(allow)}   "
          f"(of allowances the sponsor decided, share the sponsor tested)")
    print(f"  disagreement ratio   {num(ratio, '.1f')}   "
          f"(over-refusals per under-refusal)")
    print(f"  one-directional      {directional}   "
          f"(the `one_sided` convention: one cell EMPTY. Read the ratio too)")

    if false_refusals is None:
        print("\n  NO VERDICT: the rule refused nothing the sponsor decided. That is a")
        print("  finding about the corpus, not a pass. Check the inputs.")
        return 1
    print(f"\n  thresholds in use: false-refusal <= {args.max_false_refusal}, "
          f"over-refusal <= {args.over_refusal_max}")
    print("  NEITHER IS PRE-REGISTERED. Ratify them before the verdict means anything.")
    tripped = []
    if false_refusals > args.max_false_refusal:
        tripped.append(f"false-refusal share {pct(false_refusals)} > "
                       f"{args.max_false_refusal}")
    if over is not None and over > args.over_refusal_max:
        tripped.append(f"over-refusal rate {pct(over)} > {args.over_refusal_max}")
    if not tripped:
        print("  >> Both thresholds clear, which clears the stop condition and nothing")
        print("     more. What would make this not good:")
        print("     - section 3 dominated by p_value_posted: those are refusals of")
        print("       endpoints the sponsor tested against an alpha, and no reading of")
        print("       the reference explains them away")
        print("     - a high disagreement ratio with one_directional False: still bias")
        print("     - a thin phase-1 row in section 2: the group the gate exists for is")
        print("       the group this run says least about")
        print("     - a pass on results-side titles is not a pass on registered text")
        return 0
    print("  >> STOP. " + "; ".join(tripped))
    print("     Section 12.9 decision 4: the six-class scheme is carving endpoints")
    print("     wrongly and hand labelling does not rescue it. Do NOT build the full")
    print("     sponsor reference or the labelling sample until the scheme is revised.")
    print("     Read section 3 before concluding the scheme is at fault: a cell")
    print("     dominated by contrast_interval on ratio param_types may be this")
    print("     REFERENCE over-calling 'tested' on bioequivalence-shaped intervals,")
    print("     whereas a cell dominated by p_value_posted is the rule's own error and")
    print("     cannot be read any other way.")
    return 2


if __name__ == "__main__":
    sys.exit(main())