#!/usr/bin/env python3
"""
Full-lifecycle E2E: fresh → JOIN → SYNC → UPDATE → SYNC → non-last QUIT →
SYNC → priority SYNC → last-holder empty → restart without pretreat → QUERY
(146 checks).

Self-contained: the Stack auto-starts everything, and every `ms sync` plus the
mid-test restart are driven automatically.

Run:  python3 tests/app/test_lifecycle_e2e.py

It verifies:
  - JOIN / UPDATE / QUIT do NOT auto-merge.
  - root_this.mpmtrvp/root_nxt.mpmtrvp stay unchanged before sync.
  - meta.json dirty_leaf becomes non-empty after mutation.
  - manual ms sync changes/publishes root and clears dirty_leaf.
  - real QUERY is correct after sync.
  - priority: with A BUSY and B/C QUEUED, ms sync runs after A but before B/C.
"""

from __future__ import annotations

import hashlib
import json
import os
import random
import secrets
import sqlite3
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Any

import numpy as np

HERE = Path(__file__).resolve()

def locate_app() -> Path:
    for p in (
        Path.cwd() / "application",
        HERE.parent / "application",
        HERE.parent.parent / "application",
        HERE.parent.parent.parent / "application",
    ):
        if (p / "config.json").is_file():
            return p.resolve()
    raise SystemExit("cannot locate application/config.json; run from project root")

APP = locate_app()
ROOT = APP.parent
sys.path[:0] = [str(APP), str(ROOT)]

from _c3_io import read_json
import mpmt
import os as _os
_t = _os.path.dirname(_os.path.abspath(__file__))
while _t and not _os.path.isdir(_os.path.join(_t, "common")):
    _t = _os.path.dirname(_t)
sys.path.insert(0, _t)
from common.stack import Stack

_ST: Stack | None = None
CFG = read_json(str(APP / "config.json"))
PRE = read_json(str(APP / "pretreat" / "pre.json"))
MGR = CFG["manage_server"]

BASE = f"http://{MGR['server_ip']}:{MGR['http_port']}"
HTTP_TIMEOUT = max(10.0, float(CFG.get("timeout", 30.0)))
CONNECT_TIMEOUT = float(CFG.get("connect_timeout", 5.0))
PROTOCOL_TIMEOUT = max(180.0, float(CFG.get("protocol_timeout", 180.0)))

STORAGE = APP / CFG["storage_root_dir"]
DB_PATH = STORAGE / "manage_server" / MGR["db_name"]

ROLES = ("steward", "peer0", "peer1")
LIVE = ("RESERVED", "QUEUED", "ACTIVE", "BUSY")
TERMINAL = ("DONE", "FAILED", "REMOVED")
ROUTE = {
    "JOIN": "/reserve_join",
    "UPDATE": "/reserve_update",
    "QUIT": "/reserve_quit",
    "QUERY": "/reserve_query",
}

PASS = 0
FAIL = 0
T0 = time.monotonic()

def section(name: str) -> None:
    print(f"\n{'='*18} {name} {'='*18}", flush=True)

def check(name: str, ok: bool, detail: str = "") -> bool:
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  PASS {name}", flush=True)
    else:
        FAIL += 1
        print(f"  FAIL {name}: {detail}", flush=True)
    return ok

def abort(msg: str):
    print(f"\nABORT: {msg}", flush=True)
    print("If MPC had already started, follow the project's uncertain-failure recovery policy.")
    raise SystemExit(2)


