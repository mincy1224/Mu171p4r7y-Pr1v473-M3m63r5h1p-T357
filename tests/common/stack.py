"""App-layer test stack.

Spawns only the services a test actually needs, per mode:
  full          — 3 real Agents (steward/peer0/peer1) + Manager
  manager_only  — 3 fake management listeners + Manager (control-plane only)

Readiness is never a fixed sleep:
  * Agents: all three are spawned first (their ring build is mutually
    dependent), then we wait for each one's "management listening" log + that
    it is still alive.  We never TCP-probe an Agent's management port — that
    connection belongs to the real Manager.
  * Manager: after ``ms start`` we run the *safe probe* — an unreserved QUERY
    /execute for a valid querier must return NOT_RESERVED and leave the
    operations DB completely unchanged.

``ms_sync()`` only sends the command (the log is diagnostic); completion is
always judged by the test's own oracle (DB / TreeCache metadata / root files /
real QUERY).  ``restart_without_pretreat()`` never goes through the fresh path.
"""
from __future__ import annotations

import atexit
import json
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.request

from . import fake_agent, process
from .config import db_path, load_config, load_users, manager_url
from .paths import APP_DIR, ensure_syspath

ROLES = ("steward", "peer0", "peer1")
MGR_ROLES = ("STEWARD", "PEER0", "PEER1")
SAFE_TIMEOUT = 40.0


