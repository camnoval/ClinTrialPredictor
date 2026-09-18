#!/usr/bin/env python3
"""Audit the two repairable gaps in the endpoint-met label. Read-only, offline.

Step 1 of 3 agreed after the first pull. Reads the raw dumps written by
pull_aact_results.py -- no database query, nothing overwritten -- and answers two
questions the pull raised:

  1. why_stopped strings classified 'other' (190 trials, 3.7%) or 'operational'
     (634, 12.4%): does any of that text actually describe futility, i.e. is the
     broad label leaving negatives on the table? Text containing efficacy-adjacent
     vocabulary but NOT classified futility/efficacy_success is printed verbatim,
     since that is the candidate-miss list and it has to be read rather than counted.

  2. Analysis rows that carried data but produced no verdict (53 trials' worth) plus
     the 1,591 trials that posted primary outcomes with no analysis rows at all.
     Grouped by param_type, so the parameter vocabulary the tier-C null rule does not
     recognise becomes a list rather than a suspicion. Also splits tier D by arm count,
     which tests the assumption that tier D is mostly single-arm trials.

Every number here is a coverage question: how much of the 73.8% tier-D population is
genuinely unlabelable versus merely unrecognised by the current rules. Nothing is
repaired automatically -- the output is for reading, then the keyword lists in
endpoint_label.py get edited deliberately and the tests updated with them.

Usage (PowerShell):
  python scripts\\audit_label_gaps.py
  python scripts\\audit_label_gaps.py --show 40 --review data\\aact\\label_gap_review.csv
"""
from __future__ import annotations

import argparse
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

# Make src/ importable without depending on PYTHONPATH being set in the session.
# `pip install -e .` is the durable fix and makes this a no-op; this guard means the
# script still runs in a fresh shell, or in a conda env where the package was never
# installed. Matches validate_reconstruction.py and pull_aact_results.py.
_SRC = Path(__file__).resolve().parents[1] / "src"
if _SRC.is_dir() and str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from trial_pos.services.endpoint_label import (
    DEFAULT_ALPHA, TIER_D, classify_analysis, classify_stop_reason, met_from_ci,
    met_from_p, null_value_for, parse_p_value,
)

# Vocabulary that *might* indicate an efficacy-driven stop. Deliberately broad and
# deliberately NOT used for classification -- it only selects text for human reading.
# A term here is a reason to look, not a reason to relabel.
EFFICACY_HINTS = (
    "efficac", "endpoint", "end point", "futil", "benefit", "interim", "dsmb", "dmc",
    "response rate", "no improvement", "ineffective", "primary outcome", "not superior",
    "did not", "failed", "negative", "unlikely", "boundary", "stopping rule",
)


def _scalar(v):
    """None for anything blank-ish. pandas reads empty CSV cells as float NaN, which is
    truthy -- the same trap that once made every missing posting date look posted."""
    if v is None:
        return None
    if isinstance(v, float) and v != v:
        return None
    s = str(v).strip()
    if not s or s.lower() in ("nan", "none", "null", "na", "nat"):
        return None
    return v


def _rule(title):
    return f"\n{'=' * 74}\n{title}\n{'=' * 74}"


def _pct(n, d):
    return f"{100.0 * n / d:.1f}%" if d else "n/a"


def _trunc(s, w):
    s = re.sub(r"\s+", " ", str(s)).strip()
    return s if len(s) <= w else s[: w - 1] + "\u2026"


def load(prefix: Path, labels: Path):
    import pandas as pd
    s_path, o_path = Path(f"{prefix}_studies.csv"), Path(f"{prefix}_outcomes.csv")
    for p in (s_path, o_path):
        if not p.exists():
            raise SystemExit(f"!! missing {p} -- run pull_aact_results.py first")
    studies = pd.read_csv(s_path, low_memory=False)
    orows = pd.read_csv(o_path, low_memory=False)
    lab = pd.read_csv(labels) if labels.exists() else None
    return studies, orows, lab


