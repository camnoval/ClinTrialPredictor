#!/usr/bin/env python3
"""Pull REGISTERED primary outcome text from AACT `design_outcomes`. Read-only, audit-first.

WHY A SEPARATE PULL AND NOT A RE-PULL
=====================================
Nothing about the endpoint-met label changes, so there is no reason to regenerate 460,569
label rows and re-verify every number in the handoff against a new snapshot. This writes
its own file, joined back on a NATURAL key, and leaves `trial_labels.csv` and
`trial_entities.csv` untouched.

WHY design_outcomes AND NOT outcomes
====================================
`outcomes.title` is the primary outcome text as POSTED WITH RESULTS. It exists only for the
trials that posted at all -- 12.2% of phase 1 drug trials -- and it is edited at posting
time. `design_outcomes.measure` is what the sponsor REGISTERED, exists for essentially
every trial, and is the same field the CT.gov v2 API serves as
`primaryOutcomes[].measure`, which is what the live tool will read when somebody pastes an
id. The endpoint-type classifier has to be validated on the field it will be deployed
against, so it is built on this one.

THE KEY IS (nct_id, index), AND design_outcomes.id IS DELIBERATELY NOT PERSISTED
================================================================================
AACT regenerates surrogate keys on every nightly rebuild (lesson 1). A file keyed on
`design_outcomes.id` would join cleanly against the wrong rows after any rebuild, with no
error. So the row is keyed on `nct_id` plus a 1-based index in `id` order -- the registry's
own ordering of a trial's primary outcomes -- and `measure_sha8` travels beside it so a
text change between snapshots is DETECTABLE rather than silent. The index is stable as long
as the registry does not reorder a trial's outcomes; the hash is what catches it if it does.

ORDER OF OPERATIONS
===================
  1. credentials, without ever printing the password
  2. connect
  3. probe: does the table exist, which of the wanted columns are present
  4. TALLY outcome_type over the whole table, before filtering to primary -- so the
     denominator is known rather than assumed
  5. the chunked pull, streaming to CSV
  6. coverage: how many interventional trials came back with at least one primary outcome

Usage (PowerShell), one command at a time:

  python scripts\\pull_design_outcomes.py --probe-only
  python scripts\\pull_design_outcomes.py --print-sql
  python scripts\\pull_design_outcomes.py --out data\\aact\\trial_design_outcomes.csv

Run the real pull from a STANDALONE PowerShell window, not VS Code's integrated terminal:
its Python extension injects `conda activate base` and sends Ctrl+C to clear the line
first, which killed two label pulls with a KeyboardInterrupt nobody sent (lesson 13).
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from trial_pos.services.population import (  # noqa: E402
    UNKNOWN, is_interventional, normalize_study_type,
)
from trial_pos.services.resume import header_problems, ids_remaining  # noqa: E402

HOST = "aact-db.ctti-clinicaltrials.org"
PORT = 5432
DB = "aact"
SCHEMA = "ctgov"
TABLE = "design_outcomes"

# Wanted, and required. `id` is queried for ORDERING only and is never written (see the
# module docstring). `description` is wanted because a bare measure is sometimes
# uninformative ("Efficacy") while its description names the instrument, and the labeller
# should see everything the deployed tool could see.
WANT_COLUMNS = ("nct_id", "outcome_type", "measure", "time_frame", "description")
NEED_COLUMNS = ("nct_id", "outcome_type", "measure")

# The value design_outcomes uses for a primary outcome. Compared case-insensitively in SQL
# because AACT's capitalisation of enum-like text has already moved once in this project
# ('Estimated' where a constant said 'Anticipated', lesson 17).
PRIMARY_OUTCOME_TYPE = "primary"

OUT_COLUMNS = ("nct_id", "design_outcome_index", "outcome_type", "measure", "time_frame",
               "description", "measure_sha8")

MEASURE_HASH_HEX = 8

# Fields that must match for a --resume to be sound. Narrower than the label pull's,
# because nothing here is derived: only the scope and the schema change which rows exist.
MANIFEST_FIELDS = ("population", "schema", "outcome_type")


def _rule(title: str) -> str:
    return ("\n" + "=" * 78 + f"\n{title}\n" + "=" * 78)


def _pct(n: int, d: int) -> str:
    return "n/a" if not d else f"{100.0 * n / d:.1f}%"


def measure_hash(text: str) -> str:
    """Short digest of the measure text, so a change between snapshots is detectable.

    Not a key. It exists because the row's key is (nct_id, index) and an index is only
    stable while the registry does not reorder a trial's outcomes; if it ever does, the
    hash is what makes the reorder visible instead of silently rewriting a hand label's
    subject.
    """
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()[:MEASURE_HASH_HEX]


def build_sql(schema: str, columns) -> str:
    """The one query, built from the columns the probe actually found.

    ORDER BY id inside the id block, so the per-trial index is the registry's own ordering.
    `id` is selected for nothing else and is not written out.
    """
    selected = ", ".join(f"d.{c}" for c in columns)
    return (f"SELECT {selected} FROM {schema}.{TABLE} d "
            f"WHERE d.nct_id = ANY(%(ids)s) "
            f"AND lower(d.outcome_type) = %(outcome_type)s "
            f"ORDER BY d.nct_id, d.id;")


def probe_schema(conn, schema: str) -> set:
    """-> the column names present on the table. Empty set when the table is absent."""
    with conn.cursor() as c:
        c.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = %(schema)s AND table_name = %(table)s",
            {"schema": schema, "table": TABLE},
        )
        return {row[0] for row in c.fetchall()}


def report_probe(found: set) -> tuple:
    """-> (usable columns, missing required). Reported, never silently dropped."""
    print(_rule(f"PROBE: {SCHEMA}.{TABLE}"))
    if not found:
        print(f"  !! {TABLE} is not visible in this schema at all.")
        return [], list(NEED_COLUMNS)
    usable = [c for c in WANT_COLUMNS if c in found]
    absent = [c for c in WANT_COLUMNS if c not in found]
    missing_required = [c for c in NEED_COLUMNS if c not in found]
    print(f"  wanted columns present : {len(usable)}/{len(WANT_COLUMNS)}")
    for column in WANT_COLUMNS:
        mark = "OK  " if column in found else "MISS"
        print(f"    [{mark}] {column}")
    if absent:
        print(f"  absent -> {', '.join(absent)}")
        print("  An absent optional column leaves its output column blank everywhere,")
        print("  which is weaker than having it and honest about which.")
    if "id" not in found:
        print("  !! id is absent, so a trial's outcomes cannot be put in registry order.")
        print("     The per-trial index would then be arbitrary and the key unusable.")
    if missing_required:
        print(f"  !! REQUIRED columns missing: {', '.join(missing_required)}")
    return usable, missing_required


def tally_outcome_types(conn, schema: str) -> Counter:
    """Every outcome_type on the table, before any filter.

    Audit-first: the primary-only filter is the denominator decision for everything
    downstream, and a filter whose excluded share was never counted is a filter nobody can
    review. It also catches the case where AACT's capitalisation or wording has moved.
    """
    print(_rule("OUTCOME TYPE TALLY (the whole table, before the primary filter)"))
    tally: Counter = Counter()
    with conn.cursor() as c:
        c.execute(f"SELECT outcome_type, count(*) FROM {schema}.{TABLE} "
                  f"GROUP BY outcome_type ORDER BY count(*) DESC")
        for outcome_type, count in c.fetchall():
            tally[str(outcome_type) if outcome_type is not None else UNKNOWN] = count
    total = sum(tally.values())
    print(f"  rows in {TABLE}: {total}")
    for name, count in tally.most_common():
        mark = " <-- pulled" if str(name).lower() == PRIMARY_OUTCOME_TYPE else ""
        print(f"    {str(name)[:40]:40s} {count:9d} ({_pct(count, total)}){mark}")
    if not any(str(name).lower() == PRIMARY_OUTCOME_TYPE for name in tally):
        print(f"  !! nothing in this table has outcome_type '{PRIMARY_OUTCOME_TYPE}'.")
        print("     The vocabulary has moved; fix PRIMARY_OUTCOME_TYPE rather than")
        print("     letting the pull return zero rows and read as a coverage finding.")
    return tally


def fetch_population(conn, schema: str, population: str) -> list:
    """-> interventional nct_ids, decided by the TESTED predicate rather than a SQL LIKE.

    The same approach as the label pull, and for the same two reasons: the definition lives
    in one tested place, and the tri-state result is preserved so an unknown study type is
    counted as unknown instead of being silently excluded by a predicate that reads it as
    non-matching.
    """
    print(_rule("POPULATION (defined by query, not by a cohort file)"))
    ids: list = []
    tally: Counter = Counter()
    # A NAMED cursor is a SERVER-SIDE cursor, and psycopg2 can only use one inside a
    # transaction. This connection is opened with autocommit=True, so opening one raised
    # "can't use a named cursor outside of transactions" and the pull died after the
    # probe. `pull_aact_results.py` has the same named-cursor stream and never hit this,
    # because it does NOT set autocommit -- which is why one pull worked and this one did
    # not, and why the difference was invisible when reading either script alone.
    #
    # Autocommit is toggled off for the stream and RESTORED afterwards, rather than
    # dropped from the connection: the rest of this script runs one-shot queries under
    # autocommit on a shared read-only server, and leaving a transaction open across the
    # whole chunked pull would hold a snapshot for its entire duration. The rollback is
    # what ends the read transaction; on a readonly session it discards nothing.
    was_autocommit = conn.autocommit
    conn.autocommit = False
    try:
        with conn.cursor(name="trial_pos_design_population") as c:
            c.itersize = 20000
            c.execute(
                f"SELECT nct_id, study_type FROM {schema}.studies ORDER BY nct_id;")
            for nct_id, study_type in c:
                norm = normalize_study_type(study_type)
                tally[norm if norm is not None else UNKNOWN] += 1
                keep = (True if population == "all"
                        else (is_interventional(study_type) is True))
                if keep and nct_id:
                    ids.append(str(nct_id).upper())
    finally:
        conn.rollback()
        conn.autocommit = was_autocommit
    total = sum(tally.values())
    print(f"  trials in AACT.studies       : {total}")
    for name, count in tally.most_common():
        print(f"    {str(name)[:40]:40s} {count:8d} ({_pct(count, total)})")
    print(f"  selected (scope={population:14s}): {len(ids)} ({_pct(len(ids), total)})")
    return ids


def fetch_rows(conn, sql: str, ids: list) -> list:
    from psycopg2.extras import RealDictCursor
    with conn.cursor(cursor_factory=RealDictCursor) as c:
        c.execute(sql, {"ids": ids, "outcome_type": PRIMARY_OUTCOME_TYPE})
        return [dict(r) for r in c.fetchall()]


def to_records(rows: list) -> list:
    """Raw rows -> output records, with the per-trial index assigned in query order.

    A projection, not a derivation: the text is carried VERBATIM. Normalising it here would
    bake one reading of the endpoint into the pull, which is the mistake of deciding a
    judgment inside a pull where nobody can audit it.

    Correct per chunk as well as in aggregate, because rows are filtered by nct_id and
    ordered by (nct_id, id): a chunk holds every primary outcome for every trial it
    contains, so the index needs no cross-chunk state.
    """
    out = []
    index_by_trial: Counter = Counter()
    for row in rows:
        nct = str(row.get("nct_id") or "").upper()
        if not nct:
            continue
        index_by_trial[nct] += 1
        measure = row.get("measure")
        measure = "" if measure is None else str(measure)
        out.append({
            "nct_id": nct,
            "design_outcome_index": index_by_trial[nct],
            "outcome_type": str(row.get("outcome_type") or ""),
            "measure": measure,
            "time_frame": str(row.get("time_frame") or ""),
            "description": str(row.get("description") or ""),
            "measure_sha8": measure_hash(measure),
        })
    return out


def read_existing(path: Path) -> tuple:
    """-> (header, nct_ids already written). ([], set()) when the file is absent."""
    if not path.exists():
        return None, set()
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.reader(handle)
        try:
            header = next(reader)
        except StopIteration:
            return None, set()
        column = header.index("nct_id") if "nct_id" in header else 0
        done = {row[column].strip().upper() for row in reader if row}
    return header, done


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Pull registered primary outcome text from AACT design_outcomes.")
    ap.add_argument("--out", type=Path,
                    default=Path("data") / "aact" / "trial_design_outcomes.csv")
    ap.add_argument("--population", choices=["interventional", "all"],
                    default="interventional",
                    help="which trials form the population, via the tested predicate in "
                         "services/population.py")
    ap.add_argument("--host", default=HOST)
    ap.add_argument("--port", type=int, default=PORT)
    ap.add_argument("--db", default=DB)
    ap.add_argument("--schema", default=SCHEMA)
    ap.add_argument("--user", default=os.environ.get("AACT_USER"))
    ap.add_argument("--password", default=os.environ.get("AACT_PASSWORD"))
    ap.add_argument("--sslmode", default="prefer")
    ap.add_argument("--chunk", type=int, default=2000,
                    help="nct_ids per query. Default: %(default)s")
    ap.add_argument("--max-trials", type=int, default=0,
                    help="cap for a smoke run (0 = no cap). Takes the FIRST N ids in "
                         "nct_id order, which correlates with registration era, so a "
                         "capped run's coverage rates are not the population's.")
    ap.add_argument("--probe-only", action="store_true",
                    help="schema probe and outcome_type tally, then stop")
    ap.add_argument("--print-sql", action="store_true",
                    help="print the query that would run, then stop. No connection.")
    ap.add_argument("--resume", action="store_true",
                    help="append to an existing --out, skipping nct_ids already present. "
                         "Refuses unless the saved manifest matches this run.")
    ap.add_argument("--force-resume", action="store_true",
                    help="resume even when the manifest conflicts. The resulting file "
                         "will hold rows pulled under DIFFERENT settings.")
    args = ap.parse_args()

    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", args.schema):
        print(f"!! refusing an unsafe schema name: {args.schema!r}")
        return 2
    if args.chunk < 1:
        print(f"!! --chunk must be >= 1, got {args.chunk}")
        return 2

    print(_rule("SETTINGS -- every value that changes a row below"))
    print(f"  table              : {args.schema}.{TABLE}")
    print(f"  outcome_type filter: {PRIMARY_OUTCOME_TYPE}")
    print(f"  population         : {args.population}")
    print(f"  out                : {args.out}")
    print(f"  chunk              : {args.chunk}")
    print(f"  max trials         : {args.max_trials or 'no cap'}")
    print(f"  user               : {args.user or '(unset: set $env:AACT_USER)'}")
    print("  key                : (nct_id, design_outcome_index) -- design_outcomes.id is")
    print("                       NOT written, because AACT regenerates surrogate keys")
    print("                       nightly and a file keyed on one would join cleanly")
    print("                       against the wrong rows after any rebuild (lesson 1).")

    if args.print_sql:
        print(_rule("SQL (as it would run, with all wanted columns present)"))
        print("  " + build_sql(args.schema, ("id",) + WANT_COLUMNS))
        print("\n  Printed only. No connection was opened.")
        return 0

    if not args.user or not args.password:
        print("\n!! credentials missing. In PowerShell, on their own lines, SINGLE quotes:")
        print("     $env:AACT_USER = 'your_aact_username'")
        print("     $env:AACT_PASSWORD = 'your_db_password'")
        print("   Double quotes make PowerShell expand $ and backticks, which alters the")
        print("   password before Postgres sees it.")
        print("   scripts\\check_aact_connection.py diagnoses a failure layer by layer.")
        return 2

    try:
        import psycopg2
    except ImportError:
        print("\n!! psycopg2 not installed:  pip install psycopg2-binary")
        return 2

    conn = psycopg2.connect(host=args.host, port=args.port, dbname=args.db, user=args.user,
                            password=args.password, sslmode=args.sslmode)
    conn.set_session(readonly=True, autocommit=True)
    try:
        found = probe_schema(conn, args.schema)
        usable, missing = report_probe(found)
        if missing:
            print("\n  Stopping: a required column is absent, so the pull would write a")
            print("  column of blanks and read later as a coverage finding.")
            return 3
        tally_outcome_types(conn, args.schema)
        if args.probe_only:
            print("\n  --probe-only: stopping before the pull.")
            return 0

        ids = fetch_population(conn, args.schema, args.population)
        if args.max_trials and len(ids) > args.max_trials:
            ids = ids[:args.max_trials]
            print(f"\n  !! --max-trials {args.max_trials} applied: the first {len(ids)} "
                  f"ids in nct_id order. SMOKE RUN -- rates below are not the "
                  f"population's.")

        manifest = {"population": args.population, "schema": args.schema,
                    "outcome_type": PRIMARY_OUTCOME_TYPE}
        manifest_path = Path(str(args.out) + ".manifest.json")
        mode = "w"
        if args.resume:
            header, done = read_existing(args.out)
            problems = header_problems(header, list(OUT_COLUMNS))
            saved = None
            if manifest_path.exists():
                saved = json.loads(manifest_path.read_text(encoding="utf-8"))
            conflicts = [f for f in MANIFEST_FIELDS
                         if (saved or {}).get(f) != manifest[f]]
            if problems:
                print(_rule("RESUME REFUSED"))
                for problem in problems:
                    print(f"  - {problem}")
                return 4
            if conflicts and not args.force_resume:
                print(_rule("RESUME REFUSED -- settings conflict"))
                for field in conflicts:
                    print(f"  - {field}: saved {(saved or {}).get(field)!r} vs this run "
                          f"{manifest[field]!r}")
                print("  Appending rows pulled under different settings yields one file")
                print("  holding two definitions, and nothing downstream could detect it.")
                print("  --force-resume if mixing them is your stated intent.")
                return 4
            before = len(ids)
            ids = ids_remaining(ids, done)
            print(f"\n  --resume: {before - len(ids)} trials already present, "
                  f"{len(ids)} to go.")
            mode = "a"

        args.out.parent.mkdir(parents=True, exist_ok=True)
        sql = build_sql(args.schema, ("id",) + tuple(usable))
        print(_rule("PULL"))
        total_rows = 0
        trials_with_rows = 0
        blank_measures = 0
        lengths: list = []
        chunks = (len(ids) + args.chunk - 1) // args.chunk
        with args.out.open(mode, newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(OUT_COLUMNS))
            if mode == "w":
                writer.writeheader()
            for index in range(0, len(ids), args.chunk):
                block = ids[index:index + args.chunk]
                records = to_records(fetch_rows(conn, sql, block))
                seen = set()
                for record in records:
                    writer.writerow(record)
                    total_rows += 1
                    seen.add(record["nct_id"])
                    if not record["measure"].strip():
                        blank_measures += 1
                    else:
                        lengths.append(len(record["measure"]))
                trials_with_rows += len(seen)
                handle.flush()
                chunk_number = index // args.chunk + 1
                print(f"  chunk {chunk_number}/{chunks}  ids {len(block)}  "
                      f"rows {len(records)}  cumulative {total_rows}")
        manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

        print(_rule("COVERAGE"))
        print(f"  trials queried                      : {len(ids)}")
        print(f"  trials with >=1 primary design outcome: {trials_with_rows} "
              f"({_pct(trials_with_rows, len(ids))})")
        print(f"  primary outcome rows written        : {total_rows}")
        print(f"  rows with a BLANK measure           : {blank_measures} "
              f"({_pct(blank_measures, total_rows)})")
        if lengths:
            lengths.sort()
            print(f"  measure length  median {lengths[len(lengths) // 2]}  "
                  f"p90 {lengths[int(0.9 * len(lengths))]}  max {lengths[-1]}")
        print("\n  A trial with NO primary design outcome is 'unknown', not 'zero': the")
        print("  registry was not required to carry one for older registrations, and the")
        print("  endpoint-type gate must return undeterminable for it rather than a")
        print("  refusal. build_endpoint_type_sample.py reports that share by phase.")
        print(f"\n  wrote {args.out}")
        print(f"  wrote {manifest_path}")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())