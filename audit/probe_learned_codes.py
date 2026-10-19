#!/usr/bin/env python3
"""Learned development codes, FOR REVIEW ONLY (D-47). READ-ONLY. Changes no label.

Run on the GUARDED resolver output (v9, D-44). Evidence that code C names drug set S:
  (a) an agent resolved through an other name (it passed D-44's alias tests) whose own
      intervention name is the code C;
  (b) an agent resolved by its own name, one of whose other names is the code C.
A code is kept only when its compact key has CODE shape, it does not already resolve,
every piece of evidence names the SAME drug set, and at least MIN_TRIALS distinct trials
give that evidence. The market-fixes probe showed why: unguarded other names learned
'camrelizumab -> apatinib' and 'tqb2450 -> carboplatin'.

Prints every kept mapping (for the owner to read), the conflicts, and what the kept ones
would resolve, including in W=5 unresolved market trials. Nothing is switched on here.

Usage (PowerShell):
  python audit\\probe_learned_codes.py |
      Out-File -Encoding utf8 probe_learned_codes_20261001.txt
  (one line in PowerShell)
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT / "src", ROOT / "scripts"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from audit_posting_bias import read_csv  # noqa: E402
from build_targets import OUT_TARGETS, market_columns  # noqa: E402
from resolve_trial_drugs import (  # noqa: E402
    DC_MANIFEST, DC_NEED, FDA_APPLICATIONS, FDA_PRODUCTS, OUT_AGENTS, PURPLEBOOK_FILE,
    load_fda, read_rows,
)
from trial_pos.services.drug_dictionary import (  # noqa: E402
    HIT_NONE, build_dictionary, parse_struct_id,
)
from trial_pos.services.drug_names import compact, key  # noqa: E402
from trial_pos.services.drug_resolution import (  # noqa: E402
    AGENT_RESOLVED, DRUG_SEP, NAME_OTHER, NAME_PRIMARY,
)
from trial_pos.services.market_label import REASON_PARTIAL_ONLY, REASON_UNRESOLVED  # noqa: E402
from trial_pos.services.purplebook import read_purplebook  # noqa: E402

csv.field_size_limit(10 ** 9)

MIN_TRIALS = 2                     # D-47: distinct trials that must agree
WINDOW = 5                         # D-36 headline
DEFAULT_SHOW = 150
CODE_PREFIX_MAX = 5
_CODE = re.compile(r"^[a-z]{1,%d}[\s\-]?\d{2,}[a-z]?$" % CODE_PREFIX_MAX)   # the WHOLE name


def code_key(name: str):
    """The compact key of a name that is, as a whole, a development code; else None."""
    k = key(name)
    return compact(k) if _CODE.match(k) else None


def main() -> int:
    sys.stdout.reconfigure(errors="backslashreplace")
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--aact-dir", type=Path, default=Path("data") / "aact")
    ap.add_argument("--dc-dir", type=Path, default=Path("data") / "drugcentral")
    ap.add_argument("--fda-dir", type=Path, default=Path("data") / "drugsatfda")
    ap.add_argument("--labels-dir", type=Path, default=Path("data") / "labels")
    ap.add_argument("--purplebook", type=Path,
                    default=Path("data") / "purplebook" / PURPLEBOOK_FILE)
    ap.add_argument("--show", type=int, default=DEFAULT_SHOW)
    args = ap.parse_args()
    print(f"min trials {MIN_TRIALS} | code: whole name, letter prefix <= {CODE_PREFIX_MAX} then "
          f"2+ digits | window {WINDOW}")

    m = json.loads((args.dc_dir / DC_MANIFEST).read_text(encoding="utf-8"))
    dc = {t: list(read_rows(args.dc_dir / m["tables"][t]["file"], n)) for t, n in DC_NEED.items()}
    with args.purplebook.open(encoding="utf-8", newline="") as f:
        pb_rows, _a = read_purplebook(csv.DictReader(f))
    d = build_dictionary(dc["synonyms"], dc["structures"], dc["struct2parent"],
                         dc["ob_product"], dc["struct2obprod"],
                         load_fda(args.fda_dir, FDA_PRODUCTS),
                         load_fda(args.fda_dir, FDA_APPLICATIONS), pb_rows)
    name_of = {parse_struct_id(r["id"]): r["name"] for r in dc["structures"]}
    name_of.update(d.pb_names)
    iname = {(r["nct_id"], r["id"]): r.get("name", "")
             for r in read_csv(args.aact_dir / "rows__interventions.csv")}
    others = defaultdict(list)
    for r in read_csv(args.aact_dir / "rows__intervention_other_names.csv"):
        others[(r["nct_id"], r["intervention_id"])].append(r["name"])
    agents = list(read_csv(args.labels_dir / OUT_AGENTS))

    evidence = defaultdict(lambda: defaultdict(set))       # code -> drug set -> {nct}
    for r in agents:
        if r["agent_status"] != AGENT_RESOLVED:
            continue
        drugs = frozenset(int(x) for x in r["drugs"].split(DRUG_SEP) if x)
        k = (r["nct_id"], r["intervention_id"])
        codes = []
        if r["name_kind"] == NAME_OTHER:
            codes.append(code_key(iname.get(k, "")))
        elif r["name_kind"] == NAME_PRIMARY:
            codes.extend(code_key(o) for o in others.get(k, ()))
        for c in codes:
            if c:
                evidence[c][drugs].add(r["nct_id"])
    kept, conflicts, too_few, already = {}, {}, 0, 0
    for c, sets in evidence.items():
        if d.resolve(c)[0].status != HIT_NONE:
            already += 1
            continue
        if len(sets) > 1:
            conflicts[c] = sets
            continue
        (s, ncts), = sets.items()
        if len(ncts) < MIN_TRIALS:
            too_few += 1
            continue
        kept[c] = (s, len(ncts))
    print(f"\n  codes with evidence {len(evidence)}; already resolve {already}; conflicting "
          f"{len(conflicts)}; fewer than {MIN_TRIALS} trials {too_few}; KEPT {len(kept)}")

    def names(s):
        return " + ".join(sorted(name_of.get(x, str(x)) for x in s))

    targets = {r["nct_id"]: r for r in read_csv(args.labels_dir / OUT_TARGETS)}
    _vc, rc, _lc = market_columns(WINDOW)
    would, would_market, examples = 0, set(), Counter()
    for r in agents:
        if r["agent_status"] == AGENT_RESOLVED:
            continue
        c = code_key(r["name_used"])
        if c in kept:
            would += 1
            examples[c] += 1
            if targets.get(r["nct_id"], {}).get(rc) in (REASON_UNRESOLVED, REASON_PARTIAL_ONLY):
                would_market.add(r["nct_id"])
    print(f"  not-resolved agents a kept code would resolve: {would}; W={WINDOW} unresolved "
          f"market trials touched: {len(would_market)}")

    print(f"\n  KEPT MAPPINGS FOR REVIEW, top {args.show} by trials (code | drugs | trials | "
          "agents it would resolve):")
    for c, (s, n) in sorted(kept.items(), key=lambda kv: (-kv[1][1], kv[0]))[:args.show]:
        print(f"    {c:16s} {names(s)[:50]:50s} {n:4d}  {examples[c]:4d}")
    print(f"\n  CONFLICTS, top {args.show // 3} by evidence (code | drug sets x trials):")
    ranked = sorted(conflicts.items(),
                    key=lambda kv: (-sum(len(v) for v in kv[1].values()), kv[0]))
    for c, sets in ranked[:args.show // 3]:
        top = sorted(sets.items(), key=lambda kv: (-len(kv[1]), sorted(kv[0])))[:3]
        desc = "; ".join(f"{names(s)[:28]} x{len(v)}" for s, v in top)
        print(f"    {c:16s} {desc}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