# ---- gap 1: stop reasons --------------------------------------------------
def audit_stop_reasons(studies, show: int, review_rows: list) -> None:
    print(_rule("GAP 1: why_stopped text that may describe futility"))
    texts = [(_scalar(r.get("nct_id")), _scalar(r.get("why_stopped")))
             for _, r in studies.iterrows()]
    stated = [(n, t) for n, t in texts if t]
    print(f"  trials with a why_stopped value : {len(stated)}/{len(texts)} "
          f"({_pct(len(stated), len(texts))})")

    by_class = defaultdict(list)
    for nct, t in stated:
        by_class[classify_stop_reason(t)].append((nct, t))
    print("\n  current classification:")
    for k, v in sorted(by_class.items(), key=lambda kv: -len(kv[1])):
        print(f"    {k:18s}: {len(v):5d} ({_pct(len(v), len(stated))} of stated)")

    # the candidate-miss list: efficacy vocabulary present, not classified as such
    suspects = []
    for cls, items in by_class.items():
        if cls in ("futility", "efficacy_success"):
            continue
        for nct, t in items:
            low = t.lower()
            hits = [h for h in EFFICACY_HINTS if h in low]
            if hits:
                suspects.append((cls, nct, t, hits))
    print(f"\n  CANDIDATE MISSES: {len(suspects)} trials carry efficacy-adjacent")
    print("  vocabulary but are not classified futility/efficacy_success.")
    print("  These need reading -- 'did not' and 'failed' also appear in 'failed to")
    print("  enrol', which is operational, so the list is a prompt, not a verdict.\n")
    for cls, nct, t, hits in suspects[:show]:
        print(f"    [{cls:11s}] {nct}  hits={','.join(hits[:3])}")
        print(f"                  {_trunc(t, 150)}")
    if len(suspects) > show:
        print(f"    ... ({len(suspects) - show} more; raise --show or read the review CSV)")
    for cls, nct, t, hits in suspects:
        review_rows.append({"gap": "stop_reason", "nct_id": nct, "current_class": cls,
                            "hits": "|".join(hits), "text": _trunc(t, 400)})

    # distinct strings in 'other', which is where a genuinely new pattern would hide
    others = Counter(_trunc(t.lower(), 90) for _, t in by_class.get("other", []))
    print(f"\n  distinct strings currently classified 'other' "
          f"({len(others)} distinct / {sum(others.values())} trials), most common:")
    for text, n in others.most_common(show):
        print(f"    {n:4d}x  {text}")


# ---- gap 2: undecidable analyses -----------------------------------------
def flatten_analyses(orows):
    """{nct_id: {outcome_id: [analysis dicts]}} with AACT field names restored."""
    by_trial: dict[str, dict[str, list[dict]]] = defaultdict(lambda: defaultdict(list))
    for _, r in orows.iterrows():
        nct = _scalar(r.get("outcome_nct_id"))
        oid = _scalar(r.get("outcome_id"))
        if not nct or oid is None:
            continue
        a = {k[len("analysis_"):]: _scalar(v) for k, v in r.items()
             if str(k).startswith("analysis_")}
        if a.get("id") is None and a.get("p_value") is None \
                and a.get("ci_lower_limit") is None:
            by_trial[str(nct).upper()].setdefault(str(oid), [])
        else:
            by_trial[str(nct).upper()][str(oid)].append(a)
    return by_trial


