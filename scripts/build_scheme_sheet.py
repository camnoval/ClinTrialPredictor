#!/usr/bin/env python3
"""The ~25 adjudications: do endpoints divide into these six kinds AT ALL? OFFLINE.

THIS IS A DIFFERENT QUESTION FROM THE KAPPA SAMPLE
==================================================
`build_endpoint_type_sample.py` asks whether the keyword rule applies the six classes
correctly. It takes the scheme as given and measures the rule against hand labels, class by
class. That is the right question SECOND.

This asks the question that comes first, and section 12.7 is explicit that no automated
reference can reach it: **if endpoints do not in fact divide into these six kinds, every
automated reference will agree with the rule and report a clean result.** The scheme is
upstream of every figure computed so far, including the kappa the gate's 0.60 threshold is
pre-registered against, and a 200-row sample drawn against a wrong carving is wasted.

So this sheet does NOT ask "is this endpoint pharmacokinetic?". It asks the operator to
write down, in their own words, what KIND of thing each endpoint is and whether "did this
trial meet its primary endpoint" has an answer for it. The six classes are then compared
against what a human actually produced -- which can come back as "these are not the joints".

WHY IT IS BLIND, AND WHY THAT IS NOT OPTIONAL
=============================================
The rule's class is NOT shown, and neither is its gate verdict. Showing them would make
this a confirmation exercise: an operator told that an endpoint is `pharmacokinetic` will
read the text looking for pharmacokinetics and find it. The whole value of the exercise is
that the human categories are generated independently, and that is destroyed by one column.

The key file carries the rule's reading and is written separately, so agreement can be
computed afterwards and cannot be consulted beforehand.

WHAT IT SAMPLES, AND WHY NOT PROPORTIONALLY
===========================================
Two frames, for the same reason section 12.9 decision 7 gives for the kappa sample:

  SPAN   a few endpoints from each class the rule assigns, so the sheet covers the scheme
         rather than its biggest bucket. Proportional sampling would spend most of 25 rows
         on `efficacy_shaped` and `other` and never show a bioequivalence endpoint at all.

  DISAGREEMENT  endpoints from the over-refusal cell, where the rule refuses and the
         sponsor's own analysis carries a p-value. That cell is the open contradiction: it
         runs at 75.9% of CHECKABLE refusals while the trial-level cross-check says refused
         trials are seven times less likely to carry a headline label than allowed ones.
         Both figures are correctly computed and the cross-tab cannot adjudicate between
         them, because it can only see 4% of refusals and that 4% is selected -- trials
         that posted an analysis are exactly the ones most likely to have had a real
         threshold. Human reading is the only instrument left for it.

25 rows is not a kappa sample and must never be reported as one: with a handful per class
the per-class figures are noise. It is a QUALITATIVE check on whether the carving exists,
and the deliverable is the operator's own words, not a number.
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
)
from trial_pos.services.endpoint_type import (  # noqa: E402
    CLASS_DOC, ENDPOINT_CLASSES, GATE_NOT_APPLICABLE, classify_title, matched_classes,
    trial_gate,
)
from trial_pos.services.sampling import (  # noqa: E402
    DEFAULT_SAMPLE_SALT, ordered_by_hash, presentation_rows, stable_hash,
)
from trial_pos.services.sponsor_threshold import (  # noqa: E402
    CELL_OVER_REFUSAL, REASON_P_VALUE, cell_for, outcome_threshold,
)

PRIMARY_OUTCOME_TYPE = "primary"
FRAME_SPAN = "span"
FRAME_DISAGREEMENT = "over_refusal_p_value"

# What the operator fills in. Free text first, on purpose: a fixed vocabulary would
# reimpose the scheme this sheet exists to test.
SHEET_COLUMNS = ("label_id", "endpoint_text", "what_kind_of_thing_is_this",
                 "does_met_have_an_answer", "notes")
KEY_COLUMNS = ("label_id", "nct_id", "frame", "rule_class", "rule_gate",
               "rule_matched_classes", "copy_index", "duplicate_group")

INSTRUCTIONS = """\
HOW TO FILL THIS IN  (~25 rows, about 15 minutes)

