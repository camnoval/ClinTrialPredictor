#!/usr/bin/env python3
"""Hash the local data bundle into data_sources.json, and stage it for upload to Zenodo.

For the PUBLISHER, once per bundle version. Reads only what `data_registry.BUNDLE`
declares; refuses if any declared file is missing; prints every file under data/ that the
bundle does NOT declare, so nothing is left out by accident; prints every source whose
licence has not been checked.

  --stage DIR   hard-links (or copies, across volumes) each file into DIR under its flat
                Zenodo name, ready to drag into the Zenodo upload page

Usage (PowerShell), one at a time:
  python scripts\\build_data_registry.py --record <id> --doi <doi> --stage zenodo_upload
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from trial_pos.services.data_registry import (  # noqa: E402
    TIERS, build_registry, excluded_files, missing_declared, undeclared_files,
)

DEFAULT_OUT = ROOT / "data_sources.json"


def _stage(root: Path, registry: dict, stage: Path) -> None:
    stage.mkdir(parents=True, exist_ok=True)
    for entry in registry["files"]:
        src = root / entry["path"]
        dst = stage / entry["zenodo_name"]
        if dst.exists():
            dst.unlink()
        try:
            os.link(src, dst)
            how = "linked"
        except OSError:
            shutil.copy2(src, dst)
            how = "copied"
        print(f"  {how} {entry['zenodo_name']}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--record", required=True,
                    help="the Zenodo record id (reserve it on a draft before uploading)")
    ap.add_argument("--doi", required=True, help="the DOI Zenodo reserved for the draft")
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--stage", type=Path, default=None)
    ap.add_argument("--overwrite", action="store_true")
    args = ap.parse_args()
    print(f"root {ROOT} | record {args.record} | doi {args.doi} | out {args.out} | "
          f"stage {args.stage}")

    left_out = excluded_files(ROOT)
    print(f"\n  files under data/ EXCLUDED, with the declared reason: {len(left_out)}")
    for p, reason in left_out:
        print(f"    {p}\n        {reason}")
    stray = undeclared_files(ROOT)
    print(f"\n  files under data/ neither in the bundle nor excluded: {len(stray)}")
    for p in stray:
        print(f"    !! {p}")
    missing = missing_declared(ROOT)
    if missing or stray:
        if missing:
            print(f"\n!! declared but absent: {len(missing)}")
            for p in missing:
                print(f"    {p}")
        if stray:
            print("\n!! declare or exclude every file above (data_registry.BUNDLE / "
                  "EXCLUDED) before building")
        print("  nothing written")
        return 2
    if args.out.exists() and not args.overwrite:
        raise SystemExit(f"!! {args.out} exists; pass --overwrite to replace it")

    registry = build_registry(ROOT, args.record, args.doi)
    for tier in TIERS:
        entries = [e for e in registry["files"] if e["tier"] == tier]
        print(f"\n  tier {tier}: {len(entries)} files, "
              f"{sum(e['bytes'] for e in entries)} bytes")
    unchecked = [s for s, meta in registry["sources"].items() if not meta["license_checked"]]
    print(f"\n  sources whose licence is NOT yet checked: {unchecked or 'none'}")
    if unchecked:
        print("  Set license_checked in data_registry.SOURCES only after reading each "
              "source's terms. Publishing first is the step that cannot be undone.")
    partial = args.out.with_name(args.out.name + ".partial")
    partial.write_text(json.dumps(registry, indent=2) + "\n", encoding="utf-8")
    os.replace(partial, args.out)
    print(f"\n  wrote {args.out}")
    if args.stage is not None:
        print(f"\n  staging into {args.stage}:")
        _stage(ROOT, registry, args.stage)
    return 0


if __name__ == "__main__":
    sys.exit(main())
