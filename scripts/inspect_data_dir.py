#!/usr/bin/env python3
"""Inventory + preview every file under data/ so a fresh session can see real state.

Audit-first, like every other script here: it reads, it never writes. Standard library
only, so it runs under a bare interpreter as well as the venv. Nothing is loaded whole --
record counts stream through csv.reader (correct with quoted newlines, unlike wc -l) and
large files are scan-capped rather than read to the end.

What it prints, in order:
  1. TREE       -- every file with size + modified time, grouped by directory
  2. TABULAR    -- per delimited file: record count, column list, sample rows
                   (--deep adds a per-column blank rate, which is the thing that
                    actually tells you whether a column is usable)
  3. OTHER      -- archives (member listing), .obo/.json/.txt (line count + head)
  4. READINESS  -- the files each pipeline stage expects, present or missing

Usage (PowerShell):
  python scripts\\inspect_data_dir.py
  python scripts\\inspect_data_dir.py --deep
  python scripts\\inspect_data_dir.py --root D:\\data --rows 5 --cell 60
  python scripts\\inspect_data_dir.py > data_inventory.txt      # paste the file instead
"""
from __future__ import annotations

import argparse
import csv
import datetime as _dt
import json
import os
import sys
import zipfile
from pathlib import Path

csv.field_size_limit(10_000_000)  # criteria/description cells are long

TABULAR_EXT = {".csv", ".tsv"}
TEXT_EXT = {".txt", ".obo", ".md", ".json", ".jsonl", ".yaml", ".yml", ".log"}
ARCHIVE_EXT = {".zip"}

# Files each stage expects. (relative path, which stage needs it)
EXPECTED = [
    ("top/phase_I_train.csv", "build_units / train_rollup (TOP split)"),
    ("top/phase_II_train.csv", "build_units / train_rollup (TOP split)"),
    ("top/phase_III_train.csv", "build_units / train_rollup (TOP split)"),
    ("chembl/drug_indication.csv", "fetch_chembl_labels -> build_units label"),
    ("chembl/molecules.csv", "fetch_chembl_labels -> InChIKey join"),
    ("chembl/units.csv", "build_units -> the (drug, indication) spine"),
    ("chembl/unit_class.csv", "assign_disease_ta + assign_disease_type (dt/ta keys)"),
    ("chembl/moa_class.csv", "audit_moa_prior cache (ChEMBL targets/mechanisms)"),
    ("ontology/mondo_xref.csv", "fetch_mondo -> indication resolution"),
    ("ontology/mondo_edges.csv", "build_mondo_edges -> is_a rollup"),
    ("ontology/disease_type_anchors.csv", "build_disease_type_anchors"),
    ("aact/aact_features.csv", "pull_aact -> design/sponsor/phaseendyear"),
    ("aact/moa_features.csv", "build_moa_target_encoding output"),
    ("cto/cto_trials.csv", "build_cto -> forward set"),
    ("cto/name2chembl.csv", "audit_cto_coverage resolver cache (name -> ChEMBL)"),
]


def _human(n: float) -> str:
    if n < 1024:
        return f"{n:.0f}B"
    for unit in ("KB", "MB", "GB"):
        n /= 1024.0
        if n < 1024 or unit == "GB":
            return f"{n:.1f}{unit}"
    return f"{n:.1f}GB"


