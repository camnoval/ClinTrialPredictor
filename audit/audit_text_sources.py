#!/usr/bin/env python3
"""Registered text vs posted text: what can the cross-tab actually be built on? READ-ONLY.

WHY THIS EXISTS BEFORE THE CROSS-TAB AND NOT AFTER
==================================================
Every endpoint-type figure so far was computed on `outcomes.title`, the POSTED text,
because the registered pull had not run. Section 12.2 is explicit that the gate ships
against `design_outcomes.measure`, the REGISTERED text, so the 82.2% false-refusal share
was measured on the wrong field.

Re-running against the right field needs four facts this audit supplies and nothing else.
It computes NO verdict and NO cross-tab: the point is to find out what the cross-tab can
honestly be, not to pre-empt it.

  1. THE JOIN DOES NOT EXIST. `design_outcomes` is keyed (nct_id, design_outcome_index),
     `outcomes` by its own surrogate id, and nothing relates a registered endpoint to the
     posted one it became. Sponsors also rewrite the text at posting time. So a
     per-OUTCOME cross-tab against registered text is not constructible without fuzzy
     matching, which would add a second error term on top of the one being measured.
     Section 1 sizes the only unit that works: the TRIAL, which is also what ships.

  2. HOW BIG IS THE INTERSECTION. The sponsor reference needs posted analyses; the gate
     needs registered text. The cross-tab's n is the trials with both, and if that is thin
     in the pivotal phases the registered run says little about the cell section 12.6
     cares most about.

  3. ENDPOINT COUNT PER TRIAL DIFFERS BETWEEN THE FIELDS, and the roll-up is ANY. The
     pull reported 932,714 registered primary rows over 454,110 trials, about 2.05 each,
     while the posted side runs nearer one. Under an ANY roll-up more endpoints means more
     chances for one to be allowed, so the gate MECHANICALLY allows more on the registered
     field. Any movement in the false-refusal share has to be read against section 2
     before it is attributed to the text being better or worse.

  4. DO THE TWO FIELDS EVEN DISAGREE. Section 3 crosses the gate verdict from registered
     text against the gate verdict from posted text, per trial. If they agree almost
     everywhere, the 82.2% transfers and the scheme problem is confirmed on the deployed
     field. If they disagree a lot, the earlier run measured something else and the
     registered number has to be taken fresh. This is the decisive table.

No derivations live here: classification and roll-up both come from
`trial_pos.services.endpoint_type`.
"""
from __future__ import annotations

import argparse
import csv
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from trial_pos.services.endpoint_type import (  # noqa: E402
    ENDPOINT_CLASSES, GATE_VERDICTS, classify_title, trial_gate,
)
from trial_pos.services.sampling import PHASE_GROUPS, phase_group  # noqa: E402

PRIMARY_OUTCOME_TYPE = "primary"
COUNT_BUCKETS = (0, 1, 2, 3, 4, 5)


def pct(part: int, whole: int) -> str:
    """'undefined' on a zero denominator, never 0.0%."""
    return "undefined" if not whole else f"{100.0 * part / whole:.1f}%"


def read_rows(path: Path, required: tuple):
    """Stream with the csv module, not pandas (lessons 5 and 6)."""
    if not path.exists():
        raise SystemExit(f"!! {path} not found.")
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        missing = [c for c in required if c not in (reader.fieldnames or [])]
        if missing:
            raise SystemExit(f"!! {path} missing columns {missing}; "
                             f"present: {reader.fieldnames}")
        for row in reader:
            yield row


