#!/usr/bin/env python3
"""The DSAI-2019 spine on open data: trial XGBoost -> ridge roll-up -> log-loss.

  1. trial-level XGBoost predicts approval from PRE-TRIAL features (unit label broadcast
     to each trial); out-of-fold on the fit set, folds grouped by unit,
  2. a ridge (logistic) roll-up aggregates each unit's trial predictions,
  3. scored with log-loss + F1 at the UNIT level on TOP's temporal holdout.

Features (all pre-trial, no leakage per R7):
  dense       : phase_ordinal, drug/disease/icd/smiles counts, criteria_len
  fingerprint : Morgan/ECFP4 bits, OR-pooled over the regimen's molecules (--fp-bits)
  icd         : ICD-10 chapter (first-letter) one-hot from icdcodes (disease area)
The fingerprint identifies the molecule and the ICD chapter the disease area, so the
model can finally separate an approved-for-X case from a failed-for-Y one sharing a drug.

Use --max-phase II for the DSAI-style predict-from-Phase-2 framing (strips the Phase III
progression proxy). Requires: pip install rdkit
Usage: python scripts/train_rollup.py --data-dir <TOP> --units data/chembl/units.csv --max-phase II
"""
from __future__ import annotations
import argparse
import ast
from pathlib import Path

PRE_TRIAL = ["phase_ordinal", "n_drugs", "n_diseases", "n_icd", "n_smiles", "criteria_len"]
ICD_CHAPTERS = list("ABCDEFGHIJKLMNOPQRSTUVWXYZ")
_PART_RANK = {"train": 0, "valid": 1, "test": 2}
_PHASE_ORD = {"phase_I": 1, "phase_II": 2, "phase_III": 3}
_FP = None  # rdkit fingerprint fn, lazy


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
                    if xs.startswith("[") and xs.endswith("]"):
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


def _trial_dense(data_dir):
    from trial_pos.orchestration.pipeline import build_records
    from trial_pos.sources.top_source import TopSource
    feats = {}
    for phase in ("phase_I", "phase_II", "phase_III"):
        for part in ("train", "valid", "test"):
            p = data_dir / f"{phase}_{part}.csv"
            if p.exists():
                for rec in build_records([TopSource(p)]):
                    feats[rec.nct_id.value] = rec.features
    return feats


def _trial_raw(data_dir):
    import pandas as pd
    raw = {}
    for phase in ("phase_I", "phase_II", "phase_III"):
        for part in ("train", "valid", "test"):
            p = data_dir / f"{phase}_{part}.csv"
            if p.exists():
                df = pd.read_csv(p)
                for _, r in df.iterrows():
                    raw[r["nctid"]] = (_as_list(r.get("smiless")), _as_list(r.get("icdcodes")))
    return raw


def _morgan(smi, n_bits, cache):
    global _FP
    import numpy as np
    if smi in cache:
        return cache[smi]
    if _FP is None:
        from rdkit import Chem, RDLogger
        from rdkit.Chem import AllChem
        RDLogger.DisableLog("rdApp.*")

        def fp(s):
            m = Chem.MolFromSmiles(s)
            if m is None:
                return None
            bv = AllChem.GetMorganFingerprintAsBitVect(m, 2, nBits=n_bits)
            a = np.zeros(n_bits, dtype=np.uint8)
            for b in bv.GetOnBits():
                a[b] = 1
            return a
        _FP = fp
    cache[smi] = _FP(smi)
    return cache[smi]


def _fp_pool(smiless, n_bits, cache):
    import numpy as np
    v = np.zeros(n_bits, dtype=np.uint8)
    for smi in smiless:
        a = _morgan(smi, n_bits, cache)
        if a is not None:
            v |= a
    return v


def _icd_vec(icdlist):
    import numpy as np
    v = np.zeros(len(ICD_CHAPTERS), dtype=np.uint8)
    for c in icdlist:
        c = str(c).strip().upper()
        if c and c[0] in ICD_CHAPTERS:
            v[ICD_CHAPTERS.index(c[0])] = 1
    return v


