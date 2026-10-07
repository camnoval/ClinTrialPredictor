#!/usr/bin/env python3
"""Pull the registration-side AACT tables ROW BY ROW, keyed on each row's own id.

Writes, into --out-dir, only files named rows__* (never another pull's file):
  rows__<table>.csv       one per table in services/aact_rows.py, every exported column
  rows.provenance.csv     table, column, provenance
  rows.manifest.json      population, snapshot proxy, rows and sha256 per file, the checks

--probe-only first: rows and trials per table in the population, and foreign-key orphans
counted on the server. Writes nothing.

Fails closed (lesson 54): every file goes to `.partial` and is renamed only after the last
chunk passes. A chunk with a row outside its id block, a repeated id, rows out of order, or
a child whose parent is not in the same trial stops the run, removes the partial files and
writes nothing. The schema check refuses a column the registry has not classified.

Usage (PowerShell), standalone window, local server started:
  python scripts\\pull_aact_rows.py --probe-only
  python scripts\\pull_aact_rows.py
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from trial_pos.services.aact_aggregates import IDS_PARAM  # noqa: E402
from trial_pos.services.aact_fields import csv_value  # noqa: E402
from trial_pos.services.aact_provenance import unclassified_columns  # noqa: E402
from trial_pos.services.aact_rows import (  # noqa: E402
    FOREIGN_KEYS, MANIFEST_FILE, PROVENANCE_FILE, TRIAL_TABLES, UNCHECKED_REFERENCES,
    VOCAB_TABLES, ChunkError, all_tables, check_chunk, chunk_sql, columns, count_sql,
    file_name, foreign_key_violations, keys_by_parent, orphan_sql, provenance,
    required_server_columns, vocab_sql,
)
from trial_pos.services.aact_snapshot import (  # noqa: E402
    LOCAL_DB, LOCAL_HOST, LOCAL_PASSWORD, LOCAL_PORT, LOCAL_USER,
)
from trial_pos.services.population import (  # noqa: E402
    UNKNOWN, is_interventional, normalize_study_type,
)

DEFAULT_SCHEMA = "ctgov"
DEFAULT_OUT_DIR = Path("data") / "aact"
DEFAULT_CHUNK = 2000
DEFAULT_STATEMENT_TIMEOUT_S = 900
DEFAULT_PROGRESS_EVERY = 20
MS_PER_S = 1000
HASH_CHUNK = 1 << 20
POPULATIONS = ("interventional", "all")
PARTIAL_SUFFIX = ".partial"
PARENT_TABLE = "studies"


def _rule(title: str) -> str:
    return "\n" + "=" * 78 + f"\n{title}\n" + "=" * 78


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(HASH_CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


def check_schema(conn, schema: str) -> None:
    found: dict = {}
    with conn.cursor() as c:
        c.execute("SELECT table_name, column_name FROM information_schema.columns "
                  "WHERE table_schema = %s ORDER BY table_name, column_name", (schema,))
        for table, column in c.fetchall():
            found.setdefault(table, set()).add(column)
    print(_rule(f"SCHEMA CHECK (schema '{schema}')"))
    problems = []
    for table, need in sorted(required_server_columns().items()):
        absent = sorted(need - found.get(table, set()))
        if absent:
            problems.append(f"{table}: absent {absent}")
        extra = (unclassified_columns(table, found.get(table, set()))
                 if table in TRIAL_TABLES
                 else sorted(found.get(table, set()) - set(VOCAB_TABLES[table])))
        if extra:
            problems.append(f"{table}: on the server but not classified {extra}")
    for p in problems:
        print(f"  !! {p}")
    if problems:
        raise SystemExit("!! schema differs from the registry. Nothing written. Classify "
                         "the columns in aact_provenance (or VOCAB_TABLES) first.")
    print(f"  {len(required_server_columns())} tables, every server column classified")


def population_ids(conn, schema: str, population: str) -> list:
    """Same rule as the other pulls: the population is decided by tested pure code."""
    with conn.cursor() as c:
        c.execute(f"SELECT nct_id, study_type FROM {schema}.{PARENT_TABLE} ORDER BY nct_id")
        rows = c.fetchall()
    tally: Counter = Counter()
    ids = []
    for nct_id, study_type in rows:
        norm = normalize_study_type(study_type)
        tally[norm if norm is not None else UNKNOWN] += 1
        if (population == "all" or is_interventional(study_type) is True) and nct_id:
            ids.append(str(nct_id))
    print(_rule("POPULATION"))
    for key, n in sorted(tally.items(), key=lambda kv: (-kv[1], str(kv[0]))):
        print(f"  {str(key):30s} {n:8d}")
    print(f"  selected (population={population}): {len(ids)}")
    # Byte order whatever the server's collation, so chunks and files are ordered alike.
    return sorted(ids, key=lambda v: v.encode("utf-8"))


def snapshot_proxy(conn, schema: str) -> str:
    with conn.cursor() as c:
        c.execute(f"SELECT max(updated_at) FROM {schema}.{PARENT_TABLE}")
        return csv_value(c.fetchone()[0])


def probe(conn, schema: str, ids: list) -> None:
    print(_rule("PROBE: rows and trials per table in the population"))
    for table in TRIAL_TABLES:
        with conn.cursor() as c:
            c.execute(count_sql(schema, table), {IDS_PARAM: ids})
            n, t = c.fetchone()
        print(f"  {table:32s} rows {n:10d}  trials {t:8d}  -> {file_name(table)}", flush=True)
    for table in VOCAB_TABLES:
        with conn.cursor() as c:
            c.execute(f"SELECT count(*) FROM {schema}.{table}")
            print(f"  {table:32s} rows {c.fetchone()[0]:10d}  (vocabulary, no trial key)")
    print(_rule("PROBE: child rows whose parent is not a row of the same trial"))
    for fk in FOREIGN_KEYS:
        with conn.cursor() as c:
            c.execute(orphan_sql(schema, fk), {IDS_PARAM: ids})
            print(f"  {fk.child}.{fk.column} -> {fk.parent}: {c.fetchone()[0]}")
    for child, column, parent in UNCHECKED_REFERENCES:
        print(f"  {child}.{column} -> {parent}: NOT CHECKED (parent not exported)")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--host", default=LOCAL_HOST)
    ap.add_argument("--port", type=int, default=LOCAL_PORT)
    ap.add_argument("--db", default=LOCAL_DB)
    ap.add_argument("--user", default=LOCAL_USER)
    ap.add_argument("--password", default=LOCAL_PASSWORD)
    ap.add_argument("--schema", default=DEFAULT_SCHEMA)
    ap.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    ap.add_argument("--population", choices=POPULATIONS, default="interventional")
    ap.add_argument("--chunk", type=int, default=DEFAULT_CHUNK)
    ap.add_argument("--max-trials", type=int, default=None, help="smoke run only")
    ap.add_argument("--statement-timeout-s", type=int, default=DEFAULT_STATEMENT_TIMEOUT_S)
    ap.add_argument("--progress-every", type=int, default=DEFAULT_PROGRESS_EVERY)
    ap.add_argument("--probe-only", action="store_true")
    ap.add_argument("--overwrite", action="store_true")
    args = ap.parse_args()
    if not args.schema.isidentifier():
        raise SystemExit(f"!! bad schema name {args.schema!r}")
    if args.chunk < 1:
        raise SystemExit("!! --chunk must be >= 1")

    names = {t: file_name(t) for t in all_tables()}
    names[PROVENANCE_FILE] = PROVENANCE_FILE
    names[MANIFEST_FILE] = MANIFEST_FILE
    paths = {k: args.out_dir / v for k, v in names.items()}
    partial = {k: p.with_name(p.name + PARTIAL_SUFFIX) for k, p in paths.items()}
    if not args.probe_only:
        existing = [str(p) for p in paths.values() if p.exists()]
        if existing and not args.overwrite:
            raise SystemExit("!! outputs exist; pass --overwrite: " + ", ".join(existing))

    print(f"host {args.host}:{args.port} db {args.db} schema {args.schema} | population "
          f"{args.population} | chunk {args.chunk} | max_trials {args.max_trials} | "
          f"timeout {args.statement_timeout_s}s | out {args.out_dir} | probe_only "
          f"{args.probe_only}")
    try:
        import psycopg2
    except ImportError:
        raise SystemExit("!! psycopg2 is not installed: pip install -e .[pull]")
    conn = psycopg2.connect(host=args.host, port=args.port, dbname=args.db, user=args.user,
                            password=args.password)
    conn.set_session(readonly=True, autocommit=False)
    started = _now()
    t0 = time.monotonic()
    rows_written = Counter()
    try:
        with conn.cursor() as c:
            c.execute("SET statement_timeout = %s", (args.statement_timeout_s * MS_PER_S,))
        check_schema(conn, args.schema)
        snapshot = snapshot_proxy(conn, args.schema)
        print(f"  snapshot proxy max(studies.updated_at): {snapshot}")
        ids = population_ids(conn, args.schema, args.population)
        if args.max_trials:
            ids = ids[:args.max_trials]
            print(f"  !! SMOKE RUN: first {len(ids)} ids only")
        if args.probe_only:
            probe(conn, args.schema, ids)
            print("\n  --probe-only: nothing written.")
            return 0

        args.out_dir.mkdir(parents=True, exist_ok=True)
        fks = keys_by_parent()
        handles = {t: open(partial[t], "w", newline="", encoding="utf-8")
                   for t in all_tables()}
        try:
            writers = {t: csv.writer(h) for t, h in handles.items()}
            for t, w in writers.items():
                w.writerow(columns(t))
            print(_rule("PULL"))
            n_chunks = (len(ids) + args.chunk - 1) // args.chunk
            for i in range(n_chunks):
                block = ids[i * args.chunk:(i + 1) * args.chunk]
                ids_by_table: dict = {}
                for table in TRIAL_TABLES:
                    with conn.cursor() as c:
                        c.execute(chunk_sql(args.schema, table), {IDS_PARAM: block})
                        rows = c.fetchall()
                    try:
                        ids_by_table[table] = check_chunk(table, rows, block)
                    except ChunkError as exc:
                        raise SystemExit(f"!! chunk {i}: {exc}. Nothing written.")
                    for fk in fks.get(table, ()):
                        bad = foreign_key_violations(fk, rows, ids_by_table[fk.parent])
                        if bad:
                            raise SystemExit(
                                f"!! chunk {i}: {len(bad)} {fk.child}.{fk.column} rows "
                                f"point outside their trial's {fk.parent}, e.g. "
                                f"{bad[:3]}. Nothing written.")
                    w = writers[table]
                    for r in rows:
                        w.writerow([csv_value(v) for v in r])
                    rows_written[table] += len(rows)
                if (i + 1) % args.progress_every == 0 or i + 1 == n_chunks:
                    print(f"  chunk {i + 1}/{n_chunks}  rows {sum(rows_written.values())}  "
                          f"{time.monotonic() - t0:.0f}s", flush=True)
            for table in VOCAB_TABLES:
                with conn.cursor() as c:
                    c.execute(vocab_sql(args.schema, table))
                    rows = c.fetchall()
                for r in rows:
                    writers[table].writerow([csv_value(v) for v in r])
                rows_written[table] += len(rows)
        finally:
            for h in handles.values():
                h.close()
    except BaseException:
        for p in partial.values():
            if p.exists():
                p.unlink()
        print("\n!! pull did not finish; partial files removed, previous outputs untouched.")
        raise
    finally:
        conn.rollback()
        conn.close()

    with open(partial[PROVENANCE_FILE], "w", newline="", encoding="utf-8") as pf:
        pw = csv.writer(pf)
        pw.writerow(["table", "column", "provenance"])
        for t in all_tables():
            for col in columns(t):
                pw.writerow([t, col, provenance(t, col)])
    files = {t: {"file": names[t], "rows": rows_written[t], "columns": list(columns(t)),
                 "bytes": partial[t].stat().st_size, "sha256": sha256_of(partial[t])}
             for t in all_tables()}
    manifest = {
        "script": "pull_aact_rows.py", "started_utc": started, "finished_utc": _now(),
        "host": args.host, "db": args.db, "schema": args.schema,
        "aact_snapshot_proxy_max_updated_at": snapshot, "population": args.population,
        "trials_requested": len(ids), "max_trials": args.max_trials, "chunk": args.chunk,
        "foreign_keys_checked": [list(fk) for fk in FOREIGN_KEYS],
        "references_not_checked": [list(r) for r in UNCHECKED_REFERENCES],
        "tables": files,
    }
    with open(partial[MANIFEST_FILE], "w", encoding="utf-8") as mf:
        json.dump(manifest, mf, indent=2)
    for key, path in paths.items():
        os.replace(partial[key], path)

    print(_rule("DONE"))
    print(f"  {'table':32s} {'rows':>10s} {'bytes':>14s}  sha256")
    for t, meta in files.items():
        print(f"  {t:32s} {meta['rows']:10d} {meta['bytes']:14d}  {meta['sha256']}")
    print(f"  snapshot proxy {snapshot} | trials {len(ids)} | "
          f"elapsed {time.monotonic() - t0:.0f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
