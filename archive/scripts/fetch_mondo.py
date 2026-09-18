#!/usr/bin/env python3
"""Download MONDO and build a disease cross-reference table (ICD-10 <-> MeSH <-> EFO).

MONDO (CC-BY) harmonizes disease vocabularies. Each MONDO term carries its name, exact
synonyms, and xrefs to EFO / MeSH / ICD-10 / DOID / UMLS. We parse mondo.obo into one
table so downstream matching can resolve TOP's (ICD-10 + free text) and ChEMBL's
(MeSH + EFO) to a shared MONDO id and match on identity, not string overlap.

Output data/ontology/mondo_xref.csv columns:
  mondo_id, name, exact_synonyms(|), efo(|), mesh(|), icd10(|), umls(|)

Usage:
  python scripts/fetch_mondo.py                 # downloads mondo.obo (cached), builds csv
  python scripts/fetch_mondo.py --obo path.obo  # parse a local copy instead of downloading
"""
from __future__ import annotations
import argparse
import csv
import re
from pathlib import Path

_URL = "http://purl.obolibrary.org/obo/mondo.obo"
_SYN = re.compile(r'"(.*?)"\s+(\w+)')
_ICD_PREFIXES = ("ICD10CM", "ICD10WHO", "ICD10")


def _download(url: str, dst: Path):
    import urllib.request
    print(f"downloading {url}\n  -> {dst} (this is ~50-100 MB) ...", flush=True)

    def hook(blocks, bs, total):
        if total > 0 and blocks % 500 == 0:
            print(f"    {blocks*bs/1e6:.0f} MB", flush=True)
    urllib.request.urlretrieve(url, dst, reporthook=hook)
    print("  done.")


def _flush(term, writer):
    if not term.get("id", "").startswith("MONDO:") or term.get("obsolete"):
        return 0
    writer.writerow({
        "mondo_id": term["id"],
        "name": term.get("name", ""),
        "exact_synonyms": "|".join(sorted(term["syn"])),
        "efo": "|".join(sorted(term["efo"])),
        "mesh": "|".join(sorted(term["mesh"])),
        "icd10": "|".join(sorted(term["icd10"])),
        "umls": "|".join(sorted(term["umls"])),
    })
    return 1


def _new_term():
    return {"id": "", "name": "", "obsolete": False,
            "syn": set(), "efo": set(), "mesh": set(), "icd10": set(), "umls": set()}


def parse_obo(obo: Path, out: Path) -> dict:
    out.parent.mkdir(parents=True, exist_ok=True)
    n_terms = 0
    counts = {"efo": 0, "mesh": 0, "icd10": 0}
    with obo.open(encoding="utf-8") as fh, out.open("w", newline="", encoding="utf-8") as ofh:
        writer = csv.DictWriter(ofh, fieldnames=["mondo_id", "name", "exact_synonyms",
                                                 "efo", "mesh", "icd10", "umls"])
        writer.writeheader()
        term = None
        in_term = False
        for line in fh:
            line = line.rstrip("\n")
            if line.startswith("["):
                if term is not None:
                    wrote = _flush(term, writer)
                    n_terms += wrote
                    if wrote:
                        for k in counts:
                            counts[k] += 1 if term[k] else 0
                term = _new_term()
                in_term = line.strip() == "[Term]"
                continue
            if not in_term or term is None or ":" not in line:
                continue
            key, _, val = line.partition(":")
            key = key.strip(); val = val.strip()
            if key == "id":
                term["id"] = val
            elif key == "name":
                term["name"] = val
            elif key == "is_obsolete" and val == "true":
                term["obsolete"] = True
            elif key == "synonym":
                m = _SYN.search(val)
                if m and m.group(2) == "EXACT":
                    term["syn"].add(m.group(1))
            elif key == "xref":
                tok = re.split(r"[\s{!]", val, maxsplit=1)[0]           # strip {source=..} / ! comment
                pfx, _, local = tok.partition(":")
                if not local:
                    continue
                if pfx in _ICD_PREFIXES:
                    term["icd10"].add(local.upper())
                elif pfx == "MESH":
                    term["mesh"].add(local.upper())
                elif pfx == "EFO":
                    term["efo"].add(f"EFO:{local}")
                elif pfx == "UMLS":
                    term["umls"].add(local.upper())
        if term is not None:
            wrote = _flush(term, writer)
            n_terms += wrote
            if wrote:
                for k in counts:
                    counts[k] += 1 if term[k] else 0
    counts["terms"] = n_terms
    return counts


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--obo", default=Path("data/ontology/mondo.obo"), type=Path)
    ap.add_argument("--out", default=Path("data/ontology/mondo_xref.csv"), type=Path)
    ap.add_argument("--url", default=_URL)
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()

    args.obo.parent.mkdir(parents=True, exist_ok=True)
    if args.force or not args.obo.exists():
        _download(args.url, args.obo)
    else:
        print(f"using cached {args.obo}")

    c = parse_obo(args.obo, args.out)
    print(f"\nMONDO terms: {c['terms']}")
    print(f"  with EFO xref  : {c['efo']}")
    print(f"  with MeSH xref : {c['mesh']}")
    print(f"  with ICD10 xref: {c['icd10']}")
    print(f"  wrote -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())