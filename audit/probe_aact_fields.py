#!/usr/bin/env python3
"""Cardinality and column probe for the extra AACT fields. READ-ONLY, writes nothing.

SCRATCH. Run before the pull is designed, because the pull's shape depends on it.

For every table in `aact_provenance.TABLES`:
  1. which columns exist, against the registry in both directions: classified but absent
     on the server, and present on the server but unclassified
  2. rows, distinct trials, and rows-per-trial (max, percentiles, trials with >1 row),
     judged against the DECLARED cardinality -- a one-per-trial table that repeats is a
     join that would silently multiply rows
  3. exact duplicate rows, which say whether a repeat is a real second child or a copy
  4. orphan trials: ids absent from `studies`
  5. per column: provenance, non-null count, distinct count, and the value vocabulary
     when it is small, which is what the tri-state mapping at pull time needs
"""
from __future__ import annotations

import argparse
import os
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from trial_pos.services.aact_provenance import (  # noqa: E402
    CARDINALITY_ONE, KEY_COLUMNS, TABLES, VERDICT_CONTRADICTED, cardinality_verdict,
    missing_columns, unclassified_columns,
)

DEFAULT_HOST = "aact-db.ctti-clinicaltrials.org"
DEFAULT_PORT = 5432
DEFAULT_DB = "aact"
DEFAULT_SCHEMA = "ctgov"
DEFAULT_STATEMENT_TIMEOUT_S = 900
DEFAULT_VOCAB_MAX = 25
PERCENTILES = (0.5, 0.9, 0.99)
PARENT_TABLE = "studies"
UNCLASSIFIED = "UNCLASSIFIED"
MS_PER_S = 1000

_IDENT = re.compile(r"^[a-z_][a-z0-9_]*$")


def ident(name: str) -> str:
    """Identifiers are interpolated, so each one is validated first. Raises on anything
    that is not a plain lower-case Postgres name."""
    if not _IDENT.match(name or ""):
        raise SystemExit(f"!! refusing to interpolate identifier {name!r}")
    return name


def _rule(title: str) -> str:
    return "\n" + "=" * 78 + f"\n{title}\n" + "=" * 78


def _pct(n, d) -> str:
    return "n/a" if not d else f"{100.0 * n / d:.1f}%"


# ---- SQL, one builder per question ---------------------------------------
def sql_columns() -> str:
    return ("SELECT column_name, data_type FROM information_schema.columns "
            "WHERE table_schema = %s AND table_name = %s ORDER BY ordinal_position")


def sql_counts(schema: str, table: str) -> str:
    return f"SELECT count(*), count(DISTINCT nct_id) FROM {schema}.{table}"


def sql_per_trial(schema: str, table: str) -> str:
    levels = ", ".join(str(p) for p in PERCENTILES)
    return (f"SELECT max(k), percentile_disc(ARRAY[{levels}]::float8[]) "
            f"WITHIN GROUP (ORDER BY k), "
            f"count(*) FILTER (WHERE k > 1) "
            f"FROM (SELECT count(*) AS k FROM {schema}.{table} GROUP BY nct_id) g")


def sql_orphans(schema: str, table: str) -> str:
    return (f"SELECT count(*) FROM (SELECT DISTINCT nct_id FROM {schema}.{table}) x "
            f"WHERE NOT EXISTS (SELECT 1 FROM {schema}.{PARENT_TABLE} s "
            f"WHERE s.nct_id = x.nct_id)")


def sql_exact_duplicates(schema: str, table: str, columns: list) -> str:
    row = ", ".join(["nct_id"] + columns)
    return f"SELECT count(*) - count(DISTINCT ({row})) FROM {schema}.{table}"


def sql_column_stats(schema: str, table: str, columns: list) -> str:
    parts = []
    for c in columns:
        parts.append(f"count({c})")
        parts.append(f"count(DISTINCT {c})")
    return f"SELECT {', '.join(parts)} FROM {schema}.{table}"


def sql_vocab(schema: str, table: str, column: str) -> str:
    return (f"SELECT {column}, count(*) FROM {schema}.{table} "
            f"GROUP BY {column} ORDER BY count(*) DESC, {column}")


def _one(conn, sql: str, params=None):
    with conn.cursor() as c:
        c.execute(sql, params)
        return c.fetchone()


def _all(conn, sql: str, params=None):
    with conn.cursor() as c:
        c.execute(sql, params)
        return c.fetchall()


