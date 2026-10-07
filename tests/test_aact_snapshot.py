"""Tests for the pinned AACT snapshot and the local-restore commands."""
from __future__ import annotations

from datetime import date
from pathlib import Path

from trial_pos.services.aact_snapshot import (
    LOCALE, LOCAL_DB, LOCAL_HOST, LOCAL_PORT, PINNED, createdb_cmd, initdb_cmd,
    pull_connection_args, required_tables, restore_cmd, snapshot_date_from_name, start_cmd, stop_cmd,
)

_D = Path("data/pg/x")


def test_the_pin_is_self_consistent():
    assert snapshot_date_from_name(PINNED.file) == PINNED.date
    assert PINNED.date.day == 1          # monthly archives are the permanent ones
    assert len(PINNED.sha256) == 64 and PINNED.bytes > 0
    assert PINNED.url.startswith("https://aact.ctti-clinicaltrials.org/")


def test_snapshot_names():
    assert snapshot_date_from_name("20240601_clinical_trials.zip") == date(2024, 6, 1)
    for bad in ("20240601_export_ctgov.zip", "2024_clinical_trials.zip", "x.zip"):
        try:
            snapshot_date_from_name(bad)
        except ValueError:
            continue
        raise AssertionError(bad)


def test_cluster_is_byte_ordered_local_and_trusting_only_localhost():
    init = initdb_cmd(None, _D)
    assert f"--locale={LOCALE}" in init and LOCALE == "C" and "--auth=trust" in init
    start = " ".join(start_cmd(None, _D, _D / "log"))
    assert f"listen_addresses={LOCAL_HOST}" in start and f"-p {LOCAL_PORT}" in start


def test_restore_and_pulls_target_the_same_local_database():
    port = 1234
    for cmd in (createdb_cmd(None, port), restore_cmd(None, _D / "d.dmp", port)):
        assert cmd[cmd.index("-p") + 1] == str(port) and LOCAL_DB in cmd
    args = pull_connection_args(port)
    assert args[args.index("--port") + 1] == str(port)
    assert args[args.index("--host") + 1] == LOCAL_HOST


def test_restore_drops_owners_and_grants():
    cmd = restore_cmd(None, _D / "d.dmp", jobs=3)
    assert "--no-owner" in cmd and "--no-privileges" in cmd
    assert cmd[cmd.index("-j") + 1] == "3" and cmd[-1] == str(_D / "d.dmp")
    try:
        restore_cmd(None, _D / "d.dmp", jobs=0)
    except ValueError:
        return
    raise AssertionError("jobs=0 accepted")


def test_a_pg_bin_directory_is_used_when_given():
    assert stop_cmd("bin", _D)[0].startswith(str(Path("bin")))
    assert stop_cmd(None, _D)[0].startswith("pg_ctl")


def test_required_tables_cover_the_direct_and_declared_reads():
    from trial_pos.services.aact_aggregates import AGGREGATE_SOURCES
    from trial_pos.services.aact_provenance import TABLES
    from trial_pos.services.aact_snapshot import DIRECT_TABLES
    req = set(required_tables())
    assert set(DIRECT_TABLES) <= req
    assert {s.table for s in AGGREGATE_SOURCES} <= req and {t.table for t in TABLES} <= req
