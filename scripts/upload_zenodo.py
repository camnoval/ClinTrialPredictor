#!/usr/bin/env python3
"""Upload the data bundle in data_sources.json to its Zenodo DRAFT. Never publishes.

Before anything is sent, every local file is re-verified against the registry (size and
sha256), so a file changed after the registry was built cannot be uploaded under its old
hash. A file already on the draft with the same size and md5 is skipped; one that differs
is a conflict and is left alone unless --replace-conflicts. Every upload is checked against
the md5 Zenodo reports back, and the draft is re-read at the end.

Files on the draft that the registry does not list are reported and left alone, unless
--delete-extras, which deletes them from the DRAFT (a draft is editable; a published record
is not). Use it when a file has been taken out of the bundle on purpose.

Uploads stream in 1 MiB blocks; Python's default of 8 KiB makes multi-GB uploads slow.

Needs a Zenodo personal access token with deposit:write, in $env:ZENODO_TOKEN (single
quotes in PowerShell). The token is never printed. Run in a standalone PowerShell window.

Usage:
  python scripts\\upload_zenodo.py --dry-run
  python scripts\\upload_zenodo.py
"""
from __future__ import annotations

import argparse
import hashlib
import http.client
import json
import os
import sys
import time
import urllib.request
from pathlib import Path
from urllib.parse import quote, urlsplit

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from trial_pos.services.data_registry import (  # noqa: E402
    STATUS_VERIFIED, TIERS, plan, safe_relative_path, validate_registry,
)
from trial_pos.services.zenodo import (  # noqa: E402
    ACTIONS, ACTION_CONFLICT, ACTION_UPLOAD, ZenodoError, deposition_url, editable,
    file_delete_url, md5_file, normalise_md5, record_from_doi, remote_file_ids,
    remote_files, upload_plan,
)

DEFAULT_REGISTRY = ROOT / "data_sources.json"
TOKEN_ENV = "ZENODO_TOKEN"
DEFAULT_TIMEOUT_S = 600
PUT_BLOCK_BYTES = 1 << 20
REPORT_EVERY_BYTES = 1 << 28


class _HashingReader:
    """A file object that md5s exactly what the connection reads from it."""

    def __init__(self, handle, total: int):
        self.handle, self.total, self.sent = handle, total, 0
        self.digest = hashlib.md5()
        self.next_report = REPORT_EVERY_BYTES
        self.t0 = time.monotonic()

    def read(self, n: int = PUT_BLOCK_BYTES) -> bytes:
        chunk = self.handle.read(n)
        self.digest.update(chunk)
        self.sent += len(chunk)
        if self.sent >= self.next_report:
            rate = (self.sent >> 20) / max(time.monotonic() - self.t0, 1e-6)
            print(f"      {self.sent >> 20} / {self.total >> 20} MB  ({rate:.1f} MB/s)",
                  flush=True)
            self.next_report += REPORT_EVERY_BYTES
        return chunk


def _request(url: str, token: str, method: str = "GET",
             timeout: int = DEFAULT_TIMEOUT_S):
    req = urllib.request.Request(url, method=method,
                                 headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req, timeout=timeout) as response:
        body = response.read()
    return json.loads(body.decode("utf-8")) if body else None


