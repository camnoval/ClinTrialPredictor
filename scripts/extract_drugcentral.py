#!/usr/bin/env python3
"""Extract the DrugCentral tables this project uses from the official dump, to CSV.

No database. Reads plain SQL (.sql or .sql.gz) and keeps only the tables in WANTED.
DrugCentral now publishes a CUSTOM-format archive (.pgdump), which is binary: convert the
wanted tables to plain SQL first with `pg_restore -f`, which needs no server and no RDKit.
Passing the archive itself prints the exact command, derived from WANTED. Writes:

  data/drugcentral/dc_<table>.csv       one per table, NULL written as an empty field
  data/drugcentral/drugcentral.manifest.json
      the SQL file's and the original archive's (--archive) name, size and sha256, the
      dbversion row, and per table: rows, columns, null / empty-string counts per column

Fails closed (lesson 54): everything goes to `.partial` files, renamed only after every
wanted table was found and read. Existing outputs are never overwritten without
--overwrite. A column holding both NULL and empty strings cannot round-trip through the
CSV, so the manifest lists it under `ambiguous_null_empty` and the run prints it.

Once the manifest is written the archive and the SQL file can be deleted: the archive's
sha256 identifies it if it is ever needed again.

Usage (PowerShell):
  python scripts\\extract_drugcentral.py --dump <archive>.pgdump        (prints the command)
  python scripts\\extract_drugcentral.py --dump <tables>.sql --archive <archive>.pgdump
"""
from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from trial_pos.services.pgdump import (  # noqa: E402
    column_null_profile, iter_copy_blocks,
)

DEFAULT_OUT_DIR = Path("data/drugcentral")
DEFAULT_SCHEMA = "public"
FILE_PREFIX = "dc_"
OUT_MANIFEST = "drugcentral.manifest.json"
PARTIAL_SUFFIX = ".partial"
HASH_CHUNK_BYTES = 1 << 20
DUMP_ENCODING = "utf-8"
PGDMP_MAGIC = b"PGDMP"          # first bytes of a pg_dump custom-format archive

# Table -> columns to keep, or None for all. `structures` drops the molfile, image and
# descriptor columns, which are most of its bytes and nothing here reads.
WANTED = {
    "dbversion": None,
    "structures": ("id", "name", "cas_reg_no", "inchikey", "stem", "status",
                   "no_formulations", "fda_labels"),
    "approval": None,
    "approval_type": None,
    "identifier": None,
    "synonyms": None,
    "omop_relationship": None,
    "ob_product": None,
    "struct2obprod": None,
    "struct2parent": None,
    "doid": None,
    "doid_xref": None,
}


def _rule(title: str) -> str:
    return "\n" + "=" * 78 + f"\n{title}\n" + "=" * 78


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(HASH_CHUNK_BYTES), b""):
            digest.update(chunk)
    return digest.hexdigest()


def is_custom_archive(path: Path) -> bool:
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rb") as handle:
        return handle.read(len(PGDMP_MAGIC)) == PGDMP_MAGIC


def pg_restore_command(archive: Path, schema: str, sql_out: Path) -> str:
    tables = " ".join(f"-t {t}" for t in WANTED)
    return (f"pg_restore --data-only --schema={schema} {tables} "
            f"-f {sql_out} {archive}")