# ---- one table -----------------------------------------------------------
def probe_table(conn, schema: str, spec, vocab_max: int, skip_distinct: bool) -> dict:
    table = ident(spec.table)
    started = time.monotonic()
    print(_rule(f"{table}  (declared {spec.cardinality})"))
    found = {name: dtype for name, dtype in _all(conn, sql_columns(), (schema, table))}
    out = {"table": table, "present": bool(found), "missing": [], "unclassified": [],
           "verdict": None}
    if not found:
        print("  !! table not visible on the server")
        return out
    for name in found:
        ident(name)
    out["missing"] = missing_columns(table, found)
    out["unclassified"] = unclassified_columns(table, found)
    print(f"  columns on server : {len(found)}  "
          f"(classified absent: {len(out['missing'])}, "
          f"present unclassified: {len(out['unclassified'])})")
    if out["missing"]:
        print(f"  classified but ABSENT : {', '.join(out['missing'])}")
    if "nct_id" not in found:
        print("  !! no nct_id column; cardinality cannot be measured")
        return out

    n_rows, n_trials = _one(conn, sql_counts(schema, table))
    out["verdict"] = cardinality_verdict(spec.cardinality, n_rows, n_trials)
    print(f"\n  rows {n_rows}  distinct trials {n_trials}  "
          f"rows/trial {n_rows / n_trials:.3f}" if n_trials else f"\n  rows {n_rows}")
    if n_trials and table != PARENT_TABLE:
        k_max, k_pcts, n_multi = _one(conn, sql_per_trial(schema, table))
        pcts = "  ".join(f"p{int(p * 100)}={v}" for p, v in zip(PERCENTILES, k_pcts))
        print(f"  rows per trial    : max {k_max}  {pcts}")
        print(f"  trials with >1 row: {n_multi} ({_pct(n_multi, n_trials)})")
        orphans = _one(conn, sql_orphans(schema, table))[0]
        print(f"  orphan trials     : {orphans} (absent from {PARENT_TABLE})")
    flag = "  <-- CONTRADICTS the declaration" if out["verdict"] == VERDICT_CONTRADICTED \
        else ""
    print(f"  cardinality       : {out['verdict']}{flag}")

    data_cols = [c for c in found if c not in KEY_COLUMNS]
    if data_cols and table != PARENT_TABLE:
        dups = _one(conn, sql_exact_duplicates(schema, table, data_cols))[0]
        print(f"  exact duplicate rows over nct_id + every data column: {dups}")

    if skip_distinct or not data_cols:
        print(f"\n  ({time.monotonic() - started:.0f}s)")
        return out
    stats = _one(conn, sql_column_stats(schema, table, data_cols))
    print(f"\n  {'column':38s} {'provenance':24s} {'type':12s} {'non-null':>10s} "
          f"{'distinct':>9s}")
    small = []
    for i, column in enumerate(data_cols):
        non_null, distinct = stats[2 * i], stats[2 * i + 1]
        provenance = spec.columns.get(column, UNCLASSIFIED)
        print(f"  {column:38s} {provenance:24s} {found[column][:12]:12s} "
              f"{non_null:10d} {distinct:9d}")
        if distinct <= vocab_max:
            small.append(column)
    for column in small:
        print(f"\n  vocabulary of {column}:")
        for value, count in _all(conn, sql_vocab(schema, table, column)):
            shown = "(null)" if value is None else repr(value)
            print(f"    {shown:50s} {count:10d}")
    for column, note in spec.notes.items():
        if column in found:
            print(f"  note {column}: {note}")
    print(f"\n  ({time.monotonic() - started:.0f}s)")
    return out


def summary(results: list) -> int:
    print(_rule("SUMMARY"))
    absent = [r["table"] for r in results if not r["present"]]
    contradicted = [r["table"] for r in results if r["verdict"] == VERDICT_CONTRADICTED]
    print(f"  tables not visible            : {', '.join(absent) or 'none'}")
    print(f"  declared one-per-trial, isn't : {', '.join(contradicted) or 'none'}")
    for r in results:
        if r["missing"]:
            print(f"  {r['table']}: classified but absent -> {', '.join(r['missing'])}")
    for r in results:
        if r["unclassified"]:
            print(f"  {r['table']}: unclassified -> {', '.join(r['unclassified'])}")
    print("\n  A contradicted table must be aggregated, not joined. Unclassified columns")
    print("  get a provenance in aact_provenance.TABLES before the pull may take them.")
    return 1 if (absent or contradicted) else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--host", default=DEFAULT_HOST)
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--db", default=DEFAULT_DB)
    ap.add_argument("--schema", default=DEFAULT_SCHEMA)
    ap.add_argument("--user", default=os.environ.get("AACT_USER"))
    ap.add_argument("--password", default=os.environ.get("AACT_PASSWORD"))
    ap.add_argument("--tables", nargs="*", default=None,
                    help="probe only these registry tables. Default: all")
    ap.add_argument("--statement-timeout-s", type=int, default=DEFAULT_STATEMENT_TIMEOUT_S)
    ap.add_argument("--vocab-max", type=int, default=DEFAULT_VOCAB_MAX,
                    help="print the value vocabulary of columns with at most this many "
                         "distinct values")
    ap.add_argument("--skip-distinct", action="store_true",
                    help="skip per-column counts and vocabularies (much faster)")
    args = ap.parse_args()
    schema = ident(args.schema)
    specs = [s for s in TABLES if args.tables is None or s.table in args.tables]
    unknown = set(args.tables or ()) - {s.table for s in TABLES}
    if unknown:
        raise SystemExit(f"!! not in the registry: {sorted(unknown)}")
    print(f"host {args.host} | db {args.db} | schema {schema} | tables {len(specs)} | "
          f"statement timeout {args.statement_timeout_s}s | vocab max {args.vocab_max} | "
          f"skip distinct {args.skip_distinct}")
    if not args.user or not args.password:
        print("!! set AACT_USER / AACT_PASSWORD (single quotes in PowerShell)")
        return 2

    import psycopg2
    conn = psycopg2.connect(host=args.host, port=args.port, dbname=args.db,
                            user=args.user, password=args.password)
    try:
        conn.set_session(readonly=True, autocommit=True)
        with conn.cursor() as c:
            c.execute("SET statement_timeout = %s", (args.statement_timeout_s * MS_PER_S,))
        results = [probe_table(conn, schema, spec, args.vocab_max, args.skip_distinct)
                   for spec in specs]
    finally:
        conn.close()
    return summary(results)


if __name__ == "__main__":
    sys.exit(main())