def post(route: str, body: dict) -> dict:
    req = urllib.request.Request(
        BASE + route,
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as r:
            raw = r.read()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return json.loads(raw)
        except Exception:
            return {"status": f"HTTP_{e.code}", "raw": raw.decode(errors="replace")}
    except Exception as e:
        abort(f"HTTP {route} failed: {type(e).__name__}: {e}; did you run `ms start`?")

def db():
    if not DB_PATH.is_file():
        abort(f"Manager DB missing: {DB_PATH}")
    c = sqlite3.connect(DB_PATH, timeout=10.0)
    c.row_factory = sqlite3.Row
    return c

def op_by_id(op_id: int):
    with db() as c:
        r = c.execute("SELECT * FROM operations WHERE op_id=?", (op_id,)).fetchone()
    return dict(r) if r else None

def live_ops():
    with db() as c:
        return [dict(r) for r in c.execute(
            "SELECT * FROM operations "
            "WHERE status IN ('RESERVED','QUEUED','ACTIVE','BUSY') ORDER BY op_id"
        ).fetchall()]

def user_status(uid: str):
    with db() as c:
        r = c.execute("SELECT status FROM users WHERE user_id=?", (uid,)).fetchone()
    return r["status"] if r else None

def token_rows(uid: str):
    with db() as c:
        return [dict(r) for r in c.execute(
            "SELECT agent_role,token FROM user_agent_tokens "
            "WHERE user_id=? ORDER BY agent_role", (uid,)
        ).fetchall()]

def has_three_tokens(uid: str) -> bool:
    return {
        r["agent_role"] for r in token_rows(uid) if r.get("token")
    } == {"STEWARD", "PEER0", "PEER1"}

def wait_terminal(op_id: int, timeout: float = PROTOCOL_TIMEOUT) -> dict:
    end = time.monotonic() + timeout
    last = None
    while time.monotonic() < end:
        last = op_by_id(op_id)
        if last and last["status"] in TERMINAL:
            return {"status": last["status"], "_op_id": op_id,
                    "reason": last.get("error_code")}
        time.sleep(0.10)
    return {"status": "TIMEOUT", "_op_id": op_id,
            "db_status": last["status"] if last else None}

def reserve(uid: str, prot: str):
    r = post(ROUTE[prot], {"user_id": uid})
    check(f"reserve {prot} {uid[:10]}…", r.get("status") == "SUCCESSFUL", repr(r))
    if r.get("status") != "SUCCESSFUL":
        abort(f"reserve {prot} failed: {r}")
    return r

def claim(uid: str, prot: str, op_id: int | None = None):
    b = {"user_id": uid, "prot_type": prot}
    if op_id is not None:
        b["op_id"] = op_id
    return post("/execute", b)


def users(kind: str):
    name = "set_holder_users.json" if kind == "holder" else "querier_users.json"
    return list(read_json(str(APP / "pretreat" / name)).keys())

def load_set(uid: str) -> list[bytes]:
    p = STORAGE / f"set_holder_{uid}" / "set.npy"
    if not p.is_file():
        abort(f"missing dataset: {p}")
    data = [str(x).encode() for x in np.load(str(p))]
    if not data:
        abort(f"empty dataset: {p}")
    return data


def sha(path: Path):
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()

def meta(role: str):
    d = STORAGE / role
    p = d / "meta.json"

    if p.is_file():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception as e:
            abort(f"{role} meta.json malformed: {e}")

    if d.is_dir():
        cache_files = [
            x for x in d.iterdir()
            if x.is_file()
        ]
        if cache_files:
            abort(
                f"{role} meta.json missing but storage is not empty: "
                f"{[x.name for x in cache_files[:20]]}"
            )

    return {
        "state": "valid",
        "leaf_num": 0,
        "phi_f": {},
        "phi_r": {},
        "dirty_leaf": [],
        "_logical_fresh_fallback": True,
    }

def root_sig():
    ans = {}
    for role in ROLES:
        ans[role] = {}
        for n in ("root_this.mpmtrvp", "root_nxt.mpmtrvp"):
            p = STORAGE / role / n
            ans[role][n] = (
                {"exists": True, "size": p.stat().st_size, "sha256": sha(p)}
                if p.is_file() else {"exists": False}
            )
    return ans

def roots_absent(sig) -> bool:
    return all(not sig[r][n]["exists"] for r in ROLES
               for n in ("root_this.mpmtrvp", "root_nxt.mpmtrvp"))

def roots_exist(sig) -> bool:
    return all(sig[r][n]["exists"] for r in ROLES
               for n in ("root_this.mpmtrvp", "root_nxt.mpmtrvp"))

def all_clean() -> bool:
    return all(not meta(r).get("dirty_leaf") for r in ROLES)

def all_dirty() -> bool:
    return all(bool(meta(r).get("dirty_leaf")) for r in ROLES)

def check_dirty(label: str):
    d = {r: meta(r).get("dirty_leaf") for r in ROLES}
    check(f"{label}: all 3 dirty_leaf non-empty",
          all(bool(v) for v in d.values()), repr(d))

def check_clean(label: str):
    d = {r: meta(r).get("dirty_leaf") for r in ROLES}
    check(f"{label}: all 3 dirty_leaf cleared",
          all(not v for v in d.values()), repr(d))

def wait_sync(before, label: str):
    end = time.monotonic() + PROTOCOL_TIMEOUT
    while time.monotonic() < end:
        now = root_sig()
        if all_clean() and now != before:
            print(f"  observed {label} completion from root/meta", flush=True)
            return now
        time.sleep(0.10)
    abort(f"{label} not observed: clean={all_clean()} root_changed={root_sig()!=before}")

def manual_sync(before, label: str):
    print(f"\n>>> auto ms sync ({label})", flush=True)
    _ST.ms_sync()
    return wait_sync(before, label)


def setholder_mpc(agents: dict, data: list[bytes]):
    sh = mpmt.SetHolder(
        set_size=PRE["set_size"],
        fpr_mantissa=PRE["fpr_mantissa"],
        fpr_exponent=PRE["fpr_exponent"],
    )
    ch = {}
    try:
        for role in ("STEWARD", "PEER0", "PEER1"):
            x = agents[role]
            ch[role] = mpmt.Channel.connect(
                x["ip"], int(x["port"]), timeout=CONNECT_TIMEOUT
            )
        sh.share_bf(
            set=data,
            hash_seed_list=[bytes.fromhex(h) for h in PRE["hash_seed_list"]],
            ch_steward=ch["STEWARD"], ch_peer0=ch["PEER0"], ch_peer1=ch["PEER1"],
        )
    finally:
        ch.clear()

def querier_mpc(agents: dict, elem: bytes) -> int:
    q = mpmt.Querier(
        set_size=PRE["set_size"],
        fpr_mantissa=PRE["fpr_mantissa"],
        fpr_exponent=PRE["fpr_exponent"],
    )
    ch = {}
    try:
        for role in ("STEWARD", "PEER0", "PEER1"):
            x = agents[role]
            ch[role] = mpmt.Channel.connect(
                x["ip"], int(x["port"]), timeout=CONNECT_TIMEOUT
            )
        return int(q.query(
            element=elem,
            ch_steward=ch["STEWARD"], ch_peer0=ch["PEER0"], ch_peer1=ch["PEER1"],
        ))
    finally:
        ch.clear()


def execute_client(uid: str, prot: str, fn, args=(), op_id=None):
    internal = op_id
    while True:
        r = claim(uid, prot, internal)
        if r.get("_op_id") is not None:
            internal = int(r["_op_id"])
        st = r.get("status")
        if st == "WAITING":
            time.sleep(float(r.get("retry_after", 0.10)))
            continue
        if st == "BUSY":
            agents = r.get("agents")
            if not agents:
                abort(f"{prot}: BUSY without agents in single-client path: {r}")
            t = time.monotonic()
            result = fn(agents, *args)
            print(f"  [MPC] {prot} op={internal} client-side "
                  f"{time.monotonic()-t:.3f}s", flush=True)
            return wait_terminal(internal), internal, result
        if st in TERMINAL:
            return r, internal, None
        abort(f"{prot}: unexpected /execute response: {r}")

def join(uid, data, label):
    reserve(uid, "JOIN")
    term, opid, _ = execute_client(uid, "JOIN", setholder_mpc, (data,))
    check(f"{label}: JOIN DONE", term.get("status") == "DONE", repr(term))
    check(f"{label}: JOINED", user_status(uid) == "JOINED", str(user_status(uid)))
    check(f"{label}: 3 tokens", has_three_tokens(uid), repr(token_rows(uid)))
    if term.get("status") != "DONE":
        abort(f"{label} JOIN failed")
    return opid

def update(uid, data, label):
    reserve(uid, "UPDATE")
    term, opid, _ = execute_client(uid, "UPDATE", setholder_mpc, (data,))
    check(f"{label}: UPDATE DONE", term.get("status") == "DONE", repr(term))
    check(f"{label}: remains JOINED", user_status(uid) == "JOINED", str(user_status(uid)))
    if term.get("status") != "DONE":
        abort(f"{label} UPDATE failed")
    return opid

def quit_holder(uid, label):
    reserve(uid, "QUIT")
    opid = None
    while True:
        r = claim(uid, "QUIT", opid)
        if r.get("_op_id") is not None:
            opid = int(r["_op_id"])
        if r.get("status") == "WAITING":
            time.sleep(float(r.get("retry_after", 0.10)))
            continue
        if r.get("status") == "BUSY":
            term = wait_terminal(opid)
            break
        if r.get("status") in TERMINAL:
            term = r
            break
        abort(f"QUIT unexpected response: {r}")
    check(f"{label}: QUIT DONE", term.get("status") == "DONE", repr(term))
    check(f"{label}: QUITTED", user_status(uid) == "QUITTED", str(user_status(uid)))
    check(f"{label}: tokens cleared", not has_three_tokens(uid), repr(token_rows(uid)))
    if term.get("status") != "DONE":
        abort(f"{label} QUIT failed")
    return opid

def query_true(qid, elem, label):
    reserve(qid, "QUERY")
    term, opid, result = execute_client(qid, "QUERY", querier_mpc, (elem,))
    check(f"{label}: QUERY DONE", term.get("status") == "DONE", repr(term))
    check(f"{label}: membership=true", result == 1, f"result={result}")
    if term.get("status") != "DONE" or result != 1:
        abort(f"{label}: present query failed")
    return opid


def query_expect(qid, elem, expected: int, label: str):
    """Run a real MPC QUERY and score the expected membership bit."""
    reserve(qid, "QUERY")
    term, opid, result = execute_client(qid, "QUERY", querier_mpc, (elem,))
    check(f"{label}: QUERY DONE", term.get("status") == "DONE", repr(term))
    check(f"{label}: membership={expected}", result == expected, f"result={result}")
    if term.get("status") != "DONE":
        abort(f"{label}: QUERY protocol did not finish DONE")
    return opid, result


def operation_rows():
    with db() as c:
        return [
            dict(r)
            for r in c.execute(
                "SELECT * FROM operations ORDER BY op_id"
            ).fetchall()
        ]


def max_op_id() -> int:
    with db() as c:
        r = c.execute(
            "SELECT COALESCE(MAX(op_id), 0) AS m FROM operations"
        ).fetchone()
    return int(r["m"])


def all_holder_states(holder_ids):
    return {uid: user_status(uid) for uid in holder_ids}


def all_holder_tokens(holder_ids):
    return {uid: token_rows(uid) for uid in holder_ids}


def historical_empty_meta_ok():
    states = {r: meta(r) for r in ROLES}
    ok = True
    for r, m in states.items():
        ok = ok and m.get("leaf_num") == 0
        ok = ok and not m.get("dirty_leaf")
        ok = ok and m.get("phi_f") == {}
        ok = ok and m.get("phi_r") == {}
        ok = ok and not m.get("_logical_fresh_fallback", False)
    return ok, states


def manual_empty_sync(before_root, label: str):
    """
    Empty-tree SYNC completion is defined by the new canonical no-tree state:
      leaf_num=0, maps empty, dirty clear, and both persisted roots absent.
    """
    print(f">>> auto empty-tree ms sync ({label})", flush=True)
    _ST.ms_sync()

    end = time.monotonic() + PROTOCOL_TIMEOUT
    while time.monotonic() < end:
        ok_meta, _ = historical_empty_meta_ok()
        now = root_sig()
        if ok_meta and roots_absent(now):
            print(f"  observed {label}: canonical no-tree state", flush=True)
            return now
        time.sleep(0.10)

    ok_meta, states = historical_empty_meta_ok()
    abort(
        f"{label} did not reach canonical no-tree state: "
        f"meta_ok={ok_meta} roots_absent={roots_absent(root_sig())} "
        f"meta={states}"
    )


def wait_manager_after_restart(qid: str, timeout: float = PROTOCOL_TIMEOUT):
    """
    Wait until the restarted HTTP Manager is reachable, then perform a safe
    execute-without-reserve probe. It must not create an operation row.
    """
    end = time.monotonic() + timeout
    last = None

    while time.monotonic() < end:
        try:
            req = urllib.request.Request(
                BASE + "/execute",
                data=json.dumps({
                    "user_id": qid,
                    "prot_type": "QUERY",
                }).encode(),
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=3.0) as r:
                raw = r.read()
                last = json.loads(raw) if raw else {}
            return last
        except urllib.error.HTTPError as e:
            raw = e.read()
            try:
                last = json.loads(raw)
                return last
            except Exception:
                last = {"status": f"HTTP_{e.code}"}
        except Exception as e:
            last = {"error": f"{type(e).__name__}: {e}"}
            time.sleep(0.25)

    abort(f"restarted Manager not reachable: last={last}")




def begin_held_update(uid):
    reserve(uid, "UPDATE")
    r = claim(uid, "UPDATE")
    if not (r.get("status") == "BUSY" and r.get("agents")
            and r.get("_op_id") is not None):
        abort(f"held UPDATE expected BUSY+agents, got {r}")
    opid = int(r["_op_id"])
    row = op_by_id(opid)
    check("priority A UPDATE is BUSY",
          row is not None and row["status"] == "BUSY", repr(row))
    return opid, r["agents"]

def queue_query(qid, label):
    reserve(qid, "QUERY")
    r = claim(qid, "QUERY")
    if r.get("_op_id") is None:
        abort(f"{label}: missing _op_id: {r}")
    opid = int(r["_op_id"])
    check(f"{label}: WAITING", r.get("status") == "WAITING", repr(r))
    row = op_by_id(opid)
    check(f"{label}: DB QUEUED",
          row is not None and row["status"] == "QUEUED", repr(row))
    return opid

def wait_query_agents_after_sync(qid, opid, old_root, label):
    end = time.monotonic() + PROTOCOL_TIMEOUT
    saw_waiting = False
    while time.monotonic() < end:
        r = claim(qid, "QUERY", opid)
        if r.get("status") == "WAITING":
            saw_waiting = True
            time.sleep(0.03)
            continue
        if r.get("status") == "BUSY" and r.get("agents"):
            changed = root_sig() != old_root
            clean = all_clean()
            check(f"{label}: SYNC completed before QUERY got agents",
                  changed and clean,
                  f"root_changed={changed} clean={clean}")
            check(f"{label}: scheduler exposed WAITING/SYNC ordering",
                  saw_waiting or (changed and clean),
                  "no WAITING observed and root not synced")
            return r["agents"]
        abort(f"{label}: unexpected response: {r}")
    abort(f"{label}: timeout")

def main():
    global _ST, PRE
    seed = int(os.environ.get("TEST_SEED", "42"))
    random.seed(seed)

    _ST = Stack(mode="full", fresh=True)
    _ST.start()
    PRE = read_json(str(APP / "pretreat" / "pre.json"))

    print("=" * 88)
    print("             C3 lifecycle E2E — fresh → sync → empty → restart")
    print("=" * 88)
    print(f"manager={BASE}")
    print(f"project={ROOT}")
    print(f"seed={seed}")
    print()
    print("STARTING REQUIREMENTS:")
    print("  1. fresh pretreat")
    print("  2. steward / peer0 / peer1 running")
    print("  3. manage_server running")
    print("  4. Manager CLI already executed: ms start")
    print("  5. DO NOT type ms sync until this script prompts you")
    print()

    section("0. fresh preflight")

    task = read_json(str(APP / "task_status.json"))
    check("task active", task.get("status") == "active", repr(task))

    hs, qs = users("holder"), users("querier")
    if len(hs) < 2 or len(qs) < 2:
        abort("requires >=2 holders and >=2 queriers")

    h0, h1 = hs[:2]
    q0, q1 = qs[:2]

    current_live = live_ops()
    check("no live business ops", not current_live, repr(current_live))
    if current_live:
        abort("fresh run requires no live business operation")

    starting_states = {h0: user_status(h0), h1: user_status(h1)}
    check(
        "H0/H1 start NOT_JOINED",
        all(v == "NOT_JOINED" for v in starting_states.values()),
        repr(starting_states),
    )
    if not all(v == "NOT_JOINED" for v in starting_states.values()):
        abort("fresh pretreat required")

    ds0, ds1 = load_set(h0), load_set(h1)
    if len(ds0) < 2 or len(ds1) < 2:
        abort("each selected holder dataset must contain >=2 elements")

    probe0 = ds0[1]
    probe1 = ds1[1]

    fresh_root = root_sig()
    check("fresh root files absent", roots_absent(fresh_root), repr(fresh_root))

    fresh_meta = {r: meta(r) for r in ROLES}
    check(
        "fresh logical leaf_num=0",
        all(fresh_meta[r].get("leaf_num") == 0 for r in ROLES),
        repr({r: fresh_meta[r].get("leaf_num") for r in ROLES}),
    )
    check(
        "fresh logical dirty_leaf empty",
        all(not fresh_meta[r].get("dirty_leaf") for r in ROLES),
        repr({r: fresh_meta[r].get("dirty_leaf") for r in ROLES}),
    )
    if not roots_absent(fresh_root):
        abort("fresh TreeCache unexpectedly already has published roots")

    print(f"  H0={h0}")
    print(f"  H1={h1}")
    print(f"  Q0={q0}")
    print(f"  Q1={q1}")

    section("1. JOIN H0 + H1 — no automatic merge")

    join(h0, ds0, "H0")
    check(
        "JOIN H0 root unchanged",
        root_sig() == fresh_root,
        "JOIN H0 published root before ms sync",
    )
    check_dirty("after JOIN H0")

    join(h1, ds1, "H1")
    join_dirty_root = root_sig()
    check(
        "JOIN H1 root still unchanged",
        join_dirty_root == fresh_root,
        "JOIN H1 published root before ms sync",
    )
    check_dirty("after JOIN H0+H1")

    leaves = {r: meta(r).get("leaf_num") for r in ROLES}
    check(
        "leaf_num=2 before first sync",
        all(v == 2 for v in leaves.values()),
        repr(leaves),
    )

    section("2. manual SYNC #1 — publish H0 + H1")

    joined_root = manual_sync(join_dirty_root, "SYNC #1")
    check("SYNC #1 created root files", roots_exist(joined_root), repr(joined_root))
    check_clean("after SYNC #1")

    query_true(q0, probe0, "H0 member after SYNC #1")
    query_true(q1, probe1, "H1 member after SYNC #1")
    check(
        "QUERY leaves published root unchanged",
        root_sig() == joined_root,
        "QUERY mutated root files",
    )

    section("3. UPDATE H0 — no automatic merge")

    ds0u = list(ds0)
    marker0 = f"c3-ultimate-h0-{seed}-{uuid.uuid4().hex}".encode()
    ds0u[0] = marker0

    before_h0_update = root_sig()
    update(h0, ds0u, "H0")
    h0_update_dirty_root = root_sig()

    check(
        "UPDATE H0 root unchanged before sync",
        h0_update_dirty_root == before_h0_update,
        "UPDATE H0 auto-merged",
    )
    check_dirty("after UPDATE H0")

    section("4. manual SYNC #2 — publish H0 UPDATE")

    h0_updated_root = manual_sync(h0_update_dirty_root, "SYNC #2")
    check(
        "SYNC #2 changed root",
        h0_updated_root != h0_update_dirty_root,
        "root unchanged",
    )
    check_clean("after SYNC #2")

    query_true(q0, marker0, "new H0 UPDATE marker")
    query_true(q1, probe1, "H1 survives H0 UPDATE")

    section("5. QUIT H0 — no automatic merge")

    before_h0_quit = root_sig()
    quit_holder(h0, "H0")
    h0_quit_dirty_root = root_sig()

    check(
        "QUIT H0 root unchanged before sync",
        h0_quit_dirty_root == before_h0_quit,
        "QUIT H0 auto-published root",
    )
    check_dirty("after non-last QUIT H0")

    leaves = {r: meta(r).get("leaf_num") for r in ROLES}
    check(
        "leaf_num=1 after H0 QUIT",
        all(v == 1 for v in leaves.values()),
        repr(leaves),
    )

    section("6. manual SYNC #3 — publish H0 QUIT")

    h0_quit_root = manual_sync(h0_quit_dirty_root, "SYNC #3")
    check(
        "SYNC #3 changed root",
        h0_quit_root != h0_quit_dirty_root,
        "root unchanged",
    )
    check_clean("after SYNC #3")

    query_true(q0, probe1, "H1 survives H0 QUIT")
    query_expect(q0, probe0, 0, "former H0 original member removed")
    query_expect(q0, marker0, 0, "former H0 UPDATE marker removed")

    section("7. priority — A BUSY, B/C QUEUED, then ms sync")

    priority_old_root = root_sig()

    ds1u = list(ds1)
    marker1 = f"c3-ultimate-priority-h1-{seed}-{uuid.uuid4().hex}".encode()
    ds1u[0] = marker1

    op_a, agents_a = begin_held_update(h1)

    op_b = queue_query(q0, "priority B QUERY")
    op_c = queue_query(q1, "priority C QUERY")

    a, b, c = op_by_id(op_a), op_by_id(op_b), op_by_id(op_c)
    check(
        "setup A=BUSY B=QUEUED C=QUEUED",
        a and a["status"] == "BUSY"
        and b and b["status"] == "QUEUED"
        and c and c["status"] == "QUEUED",
        f"A={a} B={b} C={c}",
    )

    if (
        b and c
        and b.get("queue_pos") is not None
        and c.get("queue_pos") is not None
    ):
        check(
            "business FIFO B before C",
            b["queue_pos"] < c["queue_pos"],
            f"B={b['queue_pos']} C={c['queue_pos']}",
        )

    check(
        "held UPDATE has not changed root",
        root_sig() == priority_old_root,
        "root changed before held UPDATE client sent BF",
    )
    check_clean("while held UPDATE is BUSY")

    print("\n>>> PRIORITY MANUAL ACTION")
    print(">>> Current:")
    print(">>>     A = UPDATE H1 : BUSY")
    print(">>>     B = QUERY Q0  : QUEUED")
    print(">>>     C = QUERY Q1  : QUEUED")
    print(">>> In Manager CLI type:")
    print(">>>")
    print(">>>     ms sync")
    print(">>>")
    print(">>> auto ms sync queued (after A, before B/C)", flush=True)
    _ST.ms_sync()

    check(
        "SYNC did not execute concurrently with BUSY A",
        root_sig() == priority_old_root and all_clean(),
        "root/dirty changed while A was still BUSY",
    )

    print("  releasing held UPDATE A...", flush=True)
    t = time.monotonic()
    setholder_mpc(agents_a, ds1u)
    print(
        f"  [MPC] held UPDATE op={op_a} client-side "
        f"{time.monotonic()-t:.3f}s",
        flush=True,
    )

    term_a = wait_terminal(op_a)
    check(
        "priority A UPDATE DONE",
        term_a.get("status") == "DONE",
        repr(term_a),
    )
    if term_a.get("status") != "DONE":
        abort("priority UPDATE A failed")

    agents_b = wait_query_agents_after_sync(
        q0, op_b, priority_old_root, "priority B"
    )
    result_b = querier_mpc(agents_b, marker1)
    term_b = wait_terminal(op_b)

    check(
        "priority B QUERY DONE",
        term_b.get("status") == "DONE",
        repr(term_b),
    )
    check(
        "priority B sees new marker (proves A -> SYNC -> B)",
        result_b == 1,
        f"result={result_b}",
    )

    end = time.monotonic() + PROTOCOL_TIMEOUT
    agents_c = None
    while time.monotonic() < end:
        r = claim(q1, "QUERY", op_c)
        if r.get("status") == "WAITING":
            time.sleep(0.03)
            continue
        if r.get("status") == "BUSY" and r.get("agents"):
            agents_c = r["agents"]
            break
        abort(f"priority C unexpected response: {r}")

    if agents_c is None:
        abort("priority C timeout")

    result_c = querier_mpc(agents_c, probe1)
    term_c = wait_terminal(op_c)

    check(
        "priority C QUERY DONE",
        term_c.get("status") == "DONE",
        repr(term_c),
    )
    check(
        "priority C sees surviving H1 member",
        result_c == 1,
        f"result={result_c}",
    )
    check(
        "priority SYNC changed root",
        root_sig() != priority_old_root,
        "root unchanged",
    )
    check_clean("after priority SYNC")

    section("8. last-leaf precondition")

    check(
        "H0 is QUITTED",
        user_status(h0) == "QUITTED",
        str(user_status(h0)),
    )
    check(
        "H0 tokens cleared",
        not has_three_tokens(h0),
        repr(token_rows(h0)),
    )
    check(
        "H1 is the only JOINED holder",
        user_status(h1) == "JOINED"
        and all(
            user_status(uid) != "JOINED"
            for uid in hs
            if uid != h1
        ),
        repr(all_holder_states(hs)),
    )
    check(
        "H1 has 3 tokens",
        has_three_tokens(h1),
        repr(token_rows(h1)),
    )

    leaves = {r: meta(r).get("leaf_num") for r in ROLES}
    check(
        "all 3 TreeCaches have exactly one leaf",
        all(v == 1 for v in leaves.values()),
        repr(leaves),
    )
    check_clean("before last-holder QUIT")
    check("published root exists before last QUIT", roots_exist(root_sig()), repr(root_sig()))

    query_true(q0, probe1, "known H1 member before last QUIT")
    query_true(q0, marker1, "latest H1 UPDATE marker before last QUIT")

    section("9. QUIT last H1 — transition to leaf_num=0, no auto publish")

    root_before_last_quit = root_sig()
    quit_holder(h1, "last H1")

    root_after_last_quit = root_sig()
    check(
        "last QUIT leaves published root unchanged before sync",
        root_after_last_quit == root_before_last_quit,
        "last QUIT changed/deleted root before ms sync",
    )

    last_quit_meta = {r: meta(r) for r in ROLES}
    check(
        "after last QUIT leaf_num=0 on all 3",
        all(last_quit_meta[r].get("leaf_num") == 0 for r in ROLES),
        repr({r: last_quit_meta[r].get("leaf_num") for r in ROLES}),
    )
    check(
        "after last QUIT phi_f/phi_r empty",
        all(
            last_quit_meta[r].get("phi_f") == {}
            and last_quit_meta[r].get("phi_r") == {}
            for r in ROLES
        ),
        repr({
            r: (
                last_quit_meta[r].get("phi_f"),
                last_quit_meta[r].get("phi_r"),
            )
            for r in ROLES
        }),
    )
    check(
        "after last QUIT old published root files still exist until sync",
        roots_exist(root_after_last_quit),
        repr(root_after_last_quit),
    )
    print(
        "  NOTE: dirty_leaf may already be [] at leaf_num=0; "
        "root unchanged is the important pre-sync publication check."
    )

    section("10. manual SYNC #5 — publish canonical no-tree")

    no_tree_root = manual_empty_sync(root_after_last_quit, "EMPTY SYNC")

    check(
        "empty SYNC removes root_this/root_nxt on all 3 Agents",
        roots_absent(no_tree_root),
        repr(no_tree_root),
    )

    empty_ok, empty_meta = historical_empty_meta_ok()
    check(
        "historical empty meta canonical on all 3 Agents",
        empty_ok,
        repr(empty_meta),
    )

    random_absent = (
        b"c3-ultimate-no-tree-random-"
        + secrets.token_hex(24).encode()
    )

    query_expect(q0, probe1, 0, "former H1 original member after empty SYNC")
    query_expect(q0, marker1, 0, "former H1 UPDATE marker after empty SYNC")
    query_expect(q1, random_absent, 0, "random element in no-tree state")

    section("11. pre-restart persistence snapshot")

    check("no live business ops before restart", not live_ops(), repr(live_ops()))

    task = read_json(str(APP / "task_status.json"))
    check("task remains active before restart", task.get("status") == "active", repr(task))

    holder_states_before_restart = all_holder_states(hs)
    holder_tokens_before_restart = all_holder_tokens(hs)

    check(
        "all holders non-JOINED before restart",
        all(v != "JOINED" for v in holder_states_before_restart.values()),
        repr(holder_states_before_restart),
    )
    check(
        "all holder tokens cleared before restart",
        all(not rows for rows in holder_tokens_before_restart.values()),
        repr(holder_tokens_before_restart),
    )

    pre_restart_ops = operation_rows()
    pre_restart_max = max_op_id()
    pre_restart_root = root_sig()
    pre_restart_meta = {r: meta(r) for r in ROLES}

    check(
        "pre-restart root remains absent",
        roots_absent(pre_restart_root),
        repr(pre_restart_root),
    )

    section("12. NORMAL RESTART — DO NOT PRETREAT")

    print(">>> auto restart without pretreat ...", flush=True)
    _ST.restart_without_pretreat()

    rows_before_probe = operation_rows()
    probe_resp = wait_manager_after_restart(q0)
    check(
        "restarted Manager safe probe returns NOT_RESERVED",
        probe_resp.get("status") == "NOT_RESERVED",
        repr(probe_resp),
    )
    check(
        "safe restart probe did not mutate operation history",
        operation_rows() == rows_before_probe,
        "operation rows changed after NOT_RESERVED probe",
    )

    section("13. restarted historical no-tree state")

    check(
        "task active after restart",
        read_json(str(APP / "task_status.json")).get("status") == "active",
        repr(read_json(str(APP / "task_status.json"))),
    )
    check(
        "no live business ops immediately after restart",
        not live_ops(),
        repr(live_ops()),
    )
    check(
        "pre-restart operation history preserved exactly",
        operation_rows() == pre_restart_ops,
        "history changed across normal restart",
    )
    check(
        "max op_id preserved across restart before new work",
        max_op_id() == pre_restart_max,
        f"before={pre_restart_max} after={max_op_id()}",
    )
    check(
        "holder business states preserved across restart",
        all_holder_states(hs) == holder_states_before_restart,
        f"before={holder_states_before_restart} after={all_holder_states(hs)}",
    )
    check(
        "holder token state preserved across restart",
        all_holder_tokens(hs) == holder_tokens_before_restart,
        f"before={holder_tokens_before_restart} after={all_holder_tokens(hs)}",
    )

    restart_root = root_sig()
    restart_empty_ok, restart_meta = historical_empty_meta_ok()

    check(
        "restarted no-tree has no published root files",
        roots_absent(restart_root),
        repr(restart_root),
    )
    check(
        "restarted historical-empty meta remains canonical",
        restart_empty_ok,
        repr(restart_meta),
    )

    section("14. real QUERY after restart — still no-tree")

    op_r1, res_r1 = query_expect(
        q0, probe1, 0,
        "restart former H1 original member",
    )
    op_r2, res_r2 = query_expect(
        q0, marker1, 0,
        "restart former H1 UPDATE marker",
    )
    op_r3, res_r3 = query_expect(
        q1, random_absent, 0,
        "restart random element",
    )

    check(
        "all restart QUERY generations are newer than pre-restart max_op_id",
        all(op > pre_restart_max for op in (op_r1, op_r2, op_r3)),
        f"pre_max={pre_restart_max}, new={[op_r1, op_r2, op_r3]}",
    )

    new_rows = [
        row for row in operation_rows()
        if int(row["op_id"]) > pre_restart_max
    ]
    check(
        "exactly 3 post-restart operation generations appended",
        len(new_rows) == 3,
        repr(new_rows),
    )
    check(
        "all appended restart generations are QUERY/DONE",
        len(new_rows) == 3
        and all(
            row.get("prot_type") == "QUERY"
            and row.get("status") == "DONE"
            for row in new_rows
        ),
        repr(new_rows),
    )

    section("15. FINAL — global invariants")

    check("no live business ops at end", not live_ops(), repr(live_ops()))

    final_task = read_json(str(APP / "task_status.json"))
    check("task remains active at end", final_task.get("status") == "active", repr(final_task))

    final_holder_states = all_holder_states(hs)
    final_holder_tokens = all_holder_tokens(hs)

    check(
        "all holders remain non-JOINED",
        all(v != "JOINED" for v in final_holder_states.values()),
        repr(final_holder_states),
    )
    check(
        "all holder tokens remain cleared",
        all(not rows for rows in final_holder_tokens.values()),
        repr(final_holder_tokens),
    )

    final_empty_ok, final_meta = historical_empty_meta_ok()
    check(
        "final TreeCache metadata canonical empty",
        final_empty_ok,
        repr(final_meta),
    )
    check(
        "final published roots absent",
        roots_absent(root_sig()),
        repr(root_sig()),
    )

    elapsed = time.monotonic() - T0

    print("\n" + "=" * 88)
    print(
        f"LIFECYCLE E2E RESULT: PASS={PASS} FAIL={FAIL} "
        f"seed={seed} elapsed={elapsed:.1f}s"
    )
    print("=" * 88)

    if FAIL == 0:
        print()
        print("ALL ULTIMATE APP-LAYER / SYNC / EMPTY-TREE / RESTART CHECKS PASSED.")
        print()
        print("Validated chain:")
        print("  fresh")
        print("    -> JOIN H0/H1 without auto-merge")
        print("    -> explicit SYNC publication")
        print("    -> UPDATE without auto-merge")
        print("    -> explicit SYNC")
        print("    -> non-last QUIT without auto-merge")
        print("    -> explicit SYNC + removed-member QUERY=0")
        print("    -> BUSY A / QUEUED B,C + priority SYNC => A -> SYNC -> B -> C")
        print("    -> last-holder QUIT => leaf_num=0 while old root stays published")
        print("    -> explicit empty SYNC => canonical no-tree, root files absent")
        print("    -> real QUERYs all return 0")
        print("    -> normal shutdown/restart without pretreat")
        print("    -> persisted no-tree state preserved")
        print("    -> fresh real MPC QUERYs after restart still return 0")
        return 0

    _ST.stop()
    print()
    print("ULTIMATE TEST FAILED.")
    print("Use the first FAIL plus the surrounding Agent/Manager logs as the primary diagnosis.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())