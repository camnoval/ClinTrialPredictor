#!/usr/bin/env python3
"""Join TOP molecules to ChEMBL approval labels by structure (InChIKey).

Canonicalizes each TOP trial's SMILES to InChIKeys with RDKit and matches them against
data/chembl/molecules.csv (from fetch_chembl_labels.py). Runs as a COVERAGE AUDIT first --
match rate gates the whole label design (R5 discipline) -- and writes an enriched per-trial
file for the next step.

LEAKAGE RULE (R7): ChEMBL max_phase reflects a drug's status as of the ChEMBL release
(2026), which is the FUTURE relative to an old trial. It is the drug-indication APPROVAL
LABEL (the prediction target) and must NEVER be fed to the trial-level model as a feature.

Matching, best-effort to maximise hits:
  1. full InChIKey of the molecule as given
  2. full InChIKey of its largest fragment (salt/counter-ion stripped)
  3. skeleton block (first 14 chars) of either, ignoring stereo/protonation
Requires: pip install rdkit

Usage: python scripts/join_chembl.py --data-dir <TOP dir> --chembl-dir data/chembl
"""
from __future__ import annotations
import argparse
import ast
from pathlib import Path

# module-level so tests can inject fakes instead of RDKit
_MOL_FROM = None
_TO_KEY = None


def _load_rdkit():
    global _MOL_FROM, _TO_KEY
    if _MOL_FROM is None:
        from rdkit import Chem
        from rdkit.Chem import inchi
        from rdkit import RDLogger
        RDLogger.DisableLog("rdApp.*")
        _MOL_FROM = Chem.MolFromSmiles
        _TO_KEY = inchi.MolToInchiKey
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


def smiles_to_keys(smi):
    """Return candidate InChIKeys for one SMILES: full molecule + largest fragment."""
    mol_from, to_key = _load_rdkit()
    keys = []
    m = mol_from(smi)
    if m is not None:
        try:
            keys.append(to_key(m))
        except Exception:
            pass
    if "." in smi:  # salt/mixture -> largest organic fragment
        frags = sorted(smi.split("."), key=len, reverse=True)
        mf = mol_from(frags[0])
        if mf is not None:
            try:
                keys.append(to_key(mf))
            except Exception:
                pass
    return [k for k in keys if k]


def _load_chembl(mol_csv: Path):
    import pandas as pd
    m = pd.read_csv(mol_csv)

    def _phase(v):
        try:
            return float(v)
        except (TypeError, ValueError):
            return None

    full, skel = {}, {}
    for _, r in m.iterrows():
        ik = r.get("standard_inchi_key")
        if not isinstance(ik, str) or not ik:
            continue
        ph = _phase(r.get("max_phase"))
        if ph is None:
            continue
        full[ik] = max(ph, full.get(ik, ph))
        blk = ik.split("-")[0]
        skel[blk] = max(ph, skel.get(blk, ph))
    return full, skel


def _match(keys, full, skel):
    """Return (max_phase, kind) for a molecule's candidate keys, or (None, 'miss')."""
    for k in keys:
        if k in full:
            return full[k], "full"
    for k in keys:
        if k.split("-")[0] in skel:
            return skel[k.split("-")[0]], "skeleton"
    return None, "miss"


def main() -> int:
    import numpy as np
    import pandas as pd

    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", required=True, type=Path)
    ap.add_argument("--chembl-dir", default=Path("data/chembl"), type=Path)
    ap.add_argument("--out", default=Path("data/chembl/top_molecule_match.csv"), type=Path)
    args = ap.parse_args()

    full, skel = _load_chembl(args.chembl_dir / "molecules.csv")
    print(f"ChEMBL keys loaded: {len(full)} full, {len(skel)} skeleton")

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
    n_mol_inst = n_mol_full = n_mol_skel = n_smiles_bad = 0
    for _, r in d.iterrows():
        smis = _as_list(r.get("smiless"))
        phases = []
        n_valid = 0
        for smi in smis:
            n_mol_inst += 1
            if smi not in key_cache:
                key_cache[smi] = smiles_to_keys(smi)
            keys = key_cache[smi]
            if not keys:
                n_smiles_bad += 1
                continue
            n_valid += 1
            ph, kind = _match(keys, full, skel)
            if kind == "full":
                n_mol_full += 1
            elif kind == "skeleton":
                n_mol_skel += 1
            if ph is not None:
                phases.append(ph)
        rows.append({
            "nctid": r.get("nctid"), "__phase": r["__phase"], "__part": r["__part"],
            "label": r.get("label"), "n_mols": len(smis), "n_valid": n_valid,
            "n_matched": len(phases),
            "regimen_maxphase": max(phases) if phases else np.nan,
            "any_approved": int(any(p == 4 for p in phases)),
            "all_matched": int(len(smis) > 0 and len(phases) == len(smis)),
        })
    out = pd.DataFrame(rows)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(args.out, index=False)

    n = len(out)
    matched = n_mol_full + n_mol_skel
    print(f"\n{'='*60}\nChEMBL match coverage\n{'='*60}")
    print(f"  molecule instances     : {n_mol_inst}  (SMILES unparseable: {n_smiles_bad})")
    print(f"  molecules matched       : {matched}  ({100*matched/max(n_mol_inst,1):.1f}%)  "
          f"[full {n_mol_full}, skeleton {n_mol_skel}]")
    print(f"  trials with >=1 match   : {(out.n_matched>0).sum()}  ({100*(out.n_matched>0).mean():.1f}%)")
    print(f"  trials fully matched    : {out.all_matched.sum()}  ({100*out.all_matched.mean():.1f}%)")
    print("\n  regimen max_phase distribution (matched trials):")
    print(out.loc[out.n_matched>0, "regimen_maxphase"].value_counts(dropna=False).sort_index().to_string())
    print("\n  TOP outcome vs ChEMBL approval (association sanity, not leakage):")
    print(out.groupby("any_approved")["label"].agg(["mean","size"]).round(3).to_string())
    print(f"\n  wrote enriched per-trial file -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())