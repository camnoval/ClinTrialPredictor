#!/usr/bin/env python3
"""Probe the ClinicalTrials.gov API v2 BEFORE writing cto_source.py.

Why: we chose to pull per-trial raw text on demand from CT.gov v2 (light, fresh, and the
same fetch path the endgame tool needs) instead of the 2 GB CTTI.zip. This probe fetches a
handful of NCT ids and prints the fields we would featurize, so we can confirm the real v2
shape and field names before committing to a source. It commits to no modeling choice.

What we expect v2 to give (and what it will NOT):
  present : phase(s), overallStatus, start/completion dates, whyStopped, conditions (names),
            interventions (type+name), eligibility criteria text, derived MeSH terms.
  absent  : SMILES and ICD codes -- no registry has these. We reconstruct them downstream:
            drug name -> SMILES (TOP drug2smiles / ChEMBL), condition name + MeSH -> MONDO
            -> ICD via data/ontology/mondo_xref.csv (R11 featurization parity).

The extract_trial() function here is deliberately the seed of cto_source.normalize().

Offline test / re-parse: --from-file reads a saved JSON (a single study object, or a
{"studies":[...]} page) and runs the same parser without touching the network.

Deps: stdlib only (urllib, json).
Usage:
  python scripts/probe_ctgov.py                       # 5 default 2020-24 drug trials
  python scripts/probe_ctgov.py --ids NCT05567952 NCT03086343
  python scripts/probe_ctgov.py --raw                 # also dump full JSON of the 1st study
  python scripts/probe_ctgov.py --from-file resp.json # re-parse a saved response, no network
"""
from __future__ import annotations
import argparse
import json
import sys
import time
import urllib.error
import urllib.request

V2_ONE = "https://clinicaltrials.gov/api/v2/studies/{nct}"
# real 2020-2024 trials taken from the CTO human_labels sample (guaranteed present, drug arms)
DEFAULT_IDS = ["NCT05567952", "NCT03086343", "NCT04888585", "NCT04749433", "NCT03277586"]
DRUG_TYPES = {"DRUG", "BIOLOGICAL"}


def _get(d, *path, default=None):
    """Safe nested get: _get(study, 'protocolSection', 'statusModule', 'overallStatus')."""
    cur = d
    for k in path:
        if not isinstance(cur, dict):
            return default
        cur = cur.get(k)
        if cur is None:
            return default
    return cur


def extract_trial(study: dict) -> dict:
    """v2 study JSON -> the flat fields the spine featurizes. Seed of cto_source.normalize."""
    ps = study.get("protocolSection", {}) or {}
    ds = study.get("derivedSection", {}) or {}
    interventions = _get(ps, "armsInterventionsModule", "interventions", default=[]) or []
    drugs = [iv.get("name") for iv in interventions if iv.get("type") in DRUG_TYPES]
    cond_mesh = _get(ds, "conditionBrowseModule", "meshes", default=[]) or []
    int_mesh = _get(ds, "interventionBrowseModule", "meshes", default=[]) or []
    return {
        "nct_id": _get(ps, "identificationModule", "nctId"),
        "brief_title": _get(ps, "identificationModule", "briefTitle"),
        "study_type": _get(ps, "designModule", "studyType"),
        "phases": _get(ps, "designModule", "phases", default=[]) or [],
        "overall_status": _get(ps, "statusModule", "overallStatus"),
        "start_date": _get(ps, "statusModule", "startDateStruct", "date"),
        "completion_date": _get(ps, "statusModule", "completionDateStruct", "date"),
        "primary_completion_date": _get(ps, "statusModule",
                                         "primaryCompletionDateStruct", "date"),
        "why_stopped": _get(ps, "statusModule", "whyStopped"),
        "conditions": _get(ps, "conditionsModule", "conditions", default=[]) or [],
        "intervention_names": [iv.get("name") for iv in interventions],
        "intervention_types": sorted({iv.get("type") for iv in interventions if iv.get("type")}),
        "drug_or_biological_names": drugs,
        "criteria_len": len(_get(ps, "eligibilityModule", "eligibilityCriteria", default="") or ""),
        "criteria": _get(ps, "eligibilityModule", "eligibilityCriteria", default="") or "",
        "condition_mesh": [(m.get("id"), m.get("term")) for m in cond_mesh],
        "intervention_mesh": [(m.get("id"), m.get("term")) for m in int_mesh],
        "_ps_modules": sorted(ps.keys()),
        "_ds_modules": sorted(ds.keys()),
    }


