#!/usr/bin/env python3
"""AACT monthly-archive probe. READ-ONLY: restores nothing, extracts nothing to disk.

SCRATCH. Run on the downloaded snapshot zip before any local restore is designed:

  1. the zip: name, size, sha256, members
  2. the dump inside it: format (custom-format magic), and pg_restore's own header --
     when it was made, from which PostgreSQL version, with what compression
  3. every table with data in the snapshot's schema, against every table this project's
     pulls read. The required list is DERIVED from the pull declarations, so a table added
     to a pull is checked here without editing this file

The dump is streamed from the zip into `pg_restore -l`, which reads only the table of
contents, so the 2 GB+ dump is never written out.

Usage (PowerShell), one at a time:
  python audit\\probe_aact_archive.py --zip data\\aact_snapshots\\<file>.zip --pg-restore <path to pg_restore.exe>
"""
from __future__ import annotations

import argparse
import hashlib
import re
import shutil
import subprocess
import sys
import threading
import zipfile
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from trial_pos.services.aact_snapshot import required_tables  # noqa: E402

DEFAULT_SCHEMA = "ctgov"
DUMP_SUFFIX = ".dmp"
PGDMP_MAGIC = b"PGDMP"
STREAM_CHUNK = 1 << 20
HASH_CHUNK = 1 << 20
HEADER_KEYS = ("Archive created at", "dbname", "TOC Entries", "Compression",
               "Dump Version", "Format", "Dumped from database version",
               "Dumped by pg_dump version")
_TABLE_DATA = re.compile(r"^\d+; \d+ \d+ TABLE DATA (\S+) (\S+) ")
_SNAPSHOT_DATE = re.compile(r"^(\d{8})_")


def _rule(title: str) -> str:
    return "\n" + "=" * 78 + f"\n{title}\n" + "=" * 78


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(HASH_CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


def list_toc(pg_restore: str, source) -> tuple:
    """Feed a binary stream to `pg_restore -l` and return (returncode, stdout, stderr).
    pg_restore exits once it has read the table of contents; the broken pipe that follows
    is expected and ends the feed."""
    proc = subprocess.Popen([pg_restore, "-l"], stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    def feed():
        try:
            for chunk in iter(lambda: source.read(STREAM_CHUNK), b""):
                proc.stdin.write(chunk)
        except (BrokenPipeError, OSError):
            pass
        finally:
            try:
                proc.stdin.close()
            except OSError:
                pass

    writer = threading.Thread(target=feed, daemon=True)
    writer.start()
    out, err = proc.stdout.read(), proc.stderr.read()
    proc.wait()
    writer.join(timeout=5)
    return proc.returncode, out.decode("utf-8", "replace"), err.decode("utf-8", "replace")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--zip", type=Path, required=True)
    ap.add_argument("--pg-restore", default=shutil.which("pg_restore"),
                    help="path to pg_restore (14 or later). Default: from PATH")
    ap.add_argument("--schema", default=DEFAULT_SCHEMA)
    ap.add_argument("--url", default=None,
                    help="the link the zip was downloaded from, recorded for the registry")
    args = ap.parse_args()
    if not args.zip.exists():
        raise SystemExit(f"!! not found: {args.zip}")
    if not args.pg_restore or not Path(args.pg_restore).exists():
        raise SystemExit("!! pg_restore not found. Pass --pg-restore, e.g. "
                         "C:\\Users\\<you>\\miniconda3\\envs\\pgtools\\Library\\bin\\"
                         "pg_restore.exe")
    print(f"zip {args.zip} | pg_restore {args.pg_restore} | schema {args.schema} | "
          f"url {args.url or '(not given)'}")

    print(_rule("1. THE ZIP"))
    size = args.zip.stat().st_size
    print(f"  size {size} bytes | sha256 {sha256_of(args.zip)}")
    m = _SNAPSHOT_DATE.match(args.zip.name)
    named = datetime.strptime(m.group(1), "%Y%m%d").date() if m else None
    print(f"  snapshot date from the file name: {named or '(no YYYYMMDD_ prefix)'}")
    with zipfile.ZipFile(args.zip) as z:
        members = z.infolist()
        for info in members:
            print(f"    {info.filename:50s} {info.file_size:14d} bytes")
        dumps = [i for i in members if i.filename.endswith(DUMP_SUFFIX)]
        if len(dumps) != 1:
            print(f"!! expected exactly one {DUMP_SUFFIX} member, found {len(dumps)}")
            return 2

        print(_rule("2. THE DUMP"))
        with z.open(dumps[0]) as handle:
            magic = handle.read(len(PGDMP_MAGIC))
        print(f"  {dumps[0].filename}: first bytes {magic!r} -> "
              f"{'custom-format archive' if magic == PGDMP_MAGIC else 'NOT a custom-format archive'}")
        if magic != PGDMP_MAGIC:
            return 2
        with z.open(dumps[0]) as handle:
            code, out, err = list_toc(args.pg_restore, handle)
    if code != 0 or not out.strip():
        print(f"!! pg_restore -l failed (exit {code}): {err.strip()[:500]}")
        return 2
    for line in out.splitlines():
        stripped = line.lstrip("; ").strip()
        for key in HEADER_KEYS:
            if stripped.startswith(key):
                print(f"  {stripped}")

    print(_rule("3. TABLES WITH DATA"))
    data = {}
    for line in out.splitlines():
        t = _TABLE_DATA.match(line)
        if t:
            data.setdefault(t.group(1), []).append(t.group(2))
    for schema, tables in sorted(data.items()):
        print(f"  schema {schema}: {len(tables)} tables with data")
    present = set(data.get(args.schema, []))
    wanted = required_tables()
    missing = [t for t in wanted if t not in present]
    print(f"\n  tables the pulls read: {len(wanted)}; present in {args.schema}: "
          f"{len(wanted) - len(missing)}")
    for t in missing:
        print(f"    !! absent: {t}")
    extra = sorted(present - set(wanted))
    print(f"  tables in the snapshot no pull reads: {len(extra)}")
    print(f"    {', '.join(extra)}")
    return 1 if missing else 0


if __name__ == "__main__":
    sys.exit(main())
