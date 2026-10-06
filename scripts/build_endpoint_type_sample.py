#!/usr/bin/env python3
"""Audit the endpoint-type rule, then write the stratified hand-labelling sample.

READS ONLY. Writes the two sample files and the instructions, and nothing else.

WHY THE AUDIT COMES FIRST
=========================
Every figure this prints is the KEYWORD RULE's output, not a measurement of endpoint types.
What the audit is for is sizing and stratification: which cells exist, how big they are,
how often the precedence order had to decide anything, and how many trials the ANY/ALL
roll-up choice moves.

SAMPLING UNIT AND FRAME
=======================
The unit is one DISTINCT, non-blank primary-outcome text within a trial
(`sampling.sampling_units`). A trial that registers the same text under two
design_outcome_index values contributes one unit, not two. The frame is DRUG TRIALS, any
phase, with at least one primary outcome text -- not the endpoint-met eligible population,
because the gate has to work on whatever somebody pastes, including a trial that posted
nothing.

The trial-level gate sections (3 and 4) read every registered primary row, as the serving
gate does; only the per-outcome counts and the sample read units.

THE ALLOCATION IS DELIBERATELY NOT PROPORTIONAL
===============================================
Square-root of stratum size with a floor, over stratum group x predicted class
(`sampling.STRATUM_GROUPS`, settled in rev 8 section 12.12). The pooled agreement over
the sample is therefore not the population figure; the weights in the key file recover it.

Usage (PowerShell), one at a time:

  python scripts\\build_endpoint_type_sample.py --titles data\\aact\\trial_design_outcomes.csv --audit-only
  python scripts\\build_endpoint_type_sample.py --titles data\\aact\\trial_design_outcomes.csv
"""
from __future__ import annotations

import argparse
import csv
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from trial_pos.services.endpoint_label import HEADLINE_TIERS  # noqa: E402
from trial_pos.services.endpoint_type import (  # noqa: E402
    CLASS_DOC, ENDPOINT_CLASSES, GATE_KAPPA_MINIMUM, GATE_ROLLUPS, GATING_CRITERIA,
    LABELLER_CLASS_DOC, GATE_VERDICTS, LABELLER_CLASSES, MAX_FALSE_REFUSAL_SHARE,
    MIN_ALLOWANCE_PRECISION, MIN_STRATUM_FOR_VERDICT, ROLLUP_ALL, ROLLUP_ANY,
    DEFAULT_GATE_ROLLUP, class_coverage, classify_title, gate_coverage, gate_versus_tier,
    matched_classes, trial_gate_from_titles,
)
from trial_pos.services.population import tribool  # noqa: E402
from trial_pos.services.sampling import (  # noqa: E402
    DEFAULT_DUPLICATE_COUNT, DEFAULT_MIN_PER_STRATUM, DEFAULT_SAMPLE_SALT,
    DEFAULT_SAMPLE_SIZE, PHASE_GROUPS, PHASE_GROUP_DOC, STRATUM_GROUPS,
    STRATUM_GROUP_OF_PHASE_GROUP, UNIT_SKIP_KINDS, allocation_report, choose_duplicates,
    duplicate_pairs, phase_group, presentation_rows, sampling_units, select,
    sqrt_allocation, stratum_group, stratum_weights,
)

# The two source layouts this accepts, and how each names the fields. Detected from the
# header rather than from the filename, because a file that has been renamed is still the
# file it is.
LAYOUT_DESIGN = "design_outcomes (REGISTERED text -- the field the tool will deploy on)"
LAYOUT_RESULTS = "results outcomes (POSTED text -- diagnostic only, see the warning)"

DESIGN_FIELDS = {"nct": "nct_id", "index": "design_outcome_index", "text": "measure",
                 "time_frame": "time_frame", "description": "description"}
RESULTS_FIELDS = {"nct": "outcome_nct_id", "index": "outcome_id", "text": "outcome_title",
                  "time_frame": "outcome_time_frame", "description": "outcome_population"}

LABEL_COLUMNS = ("label_id", "primary_endpoint_text", "time_frame", "context",
                 "endpoint_class", "notes")
