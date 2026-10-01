#!/usr/bin/env python3
"""What would each candidate scheme repair buy? DIAGNOSTIC ONLY. OFFLINE.

READ THIS BEFORE READING THE TABLE
==================================
`falsify_endpoint_type.py` returned STOP: the keyword rule's refusals are contradicted by
the sponsor on most of the endpoints where both sides have a verdict. This script asks how
much of that failure each nameable cause accounts for, so the scheme revision is a decision
with evidence rather than an argument.

**Nothing here ratifies a scheme, and a good row is not a result.** Every figure is a
candidate's agreement with the SPONSOR REFERENCE, which is the same reference the candidate
was chosen to agree with. Adopting a variant because it scores well here and then citing
this run as the evidence for it is circular -- section 12.7: "a measurement that cannot
come back negative is not a measurement."

The two readings that ARE sound:

  ATTRIBUTION  a variant that barely moves the false-refusal share does not explain the
               failure, and the scheme's problem is elsewhere. A negative result here is
               trustworthy in a way a positive one is not.
  COST         every repair to over-refusal is a candidate INCREASE in under-refusal, and
               under-refusal is the worse failure: it returns a probability for a question
               with no answer, which is the category error the gate exists to prevent.
               Both cells are printed per variant so the trade is visible now rather than
               discovered after the scheme moves.

Ratifying any revised scheme still needs what it needed before: section 12.6's hand labels
on REGISTERED text, and section 12.7's ~25 adjudications.

The pharmacokinetic pattern's bare AUC alternative is NOT in the table. It is a pattern
change rather than a resolution change, so it is outside the probe by construction -- and
it is the change most at risk of being tuned against this reference. It belongs with the
hand labels. `scheme_probe.UNPROBEABLE` records that so its absence is not read as its
being fine.

No derivations live in this file.
"""
from __future__ import annotations

import argparse
import csv
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from trial_pos.services.endpoint_label import (  # noqa: E402
    DEFAULT_ALPHA, DEFAULT_COVERAGE_TOLERANCE_POINTS, DEFAULT_RATIO_SCALE_FLOOR,
    HEADLINE_TIERS,
)
from trial_pos.services.endpoint_type import (  # noqa: E402
    CLASS_PHARMACOKINETIC, classify_title,
)
from trial_pos.services.sampling import PHASE_GROUPS, phase_group  # noqa: E402
from trial_pos.services.scheme_probe import (  # noqa: E402
    UNPROBEABLE, VARIANT_DOC, VARIANT_ORDER, moved_share, reclassified, variant_gates,
)
from trial_pos.services.sponsor_threshold import (  # noqa: E402
    CELL_BOTH_REFUSE, CELL_OVER_REFUSAL, CELL_UNDER_REFUSAL, allowance_precision,
    crosstab, decisive_total, disagreement_ratio, false_refusal_share, outcome_threshold,
    over_refusal_rate,
)

PRIMARY_OUTCOME_TYPE = "primary"


def pct(value, spec: str = ".1f") -> str:
    """None prints as 'undefined', never 0 or nan."""
    return "undefined" if value is None else format(100.0 * value, spec) + "%"


def num(value, spec: str = ".1f") -> str:
    return "undefined" if value is None else format(value, spec)


def read_rows(path: Path, required: tuple):
    """Stream a CSV with the csv module, not pandas (lessons 5 and 6)."""
    if not path.exists():
        raise SystemExit(f"!! {path} not found. This script reads files already on disk.")
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        missing = [c for c in required if c not in (reader.fieldnames or [])]
        if missing:
            raise SystemExit(f"!! {path} is missing expected columns: {missing}")
        for row in reader:
            yield row


def analysis_view(row: dict) -> dict:
    """A rename, not a derivation. Same mapping as validate_interval_rule.py."""
    return {
        "non_inferiority_type": row.get("analysis_non_inferiority_type"),
        "param_type": row.get("analysis_param_type"),
        "p_value": row.get("analysis_p_value"),
        "p_value_modifier": row.get("analysis_p_value_modifier"),
        "ci_lower_limit": row.get("analysis_ci_lower_limit"),
        "ci_upper_limit": row.get("analysis_ci_upper_limit"),
        "ci_percent": row.get("analysis_ci_percent"),
    }


