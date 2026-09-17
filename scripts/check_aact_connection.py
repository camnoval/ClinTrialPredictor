#!/usr/bin/env python3
"""Diagnose an AACT connection failure layer by layer. Read-only, no data pulled.

Tests in dependency order and stops at the first failure, so the output names the broken
layer instead of leaving you to guess: credentials present -> DNS -> TCP -> Postgres auth
-> schema visible -> a real query returns rows.

Never prints the password. It reports length and flags characters that PowerShell would
have mangled if the value was assigned inside double quotes, which is the single most
common cause of "my password is definitely right".

Usage (PowerShell):
  python scripts\\check_aact_connection.py
  python scripts\\check_aact_connection.py --user myuser          # override env
  python scripts\\check_aact_connection.py --sslmode require
"""
from __future__ import annotations

import argparse
import os
import socket
import sys

HOST = "aact-db.ctti-clinicaltrials.org"
PORT = 5432
DB = "aact"
SCHEMA = "ctgov"

# Characters PowerShell expands inside double quotes. A password containing any of these,
# assigned as "pa$$w0rd" rather than 'pa$$w0rd', arrives at Postgres as something else.
SHELL_HAZARDS = ("$", "`", '"', "'")


def ok(msg):
    print(f"  [ok]   {msg}")


def bad(msg):
    print(f"  [FAIL] {msg}")


def note(msg):
    print(f"         {msg}")


def step(n, title):
    print(f"\n[{n}] {title}")


def check_credentials(user, password) -> bool:
    step(1, "Credentials present and undamaged")
    if not user:
        bad("no username (AACT_USER unset and --user not given)")
        note("PowerShell:  $env:AACT_USER = 'your_aact_username'")
        note("Run that on its own line. Pasting a multi-line block lets Read-Host")
        note("swallow the next line as input.")
        return False
    if not password:
        bad("no password (AACT_PASSWORD unset and --password not given)")
        note("PowerShell:  $env:AACT_PASSWORD = 'your_db_password'   <- SINGLE quotes")
        return False
    ok(f"username: {len(user)} chars, starts {user[:2]!r}")
    ok(f"password: {len(password)} chars")

    if user.strip() != user or password.strip() != password:
        bad("leading/trailing whitespace found -- likely a stray space or newline "
            "from a paste")
        note("re-assign with no trailing space after the closing quote")
        return False
    if user in ("your_aact_username", "username", "user"):
        bad(f"username is still the placeholder {user!r}")
        note("Real credentials appear on https://aact.ctti-clinicaltrials.org/connect")
        note("AFTER you log in. They are NOT your account email and login password.")
        return False
    hazards = [c for c in SHELL_HAZARDS if c in password]
    if hazards:
        note(f"password contains {hazards} -- harmless IF you assigned it with single")
        note("quotes. With double quotes PowerShell would have expanded it. If auth")
        note("fails below, re-assign using single quotes and retry.")
    if any(ord(c) > 126 for c in user + password):
        note("non-ASCII character present; check for a smart quote from copy-paste")
    return True


def check_dns() -> bool:
    step(2, f"DNS resolves {HOST}")
    try:
        addrs = sorted({ai[4][0] for ai in socket.getaddrinfo(HOST, PORT)})
    except socket.gaierror as e:
        bad(f"cannot resolve: {e}")
        note("no network, a VPN swallowing DNS, or a typo in the host name")
        return False
    ok(f"resolves to {', '.join(addrs)}")
    return True


def check_tcp(timeout: float) -> bool:
    step(3, f"TCP reaches {HOST}:{PORT}")
    try:
        with socket.create_connection((HOST, PORT), timeout=timeout):
            ok("port open")
            return True
    except socket.timeout:
        bad(f"timed out after {timeout:.0f}s")
        note("outbound 5432 is almost certainly blocked -- corporate network, VPN, or")
        note("a local firewall rule. A hang rather than an auth error points here.")
        note("Test independently:  Test-NetConnection aact-db.ctti-clinicaltrials.org "
             "-Port 5432")
        return False
    except OSError as e:
        bad(f"refused or unreachable: {e}")
        return False