KEY_COLUMNS = ("label_id", "nct_id", "outcome_key", "stratum", "stratum_group",
               "phase_group", "predicted_class", "matched_classes", "copy_index",
               "duplicate_group", "stratum_weight")

STRATUM_SEPARATOR = "|"


def _rule(title: str) -> str:
    return ("\n" + "=" * 78 + f"\n{title}\n" + "=" * 78)


def _pct(n, d) -> str:
    return "n/a" if not d else f"{100.0 * n / d:.1f}%"


def read_csv_rows(path: Path):
    """Stream a CSV with the `csv` module, never pandas (lessons 5 and 6)."""
    with path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            yield row


def detect_layout(path: Path) -> tuple:
    """-> (layout name, field map). Raises when the header is neither shape."""
    with path.open(newline="", encoding="utf-8") as handle:
        header = next(csv.reader(handle), [])
    columns = set(header)
    if DESIGN_FIELDS["text"] in columns and DESIGN_FIELDS["nct"] in columns:
        return LAYOUT_DESIGN, DESIGN_FIELDS
    if RESULTS_FIELDS["text"] in columns and RESULTS_FIELDS["nct"] in columns:
        return LAYOUT_RESULTS, RESULTS_FIELDS
    raise SystemExit(
        f"!! {path} is neither layout. Expected either {DESIGN_FIELDS['text']!r} "
        f"(design_outcomes) or {RESULTS_FIELDS['text']!r} (results outcomes) in the "
        f"header, found: {sorted(columns)[:8]}...")


def load_labels(path: Path) -> dict:
    """nct_id -> phase, the drug flag and tier_min. The label itself is never read: the
    endpoint-type class is GATE-ONLY and must never sit beside it."""
    out = {}
    for row in read_csv_rows(path):
        out[row["nct_id"].strip().upper()] = {
            "phase": row.get("phase", ""),
            "is_drug_trial": row.get("is_drug_trial", ""),
            "tier_min": row.get("tier_min", ""),
        }
    return out


def _in_frame(meta: dict, drug_only: bool) -> bool:
    return not drug_only or tribool(meta["is_drug_trial"]) is True


def load_outcomes(path: Path, fields: dict, labels: dict, drug_only: bool) -> tuple:
    """-> ({nct: [outcome dicts in file order]}, counters). One entry per OUTCOME KEY.

    The results-side file is one row per outcome x ANALYSIS, so a repeated key is skipped
    and counted here. Repeated TEXT under different keys is a different collapse and
    happens in `sampling_units`.
    """
    by_trial: dict = defaultdict(list)
    seen: set = set()
    counters = Counter()
    for row in read_csv_rows(path):
        nct = (row.get(fields["nct"]) or "").strip().upper()
        if not nct:
            continue
        meta = labels.get(nct)
        if meta is None:
            counters["not_in_labels"] += 1
            continue
        if not _in_frame(meta, drug_only):
            counters["not_drug"] += 1
            continue
        key = f"{nct}#{(row.get(fields['index']) or len(by_trial[nct]) + 1)}"
        if key in seen:
            counters["repeated_outcome_key"] += 1
            continue
        seen.add(key)
        by_trial[nct].append({
            "key": key,
            "text": (row.get(fields["text"]) or "").strip(),
            "time_frame": (row.get(fields["time_frame"]) or "").strip(),
            "description": (row.get(fields["description"]) or "").strip(),
        })
    return dict(by_trial), counters


