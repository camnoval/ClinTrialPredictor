#!/usr/bin/env python3
"""The confirming sample for the two candidate gate flips. BUILD, then SCORE. OFFLINE.

WHAT THIS TESTS AND WHY IT IS NOT THE RATIFICATION SHEET AGAIN
==============================================================
`build_scheme_sheet.py` asked whether the six-class carving exists. It does: a labeller
reproduced five of the six classes in their own words, blind. That question is closed.

It also found the class-to-gate MAPPING wrong for exactly two classes -- `dose_finding`
(hand answer yes x3 against a refusal) and `safety_tolerability` (yes x4, no x1) -- while
`pharmacokinetic` was correct 8 times out of 8. That finding was read OFF those 25
endpoints, so it cannot be confirmed by them. This sheet is the fresh evidence, and the
bars it is judged against were written into `endpoint_type.py` before it was drawn:
`MIN_ALLOWANCE_PRECISION`, `MAX_FALSE_REFUSAL_SHARE`, and the two candidate flips.

BOTH MAPPINGS ARE SCORED ON THE SAME ROWS
=========================================
The flips are deliberately NOT applied to `CLASS_GATE`. `--score` evaluates the shipped
mapping and the candidate mapping against the identical hand labels, so the comparison is
on one body of evidence rather than on one rule's sample versus another's. A test in
`test_endpoint_type.py` fails if someone applies the flips before this runs, because once
the shipped mapping is gone that comparison is impossible.

EXCLUSION IS BY TRIAL, NOT BY ENDPOINT TEXT
===========================================
Every `nct_id` in the ratification key is excluded, not merely the exact endpoints that
were shown. A trial the labeller has already thought about is not fresh, even in its other
endpoints: they share a disease, a drug and a writing style, and partial familiarity is a
contamination that cannot be measured afterwards. 25 trials out of 216,197 costs nothing.

WHY 80 ROWS AND NOT THE SAMPLER'S 200
=====================================
`MIN_STRATUM_FOR_VERDICT` is 20. Three phase groups x six classes is eighteen strata, so
the existing sampler's 200 rows -- and section 12.9's indicative ~100 -- clear the verdict
threshold in NO stratum at all. The ratification narrowed the question to two classes, so
this is scoped to them plus controls: 25 each on the two mappings under test, 15 each on
`pharmacokinetic` and `efficacy_shaped` to catch under-refusals the flips introduce.

It therefore does NOT support a six-class kappa, a per-phase figure, or any statement
about `bioequivalence` or `other`. Those were correct in the ratification and are not
retested here.
"""
from __future__ import annotations

import argparse
import csv
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from trial_pos.services.agreement import (  # noqa: E402
    cohens_kappa_k, confusion_k, kappa_verdict, raw_agreement_k,
)
from trial_pos.services.endpoint_type import (  # noqa: E402
    CANDIDATE_APPLICABLE_FLIPS, CLASS_EFFICACY, CLASS_GATE, CLASS_PHARMACOKINETIC,
    CONFIRM_SAMPLE_PER_CONTROL_CLASS, CONFIRM_SAMPLE_PER_TESTED_CLASS,
    GATE_APPLICABLE, GATE_KAPPA_MINIMUM, GATE_NOT_APPLICABLE, GATING_CRITERIA,
    MAX_FALSE_REFUSAL_SHARE, MIN_ALLOWANCE_PRECISION, MIN_STRATUM_FOR_VERDICT,
    REPORTABLE_KAPPA_MINIMUM, classify_title,
)
from trial_pos.services.sampling import (  # noqa: E402
    DEFAULT_SAMPLE_SALT, ordered_by_hash, presentation_rows, stable_hash,
)
from trial_pos.services.sponsor_threshold import (  # noqa: E402
    CELL_BOTH_ALLOW, CELL_BOTH_REFUSE, CELL_OVER_REFUSAL, CELL_UNDER_REFUSAL,
    THRESHOLD_TESTED, allowance_precision, cell_for, crosstab, decisive_total,
    false_refusal_share, hand_verdict,
)

PRIMARY_OUTCOME_TYPE = "primary"
CONTROL_CLASSES = (CLASS_PHARMACOKINETIC, CLASS_EFFICACY)
SHEET_COLUMNS = ("label_id", "endpoint_text", "does_met_have_an_answer", "notes")
KEY_COLUMNS = ("label_id", "nct_id", "rule_class", "shipped_gate", "candidate_gate",
               "copy_index", "duplicate_group")

