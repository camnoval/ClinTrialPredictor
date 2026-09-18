#!/usr/bin/env python3
"""Train + evaluate on TOP using TOP's own per-phase split (R2).

TOP ships data/phase_{I,II,III}_{train,valid,test}.csv, split temporally at 2014-01-01
(test start dates after the boundary; train/valid completions before it), so using these
files honors the R1 no-leakage rule by construction.

LEAKAGE NOTE (R1/R4): the model must only use information knowable at/before trial start.
`status` (-> was_terminated), `why_stop` (-> has_why_stop), and realized `duration_days`
are only known at/after the trial ends -- a terminated status or a populated stop-reason
essentially *is* the failure label. They are excluded by default. `--with-leaky` adds them
back ONLY to demonstrate how much they inflate the metric; never report those numbers.

Usage:
    python scripts/train_top.py --data-dir data/top --phase all
    python scripts/train_top.py --data-dir data/top --phase II --with-leaky   # demo only
"""
from __future__ import annotations
import argparse
import sys
from pathlib import Path

# Known at/before trial start -> legitimate inputs.
PRE_TRIAL_FEATURES = [
    "phase_ordinal", "n_drugs", "n_diseases", "n_icd", "n_smiles", "criteria_len",
]
# Only knowable at/after the trial ends -> leakage if used to predict the outcome.
LEAKY_FEATURES = ["was_terminated", "has_why_stop", "duration_days"]
PHASES = {"I": "phase_I", "II": "phase_II", "III": "phase_III"}


def _load_split(data_dir: Path, phase_stub: str, part: str):
    """Return built TrialRecords for one phase/part (train|valid|test)."""
    from trial_pos.orchestration.pipeline import build_records
    from trial_pos.sources.top_source import TopSource
    path = data_dir / f"{phase_stub}_{part}.csv"
    if not path.exists():
        raise FileNotFoundError(path)
    return build_records([TopSource(path)])


def _matrix(records, feature_order):
    import numpy as np
    X, y = [], []
    for r in records:
        if r.label.approved is None:      # unlabeled rows can't train/eval
            continue
        X.append([_nan_if_none(r.features.get(k)) for k in feature_order])
        y.append(int(r.label.approved))
    return np.asarray(X, dtype=float), np.asarray(y, dtype=int)


def _nan_if_none(v):
    return float("nan") if v is None else float(v)


def _run_phase(data_dir: Path, phase_key: str, feature_order, leaky: bool) -> int:
    import numpy as np
    from sklearn.metrics import (average_precision_score, f1_score, log_loss,
                                 roc_auc_score)
    from trial_pos.services.models import make_xgboost

    stub = PHASES[phase_key]
    tr = _load_split(data_dir, stub, "train")
    va = _load_split(data_dir, stub, "valid")
    te = _load_split(data_dir, stub, "test")

    Xtr, ytr = _matrix(tr + va, feature_order)   # fit on train+valid, report on test
    Xte, yte = _matrix(te, feature_order)

    print(f"\n=== TOP  Phase {phase_key}  (split: TOP official train+valid / test) ===")
    print(f"  features: {feature_order}")
    if leaky:
        print("  *** --with-leaky: includes outcome-derived features. DEMO ONLY -- do not report. ***")
    print(f"  train+valid n={len(ytr)} (pos={ytr.mean():.3f})   test n={len(yte)} (pos={yte.mean():.3f})")
    nunique = [len(np.unique(Xtr[:, i][~np.isnan(Xtr[:, i])])) for i in range(Xtr.shape[1])]
    degenerate = [feature_order[i] for i, u in enumerate(nunique) if u <= 1]
    if degenerate:
        print(f"  note: constant/absent features on this split: {degenerate}")

    try:
        model = make_xgboost()
    except ImportError:
        print("  xgboost not installed -- `pip install xgboost` then rerun. Skipping fit.")
        return 1

    model.fit(Xtr, ytr)
    p = model.predict_proba(Xte)[:, 1]
    auroc = roc_auc_score(yte, p)
    prauc = average_precision_score(yte, p)
    ll = log_loss(yte, p, labels=[0, 1])          # DSAI competition metric
    f1 = f1_score(yte, (p >= 0.5).astype(int))     # DSAI reported this too (thr=0.5)
    print(f"  log-loss = {ll:.4f}   F1@0.5 = {f1:.4f}   <- DSAI competition metrics")
    print(f"  AUROC    = {auroc:.4f}   PR-AUC = {prauc:.4f}")
    print("  note: these are TRIAL-level. The DSAI 0.202 log-loss is at the "
          "drug-indication level (after the ridge roll-up).")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", default="data/top", type=Path)
    ap.add_argument("--phase", default="all", choices=[*PHASES, "all"])
    ap.add_argument("--with-leaky", action="store_true",
                    help="add outcome-derived features (status/why_stop/duration) to show "
                         "the leakage inflation -- demo only, never report these numbers")
    args = ap.parse_args()

    feature_order = list(PRE_TRIAL_FEATURES)
    if args.with_leaky:
        feature_order += LEAKY_FEATURES

    if not args.data_dir.exists():
        print(f"data dir not found: {args.data_dir}")
        return 2
    keys = list(PHASES) if args.phase == "all" else [args.phase]
    rc = 0
    for k in keys:
        try:
            rc |= _run_phase(args.data_dir, k, feature_order, args.with_leaky)
        except FileNotFoundError as e:
            print(f"\n=== TOP Phase {k} === missing file: {e} (run TOP's data_split.py)")
            rc |= 1
    return rc


if __name__ == "__main__":
    sys.exit(main())