"""Project path resolution + sys.path bootstrap.

Every test file should call ``ensure_syspath()`` before importing ``mpmt`` or
``common.*``, so it works both standalone (subprocess) and under a runner.
"""
from __future__ import annotations

import sys
from pathlib import Path

TESTS_ROOT = Path(__file__).resolve().parent.parent
PROJECT_ROOT = TESTS_ROOT.parent
APP_DIR = PROJECT_ROOT / "application"
SRC_DIR = PROJECT_ROOT / "src"


def ensure_syspath() -> None:
    """Idempotently put PROJECT_ROOT / TESTS_ROOT / APP_DIR on sys.path."""
    for p in (str(PROJECT_ROOT), str(TESTS_ROOT), str(APP_DIR)):
        if p not in sys.path:
            sys.path.insert(0, p)
