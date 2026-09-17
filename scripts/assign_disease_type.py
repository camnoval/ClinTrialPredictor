#!/usr/bin/env python3
"""Step C of disease-type-152: assign multi-hot disease types to each unit by MONDO rollup.

Resolves each unit's indication to a MONDO term (shared resolver, same as assign_disease_ta),
walks UP the is_a DAG (mondo_edges.csv), and tags every disease-type whose anchor
(disease_type_anchors.csv) is the term itself OR any ancestor. Multi-hot, tagging ALL matching
anchors at every depth (a specific leukemia tags both its acute-subtype anchor and any broader
leukemia anchor) -- "keep everything, combine later". Merges dt_key into unit_class.csv,
preserving the ta_key from assign_disease_ta.

This is the payoff step: its dt coverage + how the oncology mass splits across types is what
decides whether disease-conditioning is worth carrying past the mechanism-only 0.70 prior.

Inputs:
  --units    data/chembl/units.csv               (unit_key + unit_indication)
  --xref     data/ontology/mondo_xref.csv         (resolution)
  --edges    data/ontology/mondo_edges.csv        (build_mondo_edges.py)
  --anchors  data/ontology/disease_type_anchors.csv (build_disease_type_anchors.py)
  --unit-class data/chembl/unit_class.csv          (updated in place: dt_key filled, ta_key kept)
Deps: pandas. Usage:
  python scripts/assign_disease_type.py --units data/chembl/units.csv \
      --xref data/ontology/mondo_xref.csv --edges data/ontology/mondo_edges.csv \
      --anchors data/ontology/disease_type_anchors.csv
"""
from __future__ import annotations

import argparse
from collections import Counter
from pathlib import Path

from trial_pos.services.disease_ta import build_mondo_resolver
from trial_pos.services.obo_edges import build_parent_map, ancestors


def _pct(n, d):
    return f"{100.0 * n / d:.1f}%" if d else "n/a"


def main() -> int:
    import pandas as pd

    ap = argparse.ArgumentParser()
    ap.add_argument("--units", default=Path("data/chembl/units.csv"), type=Path)
    ap.add_argument("--xref", default=Path("data/ontology/mondo_xref.csv"), type=Path)
    ap.add_argument("--edges", default=Path("data/ontology/mondo_edges.csv"), type=Path)
    ap.add_argument("--anchors", default=Path("data/ontology/disease_type_anchors.csv"), type=Path)
    ap.add_argument("--unit-class", default=Path("data/chembl/unit_class.csv"), type=Path)
    ap.add_argument("--max-hops", type=int, default=40)
    args = ap.parse_args()

    print(f"{'='*70}\nDISEASE-TYPE-152 ASSIGNMENT (step C: MONDO rollup)\n{'='*70}")
    for p in (args.units, args.xref, args.edges, args.anchors):
        if not p.exists():
            print(f"!! missing input: {p}")
            return 2

    resolve = build_mondo_resolver(pd.read_csv(args.xref).fillna("").to_dict("records"))
    edges = pd.read_csv(args.edges)
    parent_map = build_parent_map(zip(edges["child_mondo_id"], edges["parent_mondo_id"]))
    print(f"  parent map: {len(parent_map)} child terms")

    # anchor MONDO id -> set(disease_id label); only real anchors (skip intentional/unresolved)
    anchors_df = pd.read_csv(args.anchors).fillna("")
    anchor2types: dict[str, set] = {}
    label_of = {}
    for _, r in anchors_df.iterrows():
        if r["method"] in ("intentional", "unresolved") or not str(r["mondo_anchors"]):
            continue
        label_of[r["disease_id"]] = r["label"]
        for a in str(r["mondo_anchors"]).split("|"):
            if a:
                anchor2types.setdefault(a, set()).add(r["disease_id"])
    print(f"  anchor terms: {len(anchor2types)} MONDO ids -> {len(label_of)} disease types")

    u = pd.read_csv(args.units)
    u["unit_key"] = u["unit_mols"].astype(str) + " :: " + u["unit_indication"].astype(str)
    units = u.drop_duplicates("unit_key")[["unit_key", "unit_indication"]]

    dt_by_key = {}
    n_mondo = n_dt = 0
    ndt_hist = Counter()
    type_counts = Counter()
    for _, r in units.iterrows():
        mondo, _path = resolve(r["unit_indication"])
        if mondo:
            n_mondo += 1
        anc = ancestors(mondo, parent_map, max_hops=args.max_hops) if mondo else set()
        types = set()
        for a in anc:
            types |= anchor2types.get(a, set())
        if types:
            n_dt += 1
        ndt_hist[min(len(types), 4)] += 1
        for t in types:
            type_counts[t] += 1
        dt_by_key[r["unit_key"]] = "|".join(sorted(types))

    # merge into unit_class.csv (fill dt_key, keep ta_key/ta_count)
    if args.unit_class.exists():
        uc = pd.read_csv(args.unit_class).fillna("")
    else:
        uc = pd.DataFrame({"unit_key": list(dt_by_key), "ta_key": "", "ta_count": 0})
    uc["dt_key"] = uc["unit_key"].map(dt_by_key).fillna("")
    uc["dt_count"] = uc["dt_key"].map(lambda s: 0 if not s else len(s.split("|")))
    uc.to_csv(args.unit_class, index=False)

    tot = len(units)
    print(f"\n{'='*70}\nDISEASE-TYPE COVERAGE (the payoff audit)\n{'='*70}")
    print(f"  units resolved to MONDO    : {n_mondo}/{tot} ({_pct(n_mondo, tot)})")
    print(f"  units with >=1 disease-type: {n_dt}/{tot} ({_pct(n_dt, tot)})   <- dt coverage")
    print("  #disease-types per unit    : " +
          "  ".join(f"{k if k < 4 else '4+'}:{ndt_hist[k]}" for k in sorted(ndt_hist)))
    print("  top disease-types by unit count (does the oncology mass split?):")
    for did, c in type_counts.most_common(15):
        print(f"    {did:11s} {label_of.get(did,'?')[:34]:34s}: {c:5d} ({_pct(c, tot)})")
    print(f"\n  merged dt_key into -> {args.unit_class}")
    print("  next (step D): re-run build_moa_target_encoding.py --unit-class -> `dt` lights up;")
    print("  read the dt-vs-any AUROC to see if splitting oncology beats the 0.70 mechanism prior.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())