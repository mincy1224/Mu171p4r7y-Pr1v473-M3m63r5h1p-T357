#!/usr/bin/env python3
"""Run the whole suite: components → protocols → app.  Each layer runs in its
own subprocess runner; every test file runs in its own subprocess and is judged
purely by its return code.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve()
TESTS_ROOT = HERE.parent


def main() -> int:
    runners = ["run_components.py", "run_protocols.py", "run_app.py"]
    fails = []
    for name in runners:
        print(f"\n########## {name} ##########", flush=True)
        r = subprocess.run([sys.executable, "-u", str(TESTS_ROOT / name)],
                           cwd=str(TESTS_ROOT.parent))
        if r.returncode != 0:
            fails.append(name)
    print("\n" + "=" * 60)
    if fails:
        print(f"RUN ALL FAILED in: {fails}")
        return 1
    print("ALL TEST LAYERS PASSED.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