def audit_undecidable(by_trial, studies, alpha, show, review_rows) -> None:
    print(_rule("GAP 2: analysis rows that carried data but produced no verdict"))
    undecided, decided, empty_outcomes = [], 0, 0
    for nct, outcomes in by_trial.items():
        for oid, analyses in outcomes.items():
            if not analyses:
                empty_outcomes += 1
                continue
            for a in analyses:
                tier, met, reason = classify_analysis(a, alpha)
                if met is None:
                    undecided.append((nct, a, reason))
                else:
                    decided += 1
    total = decided + len(undecided)
    print(f"  analysis rows with data      : {total}")
    print(f"    decided                    : {decided} ({_pct(decided, total)})")
    print(f"    UNDECIDED                  : {len(undecided)} "
          f"({_pct(len(undecided), total)})")
    print(f"  primary outcomes with NO analysis row at all: {empty_outcomes}")

    # why did each undecided row fail? the three causes need different fixes
    causes = Counter()
    for _, a, _ in undecided:
        op, val = parse_p_value(a.get("p_value"), a.get("p_value_modifier"))
        if op is not None and met_from_p(op, val, alpha)[0] is None:
            causes["p-value present but straddles alpha (e.g. 'p<0.5')"] += 1
        elif op is None and a.get("p_value") is not None:
            causes["p-value present but unparseable"] += 1
        elif null_value_for(a.get("param_type")) is None \
                and a.get("ci_lower_limit") is not None:
            causes["CI present, param_type null value unknown -> FIXABLE"] += 1
        elif a.get("ci_lower_limit") is None:
            causes["no p-value and no CI -> genuinely undecidable"] += 1
        else:
            causes["other"] += 1
    print("\n  cause breakdown:")
    for k, n in causes.most_common():
        print(f"    {n:6d}  {k}")

    # the fixable bucket, by parameter type -- this is the tier-C vocabulary gap
    fixable = defaultdict(list)
    for nct, a, _ in undecided:
        if null_value_for(a.get("param_type")) is None \
                and a.get("ci_lower_limit") is not None:
            fixable[str(a.get("param_type"))].append(nct)
    if fixable:
        print(f"\n  UNRECOGNISED param_type values with intervals "
              f"({len(fixable)} distinct):")
        print("  For each, decide the null: ratio-like -> 1, difference-like -> 0,")
        print("  single-arm quantity -> no null exists and the row stays tier D.")
        for pt, ncts in sorted(fixable.items(), key=lambda kv: -len(kv[1]))[:show]:
            print(f"    {len(ncts):5d} rows / {len(set(ncts)):5d} trials  {_trunc(pt, 46)}")
            review_rows.append({"gap": "param_type", "nct_id": "", "current_class": pt,
                                "hits": f"{len(ncts)} rows/{len(set(ncts))} trials",
                                "text": ""})
        gain = len({n for ncts in fixable.values() for n in ncts})
        print(f"\n  upper bound on new trials labelled if every one of those got a null:")
        print(f"    {gain} trials -- and they would be TIER C, excluded from headline.")
        print("    So this repair widens the sensitivity analysis, not the headline.")

    # is tier D really single-arm? test it against number_of_arms
    print(_rule("Is tier D single-arm, as assumed?"))
    arms = {}
    for _, r in studies.iterrows():
        nct = _scalar(r.get("nct_id"))
        if nct:
            arms[str(nct).upper()] = _scalar(r.get("number_of_arms"))
    no_verdict = set()
    for nct, outcomes in by_trial.items():
        any_decided = any(
            classify_analysis(a, alpha)[1] is not None
            for al in outcomes.values() for a in al)
        if not any_decided:
            no_verdict.add(nct)
    posted_no_verdict = {n for n in no_verdict if by_trial[n]}
    dist = Counter()
    for n in posted_no_verdict:
        v = arms.get(n)
        try:
            k = int(float(v)) if v is not None else -1
        except (TypeError, ValueError):
            k = -1
        dist["1 arm" if k == 1 else ("unknown" if k < 0 else f"{min(k, 5)}+ arms"
                                     if k >= 5 else f"{k} arms")] += 1
    tot = sum(dist.values())
    print(f"  trials that posted primary outcomes but got NO verdict: {tot}")
    for k, n in sorted(dist.items(), key=lambda kv: -kv[1]):
        print(f"    {k:12s}: {n:5d} ({_pct(n, tot)})")
    print("  Multi-arm trials here did have a comparator and chose not to post an")
    print("  analysis. They are not unlabelable in principle -- they are unlabelable")
    print("  from structured fields, which is a different and more troubling fact.")


def main() -> int:
    import pandas as pd
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw-prefix", default=Path("data/aact/results_raw"), type=Path)
    ap.add_argument("--labels", default=Path("data/aact/trial_labels.csv"), type=Path)
    ap.add_argument("--review", default=Path("data/aact/label_gap_review.csv"), type=Path)
    ap.add_argument("--alpha", type=float, default=DEFAULT_ALPHA)
    ap.add_argument("--show", type=int, default=25, help="rows printed per section")
    args = ap.parse_args()

    print(_rule("LABEL GAP AUDIT (offline, read-only)"))
    studies, orows, lab = load(args.raw_prefix, args.labels)
    print(f"  studies rows                 : {len(studies)}")
    print(f"  outcome/analysis rows        : {len(orows)}")
    if lab is not None:
        n = len(lab)
        strict = int(lab["endpoint_met_strict"].notna().sum())
        print(f"  current strict coverage      : {strict}/{n} ({_pct(strict, n)})")

    review_rows: list[dict] = []
    audit_stop_reasons(studies, args.show, review_rows)
    by_trial = flatten_analyses(orows)
    audit_undecidable(by_trial, studies, args.alpha, args.show, review_rows)

    if review_rows:
        args.review.parent.mkdir(parents=True, exist_ok=True)
        pd.DataFrame(review_rows).to_csv(args.review, index=False)
        print(f"\n  full candidate list -> {args.review} ({len(review_rows)} rows)")
    print("\n  Nothing was changed. Read the output, then the keyword lists in")
    print("  endpoint_label.py get edited deliberately, with tests, and the label")
    print("  rebuilt via: python scripts\\pull_aact_results.py --from-raw "
          "data\\aact\\results_raw")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())