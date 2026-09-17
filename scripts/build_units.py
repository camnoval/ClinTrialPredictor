#!/usr/bin/env python3
"""Build the drug-indication UNIT table for the roll-up.

Explodes each TOP trial into its (regimen, indication) units and labels each from ChEMBL
max_phase_for_ind (4->1 approved, {1,2}->0, 3 excluded). Two indication-matching modes:

  default        : exact normalized disease-name match (the 28%-coverage floor)
  --xref FILE     : ontology crosswalk via MONDO -- resolve BOTH TOP (ICD-10 + name) and
                    ChEMBL (MeSH id + EFO id + name) to a set of MONDO ids and match on
                    intersection, plus MONDO exact-synonym names. Maximizes real matches
                    (e.g. NSCLC <-> non-small cell lung carcinoma) without sibling errors.

LEAKAGE (R7): unit_label is the target; never a trial feature. Requires: pip install rdkit
Usage: python scripts/build_units.py --data-dir <TOP> --chembl-dir data/chembl \
                                      --xref data/ontology/mondo_xref.csv
"""
from __future__ import annotations
import argparse
import ast
import re
from pathlib import Path

_MOL_FROM = None
_TO_KEY = None


def _load_rdkit():
    global _MOL_FROM, _TO_KEY
    if _MOL_FROM is None:
        from rdkit import Chem, RDLogger
        from rdkit.Chem import inchi
        RDLogger.DisableLog("rdApp.*")
        _MOL_FROM, _TO_KEY = Chem.MolFromSmiles, inchi.MolToInchiKey
    return _MOL_FROM, _TO_KEY


def _as_list(s):
    if s is None:
        return []
    s = str(s).strip()
    if not s or s.lower() == "nan":
        return []
    if s.startswith("[") and s.endswith("]"):
        try:
            v = ast.literal_eval(s)
            if isinstance(v, (list, tuple)):
                out = []
                for x in v:
                    xs = str(x).strip()
                    if xs.startswith("[") and xs.endswith("]"):   # nested (icdcodes)
                        try:
                            out += [str(i).strip() for i in ast.literal_eval(xs)]
                            continue
                        except (ValueError, SyntaxError):
                            pass
                    out.append(xs)
                return [x for x in out if x]
        except (ValueError, SyntaxError):
            pass
    return [s]


def _norm(name):
    n = re.sub(r"[^a-z0-9 ]+", " ", str(name).lower())
    return re.sub(r"\s+", " ", n).strip()


def _icd_variants(code):
    """Full code plus 3-char category, e.g. 'J45.909' -> {'J45.909','J45'}."""
    c = str(code).strip().upper()
    return {c, c.split(".")[0][:3]} if c else set()


def smiles_to_keys(smi):
    mol_from, to_key = _load_rdkit()
    keys = []
    m = mol_from(smi)
    if m is not None:
        try:
            keys.append(to_key(m))
        except Exception:
            pass
    if "." in smi:
        mf = mol_from(sorted(smi.split("."), key=len, reverse=True)[0])
        if mf is not None:
            try:
                keys.append(to_key(mf))
            except Exception:
                pass
    return [k for k in keys if k]


def _load_mondo(path: Path):
    """Return reverse maps: name->{mondo}, mesh->{mondo}, efo->{mondo}, icd->{mondo}."""
    import pandas as pd
    m = pd.read_csv(path).fillna("")
    name2, mesh2, efo2, icd2 = {}, {}, {}, {}
    for _, r in m.iterrows():
        mid = r["mondo_id"]
        for nm in [r["name"], *r["exact_synonyms"].split("|")]:
            nn = _norm(nm)
            if nn:
                name2.setdefault(nn, set()).add(mid)
        for x in r["mesh"].split("|"):
            if x:
                mesh2.setdefault(x.upper(), set()).add(mid)
        for x in r["efo"].split("|"):
            if x:
                efo2.setdefault(x.upper(), set()).add(mid)
        for x in r["icd10"].split("|"):
            for v in _icd_variants(x):
                icd2.setdefault(v, set()).add(mid)
    return name2, mesh2, efo2, icd2