def report_source(layout: str, path: Path, counters: dict, by_trial: dict, units: dict,
                  skipped: dict, labels: dict, drug_only: bool) -> None:
    print(_rule("1. SOURCE AND COVERAGE"))
    print(f"  file   : {path}")
    print(f"  layout : {layout}")
    if layout == LAYOUT_RESULTS:
        print("\n  !! THIS IS THE RESULTS-SIDE FIELD. It exists only for trials that posted")
        print("     results, so a sample drawn from it says nothing about the trials the")
        print("     gate mostly serves. Use it to compare fields on the overlap.")
    frame_trials = sum(1 for meta in labels.values() if _in_frame(meta, drug_only))
    n_rows = sum(len(rows) for rows in by_trial.values())
    n_units = sum(len(rows) for rows in units.values())
    print(f"\n  trials in the label file              : {len(labels)}")
    print(f"  trials in the SAMPLING FRAME          : {frame_trials}"
          f"{'  (is_drug_trial true)' if drug_only else '  (all trials)'}")
    print(f"  frame trials with a primary row       : {len(by_trial)} "
          f"({_pct(len(by_trial), frame_trials)})")
    print(f"  frame trials with a sampling unit     : {len(units)} "
          f"({_pct(len(units), frame_trials)})")
    print(f"  rows skipped, trial not in labels     : {counters['not_in_labels']}")
    print(f"  rows skipped, not a drug trial        : {counters['not_drug']}")
    print(f"  rows skipped, outcome key repeated    : {counters['repeated_outcome_key']}")
    print(f"  primary outcome rows kept (gate input): {n_rows}")
    for kind in UNIT_SKIP_KINDS:
        print(f"    of which not a unit, {kind:26s}: {skipped[kind]}")
    print(f"  SAMPLING UNITS                        : {n_units}")
    print("\n  A frame trial with no endpoint text is UNKNOWN, not zero: the gate returns")
    print("  undeterminable for it. The share bounds what the gate can ever speak for.")
    print("\n  COVERAGE BY PHASE GROUP (frame trials)")
    print(f"    {'group':16s} {'frame':>9s} {'with unit':>11s} {'share':>8s} "
          f"{'units':>10s} {'per trial':>10s}")
    frame_by_group: Counter = Counter()
    for meta in labels.values():
        if _in_frame(meta, drug_only):
            frame_by_group[phase_group(meta["phase"])] += 1
    with_unit: Counter = Counter()
    units_by_group: Counter = Counter()
    for nct, rows in units.items():
        group = phase_group(labels[nct]["phase"])
        with_unit[group] += 1
        units_by_group[group] += len(rows)
    for group in PHASE_GROUPS:
        frame = frame_by_group[group]
        if not frame:
            continue
        per_trial = (units_by_group[group] / with_unit[group]) if with_unit[group] else 0
        print(f"    {group:16s} {frame:9d} {with_unit[group]:11d} "
              f"{_pct(with_unit[group], frame):>8s} {units_by_group[group]:10d} "
              f"{per_trial:10.2f}")
    for group in PHASE_GROUPS:
        if frame_by_group[group]:
            print(f"      {group} -> stratum {STRATUM_GROUP_OF_PHASE_GROUP[group]}: "
                  f"{PHASE_GROUP_DOC[group]}")


def report_classes(units: dict, labels: dict, show: int) -> dict:
    """Per-unit class distribution by stratum group. Returns the stratum membership."""
    print(_rule("2. WHAT THE KEYWORD RULE READS, per sampling unit (its output)"))
    print("  The rule's readings, not a measurement of endpoint types.")
    print("\n  For reference, what each class means:")
    for cls in ENDPOINT_CLASSES:
        print(f"    {cls:22s} {CLASS_DOC[cls][:120]}")

    members: dict = defaultdict(list)
    per_group: dict = defaultdict(Counter)
    all_titles: list = []
    for nct, rows in units.items():
        group = stratum_group(labels[nct]["phase"])
        for row in rows:
            cls = classify_title(row["text"])
            per_group[group][cls] += 1
            all_titles.append(row["text"])
            members[f"{group}{STRATUM_SEPARATOR}{cls}"].append(row["key"])

    print(f"\n  {'stratum group':22s} {'units':>9s} " +
          " ".join(f"{cls[:10]:>11s}" for cls in ENDPOINT_CLASSES))
    for group in STRATUM_GROUPS:
        counts = per_group.get(group)
        if not counts:
            continue
        total = sum(counts.values())
        cells = " ".join(f"{counts[cls]:5d} {_pct(counts[cls], total):>5s}"
                         for cls in ENDPOINT_CLASSES)
        print(f"  {group:22s} {total:9d} {cells}")

    coverage = class_coverage(all_titles)
    print(f"\n  units matching MORE THAN ONE class: {coverage['multi_match']} "
          f"({_pct(coverage['multi_match'], coverage['total'])})")
    print("  How much work the precedence order is doing. Near zero and a disagreement")
    print("  with hand labels is about the PATTERNS; large and the ORDER is deciding.")
    if coverage["multi_match_pairs"]:
        print("\n  top competing pairs (winner first):")
        pairs = sorted(coverage["multi_match_pairs"].items(), key=lambda kv: -kv[1])
        for (winner, loser), count in pairs[:show]:
            print(f"    {winner:22s} over {loser:22s} {count:7d}")
    return dict(members)


