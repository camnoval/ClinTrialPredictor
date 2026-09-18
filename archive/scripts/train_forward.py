#!/usr/bin/env python3
"""The 2024 forward test: does the TOP-trained DSAI spine predict CTO 2020-2024 outcomes?

Fits the trial-level XGBoost on ALL of TOP (units.csv labels, --max-phase II framing),
then predicts every trial in data/cto/cto_trials.csv -- built by build_cto.py in TOP's
exact column schema, so train_rollup's IDENTICAL featurizer applies (dense via TopSource +
OR-pooled Morgan bits from smiless + ICD-chapter from icdcodes; R11 parity).

Headline label is CTO gold (label (ii)), which exists for ~all trials. Because only ~half
the cohort is fingerprint-able (the rest coded/novel/biologic, R8), every metric is reported
three ways -- all / small-molecule / biologic -- plus seen-vs-novel molecule, plus calibration
(Brier + ECE + reliability), because a deployable "N% chance" is a calibration claim (R10).

This is a from-trial-start prediction vs an independent outcome label; it does NOT compare
to CTO's own pred_proba, which is an outcome-adjacent labeler, not a predictor (R9).

Usage: python scripts/train_forward.py --data-dir <TOP> --cto data/cto/cto_trials.csv --max-phase II
"""
from __future__ import annotations
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from train_rollup import (                                              # noqa: E402
    _as_list, _trial_dense, _trial_raw, _fp_pool, _icd_vec,
    PRE_TRIAL, ICD_CHAPTERS, _PHASE_ORD,
)

_PART_RANK = {"train": 0, "valid": 1, "test": 2}


# ---- calibration (pure, offline-testable) ----------------------------------
def brier(y, p):
    import numpy as np
    return float(np.mean((np.asarray(p) - np.asarray(y)) ** 2))


def ece(y, p, bins=10):
    """Expected Calibration Error: avg |confidence - accuracy| over equal-width bins."""
    import numpy as np
    y, p = np.asarray(y, float), np.asarray(p, float)
    edges = np.linspace(0, 1, bins + 1)
    tot, n = 0.0, len(y)
    for i in range(bins):
        lo, hi = edges[i], edges[i + 1]
        m = (p >= lo) & (p < hi if i < bins - 1 else p <= hi)
        if m.sum():
            tot += (m.sum() / n) * abs(p[m].mean() - y[m].mean())
    return float(tot)


def reliability(y, p, bins=5):
    import numpy as np
    y, p = np.asarray(y, float), np.asarray(p, float)
    edges = np.linspace(0, 1, bins + 1)
    rows = []
    for i in range(bins):
        lo, hi = edges[i], edges[i + 1]
        m = (p >= lo) & (p < hi if i < bins - 1 else p <= hi)
        if m.sum():
            rows.append((lo, hi, int(m.sum()), float(p[m].mean()), float(y[m].mean())))
    return rows


def _feature_vec(feats, smiless, icd, fp_bits, use_icd, fp_cache):
    vec = [float("nan") if feats.get(k) is None else float(feats.get(k)) for k in PRE_TRIAL]
    if fp_bits:
        vec += list(_fp_pool(smiless, fp_bits, fp_cache))
    if use_icd:
        vec += list(_icd_vec(icd))
    return vec


