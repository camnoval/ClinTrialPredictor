#!/usr/bin/env python3
"""Minimal stdlib test runner -- lets run_checks.py gate a bare env (no pytest).

Discovers test_*.py files in this folder, runs every test_* function, reports
pass/fail. pytest is still the intended runner in a real dev env.
"""

from __future__ import annotations

import importlib.util
import sys
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent


def _load(path: Path):
    spec = importlib.util.spec_from_file_location(path.stem, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


def main() -> int:
    passed = failed = 0
    failures: list[str] = []
    for path in sorted(HERE.glob("test_*.py")):
        mod = _load(path)
        for name in dir(mod):
            if not name.startswith("test_"):
                continue
            fn = getattr(mod, name)
            if not callable(fn):
                continue
            try:
                fn()
                passed += 1
            except Exception:  # noqa: BLE001
                failed += 1
                failures.append(f"{path.name}::{name}\n{traceback.format_exc()}")
    for f in failures:
        print("FAIL", f)
    print(f"\n{passed} passed, {failed} failed")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
