#!/usr/bin/env python3
"""Build the CTO 2020-2024 forward set as a TOP-schema CSV the existing spine can read.

The spine (build_units.py / train_rollup.py) reads TOP's raw columns directly and computes
fingerprints from `smiless` and ICD features from `icdcodes`. To run the forward test
through the IDENTICAL featurizer (R11 parity), this builder emits exactly those columns:

    nctid, phase, status, why_stop, label, diseases, icdcodes, drugs, smiless, criteria

filled from ClinicalTrials.gov v2 + the cross-verified name->molecule resolver
(audit_cto_coverage.MultiSourceResolver, reusing its warmed data/cto/name2chembl.csv cache)
+ the MONDO crosswalk (condition/MeSH -> MONDO -> ICD, the inverse of TOP's icd->mondo).

Decisions baked in (see docs/Handoff.md, Risks R9/R11):
- label   = CTO gold `labels` (label (ii)); provenance is DERIVED (weak supervision, R9).
- smiless = only resolutions at >= --min-confidence (default 'high' = ChEMBL & PubChem
            agree). Biologic/coded/unresolved trials keep an empty smiless -- they stay in
            the file for label-(ii) scoring but carry no fingerprint; a sidecar column flags
            them so the forward result can report small-molecule vs biologic separately.
- drugs   = all cleaned regimen names (for the n_drugs count), incl. combo components.
- dedup   = drop any nctid already in TOP train/valid/test so the holdout is truly unseen.

Sidecar columns (min_confidence, n_smiles_resolved, is_small_molecule, completion_year,
start_date) are ignored by the spine but let build/train report coverage honestly.

Known limitation (logged, not silently wrong): a fixed-dose combo arriving as ONE token
(e.g. "ATRIPLA") resolves to a single molecule via RxNorm's first ingredient, under-counting
the other components. Multi-ingredient FDC expansion is a follow-up; the molecule it does
pick is real, so nothing is corrupted.

Usage:
  python scripts/build_cto.py --limit 200                 # smoke: first 200 gold trials
  python scripts/build_cto.py --top-dir data/top          # full, with TOP dedup
  python scripts/build_cto.py --min-confidence medium     # widen coverage (less strict)
"""
from __future__ import annotations
import argparse
import csv as _csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from probe_ctgov import extract_trial                                    # noqa: E402
from audit_cto_coverage import (                                         # noqa: E402
    HF_LABELS, fetch_batch, MultiSourceResolver, drug_candidates, cohort_ok,
    trial_mondo, conf_usable, _load_mondo, _norm,
)


def to_list_literal(xs):
    """Python-literal list string, matching TOP's columns (parsed by build_units._as_list)."""
    seen, out = set(), []
    for x in xs:
        s = str(x).strip()
        if s and s not in seen:
            seen.add(s)
            out.append(s)
    return "[" + ", ".join(repr(s) for s in out) + "]"


def load_mondo2icd(xref_path):
    """mondo_id -> set(icd10 codes); the inverse direction TOP does not need but CTO does."""
    import pandas as pd
    m = pd.read_csv(xref_path).fillna("")
    out = {}
    for _, r in m.iterrows():
        codes = {c.strip() for c in str(r["icd10"]).split("|") if c.strip()}
        if codes:
            out.setdefault(r["mondo_id"], set()).update(codes)
    return out


def trial_icdcodes(trial, name2, mesh2, mondo2icd):
    icds = set()
    for mid in trial_mondo(trial, name2, mesh2):
        icds |= mondo2icd.get(mid, set())
    return sorted(icds)