def main() -> int:
    import numpy as np
    import pandas as pd
    from sklearn.metrics import (average_precision_score, f1_score, log_loss,
                                 roc_auc_score)
    from trial_pos.services.models import make_xgboost

    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", required=True, type=Path, help="TOP dir")
    ap.add_argument("--cto", default=Path("data/cto/cto_trials.csv"), type=Path)
    ap.add_argument("--units", default=Path("data/chembl/units.csv"), type=Path)
    ap.add_argument("--max-phase", choices=["I", "II", "III"], default="II")
    ap.add_argument("--fp-bits", type=int, default=256)
    ap.add_argument("--no-icd", action="store_true")
    args = ap.parse_args()
    cap = {"I": 1, "II": 2, "III": 3}[args.max_phase]
    use_icd = not args.no_icd
    fp_cache: dict = {}

    # ---- 1. fit trial model on ALL of TOP -----------------------------------
    u = pd.read_csv(args.units)
    u = u[u["__phase"].map(_PHASE_ORD).fillna(9) <= cap].copy()
    dense = _trial_dense(args.data_dir)
    raw = _trial_raw(args.data_dir)
    top_smiles = set()
    X, y, miss = [], [], 0
    for _, r in u.iterrows():
        f = dense.get(r["nctid"])
        if f is None:
            miss += 1
            continue
        smiless, icd = raw.get(r["nctid"], ([], []))
        top_smiles.update(smiless)
        X.append(_feature_vec(f, smiless, icd, args.fp_bits, use_icd, fp_cache))
        y.append(int(r["unit_label"]))
    X, y = np.asarray(X, float), np.asarray(y, int)
    print(f"TOP fit: {len(y)} (trial,unit) rows, {X.shape[1]} features "
          f"(phase<={args.max_phase}, dense-miss={miss}, TOP smiles={len(top_smiles)})")
    model = make_xgboost()
    model.fit(X, y)

    # ---- 2. featurize + predict CTO (TOP-identical featurizer) --------------
    cto = pd.read_csv(args.cto).fillna("")
    cto = cto[cto["label"] != ""].copy()               # gold label present
    cto_dense = {rec.nct_id.value: rec.features
                 for rec in _records_for(args.cto)}
    rows = []
    cto_miss = 0
    for _, r in cto.iterrows():
        f = cto_dense.get(r["nctid"])
        if f is None:
            cto_miss += 1
            continue
        smiless, icd = _as_list(r.get("smiless")), _as_list(r.get("icdcodes"))
        vec = _feature_vec(f, smiless, icd, args.fp_bits, use_icd, fp_cache)
        p = float(model.predict_proba(np.asarray([vec], float))[:, 1][0])
        rows.append({
            "y": int(r["label"]), "p": p,
            "is_sm": bool(smiless),
            "seen": bool(set(smiless) & top_smiles),
        })
    d = pd.DataFrame(rows)
    if cto_miss:
        print(f"  (warning: {cto_miss} CTO trials lacked dense features -- if this is most of"
              f" them, TopSource isn't parsing cto_trials.csv; tell me and I'll featurize direct)")
    yb = d["y"].to_numpy()
    print(f"CTO predicted: {len(d)} trials  ({d['is_sm'].sum()} small-molecule, "
          f"{(~d['is_sm']).sum()} biologic)  gold pos-rate={yb.mean():.3f}")

    # ---- 3. report: stratified + calibrated ---------------------------------
    def rep(name, sub):
        yy, pp = sub["y"].to_numpy(), np.clip(sub["p"].to_numpy(), 1e-6, 1 - 1e-6)
        if len(yy) == 0 or len(np.unique(yy)) < 2:
            print(f"  {name:28s} (n={len(yy)}: too few/one-class)")
            return
        print(f"  {name:28s} n={len(yy):4d}  log-loss={log_loss(yy, pp, labels=[0,1]):.4f}  "
              f"AUROC={roc_auc_score(yy, pp):.4f}  PR-AUC={average_precision_score(yy, pp):.4f}  "
              f"F1@.5={f1_score(yy, (pp >= .5).astype(int)):.4f}  "
              f"Brier={brier(yy, pp):.4f}  ECE={ece(yy, pp):.4f}")

    print(f"\n{'='*74}\nCTO 2020-2024 FORWARD TEST vs CTO gold (max-phase {args.max_phase})\n{'='*74}")
    rep("base rate", pd.DataFrame({"y": yb, "p": np.full(len(yb), yb.mean())}))
    rep("all trials", d)
    rep("small-molecule (fp)", d[d["is_sm"]])
    rep("biologic/no-fp", d[~d["is_sm"]])
    sm = d[d["is_sm"]]
    rep("  small-mol, seen mol", sm[sm["seen"]])
    rep("  small-mol, novel mol", sm[~sm["seen"]])

    print("\n  reliability (small-molecule, pred-bin -> mean_pred vs actual):")
    for lo, hi, cnt, mp, ay in reliability(sm["y"].to_numpy(), sm["p"].to_numpy(), bins=5):
        print(f"    [{lo:.1f},{hi:.1f})  n={cnt:4d}  pred={mp:.3f}  actual={ay:.3f}")
    print("\n  note: biologic/no-fp trials carry an empty fingerprint, so the model scores")
    print("  them off sparse dense features -- expect ~base-rate there. Headline is CTO gold;")
    print("  ChEMBL-label (i) unit-level check is a separate, smaller-N run (build CTO units).")
    return 0


def _records_for(cto_path):
    """Dense features for the CTO file via the SAME TopSource cleaner used for TOP."""
    from trial_pos.orchestration.pipeline import build_records
    from trial_pos.sources.top_source import TopSource
    return build_records([TopSource(cto_path)])


if __name__ == "__main__":
    raise SystemExit(main())