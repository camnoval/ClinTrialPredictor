#!/usr/bin/env python3
"""Restore the pinned AACT monthly snapshot into a local PostgreSQL, for the pulls to read.

Verifies the downloaded zip's size and sha256 against `aact_snapshot.PINNED` first and
stops on any mismatch. Then creates a cluster in data/pg/aact (C locale, localhost only),
starts it on a port away from 5432, extracts the dump next to it, and restores it with
parallel jobs; the extracted dump is deleted afterwards unless --keep-dump. Finally checks
that every table a pull reads exists and has rows. The server is left running; --stop
stops it, --start restarts it.

Needs the PostgreSQL server programs (initdb, pg_ctl, createdb, pg_restore), version 14
or later: the `pgtools` conda environment has them. Restored size is several times the
zip; check free disk space first.

Usage (PowerShell), one at a time:
  python scripts\\restore_aact_snapshot.py --pg-bin $HOME\\miniconda3\\envs\\pgtools\\Library\\bin
  python scripts\\restore_aact_snapshot.py --pg-bin <same> --stop
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import time
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from trial_pos.services.aact_snapshot import (  # noqa: E402
    CLUSTER_DIR, DEFAULT_RESTORE_JOBS, DUMP_MEMBER_SUFFIX, LOCAL_DB, LOCAL_HOST,
    LOCAL_PORT, LOCAL_USER, PINNED,
    SNAPSHOT_DIR, createdb_cmd, initdb_cmd, required_tables, restore_cmd, start_cmd,
    stop_cmd,
)
from trial_pos.services.data_registry import (  # noqa: E402
    STATUS_VERIFIED, verify_file,
)

STREAM_CHUNK = 1 << 20
SCHEMA = "ctgov"
ERROR_LINES_SHOWN = 20


def _run(cmd: list) -> None:
    print(f"  $ {' '.join(cmd)}", flush=True)
    rc = subprocess.run(cmd).returncode
    if rc != 0:
        raise SystemExit(f"!! exit {rc}: {cmd[0]}")


def _extract_dump(zip_path: Path, dest: Path) -> Path:
    """Copy the zip's one .dmp member to `dest`, checking its length."""
    with zipfile.ZipFile(zip_path) as z:
        dumps = [i for i in z.infolist() if i.filename.endswith(DUMP_MEMBER_SUFFIX)]
        if len(dumps) != 1:
            raise SystemExit(f"!! expected one {DUMP_MEMBER_SUFFIX} in the zip, found "
                             f"{len(dumps)}")
        total = dumps[0].file_size
        print(f"  extracting {dumps[0].filename} ({total >> 20} MB) -> {dest}", flush=True)
        with z.open(dumps[0]) as src, dest.open("wb") as out:
            shutil.copyfileobj(src, out, STREAM_CHUNK)
    if dest.stat().st_size != total:
        raise SystemExit(f"!! extracted {dest.stat().st_size} bytes, expected {total}")
    return dest


def _restore(cmd: list, log: Path) -> int:
    print(f"  $ {' '.join(cmd)}", flush=True)
    with log.open("wb") as err:
        return subprocess.run(cmd, stderr=err).returncode


def _verify_tables(port: int) -> int:
    import psycopg2
    conn = psycopg2.connect(host=LOCAL_HOST, port=port, dbname=LOCAL_DB, user=LOCAL_USER)
    bad = 0
    try:
        with conn.cursor() as c:
            c.execute("SELECT table_name FROM information_schema.tables "
                      "WHERE table_schema = %s", (SCHEMA,))
            present = {r[0] for r in c.fetchall()}
            print(f"\n  tables in {SCHEMA}: {len(present)}")
            for table in required_tables():
                if table not in present:
                    print(f"    !! absent  {table}")
                    bad += 1
                    continue
                c.execute(f"SELECT count(*) FROM {SCHEMA}.{table}")
                n = c.fetchone()[0]
                flag = "!! EMPTY" if n == 0 else "        "
                bad += n == 0
                print(f"    {flag} {table:32s} {n:12d}")
    finally:
        conn.close()
    return bad


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--pg-bin", default=None,
                    help="folder holding initdb, pg_ctl, createdb, pg_restore")
    ap.add_argument("--zip", type=Path, default=ROOT / SNAPSHOT_DIR / PINNED.file)
    ap.add_argument("--cluster", type=Path, default=ROOT / CLUSTER_DIR)
    ap.add_argument("--port", type=int, default=LOCAL_PORT)
    ap.add_argument("--jobs", type=int, default=DEFAULT_RESTORE_JOBS,
                    help="parallel pg_restore jobs. Default: %(default)s")
    ap.add_argument("--keep-dump", action="store_true",
                    help="keep the extracted postgres.dmp after the restore")
    ap.add_argument("--replace-cluster", action="store_true",
                    help="delete an existing cluster folder first")
    ap.add_argument("--start", action="store_true", help="start an existing cluster, then stop")
    ap.add_argument("--stop", action="store_true", help="stop the cluster, then stop")
    args = ap.parse_args()
    log = args.cluster.parent / "aact_server.log"
    print(f"pinned {PINNED.file} ({PINNED.date}) | cluster {args.cluster} | port {args.port} "
          f"| pg-bin {args.pg_bin or '(PATH)'}")

    if args.stop:
        _run(stop_cmd(args.pg_bin, args.cluster))
        return 0
    if args.start:
        _run(start_cmd(args.pg_bin, args.cluster, log, args.port))
        return 0

    t0 = time.monotonic()
    print(f"\n  verifying {args.zip} against the pin ...", flush=True)
    status = verify_file(args.zip, PINNED.bytes, PINNED.sha256)
    if status != STATUS_VERIFIED:
        raise SystemExit(f"!! {args.zip.name}: {status}. Expected {PINNED.bytes} bytes, "
                         f"sha256 {PINNED.sha256}. Download it from {PINNED.url}")
    print(f"  verified ({time.monotonic() - t0:.0f}s)")

    if args.cluster.exists():
        if not args.replace_cluster:
            raise SystemExit(f"!! {args.cluster} exists. --stop it and pass "
                             "--replace-cluster to rebuild it from the snapshot.")
        subprocess.run(stop_cmd(args.pg_bin, args.cluster))
        shutil.rmtree(args.cluster)
    args.cluster.parent.mkdir(parents=True, exist_ok=True)
    _run(initdb_cmd(args.pg_bin, args.cluster))
    _run(start_cmd(args.pg_bin, args.cluster, log, args.port))
    _run(createdb_cmd(args.pg_bin, args.port))

    restore_log = args.cluster.parent / "aact_restore.log"
    dump = _extract_dump(args.zip, args.cluster.parent / "postgres.dmp")
    rc = _restore(restore_cmd(args.pg_bin, dump, args.port, args.jobs), restore_log)
    if not args.keep_dump:
        dump.unlink()
    errors = [l for l in restore_log.read_text(encoding="utf-8", errors="replace").splitlines()
              if "error" in l.lower()]
    print(f"\n  pg_restore exit {rc}; {len(errors)} error lines in {restore_log}")
    for line in errors[:ERROR_LINES_SHOWN]:
        print(f"    {line}")
    bad = _verify_tables(args.port)
    print(f"\n  elapsed {time.monotonic() - t0:.0f}s")
    if bad:
        print(f"!! {bad} required tables absent or empty. The restore is not usable.")
        return 1
    print(f"  ready: {LOCAL_HOST}:{args.port}/{LOCAL_DB}. Server left running.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
