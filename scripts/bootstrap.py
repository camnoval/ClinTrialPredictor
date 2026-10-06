#!/usr/bin/env python3
"""Fetch the pinned data bundle from Zenodo, verify every file, then run the checks.

For anyone with a fresh clone. Reads `data_sources.json`, downloads each file of the
requested tiers that is absent, and verifies size and sha256 against the registry. A file
that is present but does not match is REPORTED, never silently replaced: --replace-mismatched
replaces it. Downloads go to `.partial` and are renamed only after they verify.

Usage:
  python scripts/bootstrap.py                     # run tier, then scripts/run_checks.py
  python scripts/bootstrap.py --tier run rebuild  # also the rebuild inputs
  python scripts/bootstrap.py --dry-run           # report only
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from trial_pos.services.data_registry import (  # noqa: E402
    HASH_CHUNK_BYTES, STATUSES, STATUS_ABSENT, STATUS_VERIFIED, TIERS, TIER_RUN,
    ZENODO_FILE_URL,
    file_url, plan, safe_relative_path, status_counts, validate_registry,
)

DEFAULT_REGISTRY = ROOT / "data_sources.json"
DEFAULT_TIMEOUT_S = 120
DEFAULT_RETRIES = 3
RETRY_PAUSE_S = 10
PROGRESS_EVERY_BYTES = 100 * (1 << 20)
PARTIAL_SUFFIX = ".partial"


def download(url: str, dest: Path, expected_bytes: int, expected_sha: str,
             timeout_s: int) -> None:
    """Stream to dest.partial while hashing; rename only if size and hash both match."""
    partial = dest.with_name(dest.name + PARTIAL_SUFFIX)
    dest.parent.mkdir(parents=True, exist_ok=True)
    digest, done, next_report = hashlib.sha256(), 0, PROGRESS_EVERY_BYTES
    with urllib.request.urlopen(url, timeout=timeout_s) as response, \
            partial.open("wb") as out:
        for chunk in iter(lambda: response.read(HASH_CHUNK_BYTES), b""):
            out.write(chunk)
            digest.update(chunk)
            done += len(chunk)
            if done >= next_report:
                print(f"      {done >> 20} / {expected_bytes >> 20} MB", flush=True)
                next_report += PROGRESS_EVERY_BYTES
    if done != expected_bytes or digest.hexdigest() != expected_sha:
        partial.unlink()
        raise IOError(f"{dest.name}: got {done} bytes / sha {digest.hexdigest()[:12]}, "
                      f"expected {expected_bytes} / {expected_sha[:12]}")
    os.replace(partial, dest)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY)
    ap.add_argument("--tier", nargs="+", default=[TIER_RUN], choices=TIERS)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--replace-mismatched", action="store_true")
    ap.add_argument("--skip-checks", action="store_true")
    ap.add_argument("--timeout-s", type=int, default=DEFAULT_TIMEOUT_S)
    ap.add_argument("--retries", type=int, default=DEFAULT_RETRIES)
    ap.add_argument("--url-template", default=ZENODO_FILE_URL,
                    help="for a mirror; takes {record} and {name}. Default: Zenodo")
    args = ap.parse_args()

    if not args.registry.exists():
        raise SystemExit(f"!! {args.registry} not found: no data bundle has been published "
                         "for this checkout yet.")
    registry = validate_registry(json.loads(args.registry.read_text(encoding="utf-8")))
    print(f"registry {args.registry.name} | record {registry['zenodo_record']} | doi "
          f"{registry['doi']} | tiers {args.tier} | dry run {args.dry_run} | "
          f"replace mismatched {args.replace_mismatched}")
    planned = plan(registry, ROOT, args.tier)
    before = status_counts(planned)
    print("  before: " + "  ".join(f"{s} {before[s]}" for s in STATUSES))

    failures = []
    for entry, status in planned:
        if status == STATUS_VERIFIED:
            continue
        if status != STATUS_ABSENT and not args.replace_mismatched:
            print(f"  !! {entry['path']}: {status} -- left as is (--replace-mismatched)")
            failures.append(entry["path"])
            continue
        url = file_url(registry["zenodo_record"], entry["zenodo_name"], args.url_template)
        print(f"  fetch {entry['path']} ({entry['bytes'] >> 20} MB)")
        if args.dry_run:
            continue
        dest = ROOT / safe_relative_path(entry["path"])
        for attempt in range(1, args.retries + 1):
            try:
                download(url, dest, entry["bytes"], entry["sha256"], args.timeout_s)
                break
            except Exception as exc:     # network or verification; both are retried
                print(f"      attempt {attempt}/{args.retries} failed: {exc}")
                if attempt == args.retries:
                    failures.append(entry["path"])
                else:
                    time.sleep(RETRY_PAUSE_S)

    after = status_counts(plan(registry, ROOT, args.tier))
    print("  after:  " + "  ".join(f"{s} {after[s]}" for s in STATUSES))
    if args.dry_run:
        return 0
    if after[STATUS_VERIFIED] != sum(after.values()):
        print(f"!! {sum(after.values()) - after[STATUS_VERIFIED]} files not verified: "
              f"{failures}")
        return 1
    if args.skip_checks:
        return 0
    return subprocess.run([sys.executable, str(ROOT / "scripts" / "run_checks.py")]).returncode


if __name__ == "__main__":
    sys.exit(main())