def open_dump(path: Path):
    """Text lines, strict decoding: an undecodable byte raises rather than becoming '?'."""
    if path.suffix == ".gz":
        return gzip.open(path, "rt", encoding=DUMP_ENCODING, errors="strict", newline="")
    return path.open("r", encoding=DUMP_ENCODING, errors="strict", newline="")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dump", type=Path, required=True,
                    help="plain SQL (.sql or .sql.gz) holding the wanted tables' data")
    ap.add_argument("--archive", type=Path, default=None,
                    help="the original .pgdump the SQL was converted from; its name, size "
                         "and sha256 go into the manifest")
    ap.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    ap.add_argument("--schema", default=DEFAULT_SCHEMA)
    ap.add_argument("--overwrite", action="store_true")
    args = ap.parse_args()

    if not args.dump.exists():
        raise SystemExit(f"!! not found: {args.dump}")
    if is_custom_archive(args.dump):
        sql_out = args.dump.with_suffix(".tables.sql")
        print(f"!! {args.dump} is a custom-format archive, not plain SQL. Convert the "
              f"{len(WANTED)} wanted tables first (pg_restore 16 or later; no server "
              f"needed):\n")
        print("  " + pg_restore_command(args.dump, args.schema, sql_out))
        print(f"\nthen:\n\n  python scripts\\extract_drugcentral.py --dump {sql_out} "
              f"--archive {args.dump}")
        return 2
    if args.archive is not None and not args.archive.exists():
        raise SystemExit(f"!! not found: {args.archive}")
    paths = {t: args.out_dir / f"{FILE_PREFIX}{t}.csv" for t in WANTED}
    paths[OUT_MANIFEST] = args.out_dir / OUT_MANIFEST
    existing = [p for p in paths.values() if p.exists()]
    if existing and not args.overwrite:
        raise SystemExit("!! outputs exist; pass --overwrite to replace them: "
                         + ", ".join(str(p) for p in existing))
    partial = {k: p.with_name(p.name + PARTIAL_SUFFIX) for k, p in paths.items()}

    print(f"dump {args.dump} | schema {args.schema} | out {args.out_dir} | "
          f"tables {len(WANTED)} | overwrite {args.overwrite}")
    started = _now()
    t0 = time.monotonic()
    size = args.dump.stat().st_size
    digest = sha256_of(args.dump)
    print(f"  size {size} bytes | sha256 {digest} | hashed in {time.monotonic() - t0:.0f}s")
    archive = None
    if args.archive is not None:
        archive = {"file": args.archive.name, "bytes": args.archive.stat().st_size,
                   "sha256": sha256_of(args.archive)}
        print(f"  archive {archive['file']} | size {archive['bytes']} | "
              f"sha256 {archive['sha256']}")
    else:
        print("  !! no --archive: the manifest will not identify the published release")

    args.out_dir.mkdir(parents=True, exist_ok=True)
    tables: dict = {}
    dbversion = None
    try:
        with open_dump(args.dump) as lines:
            for table, columns, rows in iter_copy_blocks(lines, WANTED, args.schema):
                with partial[table].open("w", newline="", encoding="utf-8") as handle:
                    writer = csv.writer(handle)
                    writer.writerow(columns)
                    for row in rows:
                        writer.writerow(["" if v is None else v for v in row])
                profile = column_null_profile(columns, rows)
                tables[table] = {
                    "file": paths[table].name, "rows": len(rows), "columns": list(columns),
                    "null_profile": profile,
                    "ambiguous_null_empty": [c for c, p in profile.items()
                                             if p["null"] and p["empty"]],
                }
                if table == "dbversion":
                    dbversion = [dict(zip(columns, r)) for r in rows]
                print(f"  {table:20s} rows {len(rows):8d}  columns {len(columns):3d}  "
                      f"{time.monotonic() - t0:.0f}s", flush=True)
        missing = [t for t in WANTED if t not in tables]
        if missing:
            raise SystemExit(f"!! tables absent from the dump: {missing}. Nothing written.")
    except BaseException:
        for p in partial.values():
            if p.exists():
                p.unlink()
        print("\n!! extraction did not finish; partial files removed, previous outputs "
              "untouched.")
        raise

    manifest = {
        "script": "extract_drugcentral.py", "started_utc": started, "finished_utc": _now(),
        "dump_file": args.dump.name, "dump_bytes": size, "dump_sha256": digest,
        "archive": archive,
        "schema": args.schema, "dbversion": dbversion,
        "csv_null_convention": ("NULL is written as an empty field; a column listed in "
                                "ambiguous_null_empty also holds empty strings, so blank "
                                "there does not mean NULL"),
        "tables": tables,
    }
    with partial[OUT_MANIFEST].open("w", encoding="utf-8") as handle:
        json.dump(manifest, handle, indent=2)
    for key, path in paths.items():
        os.replace(partial[key], path)

    print(_rule("DONE"))
    print(f"  dbversion: {dbversion}")
    ambiguous = {t: v["ambiguous_null_empty"] for t, v in tables.items()
                 if v["ambiguous_null_empty"]}
    print(f"  columns holding both NULL and empty strings: {ambiguous or 'none'}")
    for path in paths.values():
        print(f"  wrote {path}")
    print(f"  elapsed {time.monotonic() - t0:.0f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