Read the endpoint text. Ignore that it came from a trial registry; just read it.

COLUMN: what_kind_of_thing_is_this
  In YOUR OWN WORDS, a few words. Do not pick from a list -- there is deliberately no
  list, because the thing being tested is whether a natural list exists. Write what you
  would actually call this kind of endpoint if you were sorting them into piles.
  Examples of the GRAIN wanted: "blood level of the drug", "how many people got worse",
  "did the tumour shrink", "how many had side effects", "picked a dose", "can't tell".

COLUMN: does_met_have_an_answer
  yes / no / unclear. The question is whether "did this trial meet its primary endpoint"
  is a question with an answer for THIS endpoint -- not whether you know the answer.
    yes     the endpoint was something you either hit or missed
    no      the endpoint is a measurement, a number with no pass or fail attached
    unclear you cannot tell from the text. This is a real answer, use it.

COLUMN: notes
  Anything. Especially: if the endpoint seems to be two things at once, or if none of your
  own categories fit it, say so. Those are the most informative rows in the sheet.

RULES
  Do not look up the trial. The tool will only ever see this text, so if the text is
  ambiguous that is a finding about the tool, not a gap to fill in.
  Do not try to be consistent with earlier rows on purpose. Natural drift is data: it
  shows where the categories are unstable.
  Some endpoints appear twice. That is intentional, it measures your own consistency, and
  you are not meant to be able to spot them.

WHAT HAPPENS NEXT
  Your words get compared against the six classes the rule uses. Three outcomes:
    your piles roughly match the six      -> the scheme survives; the kappa sample is next
    your piles are a DIFFERENT carving    -> the scheme is revised before anything else
    you could not sort them consistently  -> endpoint type may not be readable from text
                                             at all, and the gate's premise is wrong
  The third outcome is a real possibility and is the reason this sheet exists.
