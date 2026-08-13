#!/usr/bin/env python3
"""Run every app-layer test in its own subprocess; judge by rc only.

Each app test spawns its own minimal stack (fresh pretreat + the services its
mode needs) and cleans up after itself — no manual consoles required.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve()
TESTS_ROOT = HERE.parent
PROJECT = TESTS_ROOT.parent
APP_DIR = TESTS_ROOT / "app"

APP_TESTS = [
    "test_state_machine.py",
    "test_business_e2e.py",
    "test_sync_e2e.py",
    "test_lifecycle_e2e.py",
]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default=None, help="run a single test file name")
    args = ap.parse_args()

    files = [args.only] if args.only else list(APP_TESTS)
    fails = []
    for rel in files:
        path = APP_DIR / rel
        print(f"\n===== {rel} =====", flush=True)
        r = subprocess.run([sys.executable, "-u", str(path)], cwd=str(PROJECT))
        if r.returncode != 0:
            fails.append((rel, r.returncode))

    print(f"\nAPP RESULT: {len(files) - len(fails)}/{len(files)} passed")
    if fails:
        print("FAILED:", fails)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
