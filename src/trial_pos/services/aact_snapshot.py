"""The pinned AACT snapshot, and the commands that restore it into a local PostgreSQL.

AACT's live database changes nightly, and its daily snapshot copies are deleted at the
start of each month. Its MONTHLY archives are kept, and can be downloaded without an
account, so the AACT tier is pinned to one of them and rebuilt locally rather than
redistributed. ClinicalTrials.gov's terms ask that any distributed copy be kept current,
which a frozen copy cannot be.

PINNED is the archive every AACT-derived file in this project is built from. Changing it
re-baselines every AACT figure, and is a decision, not an update.

The commands here are pure: the scripts run them. The local cluster uses the C locale,
so that text ordering and comparison are byte-wise and identical on every machine, and
listens on localhost only with trust authentication, since it holds public data and is
never exposed.
"""
from __future__ import annotations

import os
import re
from datetime import date
from pathlib import Path
from typing import NamedTuple


class Snapshot(NamedTuple):
    date: date
    file: str
    url: str
    bytes: int
    sha256: str


# Recorded by audit/probe_aact_archive.py on the project machine, 2026-10-06.
PINNED = Snapshot(
    date=date(2026, 10, 1),
    file="20261001_clinical_trials_ctgov.zip",
    url="https://aact.ctti-clinicaltrials.org/snapshots/4418/download",
    bytes=2547627020,
    sha256="8f88d1f779c70050ee8679b4440ce3e62d8a5843cb61642bcb6f91f8669bc629",
)

SNAPSHOT_DIR = Path("data/aact_snapshots")
CLUSTER_DIR = Path("data/pg/aact")
LOCAL_HOST = "localhost"
LOCAL_PORT = 54320            # away from 5432, so an existing server is never touched
LOCAL_USER = "postgres"
LOCAL_PASSWORD = "local"      # ignored under trust auth; the pulls insist on a value
LOCAL_DB = "aact"
LOCALE = "C"
ENCODING = "UTF8"
DUMP_MEMBER_SUFFIX = ".dmp"

_ZIP_NAME = re.compile(r"^(\d{4})(\d{2})(\d{2})_clinical_trials(?:_ctgov)?\.zip$")


def snapshot_date_from_name(name: str) -> date:
    """'20261001_clinical_trials_ctgov.zip' -> date(2026, 10, 1). Raises otherwise."""
    m = _ZIP_NAME.match(name)
    if not m:
        raise ValueError(f"{name!r} is not an AACT snapshot file name")
    return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))


def binary(pg_bin, name: str) -> str:
    """The path to a PostgreSQL program in `pg_bin`, or the bare name for PATH lookup."""
    exe = name + (".exe" if os.name == "nt" else "")
    return str(Path(pg_bin) / exe) if pg_bin else exe


def initdb_cmd(pg_bin, data_dir: Path) -> list:
    return [binary(pg_bin, "initdb"), "-D", str(data_dir), "-U", LOCAL_USER,
            "--auth=trust", f"--encoding={ENCODING}", f"--locale={LOCALE}"]


def start_cmd(pg_bin, data_dir: Path, log_file: Path, port: int = LOCAL_PORT) -> list:
    return [binary(pg_bin, "pg_ctl"), "-D", str(data_dir), "-l", str(log_file), "-w",
            "-o", f"-p {port} -c listen_addresses={LOCAL_HOST}", "start"]


def stop_cmd(pg_bin, data_dir: Path) -> list:
    return [binary(pg_bin, "pg_ctl"), "-D", str(data_dir), "-w", "stop"]


def createdb_cmd(pg_bin, port: int = LOCAL_PORT) -> list:
    return [binary(pg_bin, "createdb"), "-h", LOCAL_HOST, "-p", str(port), "-U", LOCAL_USER,
            LOCAL_DB]


DEFAULT_RESTORE_JOBS = 4


def restore_cmd(pg_bin, dump: Path, port: int = LOCAL_PORT,
                jobs: int = DEFAULT_RESTORE_JOBS) -> list:
    """pg_restore from the extracted dump FILE. A file rather than a pipe, because a pipe
    forces archive order and rules out parallel jobs. No owners or grants: the dump's
    roles do not exist locally, and nothing here needs them."""
    if jobs < 1:
        raise ValueError(f"jobs must be >= 1, got {jobs}")
    return [binary(pg_bin, "pg_restore"), "-h", LOCAL_HOST, "-p", str(port), "-U", LOCAL_USER,
            "-d", LOCAL_DB, "--no-owner", "--no-privileges", "-j", str(jobs), str(dump)]


# Tables the pulls read directly rather than through a declaration: pull_aact_results
# (studies, calculated_values, outcomes, outcome_analyses), pull_design_outcomes
# (design_outcomes), validate_reconstruction (outcome_measurements, outcome_counts,
# result_groups).
DIRECT_TABLES = ("studies", "calculated_values", "outcomes", "outcome_analyses",
                 "design_outcomes", "outcome_measurements", "outcome_counts",
                 "result_groups")


def required_tables() -> list:
    """Every AACT table a pull reads, derived from the pull declarations."""
    from trial_pos.services.aact_aggregates import AGGREGATE_SOURCES
    from trial_pos.services.aact_provenance import TABLES
    return sorted({s.table for s in AGGREGATE_SOURCES} | {t.table for t in TABLES}
                  | set(DIRECT_TABLES))


def pull_connection_args(port: int = LOCAL_PORT) -> list:
    """The connection flags every pull script takes, pointed at the local restore."""
    return ["--host", LOCAL_HOST, "--port", str(port), "--db", LOCAL_DB,
            "--user", LOCAL_USER, "--password", LOCAL_PASSWORD]