"""


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


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--measures", type=Path,
                    default=Path("data/aact/trial_design_outcomes.csv"),
                    help="REGISTERED endpoint text -- the field the tool deploys on")
    ap.add_argument("--outcomes", type=Path,
                    default=Path("data/aact/results_raw_outcomes.csv"),
                    help="posted analyses, used ONLY to find the disagreement "
                         "frame. Its outcome_title is byte-identical to the "
                         "registered measure on all 17,859 overlapping trials "
                         "(audit_text_sources.py), so the two frames read the same "
                         "text despite coming from different files")
    ap.add_argument("--labels", type=Path, default=Path("data/aact/trial_labels.csv"))
    ap.add_argument("--out", type=Path, default=Path("data/labels/scheme_sheet.csv"))
    ap.add_argument("--key", type=Path, default=Path("data/labels/scheme_key.csv"))
    ap.add_argument("--instructions", type=Path,
                    default=Path("data/labels/scheme_instructions.txt"))
    ap.add_argument("--per-class", type=int, default=3,
                    help="endpoints per rule class in the SPAN frame")
    ap.add_argument("--disagreement", type=int, default=7,
                    help="endpoints from the over-refusal cell")
    ap.add_argument("--duplicates", type=int, default=3,
                    help="endpoints shown twice, to measure operator consistency")
    ap.add_argument("--salt", default=DEFAULT_SAMPLE_SALT)
    ap.add_argument("--alpha", type=float, default=DEFAULT_ALPHA)
    ap.add_argument("--ratio-scale-floor", type=float, default=DEFAULT_RATIO_SCALE_FLOOR)
    ap.add_argument("--coverage-tolerance", type=float,
                    default=DEFAULT_COVERAGE_TOLERANCE_POINTS)
    ap.add_argument("--audit-only", action="store_true",
                    help="report the frames and write nothing")
    args = ap.parse_args()

    print("=" * 78)
    print("SCHEME RATIFICATION SHEET -- does the six-class carving exist?")
    print("=" * 78)
    print(f"  per class {args.per_class}   disagreement {args.disagreement}   "
          f"duplicates {args.duplicates}   salt '{args.salt}'")
    print("  BLIND: the rule's class and gate verdict are NOT in the sheet. They are in")
    print("  the key file, which must not be opened before the sheet is filled in.")
    print("  This is NOT a kappa sample. Per-class figures from ~25 rows are noise.")

    trials = {}
    for row in read_rows(args.labels, ("nct_id", "is_drug_trial")):
        if (row.get("is_drug_trial") or "").strip().lower() in ("true", "1", "t"):
            trials[row["nct_id"]] = True

    # ---- the SPAN frame, from registered text -----------------------------
    # set, not list: see the dedupe note below. Iteration order of a set is not
    # stable, which would matter if selection depended on it -- it does not:
    # `ordered_by_hash` sorts by a salted hash of each key, so the chosen rows are a
    # function of the set CONTENTS and the salt, never of iteration order.
    by_class: dict = defaultdict(set)
    skipped = Counter()
    for raw in read_rows(args.measures, ("nct_id", "outcome_type", "measure")):
        if (raw.get("outcome_type") or "").strip().lower() != PRIMARY_OUTCOME_TYPE:
            continue
        nct = (raw.get("nct_id") or "").strip()
        if nct not in trials or not (raw.get("measure") or "").strip():
            continue
        # DEDUPE BY (nct_id, measure). A trial can register the IDENTICAL primary
        # outcome text more than once -- the same "Maximum tolerated dose (MTD)" filed
        # under two design_outcome_index values, often one per cohort. Appending without
        # deduping put the same key in a pool twice, which produced two presentations
        # with the same label_id and tripped `presentation_rows`' collision guard.
        #
        # Deduped rather than keyed on design_outcome_index: identical text is the SAME
        # question, and showing it to the operator twice would be an unplanned duplicate
        # competing with the planned ones that measure self-consistency.
        key = (nct, raw["measure"])
        bucket = by_class[classify_title(raw["measure"])]
        if key in bucket:
            skipped["duplicate_text_within_trial"] += 1
            continue
        bucket.add(key)

    # ---- the DISAGREEMENT frame, from posted analyses ---------------------
    # Only the p-value-backed over-refusals. An over-refusal resting on a contrast
    # interval could be the REFERENCE misreading the statistic -- that is what the
    # geometric-ratio correction was about -- whereas a posted p-value against an alpha
    # admits no alternative reading, so it is the cell worth a human's time.
    analyses: dict = defaultdict(list)
    titles: dict = {}
    for raw in read_rows(args.outcomes, ("outcome_nct_id", "outcome_outcome_type",
                                         "outcome_title", "outcome_id", "analysis_id",
                                         "analysis_param_type", "analysis_p_value",
                                         "analysis_ci_lower_limit",
                                         "analysis_ci_upper_limit",
                                         "analysis_ci_percent",
                                         "analysis_non_inferiority_type")):
        if (raw.get("outcome_outcome_type") or "").strip().lower() != PRIMARY_OUTCOME_TYPE:
            continue
        nct = (raw.get("outcome_nct_id") or "").strip()
        if nct not in trials:
            continue
        key = (nct, (raw.get("outcome_id") or "").strip())
        titles[key] = raw.get("outcome_title")
        if (raw.get("analysis_id") or "").strip():
            analyses[key].append({
                "non_inferiority_type": raw.get("analysis_non_inferiority_type"),
                "param_type": raw.get("analysis_param_type"),
                "p_value": raw.get("analysis_p_value"),
                "p_value_modifier": raw.get("analysis_p_value_modifier"),
                "ci_lower_limit": raw.get("analysis_ci_lower_limit"),
                "ci_upper_limit": raw.get("analysis_ci_upper_limit"),
                "ci_percent": raw.get("analysis_ci_percent"),
            })
    disagreeing = []
    for key, title in titles.items():
        gate = trial_gate([classify_title(title)])["gate"]
        if gate != GATE_NOT_APPLICABLE:
            continue
        record = outcome_threshold(analyses.get(key, []), args.alpha,
                                  args.ratio_scale_floor, args.coverage_tolerance)
        if cell_for(gate, record["verdict"]) != CELL_OVER_REFUSAL:
            continue
        if record["reasons"][REASON_P_VALUE]:
            disagreeing.append((key[0], title))

    for reason, n in skipped.most_common():
        print(f"  skipped {reason:<30}{n:>10,}")
    print(f"\n  SPAN frame, endpoints available per rule class:")
    for cls in ENDPOINT_CLASSES:
        print(f"    {cls:<24}{len(by_class[cls]):>10,}")
    print(f"  DISAGREEMENT frame (refused, sponsor posted a p-value): "
          f"{len(disagreeing):,}")

    # ---- select, deterministically ---------------------------------------
    selected: dict = {}
    for cls in ENDPOINT_CLASSES:
        pool = by_class[cls]
        if not pool:
            print(f"  !! no endpoints in class {cls}: the sheet cannot span it")
            continue
        picks = ordered_by_hash(pool, f"{args.salt}|span|{cls}")[:args.per_class]
        selected[f"{FRAME_SPAN}|{cls}"] = picks
    if disagreeing:
        selected[FRAME_DISAGREEMENT] = ordered_by_hash(
            disagreeing, f"{args.salt}|disagree")[:args.disagreement]

    flat = [k for keys in selected.values() for k in keys]
    dupes = ordered_by_hash(flat, f"{args.salt}|dup")[:args.duplicates]
    rows = presentation_rows(selected, dupes, args.salt)

    print(f"\n  rows to label: {len(rows)}  "
          f"(unique endpoints {len(flat)}, shown twice {len(dupes)})")
    frames = Counter(r["stratum"].split("|")[0] for r in rows)
    for frame, n in frames.most_common():
        print(f"    {frame:<24}{n:>6}")

    if args.audit_only:
        print("\n  --audit-only: nothing written.")
        return 0

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(SHEET_COLUMNS))
        writer.writeheader()
        for row in rows:
            writer.writerow({"label_id": row["label_id"],
                             "endpoint_text": row["key"][1],
                             "what_kind_of_thing_is_this": "",
                             "does_met_have_an_answer": "",
                             "notes": ""})
    with args.key.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(KEY_COLUMNS))
        writer.writeheader()
        for row in rows:
            nct, text = row["key"]
            writer.writerow({
                "label_id": row["label_id"], "nct_id": nct,
                "frame": row["stratum"],
                "rule_class": classify_title(text),
                "rule_gate": trial_gate([classify_title(text)])["gate"],
                "rule_matched_classes": "|".join(matched_classes(text)),
                "copy_index": row["copy_index"],
                # The FULL key, hashed, not just the nct_id. A trial can contribute two
                # different primary endpoints to the sheet; keying the duplicate group on
                # nct_id alone would mark those two as a repeated pair and the operator's
                # consistency score would be computed across two different questions.
                "duplicate_group": (stable_hash(row["duplicate_group"], args.salt)
                                    if row["duplicate_group"] else ""),
            })
    args.instructions.write_text(INSTRUCTIONS, encoding="utf-8")
    print(f"\n  wrote {args.out}  ({len(rows)} rows to fill in)")
    print(f"  wrote {args.key}   <- DO NOT OPEN until the sheet is filled in")
    print(f"  wrote {args.instructions}")
    print("\n  For reference, the six classes this is testing (NOT in the sheet):")
    for cls in ENDPOINT_CLASSES:
        print(f"    {cls:<24}{CLASS_DOC[cls][:46]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())