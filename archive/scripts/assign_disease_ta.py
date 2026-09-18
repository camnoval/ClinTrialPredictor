#!/usr/bin/env python3
"""Assign DSAI therapeutic areas (TA-9), multi-hot, to each drug-indication unit.

Next step after the MoA target encoding: produce the per-unit TA key that the MoA engine's
`ta` granularity (and, later, rel_ph2_size_ta) consume. Resolves each unit's indication
through the MONDO crosswalk to ICD-10 codes + disease names, then applies the pure TA-9
mapper (trial_pos.services.disease_ta). Multi-hot by design -- we keep every applicable TA.

Audit-first: open-data TA coverage is the unknown, so this reports the resolution path
(EFO / MeSH / name), how many units reach MONDO / ICD, the fraction getting >=1 TA, the
per-TA counts, and the #-TAs-per-unit distribution (which becomes `newtanos`). The zero-TA
fraction is expected and honest: TA-9 has no bucket for respiratory/digestive/blood/ENT.

Output data/chembl/unit_class.csv: unit_key, dt_key (EMPTY -- disease-type-152 is a later,
harder step), ta_key ('|'-separated multi-hot), ta_count. Feed it to the MoA builder with
--unit-class AFTER the engine multi-hot upgrade (the current single-key reader treats
'ta2|ta3' as one opaque token).

Inputs:
  --units  data/chembl/units.csv            (build_units.py)
  --xref   data/ontology/mondo_xref.csv     (fetch_mondo.py: mondo_id,name,exact_synonyms,
                                             efo,mesh,icd10,umls)
Deps: pandas.
Usage:
  python scripts/assign_disease_ta.py --units data/chembl/units.csv \
      --xref data/ontology/mondo_xref.csv
"""
from __future__ import annotations

import argparse
import re
from pathlib import Path

from trial_pos.services import disease_ta as D
from trial_pos.services.disease_ta import _norm

_MESH_RE = re.compile(r"^(MESH:)?[CD]\d{6}$", re.I)


def _load_xref(path):
    """Build reverse maps (efo/mesh/name -> mondo) and forward maps (mondo -> icd/names)."""
    import pandas as pd
    m = pd.read_csv(path).fillna("")
    efo2, mesh2, name2 = {}, {}, {}
    mondo2icd, mondo2names = {}, {}
    for _, r in m.iterrows():
        mid = r["mondo_id"]
        names = [r["name"], *r["exact_synonyms"].split("|")]
        mondo2names[mid] = {x for x in names if x}
        mondo2icd[mid] = {x for x in r["icd10"].split("|") if x}
        for x in r["efo"].split("|"):
            if x:
                efo2.setdefault(x.upper(), set()).add(mid)
        for x in r["mesh"].split("|"):
            if x:
                mesh2.setdefault(x.upper(), set()).add(mid)
        for nm in names:
            nn = _norm(nm)
            if nn:
                name2.setdefault(nn, set()).add(mid)
    return efo2, mesh2, name2, mondo2icd, mondo2names


def _resolve(key, efo2, mesh2, name2):
    """unit_indication key -> (set(mondo_id), path). Tries EFO/MONDO, then MeSH, then name."""
    k = str(key).strip()
    up = k.upper()
    if up.startswith("MONDO:"):
        return {k}, "mondo"
    if up in efo2:                       # EFO ids (ChEMBL efo_id), also Orphanet/HP/DOID if xref'd
        return set(efo2[up]), "efo"
    if _MESH_RE.match(k):
        mk = up.replace("MESH:", "")
        if mk in mesh2:
            return set(mesh2[mk]), "mesh"
    nn = _norm(k)
    if nn in name2:
        return set(name2[nn]), "name"
    return set(), "unresolved"


def _pct(n, d):
    return f"{100.0 * n / d:.1f}%" if d else "n/a"