def report_gate(by_trial: dict, labels: dict) -> None:
    print(_rule("3. TRIAL-LEVEL GATE over every registered primary row"))
    print(f"  Default roll-up '{DEFAULT_GATE_ROLLUP}', matching the strict label's")
    print("  any_primary_met; a test enforces the pairing.")
    records = {rollup: [] for rollup in GATE_ROLLUPS}
    by_group = {rollup: defaultdict(Counter) for rollup in GATE_ROLLUPS}
    moved = 0
    for nct, rows in by_trial.items():
        group = phase_group(labels[nct]["phase"])
        texts = [row["text"] for row in rows]
        verdicts = {}
        for rollup in GATE_ROLLUPS:
            record = trial_gate_from_titles(texts, rollup)
            records[rollup].append(record)
            by_group[rollup][group][record["gate"]] += 1
            verdicts[rollup] = record["gate"]
        if verdicts[ROLLUP_ANY] != verdicts[ROLLUP_ALL]:
            moved += 1
    for rollup in GATE_ROLLUPS:
        coverage = gate_coverage(records[rollup])
        total = coverage["total"]
        print(f"\n  ROLL-UP = {rollup}   ({total} trials)")
        for verdict in GATE_VERDICTS:
            n = coverage["verdicts"][verdict]
            print(f"    {verdict:18s} {n:8d} {_pct(n, total):>8s}")
        print(f"    {'mixed (own sentence)':18s} {coverage['mixed']:8d} "
              f"{_pct(coverage['mixed'], total):>8s}")
        print("    by reason:")
        for reason, n in sorted(coverage["reasons"].items(), key=lambda kv: -kv[1]):
            print(f"      {reason:52s} {n:8d} {_pct(n, total):>8s}")
        print(f"    {'group':14s} " +
              " ".join(f"{v[:13]:>14s}" for v in GATE_VERDICTS))
        for group in PHASE_GROUPS:
            counts = by_group[rollup].get(group)
            if not counts:
                continue
            group_total = sum(counts.values())
            cells = " ".join(f"{counts[v]:6d} {_pct(counts[v], group_total):>6s}"
                             for v in GATE_VERDICTS)
            print(f"    {group:14s} {cells}")
    print(f"\n  TRIALS THE ROLL-UP CHOICE MOVES: {moved} "
          f"({_pct(moved, len(by_trial))})")


def report_gate_versus_tier(by_trial: dict, labels: dict) -> None:
    print(_rule("4. CROSS-CHECK: the gate against the label the trial actually carries"))
    print("  Not ground truth. Only the refused-but-labelled cell carries information:")
    print("  an absent label mostly means nothing was posted. No kappa is computed.")
    rows = []
    for nct, outcomes in by_trial.items():
        record = trial_gate_from_titles([row["text"] for row in outcomes])
        rows.append((record, labels[nct]["tier_min"]))
    out = gate_versus_tier(rows, HEADLINE_TIERS)
    total = out["total"]
    labelled = (out["refused_but_labelled"] + out["allowed_and_labelled"]
                + out["undeterminable_and_labelled"])
    print(f"\n  trials compared                     : {total}")
    print(f"  of those, carrying a headline label : {labelled} "
          f"({_pct(labelled, total)})")
    print(f"\n  {'gate says':20s} {'labelled':>10s} {'unlabelled':>12s} "
          f"{'labelled share':>16s}")
    for name, pair in (("refuse the estimate", ("refused_but_labelled",
                                                "refused_and_unlabelled")),
                       ("give the estimate", ("allowed_and_labelled",
                                              "allowed_and_unlabelled")),
                       ("undeterminable", ("undeterminable_and_labelled",
                                           "undeterminable_and_unlabelled"))):
        yes, no = out[pair[0]], out[pair[1]]
        print(f"  {name:20s} {yes:10d} {no:12d} {_pct(yes, yes + no):>16s}")