def check_auth(user, password, sslmode) -> object | None:
    step(4, "Postgres accepts the credentials")
    try:
        import psycopg2
    except ImportError:
        bad("psycopg2 not installed")
        note("pip install psycopg2-binary")
        return None
    try:
        conn = psycopg2.connect(host=HOST, port=PORT, dbname=DB, user=user,
                                password=password, sslmode=sslmode, connect_timeout=20)
    except Exception as e:                      # psycopg2.OperationalError and friends
        text = str(e).strip()
        bad(f"connection rejected: {text.splitlines()[0] if text else type(e).__name__}")
        low = text.lower()
        if "password authentication failed" in low:
            note("The server saw your username and rejected the password. Either the")
            note("password is wrong, or the shell mangled it (see step 1), or you used")
            note("your ACCOUNT password instead of the DATABASE password shown on the")
            note("connect page after login.")
        elif "role" in low and "does not exist" in low:
            note("The username itself is unknown to the server. Copy it again from the")
            note("connect page -- it is not your email address.")
        elif "pg_hba" in low:
            note("The server refused this connection type. Try --sslmode require.")
        elif "timeout" in low or "could not connect" in low:
            note("Network-level, not credential-level. See step 3.")
        else:
            note("Paste this message and it can be read precisely.")
        return None
    ok("authenticated")
    return conn


def check_schema(conn) -> bool:
    step(5, f"Schema '{SCHEMA}' and the results tables are visible")
    wanted = ["studies", "outcomes", "outcome_analyses", "result_groups"]
    with conn.cursor() as c:
        c.execute("SELECT current_user, version();")
        who, ver = c.fetchone()
        ok(f"connected as {who}")
        note(ver.split(",")[0])
        c.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = %s AND table_name = ANY(%s);", (SCHEMA, wanted))
        present = sorted(r[0] for r in c.fetchall())
    missing = [t for t in wanted if t not in present]
    for t in present:
        ok(f"{SCHEMA}.{t}")
    for t in missing:
        bad(f"{SCHEMA}.{t} not visible")
    if missing:
        note("Unexpected -- these are core AACT tables. Check the schema name.")
        return False
    return True


def check_query(conn) -> bool:
    step(6, "A real query returns results rows")
    with conn.cursor() as c:
        # reltuples is an estimate from the planner: instant, where count(*) on
        # outcome_analyses would scan millions of rows
        c.execute(
            "SELECT relname, reltuples::bigint FROM pg_class c "
            "JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname = %s AND relname = ANY(%s) ORDER BY relname;",
            (SCHEMA, ["studies", "outcomes", "outcome_analyses"]))
        for name, est in c.fetchall():
            ok(f"{name}: ~{est:,} rows (planner estimate)")
        c.execute(
            f"SELECT oa.p_value, oa.p_value_modifier, oa.non_inferiority_type, "
            f"oa.param_type FROM {SCHEMA}.outcome_analyses oa "
            f"JOIN {SCHEMA}.outcomes o ON o.id = oa.outcome_id "
            f"WHERE lower(o.outcome_type) = 'primary' AND oa.p_value IS NOT NULL "
            f"LIMIT 3;")
        rows = c.fetchall()
    if not rows:
        bad("no primary-outcome analyses came back -- the join or filter is wrong")
        return False
    ok("sample primary-outcome analyses:")
    for r in rows:
        note(f"p_value={r[0]!r} modifier={r[1]!r} design={r[2]!r} param={r[3]!r}")
    return True


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--user", default=os.environ.get("AACT_USER"))
    ap.add_argument("--password", default=os.environ.get("AACT_PASSWORD"))
    ap.add_argument("--sslmode", default="prefer",
                    choices=["disable", "allow", "prefer", "require"])
    ap.add_argument("--timeout", type=float, default=15.0)
    args = ap.parse_args()

    print("=" * 70)
    print("AACT CONNECTION DIAGNOSTIC")
    print("=" * 70)
    print(f"  target : {HOST}:{PORT}/{DB}  schema={SCHEMA}  sslmode={args.sslmode}")
    print(f"  python : {sys.version.split()[0]} ({sys.platform})")

    if not check_credentials(args.user, args.password):
        return 2
    if not check_dns():
        return 3
    if not check_tcp(args.timeout):
        return 4
    conn = check_auth(args.user, args.password, args.sslmode)
    if conn is None:
        return 5
    try:
        if not check_schema(conn):
            return 6
        if not check_query(conn):
            return 7
    finally:
        conn.close()

    print("\n" + "=" * 70)
    print("ALL LAYERS GREEN -- run: python scripts\\pull_aact_results.py")
    print("=" * 70)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())