INSTRUCTIONS = """\
HOW TO FILL THIS IN  (80 rows, about 40 minutes)

ONE COLUMN MATTERS: does_met_have_an_answer.  yes / no / unclear

  Read the endpoint text. Ask: is "did this trial meet its primary endpoint" a question
  with an answer for THIS endpoint? Not whether you know the answer -- whether there is
  one.

    yes      the endpoint is something the trial either hit or missed
    no       the endpoint is a measurement, a number with no pass or fail attached
    unclear  you cannot tell from the text. A REAL answer -- use it rather than guessing.
             It is counted separately and never read as a "no".

  Leave nothing blank. A blank is a skipped row and the scorer will refuse to run, because
  a blank scored as "unclear" would quietly shrink every denominator.

  notes is optional. Worth using when an endpoint seems to be two things at once.

WHY ONLY ONE COLUMN THIS TIME
  The previous sheet asked you to name categories in your own words, to find out whether
  the six the code uses are real. They are -- you reproduced five of them without having
  seen them. That question is closed, so this sheet only asks the thing that still decides
  something: which endpoints the tool should decline to score.

RULES
  Do not look up the trial. The tool will only ever see this text.
  Do not try to be consistent with earlier rows on purpose.
  Some endpoints appear twice, to measure your own consistency. With 80 rows you may spot
  them; that is fine, answer normally.

WHAT IT DECIDES
  The code currently refuses to produce a probability for dose-finding and safety
  endpoints. The previous 25 rows suggested that is wrong. Your answers here either
  confirm that on endpoints nobody has looked at, or they do not. The thresholds were
  written down before this sheet was drawn, so the verdict is not adjustable after the
  fact.
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


def candidate_gate(endpoint_class: str) -> str:
    """The gate verdict under the candidate mapping: the flips applied, nothing else."""
    if endpoint_class in CANDIDATE_APPLICABLE_FLIPS:
        return GATE_APPLICABLE
    return CLASS_GATE[endpoint_class]


def pct(value) -> str:
    """None prints as 'undefined', never 0.0%: a rate with no denominator is not zero."""
    return "undefined" if value is None else f"{100.0 * value:.1f}%"


def num(value) -> str:
    return "undefined" if value is None else f"{value:.3f}"


# ---- build ----------------------------------------------------------------
def build(args) -> int:
    excluded = set()
    if args.exclude.exists():
        for row in read_rows(args.exclude, ("nct_id",)):
            excluded.add((row.get("nct_id") or "").strip())
        print(f"  excluding {len(excluded):,} trials seen in {args.exclude}")
    else:
        print(f"  !! {args.exclude} not found -- NOTHING IS EXCLUDED. If the ratification")
        print(f"     sheet was filled in, this sample may re-show endpoints the fix was")
        print(f"     derived from, which would score the candidate on its own evidence.")

    trials = set()
    for row in read_rows(args.labels, ("nct_id", "is_drug_trial")):
        if (row.get("is_drug_trial") or "").strip().lower() in ("true", "1", "t"):
            trials.add(row["nct_id"])

    # set, not list: see the dedupe note below. Set iteration order is not stable,
    # which would matter if selection depended on it -- it does not:
    # `ordered_by_hash` sorts by a salted hash of each key, so the chosen rows are a
    # function of the set CONTENTS and the salt, never of iteration order.
    by_class: dict = defaultdict(set)
    skipped = Counter()
    for raw in read_rows(args.measures, ("nct_id", "outcome_type", "measure")):
        if (raw.get("outcome_type") or "").strip().lower() != PRIMARY_OUTCOME_TYPE:
            continue
        nct = (raw.get("nct_id") or "").strip()
        if nct not in trials:
            skipped["not_a_drug_trial"] += 1
            continue
        if nct in excluded:
            skipped["trial_already_labelled"] += 1
            continue
        if not (raw.get("measure") or "").strip():
            skipped["blank_measure"] += 1
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
    for reason, n in skipped.most_common():
        print(f"  skipped {reason:<28}{n:>10,}")

    wanted = {cls: CONFIRM_SAMPLE_PER_TESTED_CLASS
              for cls in CANDIDATE_APPLICABLE_FLIPS}
    wanted.update({cls: CONFIRM_SAMPLE_PER_CONTROL_CLASS for cls in CONTROL_CLASSES})
    selected: dict = {}
    print(f"\n  {'class':<24}{'pool':>12}{'wanted':>8}{'taken':>7}  role")
    for cls, want in wanted.items():
        pool = by_class[cls]
        picks = ordered_by_hash(pool, f"{args.salt}|confirm|{cls}")[:want]
        selected[cls] = picks
        role = ("UNDER TEST" if cls in CANDIDATE_APPLICABLE_FLIPS else "control")
        print(f"  {cls:<24}{len(pool):>12,}{want:>8}{len(picks):>7}  {role}")
        if len(picks) < want:
            print(f"    !! pool exhausted: only {len(picks)} available")
        if cls in CANDIDATE_APPLICABLE_FLIPS and len(picks) < MIN_STRATUM_FOR_VERDICT:
            print(f"    !! below MIN_STRATUM_FOR_VERDICT ({MIN_STRATUM_FOR_VERDICT}): "
                  f"this class gets NO verdict")

    flat = [k for keys in selected.values() for k in keys]
    dupes = ordered_by_hash(flat, f"{args.salt}|confirm-dup")[:args.duplicates]
    rows = presentation_rows(selected, dupes, args.salt)
    print(f"\n  rows to label: {len(rows)}  "
          f"(unique endpoints {len(flat)}, shown twice {len(dupes)})")

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
                             "does_met_have_an_answer": "", "notes": ""})
    with args.key.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(KEY_COLUMNS))
        writer.writeheader()
        for row in rows:
            nct, text = row["key"]
            cls = classify_title(text)
            writer.writerow({
                "label_id": row["label_id"], "nct_id": nct, "rule_class": cls,
                "shipped_gate": CLASS_GATE[cls],
                "candidate_gate": candidate_gate(cls),
                "copy_index": row["copy_index"],
                "duplicate_group": (stable_hash(row["duplicate_group"], args.salt)
                                    if row["duplicate_group"] else ""),
            })
    args.instructions.write_text(INSTRUCTIONS, encoding="utf-8")
    print(f"\n  wrote {args.out}   ({len(rows)} rows, one column to fill in)")
    print(f"  wrote {args.key}    <- DO NOT OPEN until the sheet is filled in")
    print(f"  wrote {args.instructions}")
    print(f"\n  then: python scripts\\build_confirm_sample.py --score")
    return 0


# ---- score ----------------------------------------------------------------
def _report(table: dict, label: str) -> dict:
    """One mapping's numbers. No verdict here; the verdict needs both mappings."""
    cells = table["cells"]
    pairs = []
    for cell, (hand, gate) in ((CELL_BOTH_REFUSE, ("refuse", "refuse")),
                               (CELL_OVER_REFUSAL, ("allow", "refuse")),
                               (CELL_UNDER_REFUSAL, ("refuse", "allow")),
                               (CELL_BOTH_ALLOW, ("allow", "allow"))):
        pairs.extend([(hand, gate)] * cells[cell])
    matrix = confusion_k(pairs, labels=("refuse", "allow"))["matrix"]
    return {"label": label, "table": table, "matrix": matrix,
            "raw": raw_agreement_k(matrix), "kappa": cohens_kappa_k(matrix),
            "allowance_precision": allowance_precision(table),
            "false_refusal_share": false_refusal_share(table)}


