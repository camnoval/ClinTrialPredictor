#!/usr/bin/env python3
"""Build the MoA-class target encoding (DSAI crown jewel) at the drug-indication level.

Step 1 of the "next build" (see docs/DSAI_Reconstruction_Plan.md): promote the VALIDATED
audit (audit_moa_prior.py, AUROC 0.674) from TOP's split proxy to the REAL DSAI rule -- a
time-respecting leave-one-out keyed on AACT `phaseendyear`, with the index drug-indication's
own outcome masked (R7). The count/mask/Beta logic lives in the pure, tested engine
`trial_pos.services.moa_encoding`; this script is the audit-first I/O driver.

Granularity:
  any  -> class_approvals/class_counts/meancll50/meanclu50   (built ALWAYS: needs MoA + year)
  dt/ta-> dt*/ta* columns                                    (built IFF --unit-class supplies
          a per-unit disease-type / therapeutic-area key; else emitted NaN and flagged)

The dt/ta key does not exist in the handoff yet (the disease-type-152 / TA-9 assignment is a
separate reconstruction step). So this runs today at `any` granularity and lights up dt/ta the
moment that key lands -- no code change, just pass --unit-class.

Inputs (all already produced upstream):
  --units       data/chembl/units.csv         (build_units.py: unit_mols, unit_indication,
                                                unit_label, nctid, __phase, __part)
  --aact        data/aact/aact_features.csv    (pull_aact.py: nct_id, phaseendyear)
  --moa-cache   data/chembl/moa_class.csv       (audit_moa_prior.py cache: targets/mechanisms)
  --unit-class  optional CSV: unit_key, dt_key, ta_key[, moa_classes]  (enables dt/ta)

Audit-first: prints coverage per granularity, class-history stats, the safe MoA-prior AUROC
vs base rate, and the MANDATORY leakage check (leak_own / leak_future MUST inflate AUROC).
Deps: pandas (+ scipy for the Beta 25/75 pctl columns; without it those emit NaN + a note).
Usage:
  python scripts/build_moa_target_encoding.py --units data/chembl/units.csv \
      --aact data/aact/aact_features.csv --moa-cache data/chembl/moa_class.csv
"""
from __future__ import annotations

import argparse
import csv
from pathlib import Path

from trial_pos.services import moa_encoding as M
from trial_pos.services.moa_encoding import (
    UnitRow, History, encode, SAFE, LEAK_FUTURE, LEAK_OWN, LEAK_BOTH)

_PHASE_ORD = {"phase_I": 1, "phase_II": 2, "phase_III": 3}


def load_moa_cache(path: Path, class_key: str) -> dict:
    """chembl_id -> class set. Mirrors audit_moa_prior: target primary, moa:: fallback."""
    mol_classes: dict[str, set] = {}
    if not path.exists():
        print(f"!! MoA cache not found: {path} -- run audit_moa_prior.py first to build it.")
        return mol_classes
    with path.open(encoding="utf-8") as f:
        for r in csv.DictReader(f):
            targets = set(filter(None, (r.get("targets") or "").split("|")))
            mechs = set(filter(None, (r.get("mechanisms") or "").split("|")))
            if class_key == "target":
                mol_classes[r["chembl_id"]] = targets if targets else {f"moa::{x}" for x in mechs}
            else:
                mol_classes[r["chembl_id"]] = mechs
    return mol_classes


def _pct(n, d):
    return f"{100.0 * n / d:.1f}%" if d else "n/a"


