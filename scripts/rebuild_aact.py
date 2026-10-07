#!/usr/bin/env python3
"""Rebuild every AACT-derived file from the local restore of the pinned snapshot.

Runs the three pulls in order against the local server, with every verdict-producing
setting pinned: --as-of is the snapshot's own date, never today. Writes into --out-root,
which must be empty, then prints each file's size and sha256.

  --compare A B   compares two rebuilds file by file. Two runs from the same snapshot must
                  produce byte-identical data files; manifests carry run timestamps and are
                  reported apart.

Usage (PowerShell), one at a time, in a standalone window:
  python scripts\\rebuild_aact.py --out-root data\\aact_rebuild\\run1
  python scripts\\rebuild_aact.py --out-root data\\aact_rebuild\\run2
  python scripts\\rebuild_aact.py --compare data\\aact_rebuild\\run1 data\\aact_rebuild\\run2
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from trial_pos.services.aact_snapshot import (  # noqa: E402
    LOCAL_PORT, PINNED, pull_connection_args,
)
from trial_pos.services.data_registry import sha256_file  # noqa: E402

MANIFEST_MARK = "manifest"


def pulls(root: Path, port: int) -> list:
    conn = pull_connection_args(port)
    py = sys.executable
    s = ROOT / "scripts"
    return [
        ("labels, entities, raw dumps",
         [py, str(s / "pull_aact_results.py"), *conn, "--as-of", PINNED.date.isoformat(),
          "--out", str(root / "trial_labels.csv"),
          "--raw-prefix", str(root / "results_raw"),
          "--entities", str(root / "trial_entities.csv")]),
        ("registered primary outcomes",
         [py, str(s / "pull_design_outcomes.py"), *conn,
          "--out", str(root / "trial_design_outcomes.csv")]),
        ("registration fields and text",
         [py, str(s / "pull_aact_fields.py"), *conn, "--out-dir", str(root)]),
    ]


def listing(root: Path) -> dict:
    return {p.name: p for p in sorted(root.iterdir()) if p.is_file()}


def compare(a: Path, b: Path) -> int:
    fa, fb = listing(a), listing(b)
    differ = 0
    print(f"{'file':52s} result")
    for name in sorted(set(fa) | set(fb)):
        if name not in fa or name not in fb:
            print(f"{name:52s} !! only in {'A' if name in fa else 'B'}")
            differ += 1
            continue
        same = (fa[name].stat().st_size == fb[name].stat().st_size
                and sha256_file(fa[name]) == sha256_file(fb[name]))
        if MANIFEST_MARK in name:
            print(f"{name:52s} {'identical' if same else 'differs (manifest: expected)'}")
            continue
        print(f"{name:52s} {'identical' if same else '!! DIFFERS'}")
        differ += not same
    print(f"\n  data files that differ: {differ}")
    return 1 if differ else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out-root", type=Path)
    ap.add_argument("--port", type=int, default=LOCAL_PORT)
    ap.add_argument("--compare", nargs=2, type=Path, metavar=("A", "B"))
    args = ap.parse_args()
    if args.compare:
        return compare(*args.compare)
    if args.out_root is None:
        raise SystemExit("!! --out-root or --compare is required")
    if args.out_root.exists() and any(args.out_root.iterdir()):
        raise SystemExit(f"!! {args.out_root} is not empty; rebuilds go into a fresh folder")
    args.out_root.mkdir(parents=True, exist_ok=True)
    print(f"snapshot {PINNED.file} | as-of {PINNED.date} | port {args.port} | "
          f"out {args.out_root}")
    t0 = time.monotonic()
    for label, cmd in pulls(args.out_root, args.port):
        print(f"\n=== {label} ===\n  $ {' '.join(cmd)}", flush=True)
        rc = subprocess.run(cmd).returncode
        if rc != 0:
            raise SystemExit(f"!! {label}: exit {rc}. Rebuild incomplete.")
    print(f"\n=== outputs ({time.monotonic() - t0:.0f}s) ===")
    for name, path in listing(args.out_root).items():
        print(f"  {name:52s} {path.stat().st_size:12d}  {sha256_file(path)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
