#!/usr/bin/env python3
"""Run every mpmt_components test in its own subprocess; judge by rc only.

Usage:  mpmt_venv/bin/python tests/run_components.py [--small] [--stress-mode ...]
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve()
TESTS_ROOT = HERE.parent
PROJECT = TESTS_ROOT.parent
COMP_DIR = TESTS_ROOT / "mpmt_components"

COMPONENTS = [
    "test_rvector.py",
    "test_sharevec.py",
    "test_channels.py",
    "test_ring_transport.py",
    "test_util.py",
    "test_rep3_tcp_ring.py",
    "test_ringconv_only.py",
    "test_reveal_flush.py",
    "test_hash_consistency.py",
    "test_hash_party.py",
    "rep3/test_factory.py",
    "rep3/test_operations.py",
    "rep3/test_protocol.py",
    "rep3/test_compound.py",
    "add2/test_factory.py",
    "add2/test_operations.py",
    "add2/test_protocol.py",
    "dpf/test_basic.py",
    "dpf/test_operations.py",
]
SMALLABLE = {
    "test_sharevec.py", "test_channels.py", "test_ring_transport.py", "test_util.py",
    "test_rep3_tcp_ring.py",
    "rep3/test_factory.py", "rep3/test_operations.py", "rep3/test_protocol.py",
    "rep3/test_compound.py",
    "add2/test_factory.py", "add2/test_operations.py", "add2/test_protocol.py",
    "dpf/test_basic.py", "dpf/test_operations.py",
}
STRESS = "test_ringconv_stress.py"


def run_one(rel: str, flags: list[str]) -> int:
    path = COMP_DIR / rel
    tag = f"{rel} {' '.join(flags)}" if flags else rel
    print(f"\n=== {tag} ===", flush=True)
    r = subprocess.run([sys.executable, "-u", str(path), *flags], cwd=str(PROJECT))
    return r.returncode


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--small", action="store_true")
    ap.add_argument("--stress-mode", default="full",
                    choices=["quick", "full", "production"])
    args = ap.parse_args()

    files = list(COMPONENTS) + [STRESS]
    fails = []
    for rel in files:
        flags = []
        if rel == STRESS:
            flags = [f"--mode={args.stress_mode}"]
        elif args.small and rel in SMALLABLE:
            flags = ["--small"]
        rc = run_one(rel, flags)
        if rc != 0:
            fails.append((rel, rc))

    print(f"\nCOMPONENTS RESULT: {len(files) - len(fails)}/{len(files)} passed")
    if fails:
        print("FAILED:", fails)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