class Stack:
    def __init__(self, mode: str = "full", fresh: bool = True) -> None:
        if mode not in ("full", "manager_only"):
            raise ValueError(f"unknown stack mode: {mode}")
        ensure_syspath()
        self.mode = mode
        self.fresh = fresh
        self._cfg = load_config()
        self._url = manager_url(self._cfg)
        self._db = db_path(self._cfg)
        self._procs: dict[str, process.Proc] = {}
        self._fakes: list[fake_agent.FakeAgent] = []
        self._mgr: process.Proc | None = None
        self._stopped = False

    def __enter__(self) -> "Stack":
        return self.start()

    def __exit__(self, *exc) -> bool:
        self.stop()
        return False

    def start(self) -> "Stack":
        """Bring up the services the mode needs.  Registers idempotent cleanup
        with atexit so a failing test never leaks processes."""
        atexit.register(self.stop)
        if self.fresh:
            process.run_ok([sys.executable, "run.py", "pretreat", "-f"],
                           cwd=str(APP_DIR))
        if self.mode == "manager_only":
            ip = self._cfg["steward"]["ip"]
            ports = [self._cfg[r]["mgmt_port"] for r in ROLES]
            self._fakes = fake_agent.start_fakes(ip, ports)
            deadline = time.monotonic() + 5.0
            while time.monotonic() < deadline:
                if all(f.ready() for f in self._fakes):
                    break
                time.sleep(0.05)
            if not all(f.ready() for f in self._fakes):
                raise RuntimeError("fake agent listeners did not come up")
        else:
            for role in ROLES:
                self._procs[role] = self._spawn([sys.executable, "-u", "run.py", role], role)
            for role in ROLES:
                proc = self._procs[role]
                if not proc.wait_log("management listening", SAFE_TIMEOUT):
                    raise RuntimeError(f"{role} never became ready; log:\n"
                                       + proc.recent_log(30))
                if not proc.alive():
                    raise RuntimeError(f"{role} exited during startup; log:\n"
                                       + proc.recent_log(30))
        self._mgr = self._spawn([sys.executable, "-u", "run.py", "manage_server"],
                                "manage_server")
        self._mgr.send_stdin("ms start")
        self._wait_manager_ready()
        return self

    def stop(self) -> None:
        """Idempotent cleanup (safe to call more than once)."""
        if self._stopped:
            return
        self._stopped = True
        self._cleanup()

    def _spawn(self, cmd: list[str], tag: str) -> process.Proc:
        proc = process.Proc(cmd, cwd=str(APP_DIR))
        self._procs.setdefault(tag, proc)
        return proc

    def _post(self, route: str, body: dict) -> dict:
        req = urllib.request.Request(
            self._url + route,
            data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                raw = r.read()
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as e:
            raw = e.read()
            try:
                return json.loads(raw)
            except Exception:
                return {"status": f"HTTP_{e.code}"}

    def _db_rows(self):
        with sqlite3.connect(self._db, timeout=10.0) as c:
            return c.execute(
                "SELECT op_id,user_id,prot_type,status,queue_pos "
                "FROM operations ORDER BY op_id").fetchall()

    def _wait_manager_ready(self) -> None:
        """Safe probe: unreserved QUERY for a valid querier → NOT_RESERVED,
        and the operations DB must be bit-identical before/after the probe."""
        queriers = load_users("querier")
        if not queriers:
            raise RuntimeError("no querier user available for the readiness probe")
        qid = next(iter(queriers))
        deadline = time.monotonic() + SAFE_TIMEOUT
        while time.monotonic() < deadline:
            try:
                before = self._db_rows()
            except sqlite3.OperationalError:
                time.sleep(0.2)
                continue
            try:
                resp = self._post("/execute", {"user_id": qid, "prot_type": "QUERY"})
            except Exception:
                time.sleep(0.2)
                continue
            try:
                after = self._db_rows()
            except sqlite3.OperationalError:
                time.sleep(0.2)
                continue
            if resp.get("status") == "NOT_RESERVED" and before == after:
                return
            time.sleep(0.2)
        raise RuntimeError("manager not ready (safe probe failed); log:\n"
                           + (self._mgr.recent_log(30) if self._mgr else "(no manager)"))

    def ms_sync(self, timeout: float = 15.0) -> None:
        """Send ``ms sync``; wait only for acceptance (SYNC queued log).
        Completion is judged by the caller's own oracle."""
        if self._mgr is None:
            raise RuntimeError("ms_sync before stack started")
        self._mgr.send_stdin("ms sync")
        if not self._mgr.wait_log("SYNC queued", timeout):
            raise RuntimeError("manager did not accept ms sync; log:\n"
                               + self._mgr.recent_log(30))

    def restart_without_pretreat(self) -> None:
        """Normal restart: Manager exits via ``ms exit`` (graceful), then the
        Agents are stopped and re-spawned.  NEVER runs the fresh path."""
        if self._mgr is not None:
            self._mgr.send_stdin("ms exit")
            try:
                self._mgr.proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                self._mgr.stop()
            self._mgr = None
        for role in ROLES:
            if role in self._procs:
                self._procs[role].stop()
        for f in self._fakes:
            f.stop()
        self._fakes = []
        self._procs = {}
        if self.mode == "manager_only":
            ip = self._cfg["steward"]["ip"]
            self._fakes = fake_agent.start_fakes(
                ip, [self._cfg[r]["mgmt_port"] for r in ROLES])
            time.sleep(0.3)
        else:
            for role in ROLES:
                self._procs[role] = self._spawn(
                    [sys.executable, "-u", "run.py", role], role)
            for role in ROLES:
                if not self._procs[role].wait_log("management listening", SAFE_TIMEOUT):
                    raise RuntimeError(f"{role} failed to restart; log:\n"
                                       + self._procs[role].recent_log(30))
        self._mgr = self._spawn([sys.executable, "-u", "run.py", "manage_server"],
                                "manage_server")
        self._mgr.send_stdin("ms start")
        self._wait_manager_ready()

    def _cleanup(self) -> None:
        if self._mgr is not None:
            self._mgr.send_stdin("ms exit")
            try:
                self._mgr.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self._mgr.stop()
            self._mgr = None
        for f in self._fakes:
            f.stop()
        self._fakes = []
        for proc in list(self._procs.values()):
            proc.stop()
        self._procs = {}
