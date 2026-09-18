#!/usr/bin/env python3
"""Audit DSAI's crown-jewel feature on open data BEFORE building it into a model.

The feature (their drugclass2/3/4): for a drug's mechanism of action, the historical
approval rate of OTHER drugs sharing that mechanism/target, Beta(1/3, 2.7)-smoothed, averaged
over the drug's classes. It is a *generalizable prior* (doesn't need to have seen the
molecule), which is why it can work on novel drugs where fingerprints cannot -- and it is
the most leakage-prone feature we have (R7): built carelessly it sees the future and itself.

This audit answers three questions on TOP, using TOP's own past->future split so the prior
is leakage-safe by construction (built ONLY from the fit split, applied to the test split):
  1. COVERAGE  -- what fraction of holdout drug-indications have a ChEMBL mechanism/target,
                  and a class with any fit-set history? (caps the feature, like fp coverage)
  2. OWN SIGNAL-- AUROC / log-loss of the class prior alone vs the holdout label, vs base rate
  3. LEAKAGE   -- recompute the prior the WRONG way (using holdout outcomes too); AUROC must
                  inflate. If it doesn't, something is off.

Class key = ChEMBL target_chembl_id (primary) or mechanism_of_action string (fallback).
ChEMBL mechanisms are queried live and cached to data/chembl/moa_class.csv.

Runnable now: needs only the ChEMBL client + data/chembl/units.csv (no AACT, no dates, no CTO).
Deps: pip install chembl_webresource_client pandas
Usage: python scripts/audit_moa_prior.py --units data/chembl/units.csv --max-phase II
       python scripts/audit_moa_prior.py --class-key moa      # mechanism-string classes
"""
from __future__ import annotations
import argparse
import csv
import math
from pathlib import Path

_PHASE_ORD = {"phase_I": 1, "phase_II": 2, "phase_III": 3}
_PART_RANK = {"train": 0, "valid": 1, "test": 2}
# DSAI's smoothing prior: Beta(1/3, 2.7) -> prior mean ~0.11, a sensible low approval base
PRIOR_A, PRIOR_B = 1.0 / 3.0, 2.7


# ---- pure feature logic (offline-testable) ---------------------------------
def smoothed_logit(approvals, counts):
    """Beta(1/3,2.7) posterior-mean approval rate on the logit scale (DSAI-style)."""
    a = PRIOR_A + approvals
    b = PRIOR_B + (counts - approvals)
    rate = a / (a + b)
    return math.log(rate) - math.log(1 - rate)


def class_stats(units):
    """fit units -> {class_key: [counts, approvals]}. Each unit contributes to every class
    of its molecules. `units` = list of dicts with 'classes' (set) and 'label' (0/1)."""
    stats = {}
    for u in units:
        for c in u["classes"]:
            s = stats.setdefault(c, [0, 0])
            s[0] += 1
            s[1] += u["label"]
    return stats


def unit_prior(classes, stats):
    """Mean smoothed-logit class rate over the unit's classes present in `stats`; None if
    none are covered. Averaging over classes mirrors drugclass2's mean over MoAs."""
    vals = [smoothed_logit(stats[c][1], stats[c][0]) for c in classes if c in stats]
    return sum(vals) / len(vals) if vals else None


def auroc(y, p):
    """Mann-Whitney AUROC (pure; no sklearn). Ties get 0.5 credit."""
    pos = [pi for yi, pi in zip(y, p) if yi == 1]
    neg = [pi for yi, pi in zip(y, p) if yi == 0]
    if not pos or not neg:
        return float("nan")
    wins = 0.0
    for pp in pos:
        for nn in neg:
            wins += 1.0 if pp > nn else 0.5 if pp == nn else 0.0
    return wins / (len(pos) * len(neg))


def logloss(y, p):
    eps = 1e-6
    s = 0.0
    for yi, pi in zip(y, p):
        pi = min(1 - eps, max(eps, pi))
        s += -(yi * math.log(pi) + (1 - yi) * math.log(1 - pi))
    return s / len(y)


def _sigmoid(x):
    return 1.0 / (1.0 + math.exp(-x))


# ---- ChEMBL mechanism resolution (cached) ----------------------------------
class MoaClasses:
    FIELDS = ["chembl_id", "targets", "mechanisms", "version"]
    VERSION = "1"

    def __init__(self, cache_path: Path):
        self.cache_path = cache_path
        self.cache = {}
        self._client = None
        self._new = 0
        if cache_path.exists():
            with cache_path.open(encoding="utf-8") as f:
                rdr = csv.DictReader(f)
                rows = list(rdr) if set(rdr.fieldnames or []) == set(self.FIELDS) else None
            if rows is not None and all(r.get("version") == self.VERSION for r in rows):
                for r in rows:
                    self.cache[r["chembl_id"]] = r
            else:
                print(f"  (moa cache version changed -> rebuilding {cache_path.name})")

    def _mech(self):
        if self._client is None:
            from chembl_webresource_client.new_client import new_client
            self._client = new_client.mechanism
        return self._client

    def get(self, chembl_id):
        """-> (set(target_chembl_ids), set(mechanism_of_action strings))."""
        if chembl_id in self.cache:
            r = self.cache[chembl_id]
            return (set(filter(None, r["targets"].split("|"))),
                    set(filter(None, r["mechanisms"].split("|"))))
        targets, mechs = set(), set()
        try:
            for m in self._mech().filter(molecule_chembl_id=chembl_id).only(
                    ["target_chembl_id", "mechanism_of_action"]):
                if m.get("target_chembl_id"):
                    targets.add(m["target_chembl_id"])
                if m.get("mechanism_of_action"):
                    mechs.add(m["mechanism_of_action"].strip().lower())
        except Exception:
            pass
        self.cache[chembl_id] = {"chembl_id": chembl_id, "targets": "|".join(sorted(targets)),
                                 "mechanisms": "|".join(sorted(mechs)), "version": self.VERSION}
        self._new += 1
        if self._new % 25 == 0:
            self.flush()
        return targets, mechs

    def flush(self):
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        with self.cache_path.open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=self.FIELDS)
            w.writeheader()
            for r in self.cache.values():
                w.writerow(r)


