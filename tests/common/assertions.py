"""PASS/FAIL counter with a unified exit code (0 if no failures).

Deliberately small — a counter, not a framework.  Every test file returns
``harness.result()`` so runners can judge purely by subprocess return code.
"""
from __future__ import annotations


class Harness:
    def __init__(self) -> None:
        self.pass_count = 0
        self.fail_count = 0

    def check(self, name: str, cond: bool, detail: str = "") -> bool:
        if cond:
            self.pass_count += 1
            print(f"  PASS {name}", flush=True)
        else:
            self.fail_count += 1
            print(f"  FAIL {name}: {detail}", flush=True)
        return bool(cond)

    def section(self, name: str) -> None:
        print(f"\n{'=' * 18} {name} {'=' * 18}", flush=True)

    def result(self) -> int:
        print(f"\n  PASS={self.pass_count}  FAIL={self.fail_count}", flush=True)
        return 0 if self.fail_count == 0 else 1
