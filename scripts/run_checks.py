#!/usr/bin/env python3
"""Gate: import check + tests. Green = safe to build."""
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENV = {**os.environ, "PYTHONPATH": str(ROOT / "src")}


def main():
    ok = subprocess.run(
        [sys.executable, "-c", "import trial_pos; print('import OK', trial_pos.__version__)"],
        env=ENV,
    ).returncode == 0
    has_pytest = subprocess.run([sys.executable, "-c", "import pytest"], capture_output=True).returncode == 0
    if has_pytest:
        rc = subprocess.run([sys.executable, "-m", "pytest", "-q", str(ROOT / "tests")], env=ENV).returncode
    else:
        rc = subprocess.run([sys.executable, str(ROOT / "tests" / "_run_stdlib.py")], env=ENV).returncode
    ok = ok and rc == 0
    print("GATE:", "GREEN" if ok else "RED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
