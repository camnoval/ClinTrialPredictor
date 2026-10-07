#!/usr/bin/env python3
"""Every input file's identity and schema, and the rev 10 claims no shipped output backs.
READ-ONLY, no database. SCRATCH.

  1. identity: bytes and sha256 of every file under --aact-dir, --dc-dir and --fda-dir, the
     MONDO crosswalk, and the pinned AACT zip (unless --skip-snapshot-hash). Rev 10
     §12.24's hash table is checked against this by reading, so no rev 10 figure is
     written into this file. Drugs@FDA is overwritten daily and its download was never
     hashed, so this is also its first record.
  2. DrugCentral: per table, the manifest's rows and columns, the columns holding NULLs,
     and the CSV re-read with the csv module against the manifest's row count
  3. Drugs@FDA: every file's header and rows (read by probe_drugsatfda.read_table, the
     reader §12.28 came from), and every table of at most --lookup-max rows in full
  4. rev 10 §12.28, where the Drugs@FDA probe printed less than the claim:
     a. applications per ApplType and how many of EACH are in ob_product (the probe
        printed only the matched counts; §12.28 says "every NDA and ANDA")
     b. ApplNo shape in both sources, since the join zero-pads
     c. approved ORIG submissions per application, by ApplType
     d. approved ORIG submissions by ApplType x submission class: the NME vocabulary
     e. ActiveIngredient split on ';' only (as the probe did) and on every separator,
        each matched exactly to DrugCentral synonyms
     f. ingredients of BLA products against synonyms: the only route for biologics
     g. every Drugs@FDA table carrying ApplNo: distinct numbers, their shape, and how many
        are in Applications raw and after zero-padding. 4d's ApplType '?' rows are
        submissions whose ApplNo is not in Applications; this says whether that is a key
        format mismatch or a real orphan

Usage (PowerShell), one at a time:
  python audit\\probe_source_files.py
  python audit\\probe_drugsatfda.py --dc-dir data\\drugcentral
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HERE = Path(__file__).resolve().parent
for p in (ROOT / "src", HERE):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from probe_drugsatfda import (  # noqa: E402
    APPROVED, EARLIEST_PLAUSIBLE_APPROVAL, INGREDIENT_SEPARATOR, NAME_SEPARATORS, ORIGINAL,
    _norm_appl, parse_date, read_table,
)
from trial_pos.services.aact_snapshot import PINNED, SNAPSHOT_DIR  # noqa: E402

csv.field_size_limit(10 ** 9)

DEFAULT_AACT_DIR = Path("data/aact")
DEFAULT_DC_DIR = Path("data/drugcentral")
DEFAULT_FDA_DIR = Path("data/drugsatfda")
DEFAULT_MONDO = Path("data/ontology/mondo_xref.csv")
DC_MANIFEST = "drugcentral.manifest.json"
DEFAULT_LOOKUP_MAX = 100
DEFAULT_SAMPLES = 8
HASH_CHUNK = 1 << 20
FDA_BLA = "BLA"
DC_TABLES_READ = ("ob_product", "synonyms")
DC_APPL_TYPE, DC_APPL_NO, DC_LNAME = "appl_type", "appl_no", "lname"


def _rule(title: str) -> str:
    return "\n" + "=" * 78 + f"\n{title}\n" + "=" * 78


def _pct(n, d) -> str:
    return "n/a" if not d else f"{100.0 * n / d:.1f}%"


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(HASH_CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


def identity(label: str, target: Path) -> None:
    if not target.exists():
        print(f"  !! {label}: {target} not found")
        return
    files = [target] if target.is_file() else sorted(p for p in target.rglob("*") if p.is_file())
    print(f"\n  {label}: {target} ({len(files)} files)")
    for p in files:
        rel = p.name if target.is_file() else p.relative_to(target).as_posix()
        print(f"    {rel:52s} {p.stat().st_size:14d}  {sha256_of(p)}", flush=True)


def split_ingredients(value: str, separators) -> list:
    """One ActiveIngredient string -> its parts, split on every given separator."""
    parts = [value]
    for sep in separators:
        parts = [piece for part in parts for piece in part.split(sep)]
    return [p.strip().lower() for p in parts if p.strip()]


def dc_rows(folder: Path, manifest: dict, table: str) -> list:
    meta = manifest["tables"][table]
    with (folder / meta["file"]).open(newline="", encoding="utf-8") as h:
        return list(csv.DictReader(h))


def section_drugcentral(folder: Path) -> tuple:
    print(_rule("2. DRUGCENTRAL"))
    path = folder / DC_MANIFEST
    if not path.exists():
        print(f"  !! {path} not found")
        return None, {}
    manifest = json.loads(path.read_text(encoding="utf-8"))
    print(f"  dbversion {manifest.get('dbversion')}")
    print(f"  archive {manifest.get('archive')}")
    print(f"  dump {manifest.get('dump_file')} sha256 {manifest.get('dump_sha256')}")
    for table, meta in sorted(manifest["tables"].items()):
        with (folder / meta["file"]).open(newline="", encoding="utf-8") as h:
            reader = csv.reader(h)
            header = next(reader, [])
            n = sum(1 for _ in reader)
        nulls = {c: p["null"] for c, p in meta["null_profile"].items() if p["null"]}
        flag = "" if n == meta["rows"] and header == meta["columns"] else "  <-- MISMATCH"
        print(f"\n  {table}: manifest rows {meta['rows']}, csv rows {n}{flag}")
        print(f"    columns {meta['columns']}")
        print(f"    columns holding NULL (count): {nulls or 'none'}")
        if meta["ambiguous_null_empty"]:
            print(f"    NULL and empty both present: {meta['ambiguous_null_empty']}")
    data = {}
    for table in DC_TABLES_READ:
        if table in manifest["tables"]:
            data[table] = dc_rows(folder, manifest, table)
    return manifest, data


def section_fda_files(folder: Path, lookup_max: int) -> dict:
    print(_rule("3. DRUGS@FDA FILES"))
    if not folder.is_dir():
        print(f"  !! {folder} is not a folder")
        return {}
    tables = {}
    for path in sorted(folder.glob("*.txt")):
        t = read_table(path)
        tables[path.stem] = t
        print(f"\n  {path.stem}: rows {len(t['rows'])} malformed {t['malformed']} "
              f"encoding {t['encoding']}")
        print(f"    header {t['header']}")
        if len(t["rows"]) <= lookup_max:
            for r in t["rows"]:
                print(f"      {[r[h] for h in t['header']]}")
    return tables


def section_claims(tables: dict, dc: dict, samples: int) -> None:
    print(_rule("4. REV 10 SECTION 12.28 CLAIMS"))
    apps = tables.get("Applications", {}).get("rows", [])
    subs = tables.get("Submissions", {}).get("rows", [])
    prods = tables.get("Products", {}).get("rows", [])
    classes = {r["SubmissionClassCodeID"]: (r["SubmissionClassCode"],
                                            r["SubmissionClassCodeDescription"])
               for r in tables.get("SubmissionClass_Lookup", {}).get("rows", [])}
    ob = dc.get("ob_product", [])
    lnames = {(r.get(DC_LNAME) or "").strip().lower() for r in dc.get("synonyms", [])} - {""}
    if not apps or not ob:
        print("  !! Applications or ob_product absent; 4a-4b skipped")
    else:
        if DC_APPL_TYPE not in ob[0] or DC_APPL_NO not in ob[0]:
            print(f"  !! ob_product lacks {DC_APPL_TYPE}/{DC_APPL_NO}; has {list(ob[0])}")
        else:
            ob_by_no = defaultdict(set)
            for r in ob:
                ob_by_no[_norm_appl(r[DC_APPL_NO])].add(r[DC_APPL_TYPE])
            fda_type = {_norm_appl(r["ApplNo"]): r["ApplType"] for r in apps}
            print("\n  4a. Drugs@FDA applications per ApplType, and how many are in ob_product:")
            print(f"    {'ApplType':10s} {'total':>8s} {'in ob_product':>14s} {'absent':>8s}")
            for typ in sorted(set(fda_type.values())):
                nos = [a for a, t in fda_type.items() if t == typ]
                inside = sum(1 for a in nos if a in ob_by_no)
                print(f"    {typ:10s} {len(nos):8d} {inside:14d} {len(nos) - inside:8d}")
            print(f"    {'(all)':10s} {len(fda_type):8d} "
                  f"{sum(1 for a in fda_type if a in ob_by_no):14d}")
            only_dc = Counter(t for a, ts in ob_by_no.items() if a not in fda_type for t in ts)
            print(f"    ob_product application numbers absent from Drugs@FDA, by "
                  f"ob_product.{DC_APPL_TYPE}: {dict(sorted(only_dc.items()))}")
            print(f"    ob_product.{DC_APPL_TYPE} vocabulary (rows): "
                  f"{dict(sorted(Counter(r[DC_APPL_TYPE] for r in ob).items()))}")
            print("\n  4b. ApplNo shape (length, all digits) -> distinct numbers:")
            print(f"    Drugs@FDA  {dict(sorted(Counter((len(r['ApplNo']), r['ApplNo'].isdigit()) for r in apps).items()))}")
            print(f"    ob_product {dict(sorted(Counter((len(v), v.isdigit()) for v in {r[DC_APPL_NO] for r in ob}).items()))}")
    if apps and subs:
        appl_type = {r["ApplNo"]: r["ApplType"] for r in apps}
        orig_ap = [r for r in subs if r["SubmissionType"] == ORIGINAL
                   and r["SubmissionStatus"] == APPROVED]
        per = Counter(r["ApplNo"] for r in orig_ap)
        print("\n  4c. approved ORIG submissions per application, by ApplType:")
        print(f"    {'ApplType':10s} {'0':>8s} {'1':>8s} {'2+':>8s}")
        for typ in sorted(set(appl_type.values())):
            nos = [a for a, t in appl_type.items() if t == typ]
            b = Counter(min(per.get(a, 0), 2) for a in nos)
            print(f"    {typ:10s} {b[0]:8d} {b[1]:8d} {b[2]:8d}")
        early = [r for r in orig_ap if isinstance(parse_date(r["SubmissionStatusDate"]), date)
                 and parse_date(r["SubmissionStatusDate"]) < EARLIEST_PLAUSIBLE_APPROVAL]
        print(f"    approved ORIG dated before {EARLIEST_PLAUSIBLE_APPROVAL}: {len(early)} "
              f"{[(r['ApplNo'], r['SubmissionStatusDate']) for r in early[:samples]]}")
        print("\n  4d. approved ORIG by ApplType x submission class:")
        table = Counter((appl_type.get(r["ApplNo"], "?"),) +
                        classes.get(r["SubmissionClassCodeID"],
                                    ("(blank)" if not r["SubmissionClassCodeID"] else "(orphan)",
                                     "")) for r in orig_ap)
        for (typ, code, desc), n in sorted(table.items(), key=lambda kv: (kv[0][0], -kv[1])):
            print(f"    {typ:6s} {code:14s} {desc[:46]:46s} {n:8d}")
    if prods:
        strings = {r["ActiveIngredient"] for r in prods if r["ActiveIngredient"].strip()}
        semi = {p for s in strings for p in split_ingredients(s, (INGREDIENT_SEPARATOR,))}
        every = {p for s in strings for p in split_ingredients(s, NAME_SEPARATORS)}
        print(f"\n  4e. ActiveIngredient: {len(strings)} distinct strings")
        for sep in NAME_SEPARATORS:
            hits = sorted(s for s in strings if sep in s)
            print(f"    containing {sep!r}: {len(hits)}  e.g. {hits[:samples]}")
        print(f"    singles splitting on {INGREDIENT_SEPARATOR!r} only: {len(semi)}, matched to "
              f"synonyms {len(semi & lnames)} ({_pct(len(semi & lnames), len(semi))})")
        print(f"    singles splitting on all {NAME_SEPARATORS}: {len(every)}, matched "
              f"{len(every & lnames)} ({_pct(len(every & lnames), len(every))})")
        bla = {r["ApplNo"] for r in apps if r["ApplType"] == FDA_BLA}
        bla_ing = {p for r in prods if r["ApplNo"] in bla
                   for p in split_ingredients(r["ActiveIngredient"], NAME_SEPARATORS)}
        print(f"\n  4f. {FDA_BLA} applications {len(bla)}, products "
              f"{sum(1 for r in prods if r['ApplNo'] in bla)}, distinct ingredients "
              f"{len(bla_ing)}, matched to synonyms {len(bla_ing & lnames)} "
              f"({_pct(len(bla_ing & lnames), len(bla_ing))})")
        print(f"    unmatched, first {samples} in order: {sorted(bla_ing - lnames)[:samples]}")
    if apps:
        raw_keys = {r["ApplNo"] for r in apps}
        norm_keys = {_norm_appl(a) for a in raw_keys}
        print("\n  4g. ApplNo in each table against Applications:")
        print(f"    {'table':36s} {'distinct':>9s} {'raw in':>8s} {'padded in':>10s} "
              f"{'neither':>8s}  shapes (length, digits)")
        for name in sorted(tables):
            t = tables[name]
            if "ApplNo" not in t["header"] or name == "Applications":
                continue
            nos = {r["ApplNo"] for r in t["rows"]}
            raw_in = sum(1 for a in nos if a in raw_keys)
            pad_in = sum(1 for a in nos if _norm_appl(a) in norm_keys)
            missing = sorted(a for a in nos if _norm_appl(a) not in norm_keys)
            shapes = dict(sorted(Counter((len(a), a.isdigit()) for a in nos).items()))
            print(f"    {name:36s} {len(nos):9d} {raw_in:8d} {pad_in:10d} {len(missing):8d}  "
                  f"{shapes}")
            if missing:
                print(f"      absent after padding, first {samples}: {missing[:samples]}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--aact-dir", type=Path, default=DEFAULT_AACT_DIR)
    ap.add_argument("--dc-dir", type=Path, default=DEFAULT_DC_DIR)
    ap.add_argument("--fda-dir", type=Path, default=DEFAULT_FDA_DIR)
    ap.add_argument("--mondo", type=Path, default=DEFAULT_MONDO)
    ap.add_argument("--snapshot-dir", type=Path, default=SNAPSHOT_DIR)
    ap.add_argument("--skip-snapshot-hash", action="store_true")
    ap.add_argument("--skip-identity", action="store_true",
                    help="skip section 1 (hashing) entirely")
    ap.add_argument("--lookup-max", type=int, default=DEFAULT_LOOKUP_MAX)
    ap.add_argument("--samples", type=int, default=DEFAULT_SAMPLES)
    args = ap.parse_args()
    print(f"aact {args.aact_dir} | drugcentral {args.dc_dir} | drugs@fda {args.fda_dir} | "
          f"mondo {args.mondo} | lookup max {args.lookup_max} | separators {NAME_SEPARATORS}")
    print(_rule("1. FILE IDENTITY (bytes, sha256)"))
    if args.skip_identity:
        print("  --skip-identity: not hashed")
    else:
        identity("AACT-derived", args.aact_dir)
        identity("DrugCentral", args.dc_dir)
        identity("Drugs@FDA", args.fda_dir)
        identity("MONDO crosswalk", args.mondo)
    if args.skip_identity:
        pass
    elif args.skip_snapshot_hash:
        print("\n  pinned AACT zip: --skip-snapshot-hash, not hashed")
    else:
        zp = args.snapshot_dir / PINNED.file
        identity(f"pinned AACT zip (pin says {PINNED.bytes} bytes, {PINNED.sha256})", zp)
    _manifest, dc = section_drugcentral(args.dc_dir)
    tables = section_fda_files(args.fda_dir, args.lookup_max)
    section_claims(tables, dc, args.samples)
    return 0


if __name__ == "__main__":
    sys.exit(main())
