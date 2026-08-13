"""Unified console logging for the C3 application.

Every component (run / steward / peer0 / peer1 / manage_server / set_holder /
querier / pretreat) logs through these helpers so output stays consistent:

    HH:MM:SS [INFO]  [steward] message
    HH:MM:SS [WARN]  [manage]  message
    HH:MM:SS [ERROR] [querier] message

* INFO  — normal progress / state transitions
* WARN  — recoverable / non-fatal anomalies
* ERROR — failures that need the operator's attention

Terminal colour is enabled automatically when stdout is a TTY.
"""

from __future__ import annotations

import sys
import threading
from collections import deque
from datetime import datetime

_COLOR = sys.stdout.isatty()

_RESET = "\033[0m"
_BOLD = "\033[1m"
_DIM = "\033[2m"
_LEVEL_COLORS = {
    "INFO": "\033[32m",   # green
    "WARN": "\033[33m",   # yellow
    "ERROR": "\033[31m",  # red
    "DEBUG": "\033[2m",   # dim
}
_TAG_COLORS = {
    "run": "\033[36m",
    "steward": "\033[35m",
    "peer0": "\033[34m",
    "peer1": "\033[34m",
    "manage": "\033[36m",
    "set_holder": "\033[33m",
    "querier": "\033[33m",
    "pretreat": "\033[35m",
}

# Ring buffer of emitted lines, so the Manager control panel's "ms log"
# can show recent activity without a file handle.
_LOG_RING: deque[str] = deque(maxlen=200)

# Prompt-awareness for interactive control panels: while a live prompt is
# drawn, an incoming log line first erases the input line, prints, then
# restores the prompt so it is never buried under background output.
try:
    import readline as _readline
except ImportError:  # pragma: no cover
    _readline = None

_emit_lock = threading.Lock()
_prompt_active = False
_prompt_text = ""
_use_readline = False


def set_prompt(text: str | None, use_readline: bool = False) -> None:
    """Control-panel hook: mark whether a live prompt is currently drawn
    (readline-managed when ``use_readline``).  On a live log line the emitter
    erases the input line, prints the log, then restores the prompt via
    ``readline.redisplay()`` (or a plain redraw when stdin is not a tty)."""
    global _prompt_active, _prompt_text, _use_readline
    with _emit_lock:
        _prompt_active = text is not None
        _prompt_text = text or ""
        _use_readline = use_readline


def _fmt(level: str, tag: str, msg: str) -> str:
    ts = datetime.now().strftime("%H:%M:%S")
    if _COLOR:
        lc = _LEVEL_COLORS[level]
        tc = _TAG_COLORS.get(tag, "")
        return (
            f"{_DIM}{ts}{_RESET} {lc}{level:<5}{_RESET} "
            f"{tc}[{tag}]{_RESET} {msg}"
        )
    return f"{ts} {level:<5} [{tag}] {msg}"


def _emit(level: str, tag: str, msg: str, *, live: bool = True) -> None:
    line = _fmt(level, tag, msg)
    _LOG_RING.append(line)
    if not live:
        return
    with _emit_lock:
        if _prompt_active:
            sys.stdout.write("\r\x1b[K")   # erase the live input line
            sys.stdout.flush()
        print(line, flush=True)
        if _prompt_active:
            if _use_readline and _readline is not None:
                _readline.redisplay()
            else:
                print(_prompt_text, end="", flush=True)


def info(tag: str, msg: str) -> None:
    _emit("INFO", tag, msg)


def debug(tag: str, msg: str) -> None:
    """Detail log: captured in the ring buffer (visible via ``ms log``) but
    not printed live, so interactive control panels stay clean."""
    _emit("DEBUG", tag, msg, live=False)


def warn(tag: str, msg: str) -> None:
    _emit("WARN", tag, msg)


def error(tag: str, msg: str) -> None:
    _emit("ERROR", tag, msg)


def tail(n: int = 50) -> list[str]:
    """Return the last *n* emitted log lines (newest last)."""
    return list(_LOG_RING)[-n:]
