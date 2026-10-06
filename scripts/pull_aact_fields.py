#!/usr/bin/env python3
"""Pull the extra AACT fields into their OWN files. Never writes the label or entity file.

  trial_registration_fields.csv             one row per trial, short columns
  trial_registration_text.csv               one row per trial, free text (--no-text skips)
  trial_registration_fields.provenance.csv  every column -> source and provenance
  trial_registration_fields.manifest.json   what was pulled, when, with which settings

Fails closed (lesson 54): everything is written to `.partial` files and renamed only after
the last chunk passes its checks, so a failed run leaves the previous outputs untouched.
Existing outputs are never overwritten without --overwrite.

Checks per chunk, fatal: exactly one output row per requested id. A one-per-trial table
that has started repeating would otherwise multiply rows silently. Counted, not fatal:
trials whose lead-sponsor row count is not exactly one.

Design and provenance live in services/aact_fields.py and services/aact_provenance.py.
Run from a standalone PowerShell window, not VS Code's terminal (lesson 13).
"""
from __future__ import annotations

import argparse
import csv
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
from trial_pos.services.aact_fields import (  # noqa: E402
    FILE_FIELDS, FILE_TEXT, PARENT_TABLE, chunk_sql, csv_value, output_columns,
    output_name, required_server_columns,
)
from trial_pos.services.population import (  # noqa: E402
    UNKNOWN, is_interventional, normalize_study_type,
)

DEFAULT_HOST = "aact-db.ctti-clinicaltrials.org"
DEFAULT_CHUNK = 2000
DEFAULT_STATEMENT_TIMEOUT_S = 900
DEFAULT_PROGRESS_EVERY = 20
MS_PER_S = 1000
POPULATIONS = ("interventional", "all")

OUT_FIELDS = "trial_registration_fields.csv"
OUT_TEXT = "trial_registration_text.csv"
OUT_PROVENANCE = "trial_registration_fields.provenance.csv"
OUT_MANIFEST = "trial_registration_fields.manifest.json"
PARTIAL_SUFFIX = ".partial"
# Inputs other scripts own. Section 0.3: never let a pull write one of these.
PROTECTED = frozenset({"trial_labels.csv", "trial_entities.csv", "results_raw_studies.csv",
                       "results_raw_outcomes.csv", "trial_design_outcomes.csv"})
LEAD_ROWS_COLUMN = output_name("sponsors", "n_lead_rows")
EXPECTED_LEAD_ROWS = 1
SCHEMA_PROBE_SQL = ("SELECT table_name, column_name FROM information_schema.columns "
                    "WHERE table_schema = %s")


def _rule(title: str) -> str:
    return "\n" + "=" * 78 + f"\n{title}\n" + "=" * 78


def _pct(n, d) -> str:
    return "n/a" if not d else f"{100.0 * n / d:.1f}%"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def check_schema(conn, schema: str) -> None:
    found: dict = {}
    with conn.cursor() as c:
        c.execute(SCHEMA_PROBE_SQL, (schema,))
        for table, column in c.fetchall():
            found.setdefault(table, set()).add(column)
    missing = {t: sorted(cols - found.get(t, set()))
               for t, cols in required_server_columns().items()}
    missing = {t: cols for t, cols in missing.items() if cols}
    print(_rule(f"SCHEMA CHECK (schema '{schema}')"))
    if missing:
        for table, cols in sorted(missing.items()):
            print(f"  !! {table}: absent -> {', '.join(cols)}")
        raise SystemExit("!! schema changed since the probe. Nothing written. Re-run "
                         "audit\\probe_aact_fields.py and update aact_provenance.")
    print(f"  all {sum(len(v) for v in required_server_columns().values())} required "
          f"columns present across {len(required_server_columns())} tables")