def main() -> int:
    import numpy as np
    import pandas as pd
    from sklearn.linear_model import LogisticRegression
    from sklearn.metrics import (average_precision_score, f1_score, log_loss,
                                 roc_auc_score)
    from sklearn.model_selection import GroupKFold
    from trial_pos.services.models import make_xgboost

    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", required=True, type=Path)
    ap.add_argument("--units", default=Path("data/chembl/units.csv"), type=Path)
    ap.add_argument("--folds", type=int, default=5)
    ap.add_argument("--max-phase", choices=["I", "II", "III"], default="III")
    ap.add_argument("--fp-bits", type=int, default=256, help="Morgan bits (0 disables)")
    ap.add_argument("--no-icd", action="store_true")
    args = ap.parse_args()
    cap = {"I": 1, "II": 2, "III": 3}[args.max_phase]

    u = pd.read_csv(args.units)
    u = u[u["__phase"].map(_PHASE_ORD).fillna(9) <= cap].copy()
    if len(u) == 0:
        print("no rows after phase filter"); return 2
    u["unit_key"] = u["unit_mols"] + " :: " + u["unit_indication"].astype(str)
    u["rank"] = u["__part"].map(_PART_RANK)
    unit_split = u.groupby("unit_key")["rank"].max()

    dense = _trial_dense(args.data_dir)
    raw = _trial_raw(args.data_dir) if (args.fp_bits or not args.no_icd) else {}
    fp_cache: dict = {}

    # feature layout
    names = list(PRE_TRIAL)
    if args.fp_bits:
        names += [f"fp{i}" for i in range(args.fp_bits)]
    if not args.no_icd:
        names += [f"icd_{c}" for c in ICD_CHAPTERS]

    X, y, keys, splits = [], [], [], []
    miss = 0
    for _, r in u.iterrows():
        f = dense.get(r["nctid"])
        if f is None:
            miss += 1
            continue
        vec = [float("nan") if f.get(k) is None else float(f.get(k)) for k in PRE_TRIAL]
        smiless, icd = raw.get(r["nctid"], ([], []))
        if args.fp_bits:
            vec += list(_fp_pool(smiless, args.fp_bits, fp_cache))
        if not args.no_icd:
            vec += list(_icd_vec(icd))
        X.append(vec); y.append(int(r["unit_label"]))
        keys.append(r["unit_key"]); splits.append(unit_split[r["unit_key"]])
    X = np.asarray(X, float); y = np.asarray(y, int)
    keys = np.asarray(keys); splits = np.asarray(splits)
    fit = splits < _PART_RANK["test"]; test = ~fit
    print(f"phase<= {args.max_phase}: {len(y)} (trial,unit) rows, {X.shape[1]} features "
          f"(missing features: {miss}); fit trials={fit.sum()} test trials={test.sum()}")

    # 1. trial model with unit-grouped OOF
    pred = np.full(len(y), np.nan)
    Xf, yf, gf = X[fit], y[fit], keys[fit]
    oof = np.zeros(len(yf))
    n_splits = min(args.folds, len(np.unique(gf)))
    for tr, va in GroupKFold(n_splits=n_splits).split(Xf, yf, gf):
        m = make_xgboost(); m.fit(Xf[tr], yf[tr]); oof[va] = m.predict_proba(Xf[va])[:, 1]
    pred[fit] = oof
    mfull = make_xgboost(); mfull.fit(Xf, yf)
    pred[test] = mfull.predict_proba(X[test])[:, 1]
    try:
        imp = mfull.feature_importances_
        dense_imp = {n: imp[i] for i, n in enumerate(names) if n in PRE_TRIAL}
        fp_imp = sum(imp[i] for i, n in enumerate(names) if n.startswith("fp"))
        icd_imp = sum(imp[i] for i, n in enumerate(names) if n.startswith("icd_"))
        line = ", ".join(f"{k}={v:.3f}" for k, v in sorted(dense_imp.items(), key=lambda x: -x[1]))
        print(f"  importance: {line}, fingerprint(total)={fp_imp:.3f}, icd(total)={icd_imp:.3f}")
    except Exception:
        pass

    # 2. ridge roll-up
    agg = (pd.DataFrame({"unit_key": keys, "y": y, "split": splits, "p": pred})
           .groupby("unit_key")
           .agg(y=("y", "first"), split=("split", "first"), p_mean=("p", "mean"),
                p_max=("p", "max"), p_min=("p", "min"), n=("p", "size")))
    uf = agg["split"] < _PART_RANK["test"]; ut = ~uf
    cols = ["p_mean", "p_max", "p_min", "n"]
    ridge = LogisticRegression(max_iter=1000, class_weight="balanced")
    ridge.fit(agg.loc[uf, cols], agg.loc[uf, "y"])
    proba = ridge.predict_proba(agg.loc[ut, cols])[:, 1]
    yt = agg.loc[ut, "y"].to_numpy()
    print(f"units: fit={uf.sum()} test={ut.sum()} (test positives={int(yt.sum())}/{len(yt)})")

    def _rep(name, p, mask=None):
        import numpy as _np
        yy, pp = (yt, p) if mask is None else (yt[mask], p[mask])
        if len(yy) == 0 or len(_np.unique(yy)) < 2:
            print(f"  {name:26s} (n={len(yy)}: too few/one-class to score)")
            return
        pp = np.clip(pp, 1e-6, 1 - 1e-6)
        print(f"  {name:26s} log-loss={log_loss(yy,pp,labels=[0,1]):.4f}  "
              f"F1@.5={f1_score(yy,(pp>=0.5).astype(int)):.4f}  "
              f"AUROC={roc_auc_score(yy,pp):.4f}  PR-AUC={average_precision_score(yy,pp):.4f}")

    print(f"\n{'='*66}\nUNIT-LEVEL on temporal holdout (max-phase {args.max_phase}; DSAI ref 0.202)\n{'='*66}")
    _rep("base rate", np.full(len(yt), agg.loc[uf, "y"].mean()))
    _rep("mean trial pred (no ridge)", agg.loc[ut, "p_mean"].to_numpy())
    _rep("ridge roll-up (DSAI spine)", proba)

    # seen vs novel molecule: how much is memorization vs structural generalization
    mol_of = {k: set(str(k).split(" :: ")[0].split("+")) for k in agg.index}
    fit_mols = set().union(*(mol_of[k] for k in agg.index[uf.values])) if uf.any() else set()
    test_keys = agg.index[ut.values]
    seen = np.array([bool(mol_of[k] & fit_mols) for k in test_keys])
    print(f"\n  -- ridge, split by molecule seen in training --")
    print(f"  ({seen.sum()} test units have a molecule seen in fit; {(~seen).sum()} are novel)")
    _rep("  seen-molecule units", proba, seen)
    _rep("  novel-molecule units", proba, ~seen)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())