def main() -> int:
    import pandas as pd

    ap = argparse.ArgumentParser()
    ap.add_argument("--units", default=Path("data/chembl/units.csv"), type=Path)
    ap.add_argument("--xref", default=Path("data/ontology/mondo_xref.csv"), type=Path)
    ap.add_argument("--out", default=Path("data/chembl/unit_class.csv"), type=Path)
    args = ap.parse_args()

    print(f"{'='*70}\nTA-9 ASSIGNMENT (multi-hot)  via MONDO crosswalk\n{'='*70}")
    if not args.xref.exists():
        print(f"!! mondo_xref not found: {args.xref} -- run fetch_mondo.py first.")
        return 2
    efo2, mesh2, name2, mondo2icd, mondo2names = _load_xref(args.xref)
    print(f"  MONDO xref: efo={len(efo2)} mesh={len(mesh2)} names={len(name2)} "
          f"terms-with-icd={sum(1 for v in mondo2icd.values() if v)}")

    u = pd.read_csv(args.units)
    u["unit_key"] = u["unit_mols"].astype(str) + " :: " + u["unit_indication"].astype(str)
    units = u.drop_duplicates("unit_key")[["unit_key", "unit_indication"]]
    print(f"  distinct drug-indication units: {len(units)}")

    paths = {"mondo": 0, "efo": 0, "mesh": 0, "name": 0, "unresolved": 0}
    n_icd = n_ta = 0
    ta_counts = {f"ta{i}": 0 for i in range(1, 10)}
    nta_hist = {0: 0, 1: 0, 2: 0, 3: 0, 4: 0}   # last bucket = 4+
    rows = []
    for _, r in units.iterrows():
        ind = r["unit_indication"]
        mondo, path = _resolve(ind, efo2, mesh2, name2)
        paths[path] += 1
        icd = set().union(*(mondo2icd.get(m, set()) for m in mondo)) if mondo else set()
        names = set().union(*(mondo2names.get(m, set()) for m in mondo)) if mondo else set()
        names.add(str(ind))                       # always let the raw indication feed name rules
        if icd:
            n_icd += 1
        ta = D.assign_ta(icd, names)
        if ta:
            n_ta += 1
        for t in ta:
            ta_counts[t] += 1
        nta_hist[min(len(ta), 4)] += 1
        rows.append({"unit_key": r["unit_key"], "dt_key": "",
                     "ta_key": "|".join(sorted(ta)), "ta_count": len(ta)})

    args.out.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(args.out, index=False)

    tot = len(units)
    n_mondo = tot - paths["unresolved"]
    print(f"\n{'='*70}\nRESOLUTION + COVERAGE (the audit)\n{'='*70}")
    print("  indication -> MONDO path:")
    for p in ("mondo", "efo", "mesh", "name", "unresolved"):
        print(f"    {p:11s}: {paths[p]:5d} ({_pct(paths[p], tot)})")
    print(f"  units reaching MONDO   : {n_mondo}/{tot} ({_pct(n_mondo, tot)})")
    print(f"  units with any ICD-10  : {n_icd}/{tot} ({_pct(n_icd, tot)})")
    print(f"  units with >=1 TA      : {n_ta}/{tot} ({_pct(n_ta, tot)})   <- TA coverage cap")
    print(f"  zero-TA (TA-9 'Other') : {tot - n_ta}/{tot} ({_pct(tot - n_ta, tot)})   "
          "<- expected: no bucket for respiratory/digestive/blood/ENT")
    print("  per-TA unit counts:")
    for i in range(1, 10):
        t = f"ta{i}"
        print(f"    {t} {D.TA_NAMES[t]:26s}: {ta_counts[t]:5d} ({_pct(ta_counts[t], tot)})")
    print("  #TAs per unit (-> newtanos): " +
          "  ".join(f"{k if k < 4 else '4+'}:{nta_hist[k]}" for k in sorted(nta_hist)))
    if ta_counts["ta9"] == 0:
        print("  note: ta9 (Vaccines) ~0 here by design -- it is drug-derived (name 'vaccine'),")
        print("        assigned on the drug side, not from disease->TA.")
    print(f"\n  wrote -> {args.out}")
    print("  next: engine multi-hot upgrade (split ta_key on '|', dedup history by drug) ->")
    print("  re-run build_moa_target_encoding.py --unit-class to light up `ta`. Then disease-type-152.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())