def report_allocation(members: dict, sample_size: int, min_per_stratum: int,
                      show: int) -> tuple:
    print(_rule("5. ALLOCATION (square-root of stratum size, with a floor)"))
    sizes = {stratum: len(keys) for stratum, keys in members.items()}
    allocation = sqrt_allocation(sizes, sample_size, min_per_stratum)
    weights = stratum_weights(sizes, allocation)
    print(f"  sample size        : {sample_size}")
    print(f"  floor per stratum  : {min_per_stratum}")
    print(f"  strata (non-empty) : {len(sizes)}")
    print(f"  population         : {sum(sizes.values())} units")
    print("\n  NOT PROPORTIONAL. The pooled agreement over this sample is not the")
    print("  population figure; the weight column recovers it.")
    print(f"\n  {'stratum':40s} {'N':>8s} {'n':>5s} {'pop share':>10s} "
          f"{'samp share':>11s} {'weight':>9s}")
    rows = allocation_report(sizes, allocation, weights)
    for row in rows[:show]:
        weight = row["weight"]
        print(f"  {row['stratum']:40s} {row['population']:8d} {row['allocated']:5d} "
              f"{row['population_share']:9.2%} {row['sample_share']:10.2%} "
              f"{'-' if weight is None else f'{weight:9.2f}'}")
    if len(rows) > show:
        print(f"  ... {len(rows) - show} further strata not shown "
              f"(--show to see more)")
    verdict_ready = sum(1 for n in allocation.values() if n >= MIN_STRATUM_FOR_VERDICT)
    print(f"\n  allocated {sum(allocation.values())} of {sample_size} requested")
    print(f"  strata allocated at least MIN_STRATUM_FOR_VERDICT ({MIN_STRATUM_FOR_VERDICT}): "
          f"{verdict_ready} of {len(allocation)}")
    print("  A stratum below it is printed and gets no verdict of its own.")
    return allocation, weights


def write_files(members: dict, allocation: dict, weights: dict, units: dict,
                labels: dict, salt: str, duplicates_wanted: int, out_path: Path,
                key_path: Path, instructions_path: Path) -> None:
    lookup = {}
    for nct, rows in units.items():
        for row in rows:
            lookup[row["key"]] = (nct, row)
    selected = select(members, allocation, salt)
    duplicates = choose_duplicates(selected, duplicates_wanted, salt)
    rows = presentation_rows(selected, duplicates, salt)
    pairs = duplicate_pairs(rows)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    key_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(LABEL_COLUMNS))
        writer.writeheader()
        for row in rows:
            _nct, outcome = lookup[row["key"]]
            writer.writerow({
                "label_id": row["label_id"],
                "primary_endpoint_text": outcome["text"],
                "time_frame": outcome["time_frame"],
                "context": outcome["description"],
                "endpoint_class": "",
                "notes": "",
            })
    with key_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(KEY_COLUMNS))
        writer.writeheader()
        for row in rows:
            nct, outcome = lookup[row["key"]]
            group, predicted = row["stratum"].split(STRATUM_SEPARATOR, 1)
            writer.writerow({
                "label_id": row["label_id"],
                "nct_id": nct,
                "outcome_key": row["key"],
                "stratum": row["stratum"],
                "stratum_group": group,
                "phase_group": phase_group(labels[nct]["phase"]),
                "predicted_class": predicted,
                "matched_classes": "|".join(matched_classes(outcome["text"])),
                "copy_index": row["copy_index"],
                "duplicate_group": row["duplicate_group"],
                "stratum_weight": f"{weights.get(row['stratum'], ''):}",
            })
    instructions_path.parent.mkdir(parents=True, exist_ok=True)
    instructions_path.write_text(_instructions(len(rows), len(pairs)), encoding="utf-8")

    print(_rule("6. FILES WRITTEN"))
    print(f"  labelling file : {out_path}")
    print(f"    {len(rows)} rows to label ({sum(allocation.values())} distinct units "
          f"+ {len(pairs)} second presentations)")
    print(f"  key file       : {key_path}")
    print("    DO NOT OPEN THIS WHILE LABELLING. It holds the predicted class, the")
    print("    phase and which rows are repeats.")
    print(f"  instructions   : {instructions_path}")
    print(f"\n  {len(pairs)} units appear TWICE under unrelated ids, shuffled apart, to")
    print("  check the labeller's own consistency.")