def bucket(n: int) -> str:
    return str(n) if n in COUNT_BUCKETS else f"{COUNT_BUCKETS[-1] + 1}+"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--measures", type=Path,
                    default=Path("data/aact/trial_design_outcomes.csv"))
    ap.add_argument("--outcomes", type=Path,
                    default=Path("data/aact/results_raw_outcomes.csv"))
    ap.add_argument("--labels", type=Path, default=Path("data/aact/trial_labels.csv"))
    ap.add_argument("--examples", type=int, default=12,
                    help="disagreeing trials to print side by side")
    args = ap.parse_args()

    print("=" * 78)
    print("REGISTERED vs POSTED ENDPOINT TEXT -- read-only audit, no verdict computed")
    print("=" * 78)

    trials = {}
    for row in read_rows(args.labels, ("nct_id", "phase", "is_drug_trial")):
        if (row.get("is_drug_trial") or "").strip().lower() in ("true", "1", "t"):
            trials[row["nct_id"]] = phase_group(row.get("phase"))
    print(f"  drug trials in labels: {len(trials):,}")

    registered: dict = defaultdict(list)
    blank = 0
    for raw in read_rows(args.measures, ("nct_id", "outcome_type", "measure")):
        if (raw.get("outcome_type") or "").strip().lower() != PRIMARY_OUTCOME_TYPE:
            continue
        nct = (raw.get("nct_id") or "").strip()
        if nct not in trials:
            continue
        # A blank measure is not an endpoint. It would classify as `other` and gate
        # undeterminable, which is the right verdict, but counting it as a registered
        # endpoint would overstate coverage.
        if (raw.get("measure") or "").strip():
            registered[nct].append(raw["measure"])
        else:
            blank += 1
    print(f"  drug trials with >=1 registered primary measure: {len(registered):,}")
    print(f"  blank measures skipped: {blank:,}")

    # KEYED ON outcome_id, NOT APPENDED PER ROW. results_raw_outcomes.csv is an
    # outcome x analysis join, so an outcome with three posted analyses occupies three
    # rows. Appending per row duplicated every multi-analysis endpoint: it inflated the
    # posted-per-trial mean to 3.65 against a true figure nearer the registered 2.10, and
    # -- because duplicates do not change the SET of classes a trial exhibits -- it made
    # the ANY roll-up identical on both fields and reported 100.0% gate agreement across
    # 17,859 trials. A perfect diagonal was the symptom, not the result.
    # `falsify_endpoint_type.py` keys on (nct_id, outcome_id) and was never affected.
    posted_by_outcome: dict = {}
    posted_with_analysis: set = set()
    for raw in read_rows(args.outcomes, ("outcome_nct_id", "outcome_outcome_type",
                                         "outcome_title", "outcome_id", "analysis_id")):
        if (raw.get("outcome_outcome_type") or "").strip().lower() != PRIMARY_OUTCOME_TYPE:
            continue
        nct = (raw.get("outcome_nct_id") or "").strip()
        if nct not in trials:
            continue
        posted_by_outcome[(nct, (raw.get("outcome_id") or "").strip())] = \
            raw.get("outcome_title")
        if (raw.get("analysis_id") or "").strip():
            posted_with_analysis.add(nct)
    posted: dict = defaultdict(list)
    for (nct, _), title in posted_by_outcome.items():
        posted[nct].append(title)
    print(f"  drug trials with >=1 posted primary outcome : {len(posted):,}")
    print(f"  ...of those, with >=1 posted ANALYSIS row   : "
          f"{len(posted_with_analysis):,}")

    # ---- 1. what unit is available ---------------------------------------
    print("\n" + "-" * 78)
    print("1. COVERAGE OF THE INTERSECTION -- this is the registered cross-tab's n")
    print("-" * 78)
    both = set(registered) & posted_with_analysis
    print(f"  registered text AND a posted analysis: {both and len(both) or 0:,}")
    print(f"  posted analysis but NO registered primary: "
          f"{len(posted_with_analysis - set(registered)):,}")
    print("  (that second line is the slice a registered-text gate cannot reach at all,")
    print("   and it must gate undeterminable rather than refuse -- a trial with no")
    print("   registered primary is 'unknown', not 'has no pass/fail endpoint'.)")
    print(f"\n  {'phase group':<16}{'registered':>12}{'posted+analysis':>18}"
          f"{'BOTH':>10}")
    for group in PHASE_GROUPS:
        r = sum(1 for n in registered if trials[n] == group)
        p = sum(1 for n in posted_with_analysis if trials[n] == group)
        b = sum(1 for n in both if trials[n] == group)
        print(f"  {group:<16}{r:>12,}{p:>18,}{b:>10,}")

    # ---- 2. endpoint counts differ ---------------------------------------
    print("\n" + "-" * 78)
    print("2. PRIMARY ENDPOINTS PER TRIAL -- the ANY roll-up is sensitive to this")
    print("-" * 78)
    reg_counts = Counter(bucket(len(registered[n])) for n in both)
    post_counts = Counter(bucket(len(posted[n])) for n in both)
    print(f"  on the {len(both):,} trials in BOTH:")
    print(f"  {'n primaries':<14}{'registered':>12}{'posted':>12}")
    for key in [str(i) for i in COUNT_BUCKETS] + [f"{COUNT_BUCKETS[-1] + 1}+"]:
        if reg_counts[key] or post_counts[key]:
            print(f"  {key:<14}{reg_counts[key]:>12,}{post_counts[key]:>12,}")
    reg_mean = (sum(len(registered[n]) for n in both) / len(both)) if both else None
    post_mean = (sum(len(posted[n]) for n in both) / len(both)) if both else None
    print(f"\n  mean registered primaries per trial: "
          f"{'undefined' if reg_mean is None else f'{reg_mean:.2f}'}")
    print(f"  mean posted primaries per trial    : "
          f"{'undefined' if post_mean is None else f'{post_mean:.2f}'}")
    print("  If registered is materially higher, the registered gate allows more trials")
    print("  for an arithmetic reason, not because the text is better. Any movement in")
    print("  the false-refusal share must be read against this before interpreting it.")

    # ---- 3. the decisive table -------------------------------------------
    print("\n" + "-" * 78)
    print("3. GATE VERDICT: REGISTERED vs POSTED, SAME TRIAL -- the decisive table")
    print("-" * 78)
    grid: Counter = Counter()
    disagreements: list = []
    for nct in both:
        reg = trial_gate([classify_title(m) for m in registered[nct]])["gate"]
        post = trial_gate([classify_title(t) for t in posted[nct]])["gate"]
        grid[(reg, post)] += 1
        if reg != post:
            disagreements.append((nct, reg, post))
    print(f"  {'registered \\ posted':<22}" + "".join(f"{g[:14]:>18}"
                                                      for g in GATE_VERDICTS))
    for reg in GATE_VERDICTS:
        print(f"  {reg:<22}" + "".join(f"{grid[(reg, p)]:>18,}"
                                       for p in GATE_VERDICTS))
    agree = sum(grid[(g, g)] for g in GATE_VERDICTS)
    # The gate collapses six classes into three verdicts, so it can agree while the
    # underlying class sets differ. Reported beside the gate agreement because a high
    # figure on its own cannot distinguish "the two fields say the same thing" from "the
    # gate is too coarse to see the difference" -- and an IDENTICAL class set on every
    # trial would mean the two fields carry the same text, which is a third explanation
    # again and the one that would indicate a join bug.
    same_class_set = sum(
        1 for nct in both
        if {classify_title(m) for m in registered[nct]}
        == {classify_title(t) for t in posted[nct]})
    same_text_set = sum(
        1 for nct in both
        if {str(m).strip() for m in registered[nct]}
        == {str(t).strip() for t in posted[nct]})
    print(f"\n  agree {agree:,} of {len(both):,} ({pct(agree, len(both))})")
    print(f"  disagree {len(disagreements):,}")
    print(f"\n  identical CLASS set on both fields: {same_class_set:,} "
          f"({pct(same_class_set, len(both))})")
    print(f"  identical TEXT set on both fields : {same_text_set:,} "
          f"({pct(same_text_set, len(both))})")
    print("  Gate agreement above the class-set figure means the gate is coarser than")
    print("  the difference between the fields. A text-set figure near 100% would mean")
    print("  the two fields are the same strings, i.e. a join bug rather than a result.")
    print("\n  HIGH AGREEMENT -> the 82.2% false-refusal share measured on posted titles")
    print("  transfers to the deployed field, and the scheme problem is confirmed.")
    print("  LOW AGREEMENT  -> the earlier run measured a different thing and every")
    print("  endpoint-type figure in the handoff needs recomputing, not adjusting.")

    # ---- 4. marginal class distributions ---------------------------------
    print("\n" + "-" * 78)
    print("4. CLASS DISTRIBUTION PER OUTCOME, THE TWO FIELDS SIDE BY SIDE")
    print("-" * 78)
    print("  Marginal, NOT joined -- there is no outcome-level join. Reads as: does a")
    print("  given pattern fire at a different rate on registered text? A large gap on")
    print("  pharmacokinetic would mean the AUC problem is specific to posted titles.")
    reg_cls = Counter(classify_title(m) for n in both for m in registered[n])
    post_cls = Counter(classify_title(t) for n in both for t in posted[n])
    reg_n, post_n = sum(reg_cls.values()), sum(post_cls.values())
    print(f"\n  {'class':<24}{'registered':>20}{'posted':>20}")
    for cls in ENDPOINT_CLASSES:
        print(f"  {cls:<24}{reg_cls[cls]:>10,} {pct(reg_cls[cls], reg_n):>8}"
              f"{post_cls[cls]:>10,} {pct(post_cls[cls], post_n):>8}")

    # ---- 5. read the disagreements ---------------------------------------
    if args.examples and disagreements:
        print("\n" + "-" * 78)
        print("5. DISAGREEING TRIALS, SIDE BY SIDE -- for reading, not counting")
        print("-" * 78)
        disagreements.sort()
        for nct, reg, post in disagreements[:args.examples]:
            print(f"\n  {nct}   registered={reg}  posted={post}")
            for m in registered[nct][:3]:
                print(f"    REG  [{classify_title(m)}] {str(m)[:70]}")
            for t in posted[nct][:3]:
                print(f"    POST [{classify_title(t)}] {str(t)[:70]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())