def main() -> int:
    import numpy as np
    import pandas as pd

    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", required=True, type=Path)
    ap.add_argument("--chembl-dir", default=Path("data/chembl"), type=Path)
    ap.add_argument("--xref", type=Path, default=None, help="mondo_xref.csv (enables crosswalk)")
    ap.add_argument("--out", default=Path("data/chembl/units.csv"), type=Path)
    args = ap.parse_args()

    mols = pd.read_csv(args.chembl_dir / "molecules.csv")
    di = pd.read_csv(args.chembl_dir / "drug_indication.csv").fillna("")

    ik2id, skel2id = {}, {}
    for _, r in mols.iterrows():
        ik, cid = r.get("standard_inchi_key"), r.get("molecule_chembl_id")
        if isinstance(ik, str) and ik and isinstance(cid, str):
            ik2id[ik] = cid
            skel2id.setdefault(ik.split("-")[0], set()).add(cid)

    use_xref = args.xref is not None
    if use_xref:
        name2, mesh2, efo2, icd2 = _load_mondo(args.xref)
        print(f"MONDO maps: names={len(name2)} mesh={len(mesh2)} efo={len(efo2)} icd={len(icd2)}")

    def chembl_ind_mondo(mesh_id, efo_id, mesh_head, efo_term):
        s = set()
        if mesh_id:
            s |= mesh2.get(str(mesh_id).upper(), set())
        if efo_id:
            e = str(efo_id).upper()
            if e.startswith("MONDO:"):
                s.add(e)
            s |= efo2.get(e, set())
        for nm in (mesh_head, efo_term):
            s |= name2.get(_norm(nm), set())
        return s

    # per molecule_chembl_id -> list of indication cells
    id2inds: dict[str, list] = {}
    for _, r in di.iterrows():
        cid = r.get("molecule_chembl_id")
        if not isinstance(cid, str):
            continue
        try:
            ph = float(r.get("max_phase_for_ind"))
        except (TypeError, ValueError):
            continue
        names = {_norm(r.get("mesh_heading")), _norm(r.get("efo_term"))} - {""}
        key = str(r.get("efo_id") or r.get("mesh_id") or next(iter(names), "?"))
        cell = {"ph": ph, "names": names, "key": key}
        if use_xref:
            cell["mondo"] = chembl_ind_mondo(r.get("mesh_id"), r.get("efo_id"),
                                             r.get("mesh_heading"), r.get("efo_term"))
        id2inds.setdefault(cid, []).append(cell)

    frames = []
    for phase in ("phase_I", "phase_II", "phase_III"):
        for part in ("train", "valid", "test"):
            p = args.data_dir / f"{phase}_{part}.csv"
            if p.exists():
                df = pd.read_csv(p)
                df["__phase"], df["__part"] = phase, part
                frames.append(df)
    d = pd.concat(frames, ignore_index=True)
    print(f"TOP trials: {len(d)}")

    key_cache: dict[str, list[str]] = {}
    rows = []
    for _, r in d.iterrows():
        cids = set()
        for smi in _as_list(r.get("smiless")):
            if smi not in key_cache:
                key_cache[smi] = smiles_to_keys(smi)
            for k in key_cache[smi]:
                if k in ik2id:
                    cids.add(ik2id[k])
                elif k.split("-")[0] in skel2id:
                    cids |= skel2id[k.split("-")[0]]
        if not cids:
            continue
        regimen = "+".join(sorted(cids))
        top_names = {_norm(x) for x in _as_list(r.get("diseases"))} - {""}
        top_mondo = set()
        if use_xref:
            for code in _as_list(r.get("icdcodes")):
                for v in _icd_variants(code):
                    top_mondo |= icd2.get(v, set())
            for nm in top_names:
                top_mondo |= name2.get(nm, set())

        # collect best max_phase per matched indication key, and how it matched
        best: dict[str, list] = {}   # key -> [max_ph, provenance]
        for cid in cids:
            for cell in id2inds.get(cid, ()):
                hit = None
                if top_names & cell["names"]:
                    hit = "name"
                elif use_xref and top_mondo & cell.get("mondo", set()):
                    hit = "mondo"
                if hit:
                    cur = best.get(cell["key"])
                    if cur is None or cell["ph"] > cur[0]:
                        best[cell["key"]] = [cell["ph"], hit]
        for ind_key, (ph, prov) in best.items():
            if ph == 4:
                lab = 1
            elif ph in (1.0, 2.0):
                lab = 0
            else:
                continue
            rows.append({
                "nctid": r.get("nctid"), "__phase": r["__phase"], "__part": r["__part"],
                "unit_mols": regimen, "unit_indication": ind_key, "match": prov,
                "unit_max_phase": ph, "unit_label": lab, "top_label": r.get("label"),
            })
    u = pd.DataFrame(rows)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    u.to_csv(args.out, index=False)

    u["unit_key"] = u["unit_mols"] + " :: " + u["unit_indication"].astype(str)
    units = u.drop_duplicates("unit_key")
    tpu = u.groupby("unit_key").size()
    print(f"\n{'='*60}\nunit table  (mode: {'MONDO crosswalk' if use_xref else 'exact name'})\n{'='*60}")
    print(f"  (trial, unit) rows        : {len(u)}")
    print(f"  distinct units            : {len(units)}")
    print(f"  unit-level class balance  : {(units.unit_label==1).sum()} approved / "
          f"{(units.unit_label==0).sum()} negative ({100*(units.unit_label==1).mean():.1f}% pos)")
    if use_xref:
        prov = u.drop_duplicates('unit_key')['match'].value_counts()
        print(f"  units matched by          : {prov.to_dict()}")
    print(f"  trials per unit           : mean {tpu.mean():.2f}, median {int(tpu.median())}, max {tpu.max()}")
    print(f"  units with >=2 trials     : {(tpu>=2).sum()} ({100*(tpu>=2).mean():.1f}%)")
    print("  units by TOP split part:")
    print("    " + units.groupby("__part").size().to_string().replace("\n", "\n    "))
    print(f"\n  wrote -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())