def assemble_row(trial, records, gold, name2, mesh2, mondo2icd, min_conf):
    """Pure: one CT.gov trial (+resolver records + maps) -> a TOP-schema CSV row dict."""
    drugs, smis = [], []
    for raw in trial.get("drug_or_biological_names") or []:
        for nm in drug_candidates(raw):
            rec = records.get(nm)
            if rec is None:
                continue
            drugs.append(nm)
            if rec.get("smiles") and conf_usable(rec.get("confidence", "miss"), min_conf):
                smis.append(rec["smiles"])
    conditions = trial.get("conditions") or []
    icds = trial_icdcodes(trial, name2, mesh2, mondo2icd)
    phases = trial.get("phases") or []
    lab = gold.get(trial.get("nct_id"))
    return {
        # --- TOP schema (consumed by the spine) ---
        "nctid": trial.get("nct_id"),
        "phase": phases[-1] if phases else "",       # max phase token; train_forward normalizes
        "status": trial.get("overall_status") or "",
        "why_stop": trial.get("why_stopped") or "",
        "label": "" if lab is None else int(lab),
        "diseases": to_list_literal(conditions),
        "icdcodes": to_list_literal(icds),
        "drugs": to_list_literal(drugs),
        "smiless": to_list_literal(smis),
        "criteria": trial.get("criteria") or "",
        # --- sidecar (ignored by spine; for honest coverage reporting) ---
        "min_confidence": min_conf,
        "n_smiles_resolved": len(smis),
        "is_small_molecule": int(bool(smis)),
        "completion_date": trial.get("completion_date") or "",
        "start_date": trial.get("start_date") or "",
    }


TOP_COLS = ["nctid", "phase", "status", "why_stop", "label",
            "diseases", "icdcodes", "drugs", "smiless", "criteria"]
SIDECAR = ["min_confidence", "n_smiles_resolved", "is_small_molecule",
           "completion_date", "start_date"]


def load_top_nctids(top_dir: Path):
    ids = set()
    if not top_dir:
        return ids
    for phase in ("phase_I", "phase_II", "phase_III"):
        for part in ("train", "valid", "test"):
            p = top_dir / f"{phase}_{part}.csv"
            if p.exists():
                with p.open(newline="", encoding="utf-8") as f:
                    for row in _csv.DictReader(f):
                        v = (row.get("nctid") or "").strip()
                        if v:
                            ids.add(v)
    return ids


