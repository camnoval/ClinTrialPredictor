#!/usr/bin/env python3
"""R5 audit: is the DSAI drug-indication roll-up feasible on TOP, and how to key it?

The DSAI-2019 winning workflow predicts at the DRUG-INDICATION level: trial-level xgboost
predictions are combined (relaxed ridge) into one prediction per drug-indication, and a
Bayesian logistic on drug-indication features is ensembled in. TOP is TRIAL-level, so to
mimic it we must (1) define the drug-indication unit, (2) group trials under it, and
(3) define its label. This script measures coverage so those choices come from data, not
guesses (Risks.md R5). It reads the raw TOP CSVs directly (not through TopSource) so it
can see the full multi-valued drug/disease lists.

Usage: python scripts/audit_rollup.py --data-dir <TOP data dir>
"""
from __future__ import annotations
import argparse
import ast
from pathlib import Path


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
                # icdcodes nest one level deeper: ["['J45','J46']"] -> flatten
                out = []
                for x in v:
                    xs = str(x).strip()
                    if xs.startswith("[") and xs.endswith("]"):
                        try:
                            inner = ast.literal_eval(xs)
                            out += [str(i).strip() for i in inner]
                            continue
                        except (ValueError, SyntaxError):
                            pass
                    out.append(xs)
                return [x for x in out if x]
        except (ValueError, SyntaxError):
            pass
    return [s]


def _first(xs):
    return xs[0] if xs else None


def _pct(n, d):
    return f"{100.0 * n / d:.1f}%" if d else "n/a"


def main() -> int:
    import numpy as np
    import pandas as pd

    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", required=True, type=Path)
    args = ap.parse_args()

    frames = []
    for phase in ("phase_I", "phase_II", "phase_III"):
        for part in ("train", "valid", "test"):
            p = args.data_dir / f"{phase}_{part}.csv"
            if p.exists():
                df = pd.read_csv(p)
                df["__phase"] = phase
                df["__part"] = part
                frames.append(df)
    if not frames:
        print(f"no phase CSVs under {args.data_dir}")
        return 2
    d = pd.concat(frames, ignore_index=True)

    print(f"\n{'='*64}\nTOP roll-up audit  ({len(d)} trial rows)\n{'='*64}")
    print("rows per phase:")
    print(d.groupby("__phase").agg(n=("label", "size"), pos_rate=("label", "mean")).round(3))

    d["_drugs"] = d["drugs"].map(_as_list)
    d["_dis"] = d["diseases"].map(_as_list)
    d["_icd"] = d["icdcodes"].map(_as_list)
    d["_smi"] = d["smiless"].map(_as_list)
    d["nd"] = d["_drugs"].map(len)
    d["ndis"] = d["_dis"].map(len)

    n = len(d)
    print("\n-- multi-valued columns (roll-up key ambiguity, R5) --")
    print(f"  single-drug trials    : {_pct((d.nd == 1).sum(), n)}   (>=2 drugs: {_pct((d.nd >= 2).sum(), n)})")
    print(f"  single-disease trials : {_pct((d.ndis == 1).sum(), n)}   (>=2 diseases: {_pct((d.ndis >= 2).sum(), n)})")
    clean = d[(d.nd == 1) & (d.ndis == 1)]
    print(f"  CLEAN (1 drug & 1 disease): {len(clean)} trials  ({_pct(len(clean), n)})")

    # key policy A: first drug-name x first disease-name
    d["kA"] = list(zip(d["_drugs"].map(_first), d["_dis"].map(_first)))
    # key policy B: first SMILES x first ICD  (molecule-level, name-agnostic)
    d["kB"] = list(zip(d["_smi"].map(_first), d["_icd"].map(_first)))

    for name, key in (("name (drug x disease)", "kA"), ("molecule (SMILES x ICD)", "kB")):
        g = d.groupby(key)
        sizes = g.size()
        multi = (sizes >= 2).sum()
        print(f"\n-- key = {name} --")
        print(f"  unique drug-indication pairs : {len(sizes)}")
        print(f"  pairs with >=2 trials        : {multi}  ({_pct(multi, len(sizes))})   <- roll-up only helps here")
        print(f"  trials-per-pair              : mean {sizes.mean():.2f}, median {int(sizes.median())}, max {sizes.max()}")
        # phase span: pairs whose trials appear in >1 distinct phase
        pf = d.groupby(key)["__phase"].nunique()
        print(f"  pairs spanning >1 phase      : {(pf >= 2).sum()}  ({_pct((pf >= 2).sum(), len(pf))})")
        # within-pair label consistency
        lab = d.groupby(key)["label"].agg(["mean", "size"])
        mixed = ((lab["mean"] > 0) & (lab["mean"] < 1)).sum()
        print(f"  pairs with mixed labels      : {mixed}  ({_pct(mixed, len(lab))})   <- need a roll-up label rule")

    # candidate drug-indication label: does the pair reach a successful Phase III?
    print("\n-- candidate drug-indication label (approval proxy) --")
    has_p3 = d.groupby("kA")["__phase"].apply(lambda s: (s == "phase_III").any())
    p3succ = d[d["__phase"] == "phase_III"].groupby("kA")["label"].max()
    pairs = has_p3.index
    reached = has_p3.sum()
    approved = (p3succ == 1).reindex(pairs).fillna(False).sum()
    print(f"  pairs with any Phase III trial : {reached}  ({_pct(reached, len(pairs))})")
    print(f"  of those, >=1 successful P3    : {approved}  ({_pct(approved, reached)})")
    print("  (i.e. label(drug,indication)=1 if it has a successful Phase III trial; "
          "predicted from its earlier-phase trials -- the closest analog to DSAI's "
          "'approved for indication' target.)")

    print("\nRead this before building the roll-up:")
    print(" - if 'pairs with >=2 trials' is small, the ridge roll-up has little to combine;")
    print("   most drug-indications are a single trial and the roll-up ~ identity.")
    print(" - pick the key (name vs molecule) with better coverage and fewer collisions.")
    print(" - the mixed-label share tells you how much the roll-up label rule matters.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())