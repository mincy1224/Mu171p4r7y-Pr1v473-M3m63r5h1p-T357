"""Subprocess supervision.

* All commands are built by callers with ``sys.executable`` (the interpreter
  running the tests — i.e. the mpmt venv), never a hard-coded ``python3``.
* A single background reader drains stdout continuously so the pipe never
  fills (which would block the server and look like a protocol deadlock).
* The log is kept as a rolling buffer of lines plus a partial tail, so
  ``wait_log`` matches substrings across chunk boundaries and lines without a
  trailing newline (e.g. the c3-manager> prompt).
* Cleanup is graceful (SIGTERM → wait → SIGKILL) against the child's own
  process group, and only touches processes this object created.
"""
from __future__ import annotations

import os
import signal
import subprocess
import sys
import threading
import time
from collections import deque


class Proc:
    def __init__(self, cmd: list[str], *, cwd: str | None = None,
                 stdin: int | None = subprocess.PIPE, log_cap: int = 5000):
        self.cmd = list(cmd)
        self.proc = subprocess.Popen(
            self.cmd,
            stdin=stdin,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            cwd=cwd,
            start_new_session=True,
        )
        self.lines: deque[str] = deque(maxlen=log_cap)
        self.tail = ""
        self._reader = threading.Thread(target=self._drain, daemon=True)
        self._reader.start()

    def _drain(self) -> None:
        """Single reader: keep consuming stdout until EOF.  Never block the child."""
        try:
            while True:
                chunk = self.proc.stdout.read1(4096)
                if not chunk:
                    break
                self.tail += chunk.decode(errors="replace")
                while "\n" in self.tail:
                    line, self.tail = self.tail.split("\n", 1)
                    self.lines.append(line)
                if len(self.tail) > (1 << 20):
                    self.tail = self.tail[- (1 << 20):]
        except Exception:
            pass

    def wait_log(self, sub: str, timeout: float = 30.0) -> bool:
        """Wait until *sub* appears in the rolling log (across chunk/newline
        boundaries).  Returns False if the process exits first or times out."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if sub in self.tail or any(sub in ln for ln in self.lines):
                return True
            if self.proc.poll() is not None:
                return False
            time.sleep(0.05)
        return False

    def alive(self) -> bool:
        return self.proc.poll() is None

    def send_stdin(self, line: str) -> None:
        if self.proc.stdin is not None:
            self.proc.stdin.write((line + "\n").encode())
            self.proc.stdin.flush()

    def recent_log(self, n: int = 20) -> str:
        lines = list(self.lines)[-n:]
        if self.tail:
            lines.append(self.tail)
        return "\n".join(lines)

    def stop(self, grace: float = 5.0) -> None:
        """SIGTERM → wait(grace) → SIGKILL, against the whole process group."""
        if self.proc.poll() is not None:
            return
        try:
            os.killpg(self.proc.pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            pass
        try:
            self.proc.wait(timeout=grace)
            return
        except subprocess.TimeoutExpired:
            pass
        try:
            os.killpg(self.proc.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
        try:
            self.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.proc.kill()


def run_ok(cmd: list[str], *, cwd: str | None = None, timeout: float = 120.0) -> None:
    """Run *cmd* (already ``[sys.executable, ...]``) and raise on non-zero exit."""
    subprocess.run(list(cmd), cwd=cwd, check=True, timeout=timeout)
