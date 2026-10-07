#!/usr/bin/env python3
"""What is in the local restore, and what the pulls leave behind. READ-ONLY. SCRATCH.

The input to designing a pull that takes everything worth taking. Read-only transaction.

  1. server: version, the database's collation (rev 10 §12.24 says C), and the snapshot
     proxy max(studies.updated_at)
  2. every base table: exact rows and distinct nct_id, and whether any pull reads it
     (aact_snapshot.required_tables, derived from the pull declarations)
  3. every column, classed as
       declared  named in aact_provenance.TABLES or an aggregate source's required columns
       key       id / nct_id
       direct    in a table a pull reads with hand-written SQL, so column use is undeclared
       unread    nothing in the project reads it
  4. columns whose names look like identifiers (IDENTIFIER_NAME_PATTERNS), in any table
  5. id_information: id_source x id_type vocabulary over all trials and over drug trials,
     sample values per pair, and rows whose value or description matches IND_PATTERN
     (rev 7 says AACT has no IND field; a secondary id may still carry one)
  6. views, listed only

--csv writes the column inventory (table, column, type, class, table rows) for the next
step. Nothing else is written.

Usage (PowerShell), standalone window, server started:
  python audit\\probe_restore_inventory.py --csv data\\aact\\restore_inventory.csv
"""
from __future__ import annotations

import argparse
import csv
import re
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from trial_pos.services.aact_aggregates import AGGREGATE_SOURCES  # noqa: E402
from trial_pos.services.aact_provenance import KEY_COLUMNS, TABLES  # noqa: E402
from trial_pos.services.aact_snapshot import (  # noqa: E402
    DIRECT_TABLES, LOCAL_DB, LOCAL_HOST, LOCAL_PASSWORD, LOCAL_PORT, LOCAL_USER,
    required_tables,
)
from trial_pos.services.population import DRUG_INTERVENTION_TYPES  # noqa: E402

DEFAULT_SCHEMA = "ctgov"
DEFAULT_VOCAB_MAX = 40
DEFAULT_SAMPLES = 5
IDENTIFIER_NAME_PATTERNS = ("mesh", "unii", "rxnorm", "cui", "appl", "nda", "code",
                            "identifier", "id_")
IND_PATTERN = r"\mIND\M"          # PostgreSQL word boundaries, case-sensitive on purpose
_C = 'COLLATE "C"'

CLASS_DECLARED = "declared"
CLASS_KEY = "key"
CLASS_DIRECT = "direct"
CLASS_UNREAD = "unread"
COLUMN_CLASSES = (CLASS_DECLARED, CLASS_KEY, CLASS_DIRECT, CLASS_UNREAD)


def _rule(title: str) -> str:
    return "\n" + "=" * 78 + f"\n{title}\n" + "=" * 78


def declared_columns() -> dict:
    """{table: set of columns the project declares it reads}."""
    out = defaultdict(set)
    for spec in TABLES:
        out[spec.table] |= set(spec.columns)
    for source in AGGREGATE_SOURCES:
        out[source.table] |= set(source.required_columns)
    return out


def column_class(table: str, column: str, declared: dict) -> str:
    """Whether a pull reads the TABLE is printed separately in section 2."""
    if column in KEY_COLUMNS:
        return CLASS_KEY
    if column in declared.get(table, ()):
        return CLASS_DECLARED
    if table in DIRECT_TABLES:
        return CLASS_DIRECT
    return CLASS_UNREAD


def identifier_shaped(column: str) -> bool:
    low = column.lower()
    return any(p in low for p in IDENTIFIER_NAME_PATTERNS)