def main() -> int:
    import pandas as pd

    ap = argparse.ArgumentParser()
    ap.add_argument("--units", default=Path("data/chembl/units.csv"), type=Path)
    ap.add_argument("--aact", default=Path("data/aact/aact_features.csv"), type=Path)
    ap.add_argument("--moa-cache", default=Path("data/chembl/moa_class.csv"), type=Path)
    ap.add_argument("--unit-class", default=None, type=Path, help="unit_key,dt_key,ta_key[,moa_classes]")
    ap.add_argument("--class-key", choices=["target", "moa"], default="target")
    ap.add_argument("--year-agg", choices=["max", "min"], default="max",
                    help="aggregate phaseendyear over a unit's trials (max=program readout)")
    ap.add_argument("--own-mask", choices=["drug", "unit"], default="drug",
                    help="LOO mask for emitted features: 'drug' removes the index drug's whole "
                         "contribution (other-drugs-only, generalization-honest, DEFAULT); "
                         "'unit' removes only the index indication (lets same-drug siblings leak, R8)")
    ap.add_argument("--max-phase", choices=["I", "II", "III"], default="II")
    ap.add_argument("--include-priorless", action="store_true",
                    help="average smoothed rate over ALL of a drug's MoAs incl. no-history "
                         "(default off = match validated audit)")
    ap.add_argument("--out", default=Path("data/aact/moa_features.csv"), type=Path)
    args = ap.parse_args()
    cap = {"I": 1, "II": 2, "III": 3}[args.max_phase]

    # ---- load units, filter phase, keep the multi-nctid mapping for the year join ----
    u = pd.read_csv(args.units)
    u = u[u["__phase"].map(_PHASE_ORD).fillna(9) <= cap].copy()
    u["unit_key"] = u["unit_mols"].astype(str) + " :: " + u["unit_indication"].astype(str)
    print(f"{'='*70}\nMoA TARGET-ENCODING BUILD  (class-key={args.class_key}, "
          f"phase<= {args.max_phase}, year-agg={args.year_agg})\n{'='*70}")

    # ---- phaseendyear per unit: aggregate AACT year over the unit's trials ----
    year_by_nct = {}
    if args.aact.exists():
        a = pd.read_csv(args.aact)
        for _, r in a.iterrows():
            y = r.get("phaseendyear")
            if pd.notna(y):
                year_by_nct[str(r["nct_id"])] = int(y)
    else:
        print(f"!! AACT features not found: {args.aact} -- phaseendyear unknown for all units, "
              "the time-respecting LOO cannot run. Pull AACT first (pull_aact.py).")

    def agg_year(nctids):
        ys = [year_by_nct[str(n)] for n in nctids if str(n) in year_by_nct]
        if not ys:
            return None
        return max(ys) if args.year_agg == "max" else min(ys)

    unit_year, unit_label, unit_mols, unit_ind = {}, {}, {}, {}
    for key, grp in u.groupby("unit_key"):
        unit_year[key] = agg_year(grp["nctid"].tolist())
        unit_label[key] = int(grp["unit_label"].iloc[0])
        unit_mols[key] = str(grp["unit_mols"].iloc[0])
        unit_ind[key] = str(grp["unit_indication"].iloc[0])
    keys = sorted(unit_year)
    print(f"  drug-indication units (phase<= {args.max_phase}, deduped): {len(keys)}")
    n_year = sum(1 for k in keys if unit_year[k] is not None)
    print(f"  units with a phaseendyear (AACT-joined): {n_year}/{len(keys)} ({_pct(n_year, len(keys))})")

    # ---- MoA classes per unit ----
    mol_classes = load_moa_cache(args.moa_cache, args.class_key)
    override = {}
    dt_key, ta_key = {}, {}
    if args.unit_class and args.unit_class.exists():
        uc = pd.read_csv(args.unit_class).fillna("")
        for _, r in uc.iterrows():
            k = str(r["unit_key"])
            dt_key[k] = frozenset(filter(None, str(r.get("dt_key", "")).split("|")))
            ta_key[k] = frozenset(filter(None, str(r.get("ta_key", "")).split("|")))
            if "moa_classes" in uc.columns and str(r["moa_classes"]):
                override[k] = set(filter(None, str(r["moa_classes"]).split("|")))
        print(f"  --unit-class supplied: dt/ta granularity ENABLED (multi-hot; "
              f"units with dt={sum(1 for v in dt_key.values() if v)}, "
              f"ta={sum(1 for v in ta_key.values() if v)})")
    else:
        print("  --unit-class NOT supplied: dt/ta columns will be NaN (need disease-type/TA key).")

    def classes_for(key):
        if key in override:
            return override[key]
        cs = set()
        for m in unit_mols[key].split("+"):
            cs |= mol_classes.get(m, set())
        return cs

    rows = [UnitRow(unit_key=k, year=unit_year[k], label=unit_label[k],
                    classes=frozenset(classes_for(k)), drug=unit_mols[k],
                    dt=dt_key.get(k, frozenset()), ta=ta_key.get(k, frozenset())) for k in keys]
    hist = History(rows)
    row_by_key = {r.unit_key: r for r in rows}

    n_anyclass = sum(1 for r in rows if r.classes)
    print(f"  units with >=1 MoA class: {n_anyclass}/{len(rows)} ({_pct(n_anyclass, len(rows))})  "
          f"(distinct classes in history={len(hist._any)})")

    # ---- emit the feature matrix ----
    cols = ["unit_key", "unit_mols", "unit_indication", "phaseendyear", "unit_label",
            "dt_key", "ta_key", "moas", "dtmoas", "tamoas",
            "class_approvals", "class_counts", "meancll50", "meanclu50",
            "dtclass_approvals", "dtclass_counts", "dtmeancll50", "dtmeanclu50",
            "taclass_approvals", "taclass_counts", "tameancll50", "tameanclu50",
            "covered_any", "n_classes_any"]
    out_rows, scored = [], []
    cov = {"any": 0, "dt": 0, "ta": 0}
    mask = args.own_mask
    print(f"  emitting features with own-mask = '{mask}' "
          f"({'other-drugs-only, generalization-honest' if mask == 'drug' else 'index-indication-only (R8 recognition can leak)'})")
    for r in rows:
        e_any = encode(r, hist, "any", SAFE, mask, args.include_priorless)
        e_dt = encode(r, hist, "dt", SAFE, mask, args.include_priorless)
        e_ta = encode(r, hist, "ta", SAFE, mask, args.include_priorless)
        nmoa = len(r.classes)
        if e_any.mean_logit is not None:
            cov["any"] += 1
            scored.append(r)
        if e_dt.mean_logit is not None:
            cov["dt"] += 1
        if e_ta.mean_logit is not None:
            cov["ta"] += 1
        out_rows.append({
            "unit_key": r.unit_key, "unit_mols": unit_mols[r.unit_key],
            "unit_indication": unit_ind[r.unit_key],
            "phaseendyear": r.year, "unit_label": r.label,
            "dt_key": "|".join(sorted(r.dt)), "ta_key": "|".join(sorted(r.ta)),
            "moas": nmoa, "dtmoas": nmoa, "tamoas": nmoa,
            "class_approvals": e_any.ap_sum, "class_counts": e_any.cnt_sum,
            "meancll50": e_any.cll50, "meanclu50": e_any.clu50,
            "dtclass_approvals": e_dt.ap_sum, "dtclass_counts": e_dt.cnt_sum,
            "dtmeancll50": e_dt.cll50, "dtmeanclu50": e_dt.clu50,
            "taclass_approvals": e_ta.ap_sum, "taclass_counts": e_ta.cnt_sum,
            "tameancll50": e_ta.cll50, "tameanclu50": e_ta.clu50,
            "covered_any": int(e_any.mean_logit is not None), "n_classes_any": e_any.n_classes_hist,
        })
    args.out.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(out_rows).reindex(columns=cols).to_csv(args.out, index=False)

    print(f"\n{'='*70}\nCOVERAGE (units with a class carrying prior history, per granularity)\n{'='*70}")
    for g in ("any", "dt", "ta"):
        print(f"  {g:3s}: {cov[g]}/{len(rows)} ({_pct(cov[g], len(rows))})")
    cnts = sorted(hist._any[c].all.total for c in hist._any)
    if cnts:
        print(f"  class history (drugs/class): min={cnts[0]} median={cnts[len(cnts)//2]} max={cnts[-1]}")
    if not M.beta_quantiles(1, 2)[0] == M.beta_quantiles(1, 2)[0]:  # NaN check
        print("  !! scipy unavailable -> meancll50/meanclu50 columns are NaN. `pip install scipy`.")

    # ---- own-signal + MANDATORY leakage check (R7) on covered, labeled units ----
    y = [r.label for r in scored]
    base = sum(y) / len(y) if y else float("nan")
    if len(set(y)) > 1:
        def score(mode, msk="drug"):
            out = []
            for r in scored:
                ml = encode(r, hist, "any", mode, msk).mean_logit
                out.append(M.sigmoid(ml if ml is not None else 0.0))
            return out
        p_drug = score(SAFE, "drug")
        p_unit = score(SAFE, "unit")
        a_drug, a_unit = M.auroc(y, p_drug), M.auroc(y, p_unit)
        a_both = M.auroc(y, score(LEAK_BOTH))
        a_own = M.auroc(y, score(LEAK_OWN))
        a_fut = M.auroc(y, score(LEAK_FUTURE))
        a_safe = a_drug if mask == "drug" else a_unit
        p_safe = p_drug if mask == "drug" else p_unit
        print(f"\n{'='*70}\nOWN SIGNAL + LEAKAGE CHECK (any granularity, n={len(y)}, "
              f"pos-rate={base:.3f})\n{'='*70}")
        print(f"  SAFE drug-mask (other-drugs-only) : AUROC={a_drug:.4f}  <- generalization-honest")
        print(f"  SAFE unit-mask (siblings leak, R8): AUROC={a_unit:.4f}  log-loss={M.logloss(y, p_unit):.4f}")
        print(f"    gap {a_unit - a_drug:+.4f} = same-drug sibling-indication recognition "
              f"(NOT generalizable MoA signal)")
        print(f"  base-rate prediction              : log-loss={M.logloss(y, [base]*len(y)):.4f}")
        print(f"  emitted-mask ('{mask}') log-loss  : {M.logloss(y, p_safe):.4f}")
        print(f"  LEAK_both (holdout outcomes)      : AUROC={a_both:.4f}   <- R7 GATE: must exceed SAFE")
        print(f"    decompose: LEAK_own={a_own:.4f}  LEAK_future={a_fut:.4f}")
        ok = a_both > a_safe
        print(f"  R7 GATE: {'PASS' if ok else 'INVESTIGATE'} "
              f"-- leak_both = audit's 'prior built WITH holdout outcomes'; a clear gap means")
        print("    the temporal cutoff + own-mask are doing their job. (future-only inflation")
        print("    is data-dependent -- it needs within-class drift -- so it is diagnostic, not a gate.)")

        # does dt/ta-conditioning add signal over `any`? compare on each covered subset
        for gran in ("dt", "ta"):
            sub = [r for r in scored if encode(r, hist, gran, SAFE, mask).mean_logit is not None]
            y_g = [r.label for r in sub]
            if len(set(y_g)) <= 1:
                print(f"\n  {gran}-conditioning check: too few covered/one-class (n={len(y_g)}) -- skipped")
                continue
            p_any = [M.sigmoid(encode(r, hist, "any", SAFE, mask).mean_logit) for r in sub]
            p_g = [M.sigmoid(encode(r, hist, gran, SAFE, mask).mean_logit) for r in sub]
            a_any_sub, a_g_sub = M.auroc(y_g, p_any), M.auroc(y_g, p_g)
            print(f"\n  {gran}-conditioning check (n={len(y_g)} {gran}-covered, own-mask='{mask}'):")
            print(f"    any granularity : AUROC={a_any_sub:.4f}")
            print(f"    {gran:3s} granularity : AUROC={a_g_sub:.4f}  (gap {a_g_sub - a_any_sub:+.4f})")
        print("    (a real +gap means conditioning adds signal over the mechanism-only prior;")
        print("     ~0 means it does not, at that granularity, on this label.)")

        # --- dt verdict: is the dt lift real mechanism x disease, or base-rate / small-cell? ---
        dt_sub = [r for r in scored if encode(r, hist, "dt", SAFE, mask).mean_logit is not None]
        y_dt = [r.label for r in dt_sub]
        if len(set(y_dt)) > 1:
            import statistics
            # mechanism-FREE disease-type base rate: same-dt other-drug approvals (time-cut, drug-mask)
            hist_rows = [r for r in rows if r.year is not None and r.dt]
            def dt_baserate(u):
                cnt = ap = 0
                for m in hist_rows:
                    if m.drug == (u.drug or u.unit_key):
                        continue
                    if m.year > u.year:
                        continue
                    if m.dt & u.dt:
                        cnt += 1
                        ap += m.label
                return M.sigmoid(M.smoothed_logit(ap, cnt)) if cnt else 0.5
            a_dt = M.auroc(y_dt, [M.sigmoid(encode(r, hist, "dt", SAFE, mask).mean_logit) for r in dt_sub])
            a_base = M.auroc(y_dt, [dt_baserate(r) for r in dt_sub])
            a_dt_leak = M.auroc(y_dt, [M.sigmoid((encode(r, hist, "dt", LEAK_BOTH, mask).mean_logit or 0.0)) for r in dt_sub])
            support = [encode(r, hist, "dt", SAFE, mask).cnt_sum for r in dt_sub]
            support.sort()
            med = statistics.median(support)
            tiny = sum(1 for s in support if s <= 2) / len(support)
            print(f"\n  dt VERDICT (n={len(y_dt)}): is the +gap real mechanism x disease?")
            print(f"    dt (mechanism x disease)      : AUROC={a_dt:.4f}")
            print(f"    disease base-rate (no mechanism): AUROC={a_base:.4f}   "
                  f"<- if ~= dt, the lift is disease identity, not mechanism x disease")
            print(f"    dt LEAK_both (holdout)        : AUROC={a_dt_leak:.4f}   "
                  f"<- if ~= dt-safe, mask/cutoff aren't binding (small-cell risk)")
            print(f"    dt cell support (other-drugs) : median={med:.0f}, "
                  f"share with <=2 = {100*tiny:.0f}%   <- tiny cells -> memorization risk")
    else:
        print("\n  (covered holdout is one-class -- cannot score AUROC/leakage here)")

    print(f"\n  wrote -> {args.out}")
    print("  next: this feeds the trial->ridge matrix. dt/ta await the disease-type/TA key;")
    print("  rel_ph2_size_{dis,ta} + prior_approval + termination + orphan are the following steps.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())