def load(labels: Path, outcomes: Path, alpha: float, floor: float,
         tolerance: float) -> dict:
    """One pass each. Bucketing and counting only; every verdict comes from a service."""
    trials = {}
    for row in read_rows(labels, ("nct_id", "phase", "is_drug_trial", "tier_min")):
        if (row.get("is_drug_trial") or "").strip().lower() in ("true", "1", "t"):
            trials[row["nct_id"]] = {
                "phase_group": phase_group(row.get("phase")),
                "tier_min": (row.get("tier_min") or "").strip(),
                "phase": (row.get("phase") or "").strip(),
            }
    analyses: dict = defaultdict(list)
    titles: dict = {}
    required = ("outcome_nct_id", "outcome_outcome_type", "outcome_title", "outcome_id",
                "analysis_id", "analysis_param_type", "analysis_p_value",
                "analysis_ci_lower_limit", "analysis_ci_upper_limit",
                "analysis_ci_percent", "analysis_non_inferiority_type")
    for raw in read_rows(outcomes, required):
        if (raw.get("outcome_outcome_type") or "").strip().lower() != PRIMARY_OUTCOME_TYPE:
            continue
        nct = (raw.get("outcome_nct_id") or "").strip()
        if nct not in trials:
            continue
        key = (nct, (raw.get("outcome_id") or "").strip())
        titles[key] = raw.get("outcome_title")
        if (raw.get("analysis_id") or "").strip():
            analyses[key].append(analysis_view(raw))
    sponsor = {key: outcome_threshold(analyses.get(key, []), alpha, floor,
                                      tolerance)["verdict"]
               for key in titles}
    return {"trials": trials, "titles": titles, "sponsor": sponsor}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--outcomes", default="data/aact/results_raw_outcomes.csv", type=Path)
    ap.add_argument("--labels", default="data/aact/trial_labels.csv", type=Path)
    ap.add_argument("--alpha", type=float, default=DEFAULT_ALPHA)
    ap.add_argument("--ratio-scale-floor", type=float, default=DEFAULT_RATIO_SCALE_FLOOR)
    ap.add_argument("--coverage-tolerance", type=float,
                    default=DEFAULT_COVERAGE_TOLERANCE_POINTS)
    ap.add_argument("--min-pair", type=int, default=5)
    args = ap.parse_args()

    print("=" * 78)
    print("SCHEME REPAIR PROBE -- DIAGNOSTIC ONLY, RATIFIES NOTHING")
    print("=" * 78)
    print(f"  alpha {args.alpha}   ratio scale floor {args.ratio_scale_floor}   "
          f"coverage tolerance {args.coverage_tolerance}")
    print("\n  Every figure below is a candidate's agreement with the SPONSOR")
    print("  REFERENCE. A candidate adopted because it scores well here, and justified")
    print("  by this run, is justified circularly. Ratification needs section 12.6 hand")
    print("  labels on REGISTERED text and section 12.7's ~25 adjudications.")
    print("  Endpoint text here is results-side outcomes.title, not the registered")
    print("  measure -- the pull is section 14 item 2 and has not run.")

    data = load(args.labels, args.outcomes, args.alpha, args.ratio_scale_floor,
                args.coverage_tolerance)
    keys = list(data["titles"])
    ordered_titles = [data["titles"][k] for k in keys]
    verdicts = [data["sponsor"][k] for k in keys]
    print(f"\n  primary outcomes on drug trials: {len(keys):,}")

    # ---- 1. the variant table ---------------------------------------------
    print("\n" + "-" * 78)
    print("1. EACH CANDIDATE AGAINST THE SPONSOR REFERENCE")
    print("-" * 78)
    print(f"  {'variant':<38}{'false-refl':>11}{'over':>8}{'under':>8}"
          f"{'allow-prec':>12}")
    tables = {}
    for variant in VARIANT_ORDER:
        gates = variant_gates(ordered_titles, variant)
        table = crosstab(zip(gates, verdicts))
        tables[variant] = table
        print(f"  {variant:<38}{pct(false_refusal_share(table)):>11}"
              f"{table['cells'][CELL_OVER_REFUSAL]:>8,}"
              f"{table['cells'][CELL_UNDER_REFUSAL]:>8,}"
              f"{pct(allowance_precision(table)):>12}")
    print("\n  false-refl = of the rule's decided refusals, the share the sponsor")
    print("  contradicts. THE GATING STATISTIC. over/under are raw cell counts, and")
    print("  'under' is the column to watch: it is the worse error, because it returns a")
    print("  probability for a question that has no answer.")

    print(f"\n  {'variant':<38}{'refusals left':>14}{'decided':>10}"
          f"{'over:under':>12}")
    for variant in VARIANT_ORDER:
        table = tables[variant]
        refusals = (table["cells"][CELL_BOTH_REFUSE]
                    + table["cells"][CELL_OVER_REFUSAL])
        print(f"  {variant:<38}{refusals:>14,}{decisive_total(table):>10,}"
              f"{num(disagreement_ratio(table)):>12}")
    print("\n  A variant that fixes the rate by abolishing the refusals has not fixed")
    print("  the gate, it has removed it. Read 'refusals left' beside the rate.")

    # ---- 2. how much each variant moves -----------------------------------
    print("\n" + "-" * 78)
    print("2. HOW MANY ENDPOINTS EACH VARIANT MOVES, AND WHERE")
    print("-" * 78)
    for variant in VARIANT_ORDER:
        report = reclassified(ordered_titles, variant)
        print(f"\n  {variant}  ({report['moved']:,} moved, "
              f"{pct(moved_share(report))} of all primary outcomes)")
        print(f"    {VARIANT_DOC[variant][:70]}")
        for (before, after), count in sorted(report["moves"].items(),
                                             key=lambda kv: -kv[1]):
            print(f"      {before} -> {after}: {count:,}")

    # ---- 3. the phase-1 cell, per variant ---------------------------------
    print("\n" + "-" * 78)
    print("3. THE SAME TABLE BY PHASE GROUP, AND IT INVERTS THE POOLED READING")
    print("-" * 78)
    print("  Printed separately because the pooled figure hides the per-group spread,")
    print("  and because the group this section was originally written to check --")
    print("  phase 1, where the gate does most of its work -- turns out NOT to be the")
    print("  worst one on this denominator. Read the counts, not just the shares.")
    groups = {g: [i for i, k in enumerate(keys)
                  if data["trials"][k[0]]["phase_group"] == g]
              for g in PHASE_GROUPS}
    print(f"\n  {'variant':<38}" + "".join(f"{g[:13]:>17}" for g in PHASE_GROUPS))
    for variant in VARIANT_ORDER:
        gates = variant_gates(ordered_titles, variant)
        cells = []
        for group in PHASE_GROUPS:
            idx = groups[group]
            sub = crosstab((gates[i], verdicts[i]) for i in idx)
            over = sub["cells"][CELL_OVER_REFUSAL]
            refusals = sub["cells"][CELL_BOTH_REFUSE] + over
            if refusals < args.min_pair:
                cells.append("thin")
            else:
                cells.append(f"{pct(false_refusal_share(sub), '.0f')} "
                             f"{over}/{refusals}")
        print(f"  {variant:<38}" + "".join(f"{c:>17}" for c in cells))
    print("\n  false-refusal share, with the raw cell over the decided refusals beside")
    print(f"  it -- a share is not readable without its denominator. 'thin' = fewer")
    print(f"  than {args.min_pair} decided refusals.")
    print("\n  NOTE THE DIRECTION. On the POOLED-denominator over-refusal rate that")
    print("  falsify_endpoint_type.py reports per group, phase 1 looks worst because")
    print("  the rule refuses most often there. On this denominator phase 1 is the")
    print("  BEST group and the pivotal phases are near-total failures: when the rule")
    print("  refuses a pivotal endpoint the sponsor decided, it is almost always")
    print("  wrong. Section 12.6 named that exact cell as the one worth insisting on,")
    print("  because a false refusal on a phase 3 trial is the most visible failure")
    print("  the tool has. The two statistics disagree about which phase is worst and")
    print("  both are correctly computed; they are answering different questions.")

    # ---- 4. the section-13 anchor, both phase definitions -----------------
    print("\n" + "-" * 78)
    print("4. THE SECTION-13 ANCHOR, BOTH PHASE DEFINITIONS")
    print("-" * 78)
    by_trial: dict = defaultdict(list)
    for key in keys:
        by_trial[key[0]].append(classify_title(data["titles"][key]))
    counts = Counter()
    for nct, classes in by_trial.items():
        if not classes or any(c != CLASS_PHARMACOKINETIC for c in classes):
            continue
        if data["trials"][nct]["tier_min"] not in HEADLINE_TIERS:
            continue
        counts["pivotal_group" if data["trials"][nct]["phase_group"] == "pivotal"
               else "other"] += 1
        if data["trials"][nct]["phase"] == "PHASE3":
            counts["phase3_exact"] += 1
    print("  Trials where EVERY primary title classifies pharmacokinetic and the trial")
    print("  carries a headline tier label:")
    print(f"    PHASE3 exactly              {counts['phase3_exact']:,}")
    print(f"    pivotal group (P3 + P2/P3)  {counts['pivotal_group']:,}")
    print("\n  Section 13 records 152 for this, from a crude regex rather than from")
    print("  these patterns. A mismatch is a difference between two rules, not a bug in")
    print("  either -- which is itself the point section 13 was making when it said the")
    print("  old endpoint-type figures were rule-dependent rather than properties of")
    print("  the data. Do not reconcile them by adjusting a pattern.")

    print("\n" + "-" * 78)
    print("5. WHAT THIS PROBE CANNOT REACH")
    print("-" * 78)
    for entry in UNPROBEABLE:
        print(f"  - {entry}")
    print("\n  And what no variant table can reach at all: whether endpoints divide")
    print("  into these six kinds. Every row above is downstream of that carving.")
    return 0


if __name__ == "__main__":
    sys.exit(main())