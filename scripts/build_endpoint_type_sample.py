#!/usr/bin/env python3
"""Audit the endpoint-type rule, then write the stratified hand-labelling sample.

READS ONLY. Writes the two sample files and nothing else.

WHY THE AUDIT COMES FIRST
=========================
Every figure this prints is the KEYWORD RULE's output, not a measurement of endpoint types.
That distinction is the whole reason the sample exists: the handoff's 63% phase 1
pharmacokinetic figure came from a throwaway regex that is not in the repo, and a different
keyword list moves it by more than ten points. So the sections below are labelled as the
rule's readings, and none of them should enter the handoff as a fact until the scoring step
reports agreement against the hand labels.

What the audit is for is sizing and stratification: which cells exist, how big they are,
how often the precedence order had to decide anything, and how many trials the ANY/ALL
roll-up choice moves. Those are properties of the rule and the corpus, and they are what
the allocation needs.

SAMPLING UNIT AND FRAME
=======================
The unit is one PRIMARY OUTCOME, not one trial: outcomes are what carry text, and a trial
with four primaries contributes four labelling decisions. The frame is DRUG TRIALS, any
phase, with at least one primary outcome text -- not the endpoint-met eligible population,
because the gate has to work on whatever somebody pastes, including a trial that posted
nothing.

THE ALLOCATION IS DELIBERATELY NOT PROPORTIONAL
===============================================
Pharmacokinetic primary endpoints are a low single-digit share of pivotal-phase outcomes,
and that cell is the entire argument for gating per trial rather than per phase. A
proportional sample of 200 would put a handful of them in front of a human. So allocation
is square-root of stratum size with a floor, and the CONSEQUENCE is that the pooled kappa
over the whole sample is not the population figure. The weights in the key file are what
recovers it, through `agreement.weighted_summary`.

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
    CLASS_DOC, CLASS_OTHER, ENDPOINT_CLASSES, GATE_KAPPA_MINIMUM, GATE_ROLLUPS,
    LABELLER_CLASS_DOC,
    GATE_VERDICTS, LABELLER_CLASSES, REPORTABLE_KAPPA_MINIMUM, ROLLUP_ALL, ROLLUP_ANY,
    DEFAULT_GATE_ROLLUP, class_coverage, classify_title, gate_coverage, gate_versus_tier,
    matched_classes, multi_match_rate, trial_gate_from_titles,
)
from trial_pos.services.sampling import (  # noqa: E402
    DEFAULT_DUPLICATE_COUNT, DEFAULT_MIN_PER_STRATUM, DEFAULT_SAMPLE_SALT,
    DEFAULT_SAMPLE_SIZE, PHASE_GROUPS, PHASE_GROUP_DOC, allocation_report,
    choose_duplicates, duplicate_pairs, phase_group, presentation_rows, select,
    sqrt_allocation, stratum_weights,
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
KEY_COLUMNS = ("label_id", "nct_id", "outcome_key", "stratum", "phase_group",
               "predicted_class", "matched_classes", "copy_index", "duplicate_group",
               "stratum_weight")

STRATUM_SEPARATOR = "|"


def _rule(title: str) -> str:
    return ("\n" + "=" * 78 + f"\n{title}\n" + "=" * 78)


def _pct(n, d) -> str:
    return "n/a" if not d else f"{100.0 * n / d:.1f}%"


def read_csv_rows(path: Path):
    """Stream a CSV with the `csv` module, never pandas.

    pandas destroys the literal string "NA" on read and turns empty cells into float NaN,
    which is truthy (lessons 5 and 6). Both have already caused wrong numbers in this
    project, and neither is worth risking for a file this shape.
    """
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
    """nct_id -> the few label-file fields this script needs.

    Only phase, the drug flag and tier_min are read. Nothing here touches the label itself:
    the endpoint-type class is GATE-ONLY and must never become a feature, so this script
    has no reason to carry the label around beside it.
    """
    out = {}
    for row in read_csv_rows(path):
        out[row["nct_id"].strip().upper()] = {
            "phase": row.get("phase", ""),
            "is_drug_trial": row.get("is_drug_trial", ""),
            "tier_min": row.get("tier_min", ""),
        }
    return out


def load_outcomes(path: Path, fields: dict, labels: dict, drug_only: bool) -> tuple:
    """-> ({nct: [outcome dicts in file order]}, counters).

    Outcomes are kept in FILE order, which for the design-outcomes pull is registry order
    within a trial. Order matters only for the outcome key, never for the gate: every
    roll-up here is order-independent by construction.
    """
    by_trial: dict = defaultdict(list)
    seen: set = set()
    skipped_not_in_labels = 0
    skipped_not_drug = 0
    blank_text = 0
    repeated_rows = 0
    for row in read_csv_rows(path):
        nct = (row.get(fields["nct"]) or "").strip().upper()
        if not nct:
            continue
        meta = labels.get(nct)
        if meta is None:
            skipped_not_in_labels += 1
            continue
        if drug_only and meta["is_drug_trial"] != "True":
            skipped_not_drug += 1
            continue
        key = f"{nct}#{(row.get(fields['index']) or len(by_trial[nct]) + 1)}"
        # ONE ROW PER OUTCOME, enforced here rather than assumed. The design-outcomes pull
        # produces exactly that, but the results-side file is one row per outcome x
        # ANALYSIS, so an outcome with four analyses would otherwise be counted four
        # times: inflating every class share, inflating the multi-match denominator, and
        # putting the same key in a stratum four times over so it could be drawn twice.
        # Caught by smoke-testing on the real file; a fixture with one analysis per
        # outcome would have hidden it.
        if key in seen:
            repeated_rows += 1
            continue
        seen.add(key)
        text = (row.get(fields["text"]) or "").strip()
        if not text:
            blank_text += 1
        by_trial[nct].append({
            "key": key,
            "text": text,
            "time_frame": (row.get(fields["time_frame"]) or "").strip(),
            "description": (row.get(fields["description"]) or "").strip(),
        })
    return dict(by_trial), {"not_in_labels": skipped_not_in_labels,
                            "not_drug": skipped_not_drug,
                            "blank_text": blank_text,
                            "repeated_rows": repeated_rows}


def report_source(layout: str, path: Path, counters: dict, by_trial: dict,
                   labels: dict, drug_only: bool) -> None:
    print(_rule("1. SOURCE AND COVERAGE"))
    print(f"  file   : {path}")
    print(f"  layout : {layout}")
    if layout == LAYOUT_RESULTS:
        print("\n  !! THIS IS THE RESULTS-SIDE FIELD. It exists only for trials that")
        print("     posted results -- 12.2% of phase 1 drug trials -- and it is edited")
        print("     at posting time. A classifier validated on it cannot be deployed")
        print("     against a trial with no posted results, which is the case the")
        print("     endpoint flag exists for. Use it to compare the two fields on the")
        print("     overlap, not to build the sample that decides the gate.")
    frame_trials = sum(1 for meta in labels.values()
                       if not drug_only or meta["is_drug_trial"] == "True")
    n_outcomes = sum(len(rows) for rows in by_trial.values())
    print(f"\n  trials in the label file            : {len(labels)}")
    print(f"  trials in the SAMPLING FRAME        : {frame_trials}"
          f"{'  (is_drug_trial true)' if drug_only else '  (all trials)'}")
    print(f"  frame trials WITH endpoint text     : {len(by_trial)} "
          f"({_pct(len(by_trial), frame_trials)})")
    print(f"  primary outcome rows read           : {n_outcomes}")
    print(f"  rows skipped, trial not in labels   : {counters['not_in_labels']}")
    print(f"  rows skipped, not a drug trial      : {counters['not_drug']}")
    print(f"  rows collapsed, same outcome twice  : {counters['repeated_rows']}")
    print(f"  rows with BLANK endpoint text       : {counters['blank_text']}")
    print("\n  A frame trial with no endpoint text is UNKNOWN, not zero: the gate must")
    print("  return undeterminable for it rather than refusing an estimate. The share")
    print("  matters because it bounds what the gate can ever speak for.")
    print("\n  COVERAGE BY PHASE GROUP (frame trials)")
    print(f"    {'group':16s} {'frame':>9s} {'with text':>11s} {'share':>8s} "
          f"{'outcomes':>10s} {'per trial':>10s}")
    frame_by_group: Counter = Counter()
    for meta in labels.values():
        if drug_only and meta["is_drug_trial"] != "True":
            continue
        frame_by_group[phase_group(meta["phase"])] += 1
    with_text: Counter = Counter()
    outcomes_by_group: Counter = Counter()
    for nct, rows in by_trial.items():
        group = phase_group(labels[nct]["phase"])
        with_text[group] += 1
        outcomes_by_group[group] += len(rows)
    for group in PHASE_GROUPS:
        frame = frame_by_group[group]
        if not frame:
            continue
        per_trial = (outcomes_by_group[group] / with_text[group]) if with_text[group] else 0
        print(f"    {group:16s} {frame:9d} {with_text[group]:11d} "
              f"{_pct(with_text[group], frame):>8s} {outcomes_by_group[group]:10d} "
              f"{per_trial:10.2f}")
    for group in PHASE_GROUPS:
        if frame_by_group[group]:
            print(f"      {group}: {PHASE_GROUP_DOC[group]}")


def report_classes(by_trial: dict, labels: dict, show: int) -> dict:
    """Per-outcome class distribution by phase group. Returns the stratum membership."""
    print(_rule("2. WHAT THE KEYWORD RULE READS (its output, NOT a measurement)"))
    print("  These are the rule's readings. The hypothesis under test is that they are")
    print("  roughly right; the hand labels are what decides. Nothing in this section")
    print("  belongs in the handoff as a fact.")
    print("\n  For reference, what each class means:")
    for cls in ENDPOINT_CLASSES:
        print(f"    {cls:22s} {CLASS_DOC[cls][:120]}")

    members: dict = defaultdict(list)
    per_group: dict = defaultdict(Counter)
    all_titles: list = []
    for nct, rows in by_trial.items():
        group = phase_group(labels[nct]["phase"])
        for row in rows:
            cls = classify_title(row["text"])
            per_group[group][cls] += 1
            all_titles.append(row["text"])
            members[f"{group}{STRATUM_SEPARATOR}{cls}"].append(row["key"])

    print(f"\n  {'group':14s} {'outcomes':>9s} " +
          " ".join(f"{cls[:10]:>11s}" for cls in ENDPOINT_CLASSES))
    for group in PHASE_GROUPS:
        counts = per_group.get(group)
        if not counts:
            continue
        total = sum(counts.values())
        cells = " ".join(f"{counts[cls]:5d} {_pct(counts[cls], total):>5s}"
                         for cls in ENDPOINT_CLASSES)
        print(f"  {group:14s} {total:9d} {cells}")

    coverage = class_coverage(all_titles)
    rate = multi_match_rate(coverage)
    print(f"\n  outcomes matching MORE THAN ONE class: {coverage['multi_match']} "
          f"({_pct(coverage['multi_match'], coverage['total'])})")
    print("  This is how much work the precedence order is doing. Near zero and a")
    print("  disagreement with the hand labels is about the PATTERNS; large and the")
    print("  ORDER is deciding, which is a judgment rather than a fact.")
    if coverage["multi_match_pairs"]:
        print("\n  top competing pairs (winner first):")
        pairs = sorted(coverage["multi_match_pairs"].items(), key=lambda kv: -kv[1])
        for (winner, loser), count in pairs[:show]:
            print(f"    {winner:22s} over {loser:22s} {count:7d}")
    print(f"\n  rows with blank text (classified {CLASS_OTHER}): "
          f"{coverage['empty_text']}")
    return dict(members)


def report_gate(by_trial: dict, labels: dict) -> None:
    print(_rule("3. TRIAL-LEVEL GATE, AND WHAT THE ROLL-UP CHOICE COSTS"))
    print("  The gate is per TRIAL; the classes are per OUTCOME. The roll-up that joins")
    print("  them is the SAME choice section 8.4 has outstanding for the label, and the")
    print(f"  default here is '{DEFAULT_GATE_ROLLUP}' because the strict label uses")
    print("  any_primary_met. If 8.4 moves, this moves with it -- a test enforces that.")
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
    print("  That is the size of the outstanding 8.4 decision as it reaches this gate.")


def report_gate_versus_tier(by_trial: dict, labels: dict) -> None:
    print(_rule("4. CROSS-CHECK: the gate against the label the trial actually carries"))
    print("  A SECOND signal, independent of the hand labels and free. A trial the gate")
    print("  refuses which nonetheless carries a tier A/B label is a case where the")
    print("  sponsor DID apply a threshold to that endpoint, so either the rule misread")
    print("  the endpoint or the endpoint really was tested.")
    print("\n  This is NOT ground truth, and only one cell carries information: the")
    print("  ABSENCE of a label mostly means nothing was posted, which is a disclosure")
    print("  fact rather than an endpoint-type fact. So no kappa is computed here.")
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
    print("\n  READ THE FIRST ROW. Those trials were refused by the rule and labelled by")
    print("  the sponsor's own analysis. A large count there is the rule over-refusing,")
    print("  and it is the cheapest evidence available before any hand labelling.")


def report_allocation(members: dict, sample_size: int, min_per_stratum: int,
                      show: int) -> tuple:
    print(_rule("5. ALLOCATION (square-root of stratum size, with a floor)"))
    sizes = {stratum: len(keys) for stratum, keys in members.items()}
    allocation = sqrt_allocation(sizes, sample_size, min_per_stratum)
    weights = stratum_weights(sizes, allocation)
    print(f"  sample size        : {sample_size}")
    print(f"  floor per stratum  : {min_per_stratum}")
    print(f"  strata (non-empty) : {len(sizes)}")
    print(f"  population         : {sum(sizes.values())} outcomes")
    print("\n  NOT PROPORTIONAL, deliberately. The decisive cells -- a pivotal-phase")
    print("  pharmacokinetic endpoint, an early-phase efficacy endpoint -- are small, and")
    print("  a proportional sample would put a handful of each in front of a human. The")
    print("  consequence: the POOLED kappa over this sample is NOT the population")
    print("  figure. The weight column is what recovers it.")
    print(f"\n  {'stratum':34s} {'N':>8s} {'n':>5s} {'pop share':>10s} "
          f"{'samp share':>11s} {'weight':>9s}")
    rows = allocation_report(sizes, allocation, weights)
    for row in rows[:show]:
        weight = row["weight"]
        print(f"  {row['stratum']:34s} {row['population']:8d} {row['allocated']:5d} "
              f"{row['population_share']:9.2%} {row['sample_share']:10.2%} "
              f"{'-' if weight is None else f'{weight:9.2f}'}")
    if len(rows) > show:
        print(f"  ... {len(rows) - show} further strata not shown "
              f"(--show to see more)")
    print(f"\n  allocated {sum(allocation.values())} of {sample_size} requested")
    return allocation, weights


def write_files(members: dict, allocation: dict, weights: dict, by_trial: dict,
                salt: str, duplicates_wanted: int, out_path: Path, key_path: Path,
                instructions_path: Path, sample_size: int) -> None:
    lookup = {}
    for nct, rows in by_trial.items():
        for row in rows:
            lookup[row["key"]] = (nct, row)
    selected = select(members, allocation, salt)
    duplicates = choose_duplicates(selected, duplicates_wanted, salt)
    rows = presentation_rows(selected, duplicates, salt)
    pairs = duplicate_pairs(rows)

    out_path.parent.mkdir(parents=True, exist_ok=True)
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
            nct, _outcome = lookup[row["key"]]
            group, predicted = row["stratum"].split(STRATUM_SEPARATOR, 1)
            writer.writerow({
                "label_id": row["label_id"],
                "nct_id": nct,
                "outcome_key": row["key"],
                "stratum": row["stratum"],
                "phase_group": group,
                "predicted_class": predicted,
                "matched_classes": "|".join(
                    matched_classes(lookup[row["key"]][1]["text"])),
                "copy_index": row["copy_index"],
                "duplicate_group": row["duplicate_group"],
                "stratum_weight": f"{weights.get(row['stratum'], ''):}",
            })
    instructions_path.write_text(_instructions(len(rows), len(pairs)), encoding="utf-8")

    print(_rule("6. FILES WRITTEN"))
    print(f"  labelling file : {out_path}")
    print(f"    {len(rows)} rows to label ({sum(allocation.values())} distinct outcomes "
          f"+ {len(pairs)} second presentations)")
    print(f"  key file       : {key_path}")
    print("    DO NOT OPEN THIS WHILE LABELLING. It holds the predicted class, the")
    print("    phase and which rows are repeats. Knowing any of those while labelling")
    print("    biases the reference the rule is measured against, and the phase one")
    print("    biases it toward the hypothesis under test.")
    print(f"  instructions   : {instructions_path}")
    print(f"\n  {len(pairs)} outcomes appear TWICE under unrelated ids, shuffled apart.")
    print("  That measures your own self-agreement, which is the ceiling any rule can")
    print(f"  reach against these labels. A rule at {GATE_KAPPA_MINIMUM:.2f} against")
    print("  labels whose self-agreement is 0.65 is close to the ceiling; the same rule")
    print("  against 0.95 is not, and only the repeat rows can tell those apart.")


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
        "1. **Use `unclear` when you mean it.** Forcing a choice on a genuinely",
        "   ambiguous title adds noise that gets blamed on the rule afterwards. Those",
        "   rows are counted and excluded from agreement, which is the correct handling.",
        "2. **Do not look up the trial.** You see three things: the endpoint text, its",
        "   time frame, and its description. The deployed tool has all three; the",
        "   keyword rule currently reads only the first, which makes your labels",
        "   slightly better informed than the rule and the resulting agreement a LOWER",
        "   bound on what a rule using all three could reach. Going beyond these three",
        "   -- opening the registry record, reading the results -- would measure a rule",
        "   that does not exist.",
        "3. **Do not open the key file.** It holds the predicted class and the phase.",
        "   Seeing either turns agreement into confirmation.",
        "4. **Label in one or two sittings, not ten.** Drift across sessions shows up as",
        "   disagreement and is indistinguishable from the rule being wrong.",
        "",
        "## The repeated rows",
        "",
        f"{n_pairs} endpoints appear twice, under unrelated ids and far apart in the",
        "file. You are not meant to spot them. They measure your own consistency, which",
        "is the ceiling any classifier can reach against your labels: without it, a",
        "mediocre agreement score cannot be attributed to the rule rather than to the",
        "difficulty of the task.",
        "",
        "## Thresholds, stated before the labels exist",
        "",
        f"- Agreement at or above **{GATE_KAPPA_MINIMUM:.2f}** (Cohen's kappa on the",
        "  binary refuse-or-not collapse) lets the rule decide what users see.",
        f"- Between **{REPORTABLE_KAPPA_MINIMUM:.2f}** and",
        f"  **{GATE_KAPPA_MINIMUM:.2f}**, the rule is reported and gates nothing.",
        f"- Below **{REPORTABLE_KAPPA_MINIMUM:.2f}**, the rule or the class scheme is",
        "  wrong and the endpoint clause on the user-facing flag stays empty.",
        "",
        "These were fixed in advance on purpose. A number that arrives without a",
        "criterion gets rationalised into acceptability.",
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
                    help="outcomes to label, before the repeated rows. Default: "
                         "%(default)s")
    ap.add_argument("--min-per-stratum", type=int, default=DEFAULT_MIN_PER_STRATUM,
                    help="floor so no cell is represented by a number too small to "
                         "read. Default: %(default)s")
    ap.add_argument("--duplicates", type=int, default=DEFAULT_DUPLICATE_COUNT,
                    help="how many of the selected outcomes are presented a second time, "
                         "to measure the labeller's own self-agreement. Default: "
                         "%(default)s")
    ap.add_argument("--salt", default=DEFAULT_SAMPLE_SALT,
                    help="selection salt. The sample is reproducible from this value "
                         "alone; change it for an independent second sample. Default: "
                         "%(default)s")
    ap.add_argument("--all-trials", dest="drug_only", action="store_false", default=True,
                    help="do not restrict the frame to is_drug_trial (scoping is "
                         "drug-only, so this is a diagnostic)")
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
    print(f"  roll-up default    : {DEFAULT_GATE_ROLLUP}")
    print(f"  kappa gate         : {GATE_KAPPA_MINIMUM:.2f} "
          f"(reportable from {REPORTABLE_KAPPA_MINIMUM:.2f})")
    print("  unit of labelling  : one PRIMARY OUTCOME, not one trial")

    layout, fields = detect_layout(args.titles)
    labels = load_labels(args.labels)
    by_trial, counters = load_outcomes(args.titles, fields, labels, args.drug_only)
    if not by_trial:
        print("\n!! no outcomes survived the frame. Nothing to sample.")
        return 3

    report_source(layout, args.titles, counters, by_trial, labels, args.drug_only)
    members = report_classes(by_trial, labels, args.show)
    report_gate(by_trial, labels)
    report_gate_versus_tier(by_trial, labels)
    allocation, weights = report_allocation(members, args.sample_size,
                                            args.min_per_stratum, args.show)
    if args.audit_only:
        print("\n  --audit-only: nothing written.")
        return 0
    write_files(members, allocation, weights, by_trial, args.salt, args.duplicates,
                args.out, args.key, args.instructions, args.sample_size)
    print(_rule("WHAT THIS DOES NOT DO"))
    print("  No kappa is computed here -- there are no hand labels yet. Nothing from")
    print("  this run reaches fdaaa.compose_flag: the endpoint clause stays unfilled")
    print("  until the scoring step reports agreement clearing the pre-registered")
    print("  threshold, which is the whole reason the clause was left as a parameter.")
    return 0


if __name__ == "__main__":
    sys.exit(main())