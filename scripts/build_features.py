#!/usr/bin/env python3
"""Build trial_features.csv: one row per drug trial (D-60, D-62, D-63). Thin: the features,
the leakage registry and the split live in services/trial_features.py and
services/training_design.py.

Fails closed: the leakage registry is asserted for all three targets BEFORE anything is
read; the fields file's provenance file must agree with the code's registry for every column
used; output goes to `.partial` files renamed only at the end.

Writes, into --out-dir:
  trial_features.csv            nct_id then every feature, in spec order; '' = unknown
  trial_features.manifest.json  every feature with group, kind, served / toggle, its AACT
                                sources and their provenance, registration-only flag; the
                                lookback; inputs with rows and sha256; the split constants

Prints, per target, each feature's non-missing share on the TRAIN and TEST sides of the
decided split (D-59): a feature present in one era and blank in the other is the "unknown
means old" risk D-59 names.

Usage (PowerShell), one line:
  python scripts\\build_features.py | Out-File -Encoding utf8 build_features_20261001.txt
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT / "src", ROOT / "scripts"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from audit_posting_bias import read_csv  # noqa: E402
from build_targets import OUT_TARGETS, advancement_columns, market_columns  # noqa: E402
from match_form_approvals import OUT_APPROVALS  # noqa: E402
from pull_aact_fields import OUT_FIELDS, OUT_PROVENANCE, OUT_TEXT  # noqa: E402
from resolve_trial_drugs import OUT_AGENTS  # noqa: E402
from trial_pos.services import trial_features as F  # noqa: E402
from trial_pos.services import training_design as D  # noqa: E402
from trial_pos.services.aact_fields import output_name  # noqa: E402
from trial_pos.services.aact_provenance import provenance_for  # noqa: E402
from trial_pos.services.aact_rows import file_name  # noqa: E402
from trial_pos.services.advancement_label import DISEASE_EXACT, name_keys  # noqa: E402
from trial_pos.services.eligibility import normalize_phase  # noqa: E402
from trial_pos.services.form_approval import parse_approval_row  # noqa: E402
from trial_pos.services.population import parse_date, tribool  # noqa: E402
from trial_pos.services.temporal_split import SIDE_TEST, SIDE_TRAIN  # noqa: E402

csv.field_size_limit(10 ** 9)

OUT_FEATURES = "trial_features.csv"
OUT_MANIFEST = "trial_features.manifest.json"
LABELS_FILE = "trial_labels.csv"          # pull_aact_results.py: only is_drug_trial is read
LEAD = "lead"
DEFAULT_SHOW = 25
LABEL_COLUMN = {
    F.TARGET_ENDPOINT_MET: "endpoint_met_label",
    F.TARGET_ADVANCEMENT: advancement_columns(D.ADVANCEMENT_HEADLINE_WINDOW_YEARS,
                                              DISEASE_EXACT)[2],
    F.TARGET_MARKET: market_columns(D.MARKET_HEADLINE_WINDOW_YEARS)[2],
}
SUBMITTED = output_name("studies", "study_first_submitted_date")
# D-69: when another trial's outcome became public. Read only to date OTHER trials' outcomes
# for the track records; never a feature of its own trial (it is a label input, R7).
RESULTS_PUBLIC = output_name("studies", "results_first_posted_date")
ENDPOINT_LABEL = "endpoint_met_label"
START_TYPE = output_name("studies", "start_date_type")
PHASE = output_name("studies", "phase")


def _rule(t: str) -> None:
    print("\n" + "=" * 78 + f"\n{t}\n" + "=" * 78, flush=True)


def _pct(n, d) -> str:
    return "undefined" if not d else f"{100.0 * n / d:.1f}%"


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def main() -> int:
    sys.stdout.reconfigure(errors="backslashreplace")
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--aact-dir", type=Path, default=Path("data") / "aact")
    ap.add_argument("--labels-dir", type=Path, default=Path("data") / "labels")
    ap.add_argument("--out-dir", type=Path, default=Path("data") / "features")
    ap.add_argument("--show", type=int, default=DEFAULT_SHOW)
    args = ap.parse_args()
    a = args.aact_dir
    paths = {"targets": args.labels_dir / OUT_TARGETS, "agents": args.labels_dir / OUT_AGENTS,
             "approvals": args.labels_dir / OUT_APPROVALS,
             "labels": a / LABELS_FILE, "fields": a / OUT_FIELDS,
             "provenance": a / OUT_PROVENANCE, "text": a / OUT_TEXT,
             "conditions": a / file_name("browse_conditions"),
             "mesh_terms": a / file_name("mesh_terms"), "sponsors": a / file_name("sponsors"),
             "facilities": a / file_name("facilities")}
    for p in paths.values():
        if not p.exists():
            raise SystemExit(f"!! {p} not found. Nothing written.")

    # ---- the vocabulary fixes the disease flags; then the registry, before any trial ------
    term_codes, all_codes = defaultdict(set), set()
    for r in read_csv(paths["mesh_terms"]):
        codes = F.top_codes([(r.get("tree_number") or "").strip()])
        term_codes[r["downcase_mesh_term"]] |= codes
        all_codes |= codes
    specs = F.build_specs(all_codes)
    for target in F.TARGETS:
        bad = F.leakage_violations(specs, target)
        if bad:
            raise SystemExit(f"!! leakage registry refuses {target}: {bad[:5]}")
    prov_file = {r["column"]: r["provenance"] for r in read_csv(paths["provenance"])}
    read_cols = F.fields_columns_read()
    absent = [c for c in read_cols if c not in prov_file]
    stale = [c for c in read_cols if c in prov_file and prov_file[c] != F.field_provenance(c)]
    if absent or stale:
        raise SystemExit(f"!! {OUT_PROVENANCE}: columns absent {absent[:5]}, provenance "
                         f"disagreeing with the registry {stale[:5]}")

    _rule("SETTINGS")
    print(f"  features {len(specs)} (served {len(F.served(specs))}, registration-only served "
          f"{sum(F.is_registration_only(s) for s in F.served(specs))}) | disease codes "
          f"{len(all_codes)} | lookback {D.HISTORY_LOOKBACK_YEARS}y | split "
          f"{D.SPLIT_BOUNDARY} {D.TRAIN_RULE} {D.START_RULE} | advancement left cut "
          f"{D.ADVANCEMENT_LEFT_CUT}")
    print("  leakage registry: no violation for " + ", ".join(F.TARGETS))

    # ---- inputs ---------------------------------------------------------------------------
    drug = {r["nct_id"] for r in read_csv(paths["labels"]) if tribool(r.get("is_drug_trial"))}
    registered, phase, public = {}, {}, {}
    for r in read_csv(paths["fields"]):
        registered[r["nct_id"]] = parse_date(r.get(SUBMITTED))
        phase[r["nct_id"]] = normalize_phase(r.get(PHASE))
        public[r["nct_id"]] = parse_date(r.get(RESULTS_PUBLIC))
    terms = defaultdict(set)
    n_terms = Counter()
    for r in read_csv(paths["conditions"]):
        if r.get("mesh_type") == F.MESH_LIST_TYPE:
            terms[r["nct_id"]].add(r["downcase_mesh_term"])
            n_terms[r["nct_id"]] += 1
    codes = {n: set().union(*(term_codes.get(t, set()) for t in ts)) for n, ts in terms.items()}
    keys = defaultdict(set)
    for r in read_csv(paths["agents"]):
        keys[r["nct_id"]] |= name_keys(r["name_used"])
    lead = {}
    for r in read_csv(paths["sponsors"]):
        if (r.get("lead_or_collaborator") or "").lower() == LEAD:
            lead[r["nct_id"]] = F.entity_key(r.get("name"))
    sites = defaultdict(set)
    for r in read_csv(paths["facilities"]):
        sites[r["nct_id"]].add(F.site_key(r.get("name"), r.get("city"), r.get("country")))
    approval_dates = defaultdict(list)            # D-69: every dated approval of an agent
    for r in read_csv(paths["approvals"]):
        a_ = parse_approval_row(r)
        approval_dates[r["nct_id"]] += list(a_.loose_dates) + (
            [a_.dc_fda_first] if a_.dc_fda_first else [])
    crit_col = output_name("eligibilities", "criteria")
    text = {r["nct_id"]: F.text_features(r.get(crit_col)) for r in read_csv(paths["text"])
            if r["nct_id"] in drug}
    print(f"  trials {len(registered)}; drug trials {len(drug)}; with name keys "
          f"{sum(1 for n in drug if keys.get(n))}; with mesh-list terms {len(terms)}; lead "
          f"sponsors {len(lead)}; with sites {len(sites)}")

    # ---- history indices, every interventional trial (drug history: drug trials) -------
    every, term_ix, sponsor_ix, sponsor_cat_ix, site_ix = (F.HistoryIndex() for _ in range(5))
    for n, when in registered.items():
        every.add(F.ALL_TRIALS_KEY, n, when)
        for t in terms.get(n, ()):
            term_ix.add(t, n, when)
        if lead.get(n):
            sponsor_ix.add(lead[n], n, when)
            for c in codes.get(n, ()):
                sponsor_cat_ix.add((lead[n], c), n, when)
        for s in sites.get(n, ()):
            site_ix.add(s, n, when)
    for ix in (every, term_ix, sponsor_ix, sponsor_cat_ix, site_ix):
        ix.freeze()
    history = F.DrugHistory((k, registered.get(n), n, phase.get(n), terms.get(n, ()))
                            for n in drug for k in keys.get(n, ()))
    L = D.HISTORY_LOOKBACK_YEARS

    # ---- labelled trials and their split side ---------------------------------------------
    labelled = {t: {} for t in F.TARGETS}
    readout = {}
    for r in read_csv(paths["targets"]):
        readout[r["nct_id"]] = parse_date(r["readout_date"])
        for t, col in LABEL_COLUMN.items():
            if r[col] != "":
                labelled[t][r["nct_id"]] = int(r[col])
    # D-69: endpoint outcomes, each dated by when it became public (trials without a public
    # date are never used)
    outcomes = [(n, public.get(n), y_) for n, y_ in sorted(labelled[F.TARGET_ENDPOINT_MET].items())]
    drug_record = F.OutcomeHistory((k, n, d, y_) for n, d, y_ in outcomes
                                   for k in keys.get(n, ()))
    sponsor_record = F.OutcomeHistory((lead.get(n), n, d, y_) for n, d, y_ in outcomes)
    print(f"  D-69: trials with approval rows {len(approval_dates)}; dated public outcomes "
          f"{sum(d is not None for _n, d, _y in outcomes)} of {len(outcomes)}")

    # ---- pass 2: compute, write, count coverage by side -------------------------------------
    names = [s.name for s in specs]
    args.out_dir.mkdir(parents=True, exist_ok=True)
    out_path, man_path = args.out_dir / OUT_FEATURES, args.out_dir / OUT_MANIFEST
    tmp, tmp_man = Path(str(out_path) + ".partial"), Path(str(man_path) + ".partial")
    cover = {t: {SIDE_TRAIN: Counter(), SIDE_TEST: Counter()} for t in F.TARGETS}
    side_n = {t: Counter() for t in F.TARGETS}
    bundle = (approval_dates, drug_record, sponsor_record)
    try:
        rows = _write(paths, tmp, names, drug, registered, terms, n_terms, codes, keys, lead,
                      sites, text, all_codes, history, every, term_ix, sponsor_ix,
                      sponsor_cat_ix, site_ix, labelled, readout, cover, side_n, bundle)
        _finish(args, specs, names, rows, paths, tmp, tmp_man, out_path, man_path, cover,
                side_n)
    except BaseException:
        for x in (tmp, tmp_man):                   # fail closed: no partial file is left,
            x.unlink(missing_ok=True)              # wherever the failure happened
        raise
    return 0


def _write(paths, tmp, names, drug, registered, terms, n_terms, codes, keys, lead, sites,
           text, all_codes, history, every, term_ix, sponsor_ix, sponsor_cat_ix, site_ix,
           labelled, readout, cover, side_n, bundle) -> int:
    L = D.HISTORY_LOOKBACK_YEARS
    approval_dates, drug_record, sponsor_record = bundle
    rows, last = 0, ""
    with open(tmp, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["nct_id"] + names)
        for r in read_csv(paths["fields"]):        # written in nct_id byte order by the pull
            n = r["nct_id"]
            if n.encode() <= last.encode():
                raise SystemExit(f"!! {OUT_FIELDS} is not in nct_id order at {n}")
            last = n
            if n not in drug:
                continue
            when = registered.get(n)
            v = F.fields_features(r)
            v.update(text.get(n) or F.text_features(None))
            v.update(F.disease_features(n_terms.get(n, 0), codes.get(n, set()), all_codes))
            v.update(history.features(keys.get(n, ()), when, terms.get(n, ()), L))
            v["disease_crowding"] = F.disease_crowding(term_ix, every, terms.get(n, ()), when, L)
            k = lead.get(n)
            v["sponsor_prior_trials"] = sponsor_ix.count(k, when, L) if k and when else None
            v["sponsor_prior_same_category"] = (
                max((sponsor_cat_ix.count((k, c), when, L) for c in codes[n]), default=0)
                if k and when and n in codes else None)
            v.update(F.site_features(site_ix, sites.get(n, ()), when, L))
            v["prior_fda_approval"] = F.prior_approval(approval_dates.get(n, ()), when)
            v["drug_track_record"] = drug_record.track_record(keys.get(n, ()), when, n)
            v["sponsor_track_record"] = sponsor_record.track_record([lead.get(n)], when, n)
            missing = [x for x in names if x not in v]
            if missing:
                raise SystemExit(f"!! {n}: no value computed for {missing[:5]}")
            w.writerow([n] + [F.csv_cell(v[x]) for x in names])
            rows += 1
            for t in F.TARGETS:
                if n not in labelled[t]:
                    continue
                side, _why = D.split_side_for(t, readout.get(n), parse_date(r.get(F.START)),
                                              r.get(START_TYPE))
                side_n[t][side] += 1
                if side in (SIDE_TRAIN, SIDE_TEST):
                    for x in names:
                        cover[t][side][x] += v[x] is not None
    return rows


def _finish(args, specs, names, rows, paths, tmp, tmp_man, out_path, man_path, cover,
            side_n) -> None:
    L = D.HISTORY_LOOKBACK_YEARS
    manifest = {
        "rows": rows, "features": [
            {"name": s.name, "group": s.group, "kind": s.kind,
             "served": s.group in F.SERVED_GROUPS,
             "registration_only": F.is_registration_only(s),
             "sources": [{"table": t, "column": c, "provenance": F.source_provenance(t, c)}
                         for t, c in s.sources], "note": s.note} for s in specs],
        "lookback_years": L,
        "split": {"boundary": D.SPLIT_BOUNDARY.isoformat(), "train_rule": D.TRAIN_RULE,
                  "start_rule": D.START_RULE,
                  "advancement_left_cut": D.ADVANCEMENT_LEFT_CUT.isoformat(),
                  "label_lag_years": D.LABEL_LAG_YEARS},
        "biomarker_terms": list(F.BIOMARKER_TERMS),
        "inputs": {k: {"file": str(p), "sha256": sha256(p)} for k, p in sorted(paths.items())},
    }
    with open(tmp_man, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=1, sort_keys=True)
    os.replace(tmp, out_path)
    os.replace(tmp_man, man_path)

    # ---- report ---------------------------------------------------------------------------
    _rule("COVERAGE BY SPLIT SIDE (labelled trials; non-missing share train | test | "
          "difference), largest differences first")
    for t in F.TARGETS:
        tr, te = side_n[t][SIDE_TRAIN], side_n[t][SIDE_TEST]
        print(f"\n  {t}: sides {dict(sorted(side_n[t].items()))}")
        if not tr or not te:
            print("    a side is empty: no comparison")
            continue
        diffs = []
        for x in names:
            p_tr = cover[t][SIDE_TRAIN][x] / tr if tr else 0.0
            p_te = cover[t][SIDE_TEST][x] / te if te else 0.0
            diffs.append((abs(p_te - p_tr), x, p_tr, p_te))
        for d, x, p_tr, p_te in sorted(diffs, key=lambda z: (-z[0], z[1]))[:args.show]:
            print(f"    {x[:40]:40s} {100 * p_tr:6.1f}% | {100 * p_te:6.1f}% | "
                  f"{100 * (p_te - p_tr):+6.1f}")
        full = sum(1 for _d, _x, p_tr, p_te in diffs if p_tr == 1.0 and p_te == 1.0)
        print(f"    features present on every trial of both sides: {full} of {len(names)}")
    _rule("WRITTEN")
    print(f"  {out_path} ({rows} rows, {len(names)} features)\n  {man_path}")


if __name__ == "__main__":
    sys.exit(main())
