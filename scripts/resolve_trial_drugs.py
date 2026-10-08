#!/usr/bin/env python3
"""Resolve every drug trial's tested agents to DrugCentral drugs. Reads only; writes its own
three files into --out-dir:

  trial_drug_agents.csv                one row per (trial, tested agent)
  trial_drug_resolution.csv            one row per drug trial
  trial_drug_resolution.manifest.json  inputs (with hashes), settings, counts

Logic lives in services/tested_agent.py, drug_names.py, drug_dictionary.py and
drug_resolution.py; this script reads files and prints the audit. Fails closed: a missing
input column, an unparseable id, or an arm link outside its trial stops the run before
anything is written (files go to .partial and are renamed at the end).

Usage (PowerShell):
  python scripts\\resolve_trial_drugs.py | Out-File -Encoding utf8 resolve_trial_drugs_20261001.txt
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from trial_pos.services.aact_fields import MESH_LIST  # noqa: E402
from trial_pos.services.aact_rows import MANIFEST_FILE as ROWS_MANIFEST, file_name  # noqa: E402
from trial_pos.services.drug_dictionary import build_dictionary  # noqa: E402
from trial_pos.services.drug_resolution import (  # noqa: E402
    AGENT_AMBIGUOUS, AGENT_COLUMNS, AGENT_STATUSES, AGENT_UNRESOLVED, TRIAL_COLUMNS,
    agent_rows, resolve_trial, tally, trial_row,
)
from trial_pos.services.drug_names import key  # noqa: E402
from trial_pos.services.drugsatfda import decode, parse_tab, require_columns  # noqa: E402
from trial_pos.services.eligibility import phase_class  # noqa: E402
from trial_pos.services.population import tribool  # noqa: E402
from trial_pos.services.tested_agent import OUTCOMES, ROUTES, select_tested_agents  # noqa: E402

csv.field_size_limit(10 ** 9)

DEFAULT_AACT_DIR = Path("data") / "aact"
DEFAULT_DC_DIR = Path("data") / "drugcentral"
DEFAULT_FDA_DIR = Path("data") / "drugsatfda"
DEFAULT_OUT_DIR = Path("data") / "labels"
DEFAULT_SHOW = 25
LABELS_FILE = "trial_labels.csv"
DC_MANIFEST = "drugcentral.manifest.json"
FDA_PRODUCTS = "Products.txt"
FDA_APPLICATIONS = "Applications.txt"
OUT_AGENTS = "trial_drug_agents.csv"
OUT_TRIALS = "trial_drug_resolution.csv"
OUT_MANIFEST = "trial_drug_resolution.manifest.json"
PARTIAL = ".partial"
HASH_CHUNK = 1 << 20
PIVOTAL = "pivotal"

# rev 10 section 15 refs 1-2, checked against the papers 2026-10-07. Context only: both
# resolve against DrugBank, which includes investigational drugs, and [1] counts MENTIONS
# (placebo included) where this run counts TRIALS. Neither is a like-for-like benchmark.
LITERATURE = (
    ("[1] Miftahutdinov 2021, intervention mentions mapped to DrugBank", 0.696),
    ("[1] Miftahutdinov 2021, intervention mentions covered by MeSH", 0.666),
    ("[2] Vasan 2023, drug trials matched on exact name", 0.706),
    ("[2] Vasan 2023, drug trials matched after every step incl. fuzzy", 0.876),
)

NEED = {
    "interventions": ("nct_id", "id", "intervention_type", "name"),
    "intervention_other_names": ("nct_id", "intervention_id", "name"),
    "design_groups": ("nct_id", "id", "group_type"),
    "design_group_interventions": ("nct_id", "design_group_id", "intervention_id"),
    "browse_interventions": ("nct_id", "mesh_term", "mesh_type"),
}
LABEL_NEED = ("nct_id", "is_drug_trial", "phase")
DC_NEED = {"synonyms": ("id", "lname"), "structures": ("id", "name"),
           "struct2parent": ("struct_id", "parent_id"),
           "ob_product": ("id", "ingredient", "trade_name", "appl_no", "product_no"),
           "struct2obprod": ("struct_id", "prod_id")}
FDA_NEED = {FDA_PRODUCTS: ("ApplNo", "ProductNo", "DrugName", "ActiveIngredient"),
            FDA_APPLICATIONS: ("ApplNo", "ApplType")}


def _rule(title: str) -> None:
    print("\n" + "=" * 78 + f"\n{title}\n" + "=" * 78)


def _pct(n, d) -> str:
    return "undefined" if not d else f"{100.0 * n / d:.1f}%"


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(HASH_CHUNK), b""):
            h.update(chunk)
    return h.hexdigest()


def read_rows(path: Path, need: tuple):
    if not path.exists():
        raise SystemExit(f"!! {path} not found")
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        missing = [c for c in need if c not in (reader.fieldnames or [])]
        if missing:
            raise SystemExit(f"!! {path} lacks {missing}; has {reader.fieldnames}")
        yield from reader


def as_int(raw, where: str) -> int:
    s = (raw or "").strip()
    if not s.isdigit():
        raise SystemExit(f"!! {where}: id {raw!r} is not an integer")
    return int(s)


def load_fda(folder: Path, name: str) -> list:
    path = folder / name
    if not path.exists():
        raise SystemExit(f"!! {path} not found")
    text, enc = decode(path.read_bytes())
    table = parse_tab(text)
    require_columns(table, FDA_NEED[name], name)
    print(f"  {name:20s} rows {len(table['rows']):8d}  malformed {table['malformed']}  {enc}")
    return table["rows"]


def main() -> int:
    # Redirected output on Windows is cp1252, which cannot encode e.g. the Greek beta in
    # 'interleukin-1\u03b2'. Escape what the console cannot show rather than crash.
    sys.stdout.reconfigure(errors="backslashreplace")
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--aact-dir", type=Path, default=DEFAULT_AACT_DIR)
    ap.add_argument("--dc-dir", type=Path, default=DEFAULT_DC_DIR)
    ap.add_argument("--fda-dir", type=Path, default=DEFAULT_FDA_DIR)
    ap.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    ap.add_argument("--show", type=int, default=DEFAULT_SHOW)
    ap.add_argument("--overwrite", action="store_true")
    args = ap.parse_args()
    outs = {k: args.out_dir / k for k in (OUT_AGENTS, OUT_TRIALS, OUT_MANIFEST)}
    if any(p.exists() for p in outs.values()) and not args.overwrite:
        raise SystemExit("!! outputs exist; pass --overwrite: "
                         + ", ".join(str(p) for p in outs.values() if p.exists()))
    started = datetime.now(timezone.utc).isoformat(timespec="seconds")

    _rule("SETTINGS")
    print(f"  aact {args.aact_dir} | drugcentral {args.dc_dir} | drugs@fda {args.fda_dir} | "
          f"out {args.out_dir} | show {args.show} | mesh token {MESH_LIST!r}")

    _rule("INPUTS")
    drug, phase = set(), {}
    for r in read_rows(args.aact_dir / LABELS_FILE, LABEL_NEED):
        if tribool(r["is_drug_trial"]) is True:
            drug.add(r["nct_id"])
            phase[r["nct_id"]] = phase_class(r["phase"])
    print(f"  drug trials in {LABELS_FILE}: {len(drug)}")
    rows_path = {t: args.aact_dir / file_name(t) for t in NEED}
    ivs, names = defaultdict(list), defaultdict(dict)
    for r in read_rows(rows_path["interventions"], NEED["interventions"]):
        if r["nct_id"] in drug:
            iid = as_int(r["id"], "interventions")
            ivs[r["nct_id"]].append((iid, r["intervention_type"], r["name"]))
            names[r["nct_id"]][iid] = r["name"]
    groups = defaultdict(dict)
    for r in read_rows(rows_path["design_groups"], NEED["design_groups"]):
        if r["nct_id"] in drug:
            groups[r["nct_id"]][as_int(r["id"], "design_groups")] = r["group_type"]
    links = defaultdict(list)
    for r in read_rows(rows_path["design_group_interventions"],
                       NEED["design_group_interventions"]):
        if r["nct_id"] in drug:
            links[r["nct_id"]].append((as_int(r["design_group_id"], "links"),
                                       as_int(r["intervention_id"], "links")))
    others = defaultdict(lambda: defaultdict(list))
    for r in read_rows(rows_path["intervention_other_names"],
                       NEED["intervention_other_names"]):
        if r["nct_id"] in drug:
            others[r["nct_id"]][as_int(r["intervention_id"], "other names")].append(r["name"])
    mesh, mesh_types = defaultdict(list), Counter()
    for r in read_rows(rows_path["browse_interventions"], NEED["browse_interventions"]):
        if r["nct_id"] in drug:
            mesh_types[r["mesh_type"]] += 1
            if (r["mesh_type"] or "").strip().lower() == MESH_LIST:
                mesh[r["nct_id"]].append(r["mesh_term"])
    print(f"  drug trials with interventions {len(ivs)}, arms {len(groups)}, links "
          f"{len(links)}, other names {len(others)}, indexed MeSH {len(mesh)}")
    print(f"  intervention MeSH rows by mesh_type: {dict(sorted(mesh_types.items()))}")

    manifest_path = args.dc_dir / DC_MANIFEST
    if not manifest_path.exists():
        raise SystemExit(f"!! {manifest_path} not found")
    dc_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    dc = {}
    for table, need in DC_NEED.items():
        meta = dc_manifest["tables"].get(table)
        if meta is None:
            raise SystemExit(f"!! DrugCentral manifest lacks {table}")
        dc[table] = list(read_rows(args.dc_dir / meta["file"], need))
        print(f"  dc {table:20s} rows {len(dc[table]):8d}")
    fda_products = load_fda(args.fda_dir, FDA_PRODUCTS)
    fda_applications = load_fda(args.fda_dir, FDA_APPLICATIONS)

    _rule("DICTIONARY")
    d = build_dictionary(dc["synonyms"], dc["structures"], dc["struct2parent"],
                         dc["ob_product"], dc["struct2obprod"], fda_products,
                         fda_applications)
    for k, v in sorted(d.audit.items()):
        print(f"  {k:60s} {v:8d}")

    results = []
    for nct in sorted(drug, key=lambda s: s.encode("utf-8")):
        try:
            sel = select_tested_agents(ivs.get(nct, ()), groups.get(nct, {}),
                                       links.get(nct, ()))
        except ValueError as exc:
            raise SystemExit(f"!! {nct}: {exc}. Nothing written.")
        results.append(resolve_trial(d, nct, sel, names.get(nct, {}), others.get(nct, {}),
                                     mesh.get(nct, ())))
    counts = tally(results)

    _rule("TESTED-AGENT SELECTION (drug trials)")
    classes = sorted({phase[t.nct_id] for t in results})
    print(f"  {'outcome':52s} {'trials':>8s}" + "".join(f"{c:>14s}" for c in classes))
    by = defaultdict(Counter)
    for t in results:
        by[t.selection.outcome][phase[t.nct_id]] += 1
    for o in OUTCOMES:
        print(f"  {o:52s} {counts['outcomes'][o]:8d}"
              + "".join(f"{by[o][c]:14d}" for c in classes))
    print(f"  placebo-type interventions dropped from candidates: "
          f"{sum(len(t.selection.dropped_placebo) for t in results)}")

    _rule("AGENT STATUS (tested agents)")
    n_agents = sum(counts["statuses"].values())
    for s in AGENT_STATUSES:
        print(f"  {s:20s} {counts['statuses'][s]:8d}  {_pct(counts['statuses'][s], n_agents)}")
    print("\n  matching agents by (name kind, how, step):")
    for (kind, how, step), n in sorted(counts["routes"].items(), key=lambda kv: (-kv[1],
                                                                                 kv[0])):
        print(f"    {kind:18s} {how:12s} {step:40s} {n:8d}")
    print("\n  matching agents by deciding source:")
    for src, n in sorted(counts["sources"].items(), key=lambda kv: (-kv[1], kv[0])):
        print(f"    {src:40s} {n:8d}")

    _rule("POPULATION-LEVEL RATE: trials with at least one tested agent resolved")
    def rate(members, label):
        m = [t for t in members]
        det = [t for t in m if t.matched is not None]
        hit = sum(1 for t in det if t.matched)
        print(f"  {label:40s} matched {hit:7d} of determinable {len(det):7d} "
              f"({_pct(hit, len(det))}); of all {len(m):7d} ({_pct(hit, len(m))})")
    rate(results, "all drug trials")
    rate([t for t in results if phase[t.nct_id] == PIVOTAL], "pivotal drug trials")
    for route in ROUTES:
        rate([t for t in results if t.selection.outcome == route], f"route {route}")
    c = counts["corroborated"]
    print(f"\n  matched trials with indexed MeSH: corroborated {c[True]}, not {c[False]} "
          f"({_pct(c[True], c[True] + c[False])} corroborated)")
    print(f"  unmatched trials whose indexed MeSH names a drug: "
          f"{counts['unmatched_with_mesh_drug']}  (MeSH never creates a match)")
    print("\n  literature, for context only (different denominators, DrugBank not "
          "DrugCentral):")
    for label, value in LITERATURE:
        print(f"    {label:66s} {100.0 * value:.1f}%")

    _rule(f"MOST FREQUENT UNMATCHED AGENT NAMES (top {args.show}, for debugging)")
    for status in (AGENT_UNRESOLVED, AGENT_AMBIGUOUS):
        freq = Counter(key(a.name_used) for t in results for a in t.agents if a.status == status)
        print(f"  {status}: {len(freq)} distinct names")
        for name, n in sorted(freq.items(), key=lambda kv: (-kv[1], kv[0]))[:args.show]:
            print(f"    {n:6d}  {name}")

    args.out_dir.mkdir(parents=True, exist_ok=True)
    partial = {k: p.with_name(p.name + PARTIAL) for k, p in outs.items()}
    try:
        with partial[OUT_AGENTS].open("w", newline="", encoding="utf-8") as fa, \
                partial[OUT_TRIALS].open("w", newline="", encoding="utf-8") as ft:
            wa = csv.DictWriter(fa, fieldnames=AGENT_COLUMNS)
            wt = csv.DictWriter(ft, fieldnames=TRIAL_COLUMNS)
            wa.writeheader()
            wt.writeheader()
            for t in results:
                wt.writerow(trial_row(t))
                wa.writerows(agent_rows(t))
        inputs = {str(p): sha256_of(p) for p in
                  [args.aact_dir / LABELS_FILE, args.aact_dir / ROWS_MANIFEST, manifest_path,
                   args.fda_dir / FDA_PRODUCTS, args.fda_dir / FDA_APPLICATIONS]
                  if p.exists()}
        manifest = {
            "script": "resolve_trial_drugs.py", "started_utc": started,
            "finished_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "inputs_sha256": inputs, "drug_trials": len(results),
            "selection": dict(counts["outcomes"]), "agent_status": dict(counts["statuses"]),
            "matched": {str(k): v for k, v in counts["matched"].items()},
            "outputs": {k: {"rows": None} for k in (OUT_AGENTS, OUT_TRIALS)},
        }
        for k in (OUT_AGENTS, OUT_TRIALS):
            manifest["outputs"][k] = {"bytes": partial[k].stat().st_size,
                                      "sha256": sha256_of(partial[k])}
        partial[OUT_MANIFEST].write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        for k, p in outs.items():
            os.replace(partial[k], p)
    except BaseException:
        for p in partial.values():
            if p.exists():
                p.unlink()
        raise
    _rule("WRITTEN")
    for k, p in outs.items():
        print(f"  {p}")
    return 0


if __name__ == "__main__":
    sys.exit(main())