def population_ids(conn, schema: str, population: str) -> list:
    """Same rule as pull_aact_results: the population is decided by tested pure code."""
    with conn.cursor() as c:
        c.execute(f"SELECT nct_id, study_type FROM {schema}.{PARENT_TABLE} ORDER BY nct_id")
        rows = c.fetchall()
    tally: Counter = Counter()
    ids = []
    for nct_id, study_type in rows:
        norm = normalize_study_type(study_type)
        tally[norm if norm is not None else UNKNOWN] += 1
        keep = population == "all" or is_interventional(study_type) is True
        if keep and nct_id:
            ids.append(str(nct_id).upper())
    print(_rule("POPULATION"))
    for key, n in tally.most_common():
        print(f"  {str(key):30s} {n:8d} ({_pct(n, len(rows))})")
    print(f"  selected (population={population}): {len(ids)}")
    return ids


def snapshot_proxy(conn, schema: str):
    """AACT carries no download date (empty on the probe); the latest load timestamp on
    studies is the closest record of which snapshot was read."""
    with conn.cursor() as c:
        c.execute(f"SELECT max(updated_at) FROM {schema}.{PARENT_TABLE}")
        return csv_value(c.fetchone()[0])


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--host", default=DEFAULT_HOST)
    ap.add_argument("--db", default="aact")
    ap.add_argument("--schema", default="ctgov")
    ap.add_argument("--user", default=os.environ.get("AACT_USER"))
    ap.add_argument("--password", default=os.environ.get("AACT_PASSWORD"))
    ap.add_argument("--out-dir", type=Path, default=Path("data") / "aact")
    ap.add_argument("--population", choices=POPULATIONS, default="interventional")
    ap.add_argument("--chunk", type=int, default=DEFAULT_CHUNK)
    ap.add_argument("--max-trials", type=int, default=None,
                    help="SMOKE RUN: first N ids in nct_id order (era-correlated)")
    ap.add_argument("--no-text", action="store_true", help="skip the free-text file")
    ap.add_argument("--overwrite", action="store_true")
    ap.add_argument("--statement-timeout-s", type=int, default=DEFAULT_STATEMENT_TIMEOUT_S)
    ap.add_argument("--progress-every", type=int, default=DEFAULT_PROGRESS_EVERY)
    args = ap.parse_args()

    if not args.schema.isidentifier():
        raise SystemExit(f"!! bad schema name {args.schema!r}")
    include_text = not args.no_text
    paths = {name: args.out_dir / name for name in
             (OUT_FIELDS, OUT_TEXT, OUT_PROVENANCE, OUT_MANIFEST)}
    if not include_text:
        del paths[OUT_TEXT]
    for path in paths.values():
        if path.name in PROTECTED:
            raise SystemExit(f"!! {path.name} belongs to another script")
    existing = [p for p in paths.values() if p.exists()]
    if existing and not args.overwrite:
        raise SystemExit("!! outputs exist; pass --overwrite to replace them: "
                         + ", ".join(str(p) for p in existing))

    columns = output_columns(include_text)
    field_cols = [c.name for c in columns if c.file == FILE_FIELDS]
    text_cols = [c.name for c in columns if c.file == FILE_TEXT]
    print(f"host {args.host} | schema {args.schema} | population {args.population} | "
          f"chunk {args.chunk} | max trials {args.max_trials} | text {include_text} | "
          f"overwrite {args.overwrite} | timeout {args.statement_timeout_s}s")
    print(f"columns: fields {len(field_cols)} | text {len(text_cols)} | out {args.out_dir}")
    if not args.user or not args.password:
        print("!! set AACT_USER / AACT_PASSWORD (single quotes in PowerShell)")
        return 2

    import psycopg2
    conn = psycopg2.connect(host=args.host, port=5432, dbname=args.db,
                            user=args.user, password=args.password)
    started = _now()
    t0 = time.monotonic()
    partial = {name: path.with_name(path.name + PARTIAL_SUFFIX)
               for name, path in paths.items()}
    lead_rows: Counter = Counter()
    n_written = 0
    try:
        conn.set_session(readonly=True, autocommit=True)
        with conn.cursor() as c:
            c.execute("SET statement_timeout = %s", (args.statement_timeout_s * MS_PER_S,))
        check_schema(conn, args.schema)
        snapshot = snapshot_proxy(conn, args.schema)
        ids = population_ids(conn, args.schema, args.population)
        if args.max_trials:
            ids = ids[:args.max_trials]
            print(f"  !! SMOKE RUN: first {len(ids)} ids only")
        sql = chunk_sql(args.schema)
        args.out_dir.mkdir(parents=True, exist_ok=True)

        print(_rule("PULL"))
        with open(partial[OUT_FIELDS], "w", newline="", encoding="utf-8") as ff, \
                open(partial.get(OUT_TEXT, os.devnull), "w", newline="",
                     encoding="utf-8") as tf:
            fw, tw = csv.writer(ff), csv.writer(tf)
            fw.writerow(["nct_id"] + field_cols)
            if include_text:
                tw.writerow(["nct_id"] + text_cols)
            n_chunks = (len(ids) + args.chunk - 1) // args.chunk
            for i in range(n_chunks):
                block = ids[i * args.chunk:(i + 1) * args.chunk]
                with conn.cursor() as c:
                    c.execute(sql, {IDS_PARAM: block})
                    names = [d[0] for d in c.description]
                    rows = c.fetchall()
                got = [str(r[0]).upper() for r in rows]
                if len(got) != len(block) or set(got) != set(block):
                    raise SystemExit(
                        f"!! chunk {i}: {len(block)} ids in, {len(rows)} rows out, "
                        f"{len(set(got))} distinct. A one-per-trial join is repeating. "
                        "Nothing written.")
                index = {n: k for k, n in enumerate(names)}
                for r in rows:
                    lead_rows[r[index[LEAD_ROWS_COLUMN]]] += 1
                    fw.writerow([r[0]] + [csv_value(r[index[c]]) for c in field_cols])
                    if include_text:
                        tw.writerow([r[0]] + [csv_value(r[index[c]]) for c in text_cols])
                n_written += len(rows)
                if (i + 1) % args.progress_every == 0 or i + 1 == n_chunks:
                    print(f"  chunk {i + 1}/{n_chunks}  rows {n_written}  "
                          f"{time.monotonic() - t0:.0f}s", flush=True)
    except BaseException:
        left = [str(p) for p in partial.values() if p.exists()]
        print("\n!! pull did not finish. Previous outputs, if any, are untouched."
              + (f" Partial files left: {', '.join(left)}" if left else ""))
        raise
    finally:
        conn.close()

    with open(partial[OUT_PROVENANCE], "w", newline="", encoding="utf-8") as pf:
        pw = csv.writer(pf)
        pw.writerow(["column", "file", "kind", "source_table", "source_columns",
                     "provenance", "note"])
        for c in columns:
            pw.writerow([c.name, c.file, c.kind, c.table, "|".join(c.sources),
                         c.provenance, c.note])
    bad_lead = {str(k): v for k, v in lead_rows.items() if k != EXPECTED_LEAD_ROWS}
    manifest = {
        "script": "pull_aact_fields.py", "started_utc": started, "finished_utc": _now(),
        "host": args.host, "db": args.db, "schema": args.schema,
        "aact_snapshot_proxy_max_updated_at": snapshot,
        "population": args.population, "max_trials": args.max_trials,
        "chunk": args.chunk, "include_text": include_text, "rows": n_written,
        "field_columns": field_cols, "text_columns": text_cols,
        "lead_sponsor_rows_not_one": bad_lead,
    }
    with open(partial[OUT_MANIFEST], "w", encoding="utf-8") as mf:
        json.dump(manifest, mf, indent=2)
    for name, path in paths.items():
        os.replace(partial[name], path)

    print(_rule("DONE"))
    print(f"  rows written         : {n_written}")
    print(f"  lead-sponsor rows != {EXPECTED_LEAD_ROWS}: "
          f"{sum(bad_lead.values())}  {bad_lead or ''}")
    print(f"  snapshot proxy       : {snapshot}")
    for path in paths.values():
        print(f"  wrote {path}")
    print(f"  elapsed              : {time.monotonic() - t0:.0f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())