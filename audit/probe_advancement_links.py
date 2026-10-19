#!/usr/bin/env python3
"""Advancement: what links a trial to its programme's next step? READ-ONLY. SCRATCH.

Compares two linkage keys on one population, before the label is built (rev 7 section 1.3
item 1, open):
  NAME    the registry's own tested-agent names and code names (compact; also with dose
          and form words removed). Independent of any later outcome.
  MOIETY  the DrugCentral drug of the resolved tested agents. Inherits the market finding:
          DrugCentral mostly holds drugs approved somewhere, so a never-approved programme
          often has no key at all.

Population (FOCAL): early-phase drug trials (eligibility.EARLY_PHASES) eligible for
advancement at W years (actual readout, window closed, prospective registration).
A focal trial is LINKED when a later drug trial of a strictly higher phase shares a key and
was first submitted to the registry between the focal trial's own first submission and its
readout + W. The registration date is used because it is always recorded and never a plan.
FLOOR: a higher-phase trial sharing a key was registered BEFORE the focal trial; its label
would be known in advance (the market floor's principle).

Sections:
  1. per key: focal trials with no key / floor / linked / linked with a shared condition
     (a MeSH 'mesh-list' condition term in common)
  2. NAME-key link rates split by whether the trial is MOIETY-resolvable: if unresolvable
     trials link far less, the moiety key would have dropped exactly the non-advancers
  3. the two keys' agreement where both exist
  4. the NAME keys shared by the most trials (collisions: 'chemotherapy' would link
     unrelated programmes) and linked examples with no moiety

Usage (PowerShell):
  python audit\\probe_advancement_links.py --snapshot 2026-10-01 |
      Out-File -Encoding utf8 probe_advancement_links_20261001.txt
  (one line in PowerShell)
"""
from __future__ import annotations

import argparse
import csv
import sys
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT / "src", ROOT / "scripts"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from audit_posting_bias import load_submitted_dates, read_csv  # noqa: E402
from resolve_trial_drugs import OUT_AGENTS  # noqa: E402
from trial_pos.services.drug_names import compact, drop_dose_and_form, key  # noqa: E402
from trial_pos.services.drug_resolution import AGENT_RESOLVED, DRUG_SEP  # noqa: E402
from trial_pos.services.eligibility import (  # noqa: E402
    DEFAULT_ADVANCEMENT_WINDOW_YEARS, ELIGIBLE, EARLY_PHASES, eligible_for_advancement,
    merge_registration_timing, normalize_phase, readout_date, years_after,
)
from trial_pos.services.population import parse_date, tribool  # noqa: E402

csv.field_size_limit(10 ** 9)

DEFAULT_SHOW = 20
# Strictly higher rank = a later rung. Combined phases sit between their two halves.
PHASE_RANK = {"EARLY_PHASE1": 0, "PHASE1": 1, "PHASE1/PHASE2": 2, "PHASE2": 3,
              "PHASE2/PHASE3": 4, "PHASE3": 5, "PHASE4": 6}
# D-20: a compact key this short is an abbreviation, never a linkage key.
MIN_NAME_KEY = 4
MESH_LIST = "mesh-list"
SUBMITTED_COLUMN = "studies__study_first_submitted_date"
CONDITIONS_FILE = "rows__browse_conditions.csv"
KEYS = ("name", "moiety")
NO_KEY, FLOOR, LINKED, LINKED_COND, NOT_LINKED = (
    "no key", "floor: a higher phase registered first", "linked",
    "linked, sharing a condition", "not linked")


def _rule(t: str) -> None:
    print("\n" + "=" * 78 + f"\n{t}\n" + "=" * 78)


def _pct(n, d) -> str:
    return "undefined" if not d else f"{100.0 * n / d:.1f}%"


def name_keys(name: str) -> set:
    """The compact name, and the compact name without dose and form words. Both, because
    dose stripping also removes a spaced code's number ('xyz 123' -> 'xyz')."""
    base = key(name)
    return {k for k in (compact(base), compact(drop_dose_and_form(base)))
            if len(k) >= MIN_NAME_KEY}