def main() -> int:
    import pandas as pd
    ap = argparse.ArgumentParser()
    ap.add_argument("--labels", default=HF_LABELS)
    ap.add_argument("--xref", default=Path("data/ontology/mondo_xref.csv"), type=Path)
    ap.add_argument("--cache", default=Path("data/cto/name2chembl.csv"), type=Path)
    ap.add_argument("--top-dir", type=Path, default=None, help="TOP dir for NCT dedup (R11)")
    ap.add_argument("--out", default=Path("data/cto/cto_trials.csv"), type=Path)
    ap.add_argument("--min-confidence", choices=["high", "medium"], default="high")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--no-rxnorm", action="store_true")
    args = ap.parse_args()

    print(f"{'='*70}\nBUILD CTO forward set (TOP schema)\n{'='*70}")
    lab = pd.read_csv(args.labels, usecols=["nct_id", "labels"])
    if args.limit:
        lab = lab.head(args.limit)
    ids = [str(x) for x in lab["nct_id"].tolist()]
    gold = {str(k): (None if pd.isna(v) else int(v))
            for k, v in zip(lab["nct_id"], lab["labels"])}
    print(f"gold trials: {len(ids)}   min_confidence={args.min_confidence}", flush=True)

    top_ids = load_top_nctids(args.top_dir)
    print(f"TOP nctids for dedup: {len(top_ids)}", flush=True)

    studies = fetch_batch(ids)
    trials = [extract_trial(s) for s in studies]

    name2, mesh2, efo2, _icd2 = _load_mondo(args.xref)
    mondo2icd = load_mondo2icd(args.xref)
    resolver = MultiSourceResolver(args.cache, use_rxnorm=not args.no_rxnorm)

    cohort = [t for t in trials if cohort_ok(t)]
    names = sorted({n for t in cohort
                    for x in (t.get("drug_or_biological_names") or [])
                    for n in drug_candidates(x)})
    n_cached = sum(1 for nm in names if nm in resolver.cache)
    print(f"cohort trials: {len(cohort)}   unique names: {len(names)} "
          f"({n_cached} cached)", flush=True)
    records = {}
    for i, nm in enumerate(names, 1):
        records[nm] = resolver.resolve(nm)
        if i % 20 == 0 or i == len(names):
            print(f"  resolved {i}/{len(names)}", flush=True)
    resolver.flush()

    kept, deduped = [], 0
    for t in cohort:
        if t.get("nct_id") in top_ids:
            deduped += 1
            continue
        row = assemble_row(t, records, gold, name2, mesh2, mondo2icd, args.min_confidence)
        kept.append(row)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", newline="", encoding="utf-8") as f:
        w = _csv.DictWriter(f, fieldnames=TOP_COLS + SIDECAR)
        w.writeheader()
        w.writerows(kept)

    n = len(kept)
    sm = sum(r["is_small_molecule"] for r in kept)
    pos = sum(1 for r in kept if r["label"] == 1)
    neg = sum(1 for r in kept if r["label"] == 0)
    print(f"\n{'='*70}\nWROTE {n} CTO trials -> {args.out}\n{'='*70}")
    print(f"  deduped against TOP        : {deduped}")
    print(f"  small-molecule (fingerprint): {sm} ({100*sm/n:.1f}%)" if n else "  (none)")
    print(f"  biologic/unresolved         : {n - sm}")
    print(f"  CTO gold label balance      : {pos} pos / {neg} neg "
          f"({100*pos/(pos+neg):.1f}% pos)" if (pos + neg) else "  (no labels)")
    print("\n  next: train_forward.py fits the spine on all TOP and predicts this file,")
    print("  reporting log-loss/AUROC/PR-AUC + calibration + seen/novel, small-mol vs biologic.")

    # --- explain the coverage number: what are the non-small-molecule names? ---
    from collections import Counter
    tiers = Counter(records[nm]["confidence"] for nm in names)
    miss_known = sum(1 for nm in names
                     if records[nm]["confidence"] == "miss" and records[nm].get("ingredient"))
    miss_unknown = sum(1 for nm in names
                       if records[nm]["confidence"] == "miss" and not records[nm].get("ingredient"))

    def _has_sm(t, mc):
        for raw in t.get("drug_or_biological_names") or []:
            for nm in drug_candidates(raw):
                r = records.get(nm)
                if r and r.get("smiles") and conf_usable(r.get("confidence", "miss"), mc):
                    return True
        return False

    kept_trials = [t for t in cohort if t.get("nct_id") not in top_ids]
    sm_med = sum(_has_sm(t, "medium") for t in kept_trials)
    print(f"\n{'='*70}\nWHY THE COVERAGE IS WHAT IT IS (unique names n={len(names)})\n{'='*70}")
    print(f"  high (cross-verified)        : {tiers['high']}")
    print(f"  medium (single source)       : {tiers['medium']}   <- excluded at min_conf=high")
    print(f"  conflict (sources disagree)  : {tiers['conflict']}")
    print(f"  miss, RxNorm/PubChem KNOWS it : {miss_known}   <- genuine biologic (no small-mol structure)")
    print(f"  miss, nothing recognized it  : {miss_unknown}   <- coded / novel / junk")
    print(f"\n  small-molecule TRIALS: high={sm} ({100*sm/n:.0f}%)  "
          f"high+medium={sm_med} ({100*sm_med/n:.0f}%)   (of {n})")
    print("  -> 'miss KNOWS it' is the true-biologic floor we cannot fingerprint;")
    print("     'high vs high+medium' is the recall we could reclaim by relaxing confidence.")

    # surface the big bucket so its composition is visible (coded compound? junk? real drug?)
    unrecognized = sorted(nm for nm in names
                          if records[nm]["confidence"] == "miss" and not records[nm].get("ingredient"))
    conflicts = sorted(nm for nm in names if records[nm]["confidence"] == "conflict")
    print(f"\n  'nothing recognized' sample ({len(unrecognized)} total):")
    for nm in unrecognized[:30]:
        print(f"      {nm}")
    print(f"\n  conflicts ({len(conflicts)}): {conflicts}")
    review = args.out.parent / "name_review.csv"
    with review.open("w", newline="", encoding="utf-8") as f:
        w = _csv.DictWriter(f, fieldnames=["name", "confidence", "chembl_id", "ingredient"])
        w.writeheader()
        for nm in names:
            r = records[nm]
            w.writerow({"name": nm, "confidence": r["confidence"],
                        "chembl_id": r.get("chembl_id", ""), "ingredient": r.get("ingredient", "")})
    print(f"\n  full name review -> {review}  (sort by confidence to curate)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())