def main() -> int:
    import pandas as pd
    ap = argparse.ArgumentParser()
    ap.add_argument("--units", default=Path("data/chembl/units.csv"), type=Path)
    ap.add_argument("--cache", default=Path("data/chembl/moa_class.csv"), type=Path)
    ap.add_argument("--max-phase", choices=["I", "II", "III"], default="II")
    ap.add_argument("--class-key", choices=["target", "moa"], default="target")
    args = ap.parse_args()
    cap = {"I": 1, "II": 2, "III": 3}[args.max_phase]

    u = pd.read_csv(args.units)
    u = u[u["__phase"].map(_PHASE_ORD).fillna(9) <= cap].copy()
    u["unit_key"] = u["unit_mols"] + " :: " + u["unit_indication"].astype(str)
    # one row per drug-indication: its label + latest split part
    g = u.groupby("unit_key").agg(unit_mols=("unit_mols", "first"),
                                  label=("unit_label", "first"),
                                  rank=("__part", lambda s: max(_PART_RANK[x] for x in s)))
    print(f"phase<= {args.max_phase}: {len(g)} drug-indication units")

    resolver = MoaClasses(args.cache)
    mols = sorted({m for key in g["unit_mols"] for m in str(key).split("+") if m})
    print(f"resolving MoA/target for {len(mols)} unique molecules (cached after) ...", flush=True)
    mol_classes = {}
    for i, cid in enumerate(mols, 1):
        targets, mechs = resolver.get(cid)
        mol_classes[cid] = targets if args.class_key == "target" else mechs
        # target primary with mechanism fallback when a molecule has no target
        if args.class_key == "target" and not targets:
            mol_classes[cid] = {f"moa::{x}" for x in mechs}
        if i % 50 == 0 or i == len(mols):
            print(f"  {i}/{len(mols)}", flush=True)
    resolver.flush()

    units = []
    for key, r in g.iterrows():
        classes = set()
        for m in str(r["unit_mols"]).split("+"):
            classes |= mol_classes.get(m, set())
        units.append({"classes": classes, "label": int(r["label"]),
                      "is_test": r["rank"] == _PART_RANK["test"]})
    fit = [x for x in units if not x["is_test"]]
    test = [x for x in units if x["is_test"]]

    # coverage
    any_class = sum(1 for x in test if x["classes"])
    stats = class_stats(fit)
    covered = [x for x in test if unit_prior(x["classes"], stats) is not None]
    print(f"\n{'='*68}\nMoA/target PRIOR AUDIT (class-key={args.class_key})\n{'='*68}")
    print(f"  fit units={len(fit)}  test units={len(test)}  distinct classes(fit)={len(stats)}")
    print(f"  test coverage: {any_class}/{len(test)} have a class "
          f"({100*any_class/len(test):.1f}%); {len(covered)}/{len(test)} have a class with "
          f"fit-history ({100*len(covered)/len(test):.1f}%)")
    cnts = sorted((stats[c][0] for c in stats))
    if cnts:
        med = cnts[len(cnts)//2]
        print(f"  class history (drugs/class): min={cnts[0]} median={med} max={cnts[-1]}")

    # own signal on covered holdout units (leakage-safe: prior built from fit only)
    yt = [x["label"] for x in covered]
    pt = [_sigmoid(unit_prior(x["classes"], stats)) for x in covered]
    base = sum(l for l in (x["label"] for x in fit)) / max(1, len(fit))
    print(f"\n  base rate (fit) = {base:.3f}")
    if len(set(yt)) > 1:
        print(f"  MoA-prior alone : AUROC={auroc(yt, pt):.4f}  log-loss={logloss(yt, pt):.4f}"
              f"  (n={len(yt)}, holdout pos-rate={sum(yt)/len(yt):.3f})")
        print(f"  base-rate pred  : log-loss={logloss(yt, [base]*len(yt)):.4f}")
    else:
        print("  (holdout one-class after coverage filter -- cannot score)")

    # leakage check: build the prior the WRONG way (include holdout outcomes)
    stats_leak = class_stats(fit + test)
    covered_leak = [x for x in test if unit_prior(x["classes"], stats_leak) is not None]
    ylk = [x["label"] for x in covered_leak]
    plk = [_sigmoid(unit_prior(x["classes"], stats_leak)) for x in covered_leak]
    if len(set(ylk)) > 1:
        print(f"\n  LEAKAGE CHECK (prior built WITH holdout outcomes -- must be inflated):")
        print(f"    leaky AUROC={auroc(ylk, plk):.4f}  vs safe AUROC={auroc(yt, pt):.4f}")
        print("    a clear gap = the temporal split is doing its job; ~equal = investigate.")
    print(f"\n  cache -> {args.cache}")
    print("  read: coverage caps the feature; 'MoA-prior alone' AUROC vs 0.5 is whether the")
    print("  class prior carries real signal on open data before we build the full model.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())