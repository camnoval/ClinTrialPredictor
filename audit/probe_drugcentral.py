#!/usr/bin/env python3
"""DrugCentral cardinality probe over the EXTRACTED CSVs. READ-ONLY, writes nothing.

SCRATCH. Reads data/drugcentral/ as written by scripts/extract_drugcentral.py, so every
figure is tied to the archive sha256 in the manifest. Replaces the version that queried
the public instance, which serves an older release and is writable by other users.

  1. the release: manifest, dbversion, archive hash, ambiguous NULL/empty columns
  2. approval: agency vocabulary, rows per (drug, agency), dates, the window bound
  3. identifier: id_type vocabulary, MeSH fan-out both ways
  4. synonyms: the name key, both directions
  5. indications: coding coverage by first-FDA-approval stratum, including drugs first
     approved after --new-since, and how many FDA drugs carry any indication at all
  6. crosswalks: indication UMLS -> MONDO -> MeSH, and the same through doid_xref
  7. optional: AACT terms against synonyms, distinct values, with the >1-drug offenders
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from trial_pos.services.aact_aggregates import AGG_SEPARATOR  # noqa: E402
from trial_pos.services.eligibility import MARKET_WINDOW_YEARS_REPORTED  # noqa: E402
from trial_pos.services.population import tribool  # noqa: E402

csv.field_size_limit(10 ** 9)

DEFAULT_DIR = Path("data/drugcentral")
MANIFEST = "drugcentral.manifest.json"
FILE_PREFIX = "dc_"
DEFAULT_MONDO = Path("data/ontology/mondo_xref.csv")
MONDO_MESH_COL = "mesh"
MONDO_UMLS_COL = "umls"
DEFAULT_VOCAB_MAX = 30
DEFAULT_SAMPLE = 5
PERCENTILES = (0.5, 0.9, 0.99)

FDA_AGENCY_TOKEN = "FDA"
NME_DATES_COMPREHENSIVE_FROM_YEAR = 1982   # literature: earlier dates incomplete
INDICATION_PROVENANCE_SPLIT_YEAR = 2012    # literature: OMOP before, label-mined after
INDICATION_RELATIONSHIP = "indication"
MESH_PATTERN = "mesh"
UMLS_PATTERN = "umls"
# The previous release's latest FDA approval, from the 2023-11-01 public-instance run on
# the project machine. A flag, so a later release can be compared against this one.
DEFAULT_NEW_SINCE = "2023-08-18"
XREF_PREFIX_SEPARATOR = ":"
DOID_XREF_COLUMNS = ("doid", "source", "xref")

ENTITY_KEY_COLUMNS = ("intervention_mesh_terms", "intervention_names",
                      "intervention_other_names")


def _rule(title: str) -> str:
    return "\n" + "=" * 78 + f"\n{title}\n" + "=" * 78


def _pct(n, d) -> str:
    return "n/a" if not d else f"{100.0 * n / d:.1f}%"


def _date(value):
    v = (value or "").strip()
    if not v:
        return None
    return datetime.strptime(v[:10], "%Y-%m-%d").date()


def _dist(label: str, counts) -> None:
    ks = sorted(counts)
    if not ks:
        print(f"  {label:44s} no keys")
        return
    pcts = "  ".join(f"p{int(p * 100)}={ks[min(len(ks) - 1, int(p * len(ks)))]}"
                     for p in PERCENTILES)
    multi = sum(1 for k in ks if k > 1)
    print(f"  {label:44s} keys {len(ks)}  max {ks[-1]}  {pcts}  >1: {multi} "
          f"({_pct(multi, len(ks))})")


class Release:
    def __init__(self, folder: Path):
        path = folder / MANIFEST
        if not path.exists():
            raise SystemExit(f"!! {path} not found. Run scripts\\extract_drugcentral.py.")
        self.folder = folder
        self.manifest = json.loads(path.read_text(encoding="utf-8"))

    def rows(self, table: str) -> list:
        """Rows as dicts. Blank is NULL, except in a column the manifest lists as also
        holding empty strings, where it stays '' and the caller must not read it as NULL."""
        meta = self.manifest["tables"][table]
        ambiguous = set(meta["ambiguous_null_empty"])
        with (self.folder / meta["file"]).open(newline="", encoding="utf-8") as handle:
            out = []
            for row in csv.DictReader(handle):
                out.append({k: (v if (v != "" or k in ambiguous) else None)
                            for k, v in row.items()})
        if len(out) != meta["rows"]:
            raise SystemExit(f"!! {table}: {len(out)} rows read, manifest says "
                             f"{meta['rows']}")
        return out


def section_release(rel: Release) -> None:
    print(_rule("1. RELEASE"))
    m = rel.manifest
    archive = m.get("archive") or {}
    print(f"  dbversion {m['dbversion']}")
    print(f"  archive {archive.get('file')}  sha256 {archive.get('sha256')}")
    print(f"  extracted {m['finished_utc']}  tables {len(m['tables'])}")
    for table, meta in m["tables"].items():
        print(f"    {table:20s} rows {meta['rows']:8d}  ambiguous NULL/empty: "
              f"{meta['ambiguous_null_empty'] or '-'}")


def section_approval(rel: Release, structs: set) -> dict:
    print(_rule("2. APPROVAL"))
    rows = rel.rows("approval")
    by_type: dict = {}
    for r in rows:
        by_type.setdefault(r["type"], []).append(r)
    print(f"  {'agency (type)':44s} {'rows':>6s} {'drugs':>6s} {'undated':>8s} "
          f"{'earliest':>11s} {'latest':>11s}")
    for agency, items in sorted(by_type.items(), key=lambda kv: -len(kv[1])):
        dates = [d for d in (_date(r["approval"]) for r in items) if d]
        print(f"  {str(agency)[:44]:44s} {len(items):6d} "
              f"{len({r['struct_id'] for r in items}):6d} "
              f"{sum(1 for r in items if not r['approval']):8d} "
              f"{str(min(dates)) if dates else '-':>11s} {str(max(dates)) if dates else '-':>11s}")
    _dist("rows per (struct_id, type)",
          Counter((r["struct_id"], r["type"]) for r in rows).values())
    orphans = {r["struct_id"] for r in rows} - structs
    print(f"  approval struct_ids absent from structures: {len(orphans)}")
    first_fda = {}
    for r in by_type.get(FDA_AGENCY_TOKEN, []):
        d = _date(r["approval"])
        first_fda[r["struct_id"]] = d
    dated = [d for d in first_fda.values() if d]
    early = sum(1 for d in dated if d.year < NME_DATES_COMPREHENSIVE_FROM_YEAR)
    latest = max(dated) if dated else None
    print(f"\n  {FDA_AGENCY_TOKEN} drugs dated before {NME_DATES_COMPREHENSIVE_FROM_YEAR}: "
          f"{early}; latest {FDA_AGENCY_TOKEN} approval: {latest}")
    if latest:
        for w in sorted(MARKET_WINDOW_YEARS_REPORTED):
            try:
                bound = latest.replace(year=latest.year - w)
            except ValueError:
                bound = latest.replace(month=2, day=28, year=latest.year - w)
            print(f"    W={w}: latest readout whose window closes against this source "
                  f"{bound}")
    return first_fda


def section_identifier(rel: Release, vocab_max: int) -> None:
    print(_rule("3. IDENTIFIER"))
    rows = rel.rows("identifier")
    types: dict = {}
    for r in rows:
        types.setdefault(r["id_type"], []).append(r)
    print(f"  {'id_type':32s} {'rows':>8s} {'drugs':>8s} {'distinct ids':>13s}")
    for t, items in sorted(types.items(), key=lambda kv: -len(kv[1]))[:vocab_max]:
        print(f"  {str(t):32s} {len(items):8d} {len({r['struct_id'] for r in items}):8d} "
              f"{len({r['identifier'] for r in items}):13d}")
    for t, items in types.items():
        if t and MESH_PATTERN in t.lower():
            per_drug, per_id = {}, {}
            for r in items:
                per_drug.setdefault(r["struct_id"], set()).add(r["identifier"])
                per_id.setdefault(r["identifier"], set()).add(r["struct_id"])
            _dist(f"{t}: ids per drug", [len(v) for v in per_drug.values()])
            _dist(f"{t}: drugs per id", [len(v) for v in per_id.values()])


def section_synonyms(rel: Release) -> dict:
    print(_rule("4. SYNONYMS"))
    rows = rel.rows("synonyms")
    per_name, per_drug = {}, {}
    for r in rows:
        if r["lname"] is None:
            continue
        per_name.setdefault(r["lname"], set()).add(r["id"])
        per_drug.setdefault(r["id"], set()).add(r["lname"])
    print(f"  rows {len(rows)}  drugs {len(per_drug)}  distinct lname {len(per_name)}  "
          f"null lname {sum(1 for r in rows if r['lname'] is None)}")
    _dist("names per drug", [len(v) for v in per_drug.values()])
    _dist("drugs per lname", [len(v) for v in per_name.values()])
    mismatch = sum(1 for r in rows if r["lname"] is not None and r["name"] is not None
                   and r["lname"] != r["name"].lower())
    print(f"  rows where lname != Python lower(name): {mismatch}")
    return {k: len(v) for k, v in per_name.items()}


def section_indications(rel: Release, first_fda: dict, new_since: date) -> set:
    print(_rule("5. INDICATIONS"))
    rows = rel.rows("omop_relationship")
    print(f"  {'relationship_name':28s} {'rows':>8s} {'drugs':>8s} {'concepts':>9s}")
    rels: dict = {}
    for r in rows:
        rels.setdefault(r["relationship_name"], []).append(r)
    for name, items in sorted(rels.items(), key=lambda kv: -len(kv[1])):
        print(f"  {str(name):28s} {len(items):8d} {len({r['struct_id'] for r in items}):8d} "
              f"{len({r['concept_id'] for r in items}):9d}")
    ind = rels.get(INDICATION_RELATIONSHIP, [])

    def stratum(struct_id):
        d = first_fda.get(struct_id)
        if struct_id not in first_fda:
            return "no FDA approval row"
        if d is None:
            return "FDA approval undated"
        if d > new_since:
            return f"first FDA approval > {new_since}"
        if d.year <= INDICATION_PROVENANCE_SPLIT_YEAR:
            return f"first FDA approval <= {INDICATION_PROVENANCE_SPLIT_YEAR}"
        return f"first FDA approval {INDICATION_PROVENANCE_SPLIT_YEAR + 1}..{new_since}"

    table: dict = {}
    for r in ind:
        c = table.setdefault(stratum(r["struct_id"]), Counter())
        c["rows"] += 1
        c["umls"] += r["umls_cui"] is not None
        c["snomed"] += r["snomed_conceptid"] is not None
        c["neither"] += r["umls_cui"] is None and r["snomed_conceptid"] is None
    print(f"\n  '{INDICATION_RELATIONSHIP}' rows by coding:")
    print(f"    {'stratum':40s} {'rows':>7s} {'UMLS':>7s} {'SNOMED':>7s} {'neither':>8s}")
    for s, c in sorted(table.items()):
        print(f"    {s:40s} {c['rows']:7d} {c['umls']:7d} {c['snomed']:7d} {c['neither']:8d}")
    with_ind = {r["struct_id"] for r in ind}
    print(f"\n  FDA-approved drugs carrying ANY indication row, by stratum:")
    drugs: dict = {}
    for sid in first_fda:
        drugs.setdefault(stratum(sid), [0, 0])
        drugs[stratum(sid)][0] += 1
        drugs[stratum(sid)][1] += sid in with_ind
    for s, (n, k) in sorted(drugs.items()):
        print(f"    {s:40s} {k:6d} of {n:6d}  {_pct(k, n)}")
    print("  A drug with no indication row cannot be matched to any trial's condition: for")
    print("  the indication target its trials are undeterminable, not 0.")
    return {r["umls_cui"] for r in ind if r["umls_cui"]}


def _strip(value: str) -> tuple:
    v = value.strip().upper()
    return (v.rsplit(XREF_PREFIX_SEPARATOR, 1)[-1], XREF_PREFIX_SEPARATOR in v)


def section_crosswalks(rel: Release, mondo: Path, cuis: set) -> None:
    print(_rule("6. CROSSWALKS: indication UMLS -> MeSH"))
    print(f"  distinct indication UMLS CUIs: {len(cuis)}")
    if mondo.exists():
        with mondo.open(newline="", encoding="utf-8") as h:
            m = list(csv.DictReader(h))
        umls_any = {r[MONDO_UMLS_COL].strip().upper() for r in m if r.get(MONDO_UMLS_COL)}
        umls_mesh = {r[MONDO_UMLS_COL].strip().upper() for r in m
                     if r.get(MONDO_UMLS_COL) and r.get(MONDO_MESH_COL)}
        print(f"  MONDO   : in {len(cuis & umls_any)} {_pct(len(cuis & umls_any), len(cuis))}"
              f"   with MeSH {len(cuis & umls_mesh)} {_pct(len(cuis & umls_mesh), len(cuis))}")
    else:
        print(f"  (no {mondo})")
    rows = rel.rows("doid_xref")
    header = list(rows[0]) if rows else []
    if not all(c in header for c in DOID_XREF_COLUMNS):
        print(f"  !! doid_xref columns {header}; expected {DOID_XREF_COLUMNS} -- skipped")
        return
    sources = Counter(r["source"] for r in rows)
    print(f"  doid_xref sources: {dict(sources.most_common())}")
    umls_doids, mesh_doids, prefixed = {}, set(), Counter()
    for r in rows:
        src = (r["source"] or "").lower()
        if not r["xref"]:
            continue
        value, had = _strip(r["xref"])
        if UMLS_PATTERN in src:
            umls_doids.setdefault(value, set()).add(r["doid"])
            prefixed["umls"] += had
        elif MESH_PATTERN in src:
            mesh_doids.add(r["doid"])
            prefixed["mesh"] += had
    print(f"  xref values that carried a '{XREF_PREFIX_SEPARATOR}' prefix (stripped): "
          f"{dict(prefixed)}")
    found = {c for c in cuis if c in umls_doids}
    to_mesh = {c for c in found if umls_doids[c] & mesh_doids}
    print(f"  DOID    : in {len(found)} {_pct(len(found), len(cuis))}   with MeSH "
          f"{len(to_mesh)} {_pct(len(to_mesh), len(cuis))}")


def section_trial_keys(entities: Path, labels: Path, lnames: dict) -> None:
    print(_rule("7. TRIAL-SIDE KEY: AACT terms against synonyms"))
    if entities is None or not entities.exists():
        print("  (no --entities; skipped)")
        return
    drug = None
    if labels is not None and labels.exists():
        with labels.open(newline="", encoding="utf-8") as h:
            drug = {r["nct_id"].strip().upper() for r in csv.DictReader(h)
                    if tribool(r.get("is_drug_trial")) is True}
    distinct = {c: set() for c in ENTITY_KEY_COLUMNS}
    with entities.open(newline="", encoding="utf-8") as h:
        for row in csv.DictReader(h):
            if drug is not None and row["nct_id"].strip().upper() not in drug:
                continue
            for c in ENTITY_KEY_COLUMNS:
                for t in (row.get(c) or "").split(AGG_SEPARATOR):
                    if t.strip():
                        distinct[c].add(t.strip().lower())
    print(f"  frame: {'drug trials' if drug is not None else 'ALL trials'}")
    print(f"  {'source':28s} {'distinct':>10s} {'matched':>10s} {'share':>7s} "
          f"{'1 drug':>8s} {'>1 drug':>8s}")
    offenders = {}
    for c in ENTITY_KEY_COLUMNS:
        terms = distinct[c]
        matched = [t for t in terms if t in lnames]
        single = sum(1 for t in matched if lnames[t] == 1)
        offenders.update({t: lnames[t] for t in matched if lnames[t] != 1})
        print(f"  {c:28s} {len(terms):10d} {len(matched):10d} "
              f"{_pct(len(matched), len(terms)):>7s} {single:8d} {len(matched) - single:8d}")
    if offenders:
        print(f"  >1-drug terms: {sorted(offenders.items())[:DEFAULT_VOCAB_MAX]}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dir", type=Path, default=DEFAULT_DIR)
    ap.add_argument("--mondo", type=Path, default=DEFAULT_MONDO)
    ap.add_argument("--new-since", default=DEFAULT_NEW_SINCE,
                    help="first-FDA-approval date after which a drug counts as new to "
                         "this release. Default: %(default)s")
    ap.add_argument("--entities", type=Path, default=None)
    ap.add_argument("--labels", type=Path, default=None)
    ap.add_argument("--vocab-max", type=int, default=DEFAULT_VOCAB_MAX)
    args = ap.parse_args()
    new_since = date.fromisoformat(args.new_since)
    print(f"dir {args.dir} | new since {new_since} | windows "
          f"{sorted(MARKET_WINDOW_YEARS_REPORTED)} | split {INDICATION_PROVENANCE_SPLIT_YEAR}")
    rel = Release(args.dir)
    section_release(rel)
    structs = {r["id"] for r in rel.rows("structures")}
    first_fda = section_approval(rel, structs)
    section_identifier(rel, args.vocab_max)
    lnames = section_synonyms(rel)
    cuis = section_indications(rel, first_fda, new_since)
    section_crosswalks(rel, args.mondo, cuis)
    section_trial_keys(args.entities, args.labels, lnames)
    return 0


if __name__ == "__main__":
    sys.exit(main())