def write_inventory(path: Path, inventory: list) -> None:
    """Written as soon as the column inventory exists, so a later section failing
    cannot lose it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as h:
        w = csv.writer(h)
        w.writerow(("table", "column", "data_type", "class", "table_rows"))
        w.writerows(inventory)
    print(f"\n  wrote {path} ({len(inventory)} columns)")


def connect(args):
    try:
        import psycopg2
    except ImportError:
        raise SystemExit("!! psycopg2 is not installed: pip install -e .[pull]")
    conn = psycopg2.connect(host=args.host, port=args.port, dbname=args.db, user=args.user,
                            password=args.password)
    conn.set_session(readonly=True, autocommit=False)
    return conn


def q(conn, sql: str, params=None) -> list:
    with conn.cursor() as c:
        c.execute(sql, params)
        return c.fetchall()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--host", default=LOCAL_HOST)
    ap.add_argument("--port", type=int, default=LOCAL_PORT)
    ap.add_argument("--db", default=LOCAL_DB)
    ap.add_argument("--user", default=LOCAL_USER)
    ap.add_argument("--password", default=LOCAL_PASSWORD)
    ap.add_argument("--schema", default=DEFAULT_SCHEMA)
    ap.add_argument("--no-distinct", action="store_true",
                    help="skip count(DISTINCT nct_id), the slow part on the largest tables")
    ap.add_argument("--vocab-max", type=int, default=DEFAULT_VOCAB_MAX)
    ap.add_argument("--samples", type=int, default=DEFAULT_SAMPLES)
    ap.add_argument("--csv", type=Path, default=None)
    ap.add_argument("--ids-only", action="store_true",
                    help="run sections 1 and 5 only (no table counts, no column inventory)")
    args = ap.parse_args()
    s = args.schema
    if not s.isidentifier():
        raise SystemExit(f"!! bad schema name {s!r}")
    if args.csv is not None and args.csv.exists():
        raise SystemExit(f"!! {args.csv} exists; choose a new path")
    print(f"host {args.host}:{args.port} db {args.db} schema {s} | distinct "
          f"{not args.no_distinct} | id patterns {IDENTIFIER_NAME_PATTERNS} | IND pattern "
          f"{IND_PATTERN!r} | drug types {sorted(DRUG_INTERVENTION_TYPES)}")
    declared = declared_columns()
    read_tables = set(required_tables())
    conn = connect(args)
    try:
        print(_rule("1. SERVER AND SNAPSHOT"))
        print(f"  server_version {q(conn, 'SHOW server_version')[0][0]}")
        coll = q(conn, "SELECT datcollate, datctype FROM pg_database "
                       "WHERE datname = current_database()")[0]
        print(f"  datcollate {coll[0]}  datctype {coll[1]}")
        tables = q(conn, "SELECT table_name, table_type FROM information_schema.tables "
                         "WHERE table_schema = %s ORDER BY table_name", (s,))
        cols = q(conn, "SELECT table_name, column_name, data_type FROM "
                       "information_schema.columns WHERE table_schema = %s "
                       "ORDER BY table_name, ordinal_position", (s,))
        by_table = defaultdict(list)
        for t, c, dt in cols:
            by_table[t].append((c, dt))
        if "updated_at" in {c for c, _ in by_table.get("studies", ())}:
            print(f"  max(studies.updated_at) "
                  f"{q(conn, f'SELECT max(updated_at) FROM {s}.studies')[0][0]}")

        base = [t for t, kind in tables if kind == "BASE TABLE"]
        views = [t for t, kind in tables if kind != "BASE TABLE"]
        if args.ids_only:
            base_for_counts = []
        else:
            base_for_counts = base
        print(_rule(f"2. TABLES ({len(base)} base tables)"))
        rows = {}
        print(f"  {'table':40s} {'rows':>12s} {'trials':>9s} {'read by a pull':>15s}")
        for t in base_for_counts:
            has_nct = "nct_id" in {c for c, _ in by_table[t]}
            distinct = has_nct and not args.no_distinct
            sql = (f"SELECT count(*), {'count(DISTINCT nct_id)' if distinct else 'NULL'} "
                   f"FROM {s}.{t}")
            n, d = q(conn, sql)[0]
            rows[t] = n
            print(f"  {t:40s} {n:12d} {('' if d is None else str(d)):>9s} "
                  f"{'yes' if t in read_tables else '-':>15s}", flush=True)
        absent = sorted(read_tables - set(base))
        print(f"\n  tables a pull reads that the restore lacks: {absent or 'none'}")

        print(_rule("3. COLUMNS"))
        inventory = []
        for t in base_for_counts:
            classes = {c: column_class(t, c, declared) for c, _ in by_table[t]}
            tally = {k: sum(1 for v in classes.values() if v == k) for k in COLUMN_CLASSES}
            print(f"\n  {t}  ({rows[t]} rows; " +
                  ", ".join(f"{k} {tally[k]}" for k in COLUMN_CLASSES) + ")")
            for c, dt in by_table[t]:
                print(f"    {c:44s} {dt:28s} {classes[c]}")
                inventory.append((t, c, dt, classes[c], rows[t]))
        if args.csv is not None:
            write_inventory(args.csv, inventory)
        stale = sorted((t, c) for t, cs in declared.items() for c in cs
                       if t in by_table and c not in {x for x, _ in by_table[t]})
        print(f"\n  declared columns the restore lacks: {stale or 'none'}")

        print(_rule("4. IDENTIFIER-SHAPED COLUMNS"))
        for t, c, dt, klass, n in inventory:
            if identifier_shaped(c):
                print(f"  {t:36s} {c:36s} {dt:20s} {klass}")

        print(_rule("5. id_information"))
        id_cols = {c for c, _ in by_table.get("id_information", ())}
        if not {"id_source", "id_type", "id_value"} <= id_cols:
            print(f"  !! id_information lacks the expected columns; has {sorted(id_cols)}")
        else:
            drug_cte = (f"WITH drug AS (SELECT DISTINCT nct_id FROM {s}.interventions "
                        f"WHERE lower(intervention_type) = ANY(%s)) ")
            types = sorted(DRUG_INTERVENTION_TYPES)
            vocab = q(conn, drug_cte +
                      f"SELECT i.id_source, i.id_type, count(*), count(DISTINCT i.nct_id), "
                      f"count(DISTINCT i.nct_id) FILTER (WHERE d.nct_id IS NOT NULL) "
                      f"FROM {s}.id_information i LEFT JOIN drug d ON d.nct_id = i.nct_id "
                      f"GROUP BY i.id_source, i.id_type "
                      f"ORDER BY 3 DESC, i.id_source {_C}, i.id_type {_C}", (types,))
            print(f"  {'id_source':22s} {'id_type':28s} {'rows':>9s} {'trials':>8s} "
                  f"{'drug trials':>12s}")
            for src, typ, n, t_all, t_drug in vocab[:args.vocab_max]:
                print(f"  {str(src):22s} {str(typ):28s} {n:9d} {t_all:8d} {t_drug:12d}")
            if len(vocab) > args.vocab_max:
                print(f"  ... {len(vocab) - args.vocab_max} more pairs")
            print(f"\n  sample values per pair (first {args.samples} in byte order):")
            for src, typ, *_ in vocab[:args.vocab_max]:
                vals = q(conn, f"SELECT DISTINCT id_value {_C} AS v FROM {s}.id_information "
                               f"WHERE id_source IS NOT DISTINCT FROM %s AND id_type IS NOT "
                               f"DISTINCT FROM %s ORDER BY v LIMIT %s",
                         (src, typ, args.samples))
                print(f"    {str(src)[:20]:20s} {str(typ)[:26]:26s} {[v[0] for v in vals]}")
            desc = "id_type_description" in id_cols
            where = "id_value ~ %s" + (" OR id_type_description ~ %s" if desc else "")
            params = (IND_PATTERN, IND_PATTERN) if desc else (IND_PATTERN,)
            ind = q(conn, f"SELECT id_source, id_type, count(*), count(DISTINCT nct_id) FROM "
                          f"{s}.id_information WHERE {where} GROUP BY id_source, id_type "
                          f"ORDER BY 3 DESC, id_source {_C}, id_type {_C}", params)
            print(f"\n  rows matching {IND_PATTERN!r} in id_value"
                  f"{' or id_type_description' if desc else ''}:")
            for src, typ, n, t_all in ind:
                print(f"    {str(src):22s} {str(typ):28s} {n:9d} rows {t_all:8d} trials")
            if desc:
                ex = q(conn, f"SELECT id_value, id_type_description FROM {s}.id_information "
                             f"WHERE {where} ORDER BY id_value {_C}, id_type_description {_C} "
                             f"LIMIT %s", params + (args.samples,))
                print(f"    samples: {ex}")

        print(_rule("6. VIEWS (not counted)"))
        print(f"  {views or 'none'}")
    finally:
        conn.rollback()
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())