#!/usr/bin/env python3
"""Step A of disease-type-152: extract the MONDO is_a hierarchy into mondo_edges.csv.

fetch_mondo.py already downloaded/cached mondo.obo for the xref table; this re-parses the SAME
file for its parent edges (see trial_pos.services.obo_edges). Output feeds step C, where each
unit's MONDO term is walked UP these edges to its disease-type anchor(s).

Output data/ontology/mondo_edges.csv columns: child_mondo_id, parent_mondo_id  (one row per
is_a edge; MONDO is a DAG, so a term may have several parents).

Audit: edge count, distinct children/parents, roots (terms that are only ever parents), and a
rough max-depth probe so we can sanity-check the graph before the rollup relies on it.
Deps: stdlib only. Usage:
  python scripts/build_mondo_edges.py --obo data/ontology/mondo.obo
"""
from __future__ import annotations

import argparse
import csv
from pathlib import Path

from trial_pos.services.obo_edges import parse_is_a

_URL = "http://purl.obolibrary.org/obo/mondo.obo"


def _download(url: str, dst: Path):
    import urllib.request
    dst.parent.mkdir(parents=True, exist_ok=True)
    print(f"downloading {url}\n  -> {dst} (~50-100 MB) ...", flush=True)
    urllib.request.urlretrieve(url, dst)
    print("  done.")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--obo", default=Path("data/ontology/mondo.obo"), type=Path)
    ap.add_argument("--out", default=Path("data/ontology/mondo_edges.csv"), type=Path)
    ap.add_argument("--download", action="store_true", help="fetch mondo.obo if missing")
    args = ap.parse_args()

    print(f"{'='*70}\nMONDO is_a EDGE EXTRACTION (step A)\n{'='*70}")
    if not args.obo.exists():
        if args.download:
            _download(_URL, args.obo)
        else:
            print(f"!! {args.obo} not found. It was cached by fetch_mondo.py; pass its path via "
                  "--obo, or re-fetch with --download.")
            return 2

    children, parents = set(), set()
    n_edges = 0
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.obo.open(encoding="utf-8") as fh, args.out.open("w", newline="", encoding="utf-8") as ofh:
        w = csv.writer(ofh)
        w.writerow(["child_mondo_id", "parent_mondo_id"])
        for child, parent in parse_is_a(fh):
            w.writerow([child, parent])
            children.add(child)
            parents.add(parent)
            n_edges += 1

    roots = parents - children                      # appear as parent, never as child
    print(f"  is_a edges written        : {n_edges}")
    print(f"  distinct child terms      : {len(children)}")
    print(f"  distinct parent terms     : {len(parents)}")
    print(f"  root-ish terms (parent-only): {len(roots)}")
    if n_edges and len(children) and n_edges / max(len(children), 1) > 1.05:
        print(f"  avg parents per child     : {n_edges/len(children):.2f}  (DAG -> some multi-parent)")
    print(f"\n  wrote -> {args.out}")
    print("  next (step C): assign_disease_type.py -- resolve each unit to a MONDO term, walk")
    print("  UP these edges, tag disease-types whose anchor is self-or-ancestor -> unit_class dt_key.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())