def score(args) -> int:
    key = {row["label_id"]: row for row in read_rows(args.key, KEY_COLUMNS)}
    answers = {}
    blanks = []
    for row in read_rows(args.out, ("label_id", "does_met_have_an_answer")):
        lid = row["label_id"]
        if not (row.get("does_met_have_an_answer") or "").strip():
            blanks.append(lid)
            continue
        answers[lid] = row["does_met_have_an_answer"]
    if blanks:
        raise SystemExit(f"!! {len(blanks)} rows have no answer: {blanks[:8]}...\n"
                         f"   A blank is a skipped row, not an 'unclear'. Scoring a blank "
                         f"as unclear would shrink every denominator invisibly.")
    missing = set(answers) - set(key)
    if missing:
        raise SystemExit(f"!! label_ids in the sheet but not the key: {sorted(missing)[:8]}")

    print("=" * 78)
    print("CONFIRMING SAMPLE -- SCORED AGAINST PRE-REGISTERED BARS")
    print("=" * 78)
    print(f"  gating criteria       {GATING_CRITERIA}")
    print(f"  allowance precision  >= {MIN_ALLOWANCE_PRECISION}")
    print(f"  false-refusal share  <= {MAX_FALSE_REFUSAL_SHARE}")
    print(f"  kappa                   {GATE_KAPPA_MINIMUM} -- REPORTED, NOT GATING")
    print(f"  rows answered         {len(answers)}")

    # consistency on the repeated endpoints, before anything else: an operator who
    # disagrees with themselves bounds how well any rule can agree with them.
    groups: dict = defaultdict(list)
    for lid, answer in answers.items():
        group = key[lid]["duplicate_group"]
        if group:
            groups[group].append(hand_verdict(answer))
    consistent = sum(1 for v in groups.values() if len(set(v)) == 1)
    print(f"\n  repeated endpoints    {len(groups)} pairs, {consistent} consistent")
    if groups and consistent < len(groups):
        print("  !! self-disagreement caps the achievable kappa. A threshold above the")
        print("     operator's own ceiling is unreachable by construction -- see the note")
        print("     on GATE_KAPPA_MINIMUM.")

    reports = []
    for label, gate_field in (("shipped", "shipped_gate"),
                              ("candidate", "candidate_gate")):
        pairs = [(key[lid][gate_field], hand_verdict(answer))
                 for lid, answer in answers.items()]
        reports.append(_report(crosstab(pairs), label))

    print(f"\n  {'mapping':<12}{'n':>6}{'raw':>8}{'kappa':>8}"
          f"{'allow-prec':>12}{'false-refl':>12}{'over':>6}{'under':>7}")
    for r in reports:
        cells = r["table"]["cells"]
        print(f"  {r['label']:<12}{decisive_total(r['table']):>6}{num(r['raw']):>8}"
              f"{num(r['kappa']):>8}{pct(r['allowance_precision']):>12}"
              f"{pct(r['false_refusal_share']):>12}"
              f"{cells[CELL_OVER_REFUSAL]:>6}{cells[CELL_UNDER_REFUSAL]:>7}")

    print("\n  per class, hand answers (the two UNDER TEST first):")
    order = list(CANDIDATE_APPLICABLE_FLIPS) + list(CONTROL_CLASSES)
    for cls in order:
        got = [hand_verdict(a) for lid, a in answers.items()
               if key[lid]["rule_class"] == cls]
        if not got:
            continue
        tested = sum(1 for v in got if v == THRESHOLD_TESTED)
        thin = "" if len(got) >= MIN_STRATUM_FOR_VERDICT else "  <- THIN, no verdict"
        print(f"    {cls:<24}n={len(got):<4} has-an-answer={tested:<4}"
              f"no-or-unclear={len(got) - tested}{thin}")

    print("\n" + "-" * 78)
    print("VERDICT")
    print("-" * 78)
    for r in reports:
        passes = []
        if r["allowance_precision"] is None or r["false_refusal_share"] is None:
            print(f"  {r['label']:<12} NO VERDICT: one side decided nothing.")
            continue
        passes.append(r["allowance_precision"] >= MIN_ALLOWANCE_PRECISION)
        passes.append(r["false_refusal_share"] <= MAX_FALSE_REFUSAL_SHARE)
        verdict = "PASS" if all(passes) else "FAIL"
        print(f"  {r['label']:<12} {verdict}   allowance "
              f"{pct(r['allowance_precision'])} vs >={MIN_ALLOWANCE_PRECISION}, "
              f"false-refusal {pct(r['false_refusal_share'])} vs "
              f"<={MAX_FALSE_REFUSAL_SHARE}")
        print(f"               kappa {num(r['kappa'])} "
              f"({kappa_verdict(r['kappa'], GATE_KAPPA_MINIMUM, REPORTABLE_KAPPA_MINIMUM)}"
              f") -- reported only")
    print("\n  If the candidate passes and the shipped mapping fails, apply the flips in")
    print("  CLASS_GATE and record this run beside them. If BOTH pass, the flips are not")
    print("  needed and the simpler change is none. If NEITHER passes, the mapping is not")
    print("  the whole problem and the patterns are next.")
    print("  Do NOT adjust the bars after reading this. They were set beforehand so that")
    print("  this sentence would be the only available response to a FAIL.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--measures", type=Path,
                    default=Path("data/aact/trial_design_outcomes.csv"))
    ap.add_argument("--labels", type=Path, default=Path("data/aact/trial_labels.csv"))
    ap.add_argument("--exclude", type=Path,
                    default=Path("data/labels/scheme_key.csv"),
                    help="trials already labelled; excluded by nct_id, not by text")
    ap.add_argument("--out", type=Path, default=Path("data/labels/confirm_sheet.csv"))
    ap.add_argument("--key", type=Path, default=Path("data/labels/confirm_key.csv"))
    ap.add_argument("--instructions", type=Path,
                    default=Path("data/labels/confirm_instructions.txt"))
    ap.add_argument("--duplicates", type=int, default=6)
    ap.add_argument("--salt", default=f"{DEFAULT_SAMPLE_SALT}|confirm")
    ap.add_argument("--audit-only", action="store_true")
    ap.add_argument("--score", action="store_true",
                    help="read the filled sheet and score both mappings against it")
    args = ap.parse_args()
    if args.score:
        return score(args)
    print("=" * 78)
    print("CONFIRMING SAMPLE -- BUILD")
    print("=" * 78)
    print(f"  under test {CANDIDATE_APPLICABLE_FLIPS} at "
          f"{CONFIRM_SAMPLE_PER_TESTED_CLASS} each")
    print(f"  controls   {CONTROL_CLASSES} at {CONFIRM_SAMPLE_PER_CONTROL_CLASS} each")
    print(f"  BLIND: neither the rule's class nor either gate verdict is in the sheet.")
    return build(args)


if __name__ == "__main__":
    sys.exit(main())