def _instructions(n_rows: int, n_pairs: int) -> str:
    lines = [
        "# Labelling the endpoint-type sample",
        "",
        f"There are **{n_rows} rows**. Fill in the `endpoint_class` column on each one.",
        "Leave `notes` blank unless something is worth recording; a note on a row you",
        "found hard is more useful than a guess with no explanation.",
        "",
        "## The question you are answering",
        "",
        "For this primary endpoint, **was a threshold applied to decide a pass or a**",
        "**fail**, or was a value simply reported? That is the distinction the tool's",
        "endpoint-met estimate depends on. It is not about whether the trial succeeded,",
        "and not about whether the endpoint is important.",
        "",
        "## The classes",
        "",
    ]
    for cls in LABELLER_CLASSES:
        lines.append(f"- **`{cls}`** -- {LABELLER_CLASS_DOC[cls]}")
    lines += [
        "",
        "Copy the value exactly as written above, including the underscores.",
        "",
        "## Rules that keep the measurement honest",
        "",
        "1. **Use `unclear` when you mean it.** Those rows are counted and excluded from",
        "   agreement, which is the correct handling.",
        "2. **Do not look up the trial.** You see the endpoint text, its time frame and",
        "   its description. The keyword rule reads only the first, so agreement is a",
        "   lower bound on what a rule using all three could reach.",
        "3. **Do not open the key file.** It holds the predicted class and the phase.",
        "4. **Label in one or two sittings, not ten.** Drift across sessions shows up as",
        "   disagreement and is indistinguishable from the rule being wrong.",
        "",
        "## The repeated rows",
        "",
        f"{n_pairs} endpoints appear twice, under unrelated ids and far apart in the",
        "file. You are not meant to spot them. They check your own consistency.",
        "",
        "## What gates, stated before the labels exist",
        "",
        "Both, not their average:",
        "",
        f"- **{GATING_CRITERIA[0]}** at or above **{MIN_ALLOWANCE_PRECISION:.2f}**",
        f"- **{GATING_CRITERIA[1]}** at or below **{MAX_FALSE_REFUSAL_SHARE:.2f}**",
        "",
        f"Cohen's kappa is reported beside them (reference {GATE_KAPPA_MINIMUM:.2f}) and",
        "gates nothing.",
    ]
    return "\n".join(lines) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Audit the endpoint-type rule and write the hand-labelling sample.")
    ap.add_argument("--titles", type=Path,
                    default=Path("data") / "aact" / "trial_design_outcomes.csv",
                    help="registered primary outcome text from pull_design_outcomes.py. "
                         "The results-side file is accepted as a DIAGNOSTIC and warned "
                         "about. Default: %(default)s")
    ap.add_argument("--labels", type=Path,
                    default=Path("data") / "aact" / "trial_labels.csv")
    ap.add_argument("--out", type=Path,
                    default=Path("data") / "labels" / "endpoint_type_sample.csv")
    ap.add_argument("--key", type=Path,
                    default=Path("data") / "labels" / "endpoint_type_sample_key.csv")
    ap.add_argument("--instructions", type=Path,
                    default=Path("data") / "labels" / "endpoint_type_labelling.md")
    ap.add_argument("--sample-size", type=int, default=DEFAULT_SAMPLE_SIZE,
                    help="units to label, before the repeated rows. Default: %(default)s")
    ap.add_argument("--min-per-stratum", type=int, default=DEFAULT_MIN_PER_STRATUM,
                    help="floor per stratum. Default: %(default)s")
    ap.add_argument("--duplicates", type=int, default=DEFAULT_DUPLICATE_COUNT,
                    help="units presented a second time for the self-consistency check. "
                         "Default: %(default)s")
    ap.add_argument("--salt", default=DEFAULT_SAMPLE_SALT,
                    help="selection salt; change it for an independent second sample. "
                         "Default: %(default)s")
    ap.add_argument("--all-trials", dest="drug_only", action="store_false", default=True,
                    help="do not restrict the frame to is_drug_trial (diagnostic)")
    ap.add_argument("--audit-only", action="store_true",
                    help="print the audit, write nothing")
    ap.add_argument("--show", type=int, default=14,
                    help="rows per table. Default: %(default)s")
    args = ap.parse_args()

    if not args.labels.exists():
        print(f"!! labels file not found: {args.labels}")
        return 2
    if not args.titles.exists():
        print(f"!! endpoint text file not found: {args.titles}")
        print("   Run scripts\\pull_design_outcomes.py first, or pass --titles pointing")
        print("   at data\\aact\\results_raw_outcomes.csv for the diagnostic comparison.")
        return 2
    if args.duplicates > args.sample_size:
        print(f"!! --duplicates {args.duplicates} exceeds --sample-size "
              f"{args.sample_size}")
        return 2

    print(_rule("SETTINGS -- every value that changes a number below"))
    print(f"  endpoint text      : {args.titles}")
    print(f"  labels             : {args.labels}")
    print(f"  frame              : {'drug trials only' if args.drug_only else 'all'}")
    print(f"  sample size        : {args.sample_size}")
    print(f"  floor per stratum  : {args.min_per_stratum}")
    print(f"  repeated rows      : {args.duplicates}")
    print(f"  selection salt     : {args.salt!r}")
    print(f"  stratum groups     : {', '.join(STRATUM_GROUPS)}")
    print(f"  roll-up default    : {DEFAULT_GATE_ROLLUP}")
    print(f"  gating criteria    : {GATING_CRITERIA[0]} >= {MIN_ALLOWANCE_PRECISION:.2f}, "
          f"{GATING_CRITERIA[1]} <= {MAX_FALSE_REFUSAL_SHARE:.2f}")
    print(f"  verdict floor      : {MIN_STRATUM_FOR_VERDICT} per stratum")
    print("  unit of labelling  : one DISTINCT primary-outcome text per trial")

    layout, fields = detect_layout(args.titles)
    labels = load_labels(args.labels)
    by_trial, counters = load_outcomes(args.titles, fields, labels, args.drug_only)
    units, skipped = sampling_units(by_trial)
    if not units:
        print("\n!! no sampling units survived the frame. Nothing to sample.")
        return 3

    report_source(layout, args.titles, counters, by_trial, units, skipped, labels,
                  args.drug_only)
    members = report_classes(units, labels, args.show)
    report_gate(by_trial, labels)
    report_gate_versus_tier(by_trial, labels)
    allocation, weights = report_allocation(members, args.sample_size,
                                            args.min_per_stratum, args.show)
    if args.audit_only:
        print("\n  --audit-only: nothing written.")
        return 0
    write_files(members, allocation, weights, units, labels, args.salt, args.duplicates,
                args.out, args.key, args.instructions)
    print(_rule("WHAT THIS DOES NOT DO"))
    print("  It computes no agreement: there are no hand labels for this sample yet, and")
    print("  no scorer reads this key file. The gate it samples is LIVE --")
    print("  endpoint_type.CLASS_GATE reaches users through fdaaa.trial_flag -- so a")
    print("  disagreement found here is a disagreement with what users are shown.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