def _mtime(p: Path) -> str:
    try:
        return _dt.datetime.fromtimestamp(p.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
    except OSError:
        return "?"


def _trunc(s: str, width: int) -> str:
    s = "" if s is None else str(s).replace("\n", "\\n").replace("\r", "")
    return s if len(s) <= width else s[: width - 1] + "\u2026"


def _sniff_delim(path: Path) -> str:
    if path.suffix.lower() == ".tsv":
        return "\t"
    try:
        with path.open("r", encoding="utf-8", errors="replace", newline="") as f:
            head = f.readline()
    except OSError:
        return ","
    counts = {d: head.count(d) for d in (",", "\t", ";", "|")}
    best = max(counts, key=counts.get)
    return best if counts[best] > 0 else ","


def scan_tabular(path: Path, sample_rows: int, max_scan: int, deep: bool) -> dict:
    """Stream a delimited file: header, N sample rows, record count, blank rates."""
    delim = _sniff_delim(path)
    header: list[str] = []
    samples: list[list[str]] = []
    blanks: list[int] = []
    n = 0
    capped = False
    ragged = 0
    try:
        with path.open("r", encoding="utf-8", errors="replace", newline="") as f:
            reader = csv.reader(f, delimiter=delim)
            try:
                header = next(reader)
            except StopIteration:
                return {"error": "empty file", "delim": delim}
            blanks = [0] * len(header)
            for row in reader:
                n += 1
                if len(samples) < sample_rows:
                    samples.append(row)
                if len(row) != len(header):
                    ragged += 1
                if deep:
                    for i in range(min(len(row), len(header))):
                        v = row[i].strip()
                        if v == "" or v.lower() in ("na", "nan", "none", "null"):
                            blanks[i] += 1
                if n >= max_scan:
                    capped = True
                    break
    except OSError as e:
        return {"error": str(e), "delim": delim}
    return {"delim": delim, "header": header, "samples": samples, "n": n,
            "capped": capped, "ragged": ragged,
            "blanks": blanks if deep else None}


def report_tabular(path: Path, rel: str, args) -> None:
    info = scan_tabular(path, args.rows, args.max_scan, args.deep)
    print(f"\n--- {rel}  ({_human(path.stat().st_size)}, {_mtime(path)}) " + "-" * 12)
    if "error" in info:
        print(f"    !! unreadable: {info['error']}")
        return
    hdr = info["header"]
    count = f"{info['n']:,}{'+ (scan capped)' if info['capped'] else ''}"
    dl = {",": "comma", "\t": "tab", ";": "semicolon", "|": "pipe"}.get(info["delim"], info["delim"])
    print(f"    records: {count}   columns: {len(hdr)}   delimiter: {dl}")
    if info["ragged"]:
        print(f"    !! {info['ragged']} row(s) had a column count differing from the header")
    shown = hdr if len(hdr) <= args.max_cols else hdr[: args.max_cols]
    print(f"    columns: {', '.join(shown)}"
          + (f"  ... (+{len(hdr) - len(shown)} more)" if len(shown) < len(hdr) else ""))
    if info["blanks"] is not None and info["n"]:
        pairs = sorted(zip(hdr, info["blanks"]), key=lambda kv: -kv[1])
        worst = [(k, v) for k, v in pairs if v][: args.max_cols]
        if worst:
            print("    blank/NA rate (worst first):")
            for k, v in worst:
                print(f"      {_trunc(k, 28):28s} {100.0 * v / info['n']:5.1f}%  ({v:,}/{info['n']:,})")
        else:
            print("    blank/NA rate: no blanks found in scanned rows")
    for j, row in enumerate(info["samples"], 1):
        cells = [f"{_trunc(h, 18)}={_trunc(v, args.cell)}"
                 for h, v in list(zip(hdr, row))[: args.max_cols]]
        print(f"    row {j}: " + " | ".join(cells))


def report_archive(path: Path, rel: str, limit: int = 20) -> None:
    print(f"\n--- {rel}  ({_human(path.stat().st_size)}, {_mtime(path)}) " + "-" * 12)
    try:
        with zipfile.ZipFile(path) as z:
            names = z.namelist()
            print(f"    archive members: {len(names)}")
            for nm in names[:limit]:
                try:
                    sz = _human(z.getinfo(nm).file_size)
                except KeyError:
                    sz = "?"
                print(f"      {nm}  ({sz})")
            if len(names) > limit:
                print(f"      ... (+{len(names) - limit} more)")
    except (zipfile.BadZipFile, OSError) as e:
        print(f"    !! unreadable archive: {e}")


def report_text(path: Path, rel: str, args) -> None:
    print(f"\n--- {rel}  ({_human(path.stat().st_size)}, {_mtime(path)}) " + "-" * 12)
    if path.suffix.lower() == ".json":
        try:
            with path.open("r", encoding="utf-8", errors="replace") as f:
                obj = json.load(f)
            if isinstance(obj, dict):
                print(f"    JSON object, {len(obj)} top-level keys: "
                      f"{', '.join(list(obj)[: args.max_cols])}")
            elif isinstance(obj, list):
                print(f"    JSON array, {len(obj)} elements")
                if obj and isinstance(obj[0], dict):
                    print(f"    first element keys: {', '.join(list(obj[0])[: args.max_cols])}")
            else:
                print(f"    JSON scalar of type {type(obj).__name__}")
            return
        except (json.JSONDecodeError, OSError) as e:
            print(f"    (not parseable as one JSON document: {e}; showing head instead)")
    lines = 0
    head: list[str] = []
    try:
        with path.open("r", encoding="utf-8", errors="replace") as f:
            for line in f:
                lines += 1
                if len(head) < args.rows:
                    head.append(line.rstrip("\n"))
                if lines >= args.max_scan:
                    break
    except OSError as e:
        print(f"    !! unreadable: {e}")
        return
    print(f"    lines: {lines:,}{'+ (scan capped)' if lines >= args.max_scan else ''}")
    for line in head:
        print(f"      {_trunc(line, args.cell * 2)}")


def main() -> int:
    ap = argparse.ArgumentParser(description="Inventory + preview the data directory.")
    ap.add_argument("--root", default=Path("data"), type=Path, help="data directory (default: data)")
    ap.add_argument("--rows", type=int, default=3, help="sample rows / head lines per file")
    ap.add_argument("--cell", type=int, default=40, help="max characters per previewed cell")
    ap.add_argument("--max-cols", type=int, default=14, help="max columns shown per file")
    ap.add_argument("--max-scan", type=int, default=400_000, help="row/line scan cap per file")
    ap.add_argument("--deep", action="store_true", help="add per-column blank/NA rates")
    ap.add_argument("--include-hidden", action="store_true")
    args = ap.parse_args()

    root: Path = args.root
    print("=" * 78)
    print("DATA DIRECTORY INVENTORY")
    print("=" * 78)
    print(f"  root       : {root.resolve() if root.exists() else root} "
          f"{'' if root.exists() else '  !! DOES NOT EXIST'}")
    print(f"  cwd        : {Path.cwd()}")
    print(f"  python     : {sys.version.split()[0]} ({sys.platform})")
    print(f"  deep mode  : {'on (blank rates computed)' if args.deep else 'off (pass --deep)'}")
    print("  AACT creds : "
          + ("AACT_USER set" if os.environ.get("AACT_USER") else "AACT_USER not set")
          + ", "
          + ("AACT_PASSWORD set" if os.environ.get("AACT_PASSWORD") else "AACT_PASSWORD not set"))
    if not root.exists():
        print("\nNothing to inventory. Pass --root with the correct path.")
        return 2

    files: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        if not args.include_hidden:
            dirnames[:] = [d for d in dirnames if not d.startswith(".") and d != "__pycache__"]
            filenames = [f for f in filenames if not f.startswith(".")]
        for fn in sorted(filenames):
            files.append(Path(dirpath) / fn)
    files.sort(key=lambda p: str(p).lower())

    total = sum(p.stat().st_size for p in files if p.exists())
    print(f"\n{'=' * 78}\nTREE  ({len(files)} files, {_human(total)} total)\n{'=' * 78}")
    last_dir = None
    for p in files:
        d = str(p.parent)
        if d != last_dir:
            print(f"\n  {d}{os.sep}")
            last_dir = d
        print(f"    {p.name:52s} {_human(p.stat().st_size):>9s}  {_mtime(p)}")

    tab = [p for p in files if p.suffix.lower() in TABULAR_EXT]
    arc = [p for p in files if p.suffix.lower() in ARCHIVE_EXT]
    txt = [p for p in files if p.suffix.lower() in TEXT_EXT]
    other = [p for p in files if p not in set(tab) | set(arc) | set(txt)]

    if tab:
        print(f"\n{'=' * 78}\nTABULAR FILES ({len(tab)})\n{'=' * 78}")
        for p in tab:
            report_tabular(p, str(p.relative_to(root)), args)
    if arc:
        print(f"\n{'=' * 78}\nARCHIVES ({len(arc)})\n{'=' * 78}")
        for p in arc:
            report_archive(p, str(p.relative_to(root)))
    if txt:
        print(f"\n{'=' * 78}\nTEXT / ONTOLOGY / JSON ({len(txt)})\n{'=' * 78}")
        for p in txt:
            report_text(p, str(p.relative_to(root)), args)
    if other:
        print(f"\n{'=' * 78}\nOTHER / UNRECOGNIZED ({len(other)})\n{'=' * 78}")
        for p in other:
            print(f"  {p.relative_to(root)}  ({_human(p.stat().st_size)}, {_mtime(p)})")

    print(f"\n{'=' * 78}\nPIPELINE READINESS\n{'=' * 78}")
    missing = 0
    for rel, stage in EXPECTED:
        p = root / rel
        if p.exists():
            print(f"  [x] {rel:42s} {_human(p.stat().st_size):>9s}  {stage}")
        else:
            missing += 1
            print(f"  [ ] {rel:42s} {'--':>9s}  {stage}")
    print(f"\n  {len(EXPECTED) - missing}/{len(EXPECTED)} expected files present.")
    print("  A missing file is not necessarily a problem -- it may just mean that stage")
    print("  has not been run yet, or the file lives elsewhere. Paste this output and I")
    print("  will read the real state rather than assuming it.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