def main() -> int:
    sys.stdout.reconfigure(errors="backslashreplace")
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--snapshot", required=True)
    ap.add_argument("--window", type=int, default=DEFAULT_ADVANCEMENT_WINDOW_YEARS)
    ap.add_argument("--aact-dir", type=Path, default=Path("data") / "aact")
    ap.add_argument("--labels-dir", type=Path, default=Path("data") / "labels")
    ap.add_argument("--show", type=int, default=DEFAULT_SHOW)
    args = ap.parse_args()
    snapshot = date.fromisoformat(args.snapshot)
    print(f"snapshot {snapshot} | window {args.window}y | min name key {MIN_NAME_KEY} | "
          f"phase ranks {PHASE_RANK}")

    _rule("INPUTS")
    submitted_raw = load_submitted_dates(args.aact_dir / "trial_registration_fields.csv")
    trials = {}
    focal = []
    for row in merge_registration_timing(read_csv(args.aact_dir / "trial_labels.csv"),
                                         submitted_raw):
        if tribool(row.get("is_drug_trial")) is not True:
            continue
        nct = row["nct_id"]
        phase = normalize_phase(row.get("phase"))
        sub = parse_date(row.get("study_first_submitted_date"))
        trials[nct] = {"rank": PHASE_RANK.get(phase), "submitted": sub,
                       "readout": readout_date(row), "names": set(), "moieties": set(),
                       "mesh": set()}
        if phase in EARLY_PHASES and sub is not None and \
                eligible_for_advancement(row, snapshot, args.window)["verdict"] == ELIGIBLE:
            focal.append(nct)
    for r in read_csv(args.labels_dir / OUT_AGENTS):
        t = trials.get(r["nct_id"])
        if t is None:
            continue
        t["names"] |= name_keys(r["name_used"])
        if r["agent_status"] == AGENT_RESOLVED:
            t["moieties"] |= {x for x in r["drugs"].split(DRUG_SEP) if x}
    cond_path = args.aact_dir / CONDITIONS_FILE
    for r in read_csv(cond_path):
        t = trials.get(r["nct_id"])
        if t is not None and r.get("mesh_type") == MESH_LIST:
            t["mesh"].add(r["downcase_mesh_term"])
    by_key = {k: defaultdict(list) for k in KEYS}
    for nct, t in trials.items():
        if t["rank"] is None or t["submitted"] is None:
            continue
        for k in t["names"]:
            by_key["name"][k].append(nct)
        for k in t["moieties"]:
            by_key["moiety"][k].append(nct)
    print(f"  drug trials {len(trials)}; focal (early phase, advancement-eligible at "
          f"{args.window}y) {len(focal)}")

    def classify(nct: str, kind: str) -> str:
        t = trials[nct]
        keys = t["names"] if kind == "name" else t["moieties"]
        if not keys:
            return NO_KEY
        end = years_after(t["readout"], args.window)
        linked = cond = False
        for k in keys:
            for other in by_key[kind][k]:
                o = trials[other]
                if other == nct or o["rank"] <= t["rank"]:
                    continue
                if o["submitted"] < t["submitted"]:
                    return FLOOR
                if o["submitted"] <= end:
                    linked = True
                    cond = cond or bool(t["mesh"] & o["mesh"])
        return LINKED_COND if cond else LINKED if linked else NOT_LINKED

    result = {k: {n: classify(n, k) for n in focal} for k in KEYS}

    _rule("1. FOCAL TRIALS BY KEY")
    order = (NO_KEY, FLOOR, LINKED_COND, LINKED, NOT_LINKED)
    for k in KEYS:
        c = Counter(result[k].values())
        keyed = len(focal) - c[NO_KEY] - c[FLOOR]
        print(f"\n  {k}:")
        for s in order:
            print(f"    {s:42s} {c[s]:7d}  {_pct(c[s], len(focal))}")
        print(f"    link rate among keyed, not floor: any {_pct(c[LINKED] + c[LINKED_COND], keyed)}"
              f"; with a shared condition {_pct(c[LINKED_COND], keyed)}")

    _rule("2. NAME KEY, SPLIT BY WHETHER THE TRIAL HAS A MOIETY")
    for has in (True, False):
        group = [n for n in focal if bool(trials[n]["moieties"]) == has]
        c = Counter(result["name"][n] for n in group)
        keyed = len(group) - c[NO_KEY] - c[FLOOR]
        print(f"  moiety resolved {has}: {len(group)} trials; floor {c[FLOOR]}; "
              f"linked {_pct(c[LINKED] + c[LINKED_COND], keyed)}, with condition "
              f"{_pct(c[LINKED_COND], keyed)} of {keyed} keyed")

    _rule("3. AGREEMENT WHERE BOTH KEYS EXIST AND NEITHER IS FLOOR")
    both = Counter()
    for n in focal:
        a, b = result["name"][n], result["moiety"][n]
        if NO_KEY in (a, b) or FLOOR in (a, b):
            continue
        both[(a != NOT_LINKED, b != NOT_LINKED)] += 1
    for (a, b), v in sorted(both.items()):
        print(f"    name linked {a!s:5s} moiety linked {b!s:5s} {v:7d}")

    _rule("4. NAME-KEY COLLISIONS AND EXAMPLES")
    shared = Counter({k: len(v) for k, v in by_key["name"].items()})
    print(f"  name keys shared by the most drug trials, top {args.show}:")
    for k, v in sorted(shared.items(), key=lambda kv: (-kv[1], kv[0]))[:args.show]:
        print(f"    {v:6d}  {k}")
    ex = Counter()
    for n in focal:
        if result["name"][n] in (LINKED, LINKED_COND) and not trials[n]["moieties"]:
            for k in sorted(trials[n]["names"]):
                ex[k] += 1
    print(f"\n  linked by name with no moiety: most frequent keys, top {args.show}:")
    for k, v in sorted(ex.items(), key=lambda kv: (-kv[1], kv[0]))[:args.show]:
        print(f"    {v:6d}  {k}")
    return 0


if __name__ == "__main__":
    sys.exit(main())