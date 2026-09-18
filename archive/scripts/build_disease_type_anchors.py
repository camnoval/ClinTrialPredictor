#!/usr/bin/env python3
"""Build the disease-type-152 anchor crosswalk: Trialtrove label -> MONDO anchor id(s).

Step B of "disease-type-152 done properly" and the DE-RISK GATE: before building the MONDO
is_a graph (step A) and the ancestor rollup (step C), measure how many of DSAI's 152 disease
types map cleanly to a MONDO anchor using only data we already have (the spec CSV's label list
+ mondo_xref). The audit's resolution rate is the ceiling on faithful dt-152 coverage.

Matching is exact against normalized MONDO names+synonyms (see disease_type.resolve_label) --
literal variants first, then oncology-suffix augmentation for organ-site labels. Nothing is
force-matched: unresolved labels are written with empty anchors and printed, so they can be
hand-curated into --curated before step C rather than silently guessed.

Inputs:
  --spec     docs/DSAI_feature_spec.csv        (source of the 150/152 disease-type labels)
  --xref     data/ontology/mondo_xref.csv      (fetch_mondo.py)
  --curated  optional CSV (disease_id,label,mondo_anchors) to OVERRIDE/fill specific labels
Output data/ontology/disease_type_anchors.csv: disease_id, label, mondo_anchors('|'), method,
matched_variant.  Deps: pandas.
Usage:
  python scripts/build_disease_type_anchors.py --spec docs/DSAI_feature_spec.csv \
      --xref data/ontology/mondo_xref.csv
"""
from __future__ import annotations

import argparse
import csv
import re
from pathlib import Path

from trial_pos.services import disease_type as DT

_DISEASE_ID = re.compile(r"disease\d+", re.I)


def _labels_from_spec(spec: Path) -> list[tuple[str, str]]:
    out = []
    with spec.open(encoding="utf-8") as f:
        for r in csv.reader(f):
            if r and _DISEASE_ID.fullmatch(r[0].strip()):
                cells = [c for c in r if c.strip()]
                out.append((r[0].strip(), cells[-1].strip().strip('"')))
    return out


def _pct(n, d):
    return f"{100.0 * n / d:.1f}%" if d else "n/a"


def main() -> int:
    import pandas as pd

    ap = argparse.ArgumentParser()
    ap.add_argument("--spec", default=Path("docs/DSAI_feature_spec.csv"), type=Path)
    ap.add_argument("--xref", default=Path("data/ontology/mondo_xref.csv"), type=Path)
    ap.add_argument("--curated", default=None, type=Path,
                    help="CSV disease_id,label,mondo_anchors('|') to override/fill labels")
    ap.add_argument("--aliases", default=Path("docs/disease_type_aliases.csv"), type=Path,
                    help="CSV disease_id,label,alias[,reason]: alias search-string tried when "
                         "the literal resolver misses (rows with empty alias are intentional non-anchors)")
    ap.add_argument("--out", default=Path("data/ontology/disease_type_anchors.csv"), type=Path)
    args = ap.parse_args()

    print(f"{'='*70}\nDISEASE-TYPE-152 ANCHOR CROSSWALK  (de-risk gate)\n{'='*70}")
    if not args.spec.exists():
        print(f"!! spec not found: {args.spec}")
        return 2
    labels = _labels_from_spec(args.spec)
    print(f"  disease-type labels in spec: {len(labels)} (of 152)")
    if not args.xref.exists():
        print(f"!! mondo_xref not found: {args.xref} -- run fetch_mondo.py first.")
        return 2

    xref = pd.read_csv(args.xref).fillna("")
    name_index = DT.build_name_index(xref.to_dict("records"))
    print(f"  MONDO name+synonym index entries: {len(name_index)}")

    curated = {}
    if args.curated and args.curated.exists():
        cur = pd.read_csv(args.curated).fillna("")
        for _, r in cur.iterrows():
            curated[str(r["disease_id"])] = set(filter(None, str(r["mondo_anchors"]).split("|")))
        print(f"  curated overrides supplied: {len(curated)}")

    aliases, intentional = {}, {}
    if args.aliases and args.aliases.exists():
        al = pd.read_csv(args.aliases).fillna("")
        for _, r in al.iterrows():
            did, alias = str(r["disease_id"]), str(r.get("alias", "")).strip()
            if alias:
                aliases[did] = alias
            else:
                intentional[did] = str(r.get("reason", "")).strip()
        print(f"  aliases supplied: {len(aliases)} search-strings, "
              f"{len(intentional)} intentional non-anchors")

    methods = {"exact": 0, "onc-aug": 0, "alias": 0, "curated": 0, "intentional": 0, "unresolved": 0}
    ambiguous = 0
    rows, unresolved = [], []
    for did, label in labels:
        var = ""
        if did in curated:
            anchors, method = curated[did], "curated"
        else:
            anchors, method, var = DT.resolve_label(label, name_index)
            if method == "unresolved" and did in aliases:            # literal missed -> try alias
                a_anchors, a_method, a_var = DT.resolve_label(aliases[did], name_index)
                if a_anchors:
                    anchors, method, var = a_anchors, "alias", f"{aliases[did]} -> {a_var}"
            if method == "unresolved" and did in intentional:        # documented non-anchor
                method = "intentional"
        methods[method] += 1
        if len(anchors) > 1:
            ambiguous += 1
        if method == "unresolved":
            unresolved.append((did, label))
        rows.append({"disease_id": did, "label": label,
                     "mondo_anchors": "|".join(sorted(anchors)), "method": method,
                     "matched_variant": var})

    args.out.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(args.out, index=False)

    tot = len(labels)
    anchored = methods["exact"] + methods["onc-aug"] + methods["alias"] + methods["curated"]
    print(f"\n{'='*70}\nRESOLUTION AUDIT (this rate caps faithful dt-152 coverage)\n{'='*70}")
    print(f"  anchored to a MONDO term   : {anchored}/{tot} ({_pct(anchored, tot)})")
    for m in ("exact", "onc-aug", "alias", "curated"):
        print(f"    via {m:9s}: {methods[m]}")
    print(f"  intentional non-anchors    : {methods['intentional']}  "
          "(vaccine-target categories + Trialtrove catch-alls + non-diseases)")
    print(f"  ambiguous (>1 anchor)      : {ambiguous}  (kept; rollup will union descendants)")
    print(f"  STILL UNRESOLVED           : {methods['unresolved']}")
    if unresolved:
        print("  -- still unresolved (alias missed -- refine docs/disease_type_aliases.csv):")
        for did, label in unresolved:
            print(f"       {did:11s} {label}")
    covered = anchored + methods["intentional"]
    print(f"\n  accounted-for (anchored + intentional): {covered}/{tot} ({_pct(covered, tot)})")
    print(f"\n  wrote -> {args.out}")
    print("  next (step A): build_mondo_edges.py -> mondo_edges.csv (is_a graph from mondo.obo);")
    print("  then (step C): assign_disease_type.py rolls each unit up to these anchors -> dt_key.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())