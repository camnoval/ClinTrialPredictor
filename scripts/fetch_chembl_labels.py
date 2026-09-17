#!/usr/bin/env python3
"""Fetch ChEMBL drug-indication approval labels via the web API (no bulk download).

Pulls two small tables and caches them as CSV under --out-dir:
  drug_indication.csv : molecule_chembl_id, parent_molecule_chembl_id, mesh_id,
                        mesh_heading, efo_id, efo_term, max_phase_for_ind
  molecules.csv       : molecule_chembl_id, max_phase, first_approval,
                        standard_inchi_key, canonical_smiles

Why these: `drug_indication` is the universe of molecules with an indication annotation
(drugs + clinical candidates, a few thousand -- not the millions of bioactivity-only
compounds). We fetch structures + max_phase only for molecules (and their salt parents)
that appear there. That yields both label granularities and the InChIKey used to join to
TOP's SMILES:
  v1 (molecule-level): approved if molecules.max_phase == 4
  v2 (indication-level): drug_indication.max_phase_for_ind == 4 for the matched indication

Requires: pip install chembl_webresource_client

Usage:
  python scripts/fetch_chembl_labels.py --limit 200        # quick connectivity smoke test
  python scripts/fetch_chembl_labels.py                    # full pull (minutes)
  python scripts/fetch_chembl_labels.py --force            # ignore existing cache
"""
from __future__ import annotations
import argparse
import csv
import sys
import time
from pathlib import Path

_DI_FIELDS = ["molecule_chembl_id", "parent_molecule_chembl_id", "mesh_id",
              "mesh_heading", "efo_id", "efo_term", "max_phase_for_ind"]
_MOL_FIELDS = ["molecule_chembl_id", "max_phase", "first_approval",
               "standard_inchi_key", "canonical_smiles"]


def _chunks(seq, n):
    for i in range(0, len(seq), n):
        yield seq[i:i + n]


def _retry(fn, tries=4, base=2.0):
    last = None
    for k in range(tries):
        try:
            return fn()
        except Exception as e:  # network/HTTP transient
            last = e
            time.sleep(base * (k + 1))
    raise last


def iter_drug_indication(client, limit=0):
    qs = client.drug_indication.only(_DI_FIELDS)
    n = 0
    for rec in qs:
        yield {k: rec.get(k) for k in _DI_FIELDS}
        n += 1
        if n % 2000 == 0:
            print(f"  drug_indication: {n} rows...", flush=True)
        if limit and n >= limit:
            break


def fetch_molecules(client, ids, chunk=40):
    ids = sorted({i for i in ids if i})
    out = []
    for j, ch in enumerate(_chunks(ids, chunk), 1):
        def _pull(ch=ch):
            return list(client.molecule
                        .filter(molecule_chembl_id__in=list(ch))
                        .only(["molecule_chembl_id", "max_phase",
                               "first_approval", "molecule_structures"]))
        for m in _retry(_pull):
            st = m.get("molecule_structures") or {}
            out.append({
                "molecule_chembl_id": m.get("molecule_chembl_id"),
                "max_phase": m.get("max_phase"),
                "first_approval": m.get("first_approval"),
                "standard_inchi_key": st.get("standard_inchi_key"),
                "canonical_smiles": st.get("canonical_smiles"),
            })
        if j % 10 == 0:
            print(f"  molecules: {j * chunk} ids queried...", flush=True)
    return out


def _write_csv(path: Path, fields, rows):
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)


def main(client=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", default="data/chembl", type=Path)
    ap.add_argument("--limit", type=int, default=0, help="cap drug_indication rows (smoke test)")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)

    di_path = args.out_dir / "drug_indication.csv"
    mol_path = args.out_dir / "molecules.csv"
    if di_path.exists() and mol_path.exists() and not args.force:
        print(f"cache exists ({di_path}, {mol_path}); use --force to refetch.")
        return 0

    if client is None:
        from chembl_webresource_client.new_client import new_client as client

    print("fetching drug_indication ...", flush=True)
    di_rows = list(iter_drug_indication(client, limit=args.limit))
    _write_csv(di_path, _DI_FIELDS, di_rows)
    print(f"  wrote {len(di_rows)} rows -> {di_path}")

    ids = set()
    for r in di_rows:
        ids.add(r["molecule_chembl_id"])
        ids.add(r["parent_molecule_chembl_id"])
    print(f"fetching structures/max_phase for {len(ids)} molecules ...", flush=True)
    mol_rows = fetch_molecules(client, ids)
    _write_csv(mol_path, _MOL_FIELDS, mol_rows)
    print(f"  wrote {len(mol_rows)} rows -> {mol_path}")

    approved_mol = sum(1 for m in mol_rows if str(m["max_phase"]) in ("4", "4.0"))
    approved_ind = sum(1 for r in di_rows if str(r["max_phase_for_ind"]) in ("4", "4.0"))
    with_key = sum(1 for m in mol_rows if m["standard_inchi_key"])
    print(f"\nsummary: molecules={len(mol_rows)} (approved max_phase=4: {approved_mol}, "
          f"with InChIKey: {with_key}); drug_indication rows={len(di_rows)} "
          f"(approved indications: {approved_ind})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())