def _put(url: str, token: str, reader, size: int, timeout: int) -> dict:
    """PUT a file with a large send block, which urllib does not expose."""
    u = urlsplit(url)
    cls = http.client.HTTPSConnection if u.scheme == "https" else http.client.HTTPConnection
    conn = cls(u.hostname, u.port, timeout=timeout, blocksize=PUT_BLOCK_BYTES)
    try:
        conn.request("PUT", u.path + (f"?{u.query}" if u.query else ""), body=reader,
                     headers={"Authorization": f"Bearer {token}",
                              "Content-Type": "application/octet-stream",
                              "Content-Length": str(size)})
        resp = conn.getresponse()
        body = resp.read()
    finally:
        conn.close()
    if resp.status >= 300:
        raise ZenodoError(f"PUT returned {resp.status}: {body[:300]!r}")
    return json.loads(body.decode("utf-8"))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY)
    ap.add_argument("--tier", nargs="+", default=list(TIERS), choices=TIERS)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--replace-conflicts", action="store_true")
    ap.add_argument("--delete-extras", action="store_true",
                    help="delete draft files the registry does not list")
    ap.add_argument("--timeout-s", type=int, default=DEFAULT_TIMEOUT_S)
    args = ap.parse_args()

    token = os.environ.get(TOKEN_ENV)
    if not token:
        raise SystemExit(f"!! set ${TOKEN_ENV} (a Zenodo personal access token with "
                         "deposit:write)")
    if not args.registry.exists():
        raise SystemExit(f"!! {args.registry} not found. Build it first: "
                         "python scripts\\build_data_registry.py --record <id> --doi <doi>")
    registry = validate_registry(json.loads(args.registry.read_text(encoding="utf-8")))
    record = registry["zenodo_record"]
    if record_from_doi(registry["doi"]) != record:
        raise SystemExit(f"!! registry record {record} does not match DOI {registry['doi']}")
    print(f"record {record} | doi {registry['doi']} | tiers {args.tier} | dry run "
          f"{args.dry_run} | replace conflicts {args.replace_conflicts} | delete extras "
          f"{args.delete_extras}")

    local = plan(registry, ROOT, args.tier)
    bad = [(e["path"], s) for e, s in local if s != STATUS_VERIFIED]
    if bad:
        print("!! local files do not match the registry; rebuild it or restore them:")
        for path, status in bad:
            print(f"    {status:14s} {path}")
        return 2
    entries = [e for e, _ in local]
    print(f"  {len(entries)} local files verified against the registry, "
          f"{sum(e['bytes'] for e in entries) >> 20} MB")

    deposition = _request(deposition_url(record), token)
    editable(deposition)
    bucket = deposition["links"]["bucket"]
    remote = remote_files(deposition)
    # md5 only what is already on the draft; an upload is md5'd as it streams
    local_md5 = {e["zenodo_name"]: md5_file(ROOT / safe_relative_path(e["path"]))
                 for e in entries if e["zenodo_name"] in remote}
    planned, extra = upload_plan(entries, local_md5, remote)
    counts = {a: sum(1 for _, x in planned if x == a) for a in ACTIONS}
    print("  plan: " + "  ".join(f"{a} {counts[a]}" for a in ACTIONS))
    to_send = [(e, a) for e, a in planned
               if a == ACTION_UPLOAD or (a == ACTION_CONFLICT and args.replace_conflicts)]
    print(f"  to send: {len(to_send)} files, {sum(e['bytes'] for e, _ in to_send) >> 20} MB")
    for e, action in planned:
        if action == ACTION_CONFLICT:
            print(f"  !! conflict: {e['zenodo_name']} differs on the draft"
                  + ("" if args.replace_conflicts else " -- left as is"))
    if extra:
        print(f"  on the draft but not in the registry: {len(extra)}"
              + (" -- will be DELETED from the draft" if args.delete_extras
                 else " -- left as is (--delete-extras removes them)"))
        for name in extra:
            print(f"    {name}  ({remote[name][0] >> 20} MB)")
    if args.dry_run:
        return 0

    failures = []
    if extra and args.delete_extras:
        ids = remote_file_ids(deposition)
        for name in extra:
            try:
                _request(file_delete_url(record, ids[name]), token, "DELETE", args.timeout_s)
                print(f"  deleted {name}")
            except Exception as exc:
                print(f"  !! could not delete {name}: {exc}")
                failures.append(name)

    for e, _action in to_send:
        name = e["zenodo_name"]
        print(f"  upload {name} ({e['bytes'] >> 20} MB)", flush=True)
        path = ROOT / safe_relative_path(e["path"])
        try:
            with path.open("rb") as handle:
                reader = _HashingReader(handle, e["bytes"])
                reply = _put(f"{bucket}/{quote(name, safe='')}", token, reader, e["bytes"],
                             args.timeout_s)
            sent = reader.digest.hexdigest()
            if reader.sent != e["bytes"] or normalise_md5(reply.get("checksum")) != sent:
                raise ZenodoError(f"{name}: sent {reader.sent} bytes md5 {sent}, Zenodo "
                                  f"reports {reply.get('checksum')}")
            local_md5[name] = sent
        except Exception as exc:
            print(f"      !! failed: {exc}")
            failures.append(name)

    final_dep = _request(deposition_url(record), token)
    final = remote_files(final_dep)
    ok = sum(1 for e in entries if e["zenodo_name"] in local_md5
             and final.get(e["zenodo_name"]) == (e["bytes"], local_md5[e["zenodo_name"]]))
    leftover = sorted(set(final) - {e["zenodo_name"] for e in entries})
    print(f"\n  on the draft and identical to the registry: {ok} of {len(entries)}")
    print(f"  on the draft and NOT in the registry: {len(leftover)}")
    if failures or ok != len(entries):
        left = [e["zenodo_name"] for e, a in planned if a == ACTION_CONFLICT]
        print(f"!! not complete: failed {failures}; conflicts left "
              f"{left if not args.replace_conflicts else []}. Re-run; identical files are "
              "skipped, and --replace-conflicts re-sends the rest.")
        return 1
    print("  NOT published. Check the draft in the browser, add the metadata, then publish.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