def fetch_one(nct: str, timeout=30) -> dict:
    req = urllib.request.Request(
        V2_ONE.format(nct=nct),
        headers={"User-Agent": "trial-pos-probe/0.1 (research; contact via repo)",
                 "Accept": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def _studies_from_file(path):
    with open(path, "r", encoding="utf-8") as f:
        obj = json.load(f)
    if isinstance(obj, dict) and "studies" in obj:   # a list-endpoint page
        return obj["studies"]
    return [obj]                                     # a single-study object


def _print_trial(t: dict):
    print(f"\n### {t['nct_id']}  |  {t['overall_status']}  |  phases={t['phases']}")
    print(f"  title            : {t['brief_title']}")
    print(f"  dates            : start={t['start_date']}  primary_comp="
          f"{t['primary_completion_date']}  comp={t['completion_date']}")
    if t["why_stopped"]:
        print(f"  why_stopped      : {t['why_stopped']}")
    print(f"  conditions       : {t['conditions']}")
    print(f"  condition_mesh   : {t['condition_mesh']}")
    print(f"  intervention typ : {t['intervention_types']}")
    print(f"  drug/biological  : {t['drug_or_biological_names']}")
    print(f"  intervention_mesh: {t['intervention_mesh']}")
    print(f"  criteria_len     : {t['criteria_len']}")
    print(f"  ps modules       : {t['_ps_modules']}")
    print(f"  ds modules       : {t['_ds_modules']}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ids", nargs="+", default=DEFAULT_IDS)
    ap.add_argument("--from-file", default=None, help="re-parse a saved JSON, no network")
    ap.add_argument("--raw", action="store_true", help="dump full JSON of the first study")
    ap.add_argument("--sleep", type=float, default=0.5, help="pause between requests (politeness)")
    args = ap.parse_args()

    if args.from_file:
        print(f"{'='*70}\nCT.gov v2 PROBE  (from-file: {args.from_file})\n{'='*70}")
        studies = _studies_from_file(args.from_file)
    else:
        print(f"{'='*70}\nCT.gov v2 PROBE  ({len(args.ids)} ids, live)\n{'='*70}")
        studies = []
        for i, nct in enumerate(args.ids):
            try:
                studies.append(fetch_one(nct))
            except urllib.error.HTTPError as e:
                print(f"  !! {nct}: HTTP {e.code} {e.reason}")
            except Exception as e:
                print(f"  !! {nct}: {type(e).__name__}: {e}")
            if args.sleep and i < len(args.ids) - 1:
                time.sleep(args.sleep)

    if args.raw and studies:
        print("\n-- RAW JSON (first study) " + "-" * 44)
        print(json.dumps(studies[0], indent=2)[:6000])
        print("-- (truncated) " + "-" * 55)

    trials = [extract_trial(s) for s in studies]
    for t in trials:
        _print_trial(t)

    # availability summary across the batch -> the go/no-go for cto_source
    print(f"\n{'='*70}\nFIELD AVAILABILITY across {len(trials)} trials (non-empty count)\n{'='*70}")
    keys = ["phases", "overall_status", "start_date", "completion_date",
            "conditions", "drug_or_biological_names", "condition_mesh", "criteria_len"]
    for k in keys:
        n = sum(1 for t in trials if t.get(k))
        print(f"  {k:26s}: {n}/{len(trials)}")
    no_drug = [t["nct_id"] for t in trials if not t["drug_or_biological_names"]]
    if no_drug:
        print(f"\n  note: no DRUG/BIOLOGICAL intervention parsed for: {no_drug}")
        print("  (check intervention_types above -- may be DEVICE/PROCEDURE/OTHER trials,")
        print("   or v2 uses a different type label than DRUG/BIOLOGICAL)")
    print("\n  reminder: SMILES + ICD are intentionally NOT here; they are reconstructed")
    print("  downstream (drug name -> SMILES; condition/MeSH -> MONDO -> ICD). See R11.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())