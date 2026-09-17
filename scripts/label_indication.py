#!/usr/bin/env python3
"""v2 indication-level approval label: TOP trial -> molecule -> ChEMBL drug_indication.

Chains TOP SMILES -> InChIKey -> molecule_chembl_id -> that molecule's drug_indication
rows, matches the trial's disease to those indications, and reads max_phase_for_ind:
  == 4 -> positive ; in {1,2} -> negative ; == 3 -> EXCLUDED (ambiguous) ; no match -> unlabeled.

Reports the label under TWO matching strictnesses so we can see loose-match inflation:
  EXACT      : normalized disease name == ChEMBL indication name
  EXACT+LOOSE: also token-subset (looser; can match sibling indications, e.g. lung vs
               breast cancer -> inflates positives)

LEAKAGE (R7): max_phase_for_ind is the LABEL, never a trial-level feature.
Requires: pip install rdkit
Usage: python scripts/label_indication.py --data-dir <TOP dir> --chembl-dir data/chembl
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
                return [str(x).strip() for x in v if str(x).strip()]
        except (ValueError, SyntaxError):
            pass
    return [s]


def _norm(name):
    n = re.sub(r"[^a-z0-9 ]+", " ", str(name).lower())
    return re.sub(r"\s+", " ", n).strip()


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


def _tokens_subset(a, b):
    ta, tb = set(a.split()), set(b.split())
    if not ta or not tb:
        return False
    short, long = (ta, tb) if len(ta) <= len(tb) else (tb, ta)
    return short.issubset(long)


def _label(phases):
    import numpy as np
    if not phases:
        return np.nan
    best = max(phases)
    if best == 4:
        return 1
    if best in (1.0, 2.0):
        return 0
    return np.nan  # phase-3-only -> ambiguous


def _report(out, col, title):
    labeled = out[col].notna()
    print(f"\n-- {title} --")
    print(f"  labelable trials : {labeled.sum()} ({100*labeled.mean():.1f}%)")
    if labeled.sum():
        pos = (out.loc[labeled, col] == 1).sum()
        print(f"  class balance    : {pos} approved / {labeled.sum()-pos} negative "
              f"({100*pos/labeled.sum():.1f}% positive)")
        g = out.loc[labeled].groupby(col)["top_label"].agg(["mean", "size"]).round(3)
        print("  v2 vs TOP outcome:")
        print("    " + g.to_string().replace("\n", "\n    "))


def main() -> int:
    import numpy as np
    import pandas as pd

    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", required=True, type=Path)
    ap.add_argument("--chembl-dir", default=Path("data/chembl"), type=Path)
    ap.add_argument("--out", default=Path("data/chembl/top_indication_label.csv"), type=Path)
    args = ap.parse_args()

    mols = pd.read_csv(args.chembl_dir / "molecules.csv")
    di = pd.read_csv(args.chembl_dir / "drug_indication.csv")

    ik2id, skel2id = {}, {}
    for _, r in mols.iterrows():
        ik, cid = r.get("standard_inchi_key"), r.get("molecule_chembl_id")
        if isinstance(ik, str) and ik and isinstance(cid, str):
            ik2id[ik] = cid
            skel2id.setdefault(ik.split("-")[0], set()).add(cid)

    id2inds: dict[str, list] = {}
    for _, r in di.iterrows():
        cid = r.get("molecule_chembl_id")
        if not isinstance(cid, str):
            continue
        try:
            ph = float(r.get("max_phase_for_ind"))
        except (TypeError, ValueError):
            continue
        for col in ("mesh_heading", "efo_term"):
            nm = _norm(r.get(col))
            if nm:
                id2inds.setdefault(cid, []).append((nm, ph))
    print(f"ChEMBL: {len(ik2id)} InChIKeys, {len(id2inds)} molecules with indications")

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
        top_inds = [_norm(x) for x in _as_list(r.get("diseases"))]
        exact_ph, loose_ph = [], []
        for cid in cids:
            for cnm, ph in id2inds.get(cid, ()):
                for tnm in top_inds:
                    if not tnm:
                        continue
                    if tnm == cnm:
                        exact_ph.append(ph)
                    elif _tokens_subset(tnm, cnm):
                        loose_ph.append(ph)
        v2_exact = _label(exact_ph)
        v2_all = _label(exact_ph + loose_ph)
        rows.append({
            "nctid": r.get("nctid"), "__phase": r["__phase"], "__part": r["__part"],
            "top_label": r.get("label"), "n_molecules_matched": len(cids),
            "n_exact_pairs": len(exact_ph), "n_loose_pairs": len(loose_ph),
            "v2_label_exact": v2_exact, "v2_label_all": v2_all,
        })
    out = pd.DataFrame(rows)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(args.out, index=False)

    print(f"\n{'='*60}\nv2 indication-level label coverage\n{'='*60}")
    print(f"  trials with a molecule matched : {(out.n_molecules_matched>0).sum()} "
          f"({100*(out.n_molecules_matched>0).mean():.1f}%)")
    _report(out, "v2_label_exact", "EXACT name match only")
    _report(out, "v2_label_all", "EXACT + LOOSE (token-subset)")

    # how many positives exist ONLY because of loose matching
    inflated = ((out.v2_label_all == 1) & (out.v2_label_exact != 1)).sum()
    print(f"\n  positives that appear ONLY via loose matching: {inflated}")
    print(f"  wrote -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())