#!/usr/bin/env python3
"""
E2E for the Manager `ms sync` semantics (82 checks).

Self-contained: the Stack auto-starts fresh pretreat + the 3 Agents + Manage
Server (`ms start`); every `ms sync` is issued automatically.

Run:  python3 tests/app/test_sync_e2e.py

Verifies:
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
    p = STORAGE / role / "meta.json"
    if p.is_file():
        return json.loads(p.read_text())
    d = STORAGE / role
    cache = sorted(
        f.name for f in d.iterdir()
        if f.name.endswith(".mpmtrvp")
    ) if d.is_dir() else []
    if cache:
        abort(f"{role}: meta.json missing but cache files exist: {cache}")
    return {"state": "valid", "leaf_num": 0, "dirty_leaf": []}

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
    global _ST
    seed = int(os.environ.get("TEST_SEED", "42"))
    random.seed(seed)

    _ST = Stack(mode="full", fresh=True)
    _ST.start()

    global PRE
    PRE = read_json(str(APP / "pretreat" / "pre.json"))

    print("=== C3 ms sync E2E ===")
    print(f"manager={BASE}")
    print("(stack auto-started: fresh pretreat + 3 Agents + Manager `ms start`)")

    section("preflight")
    task = read_json(str(APP / "task_status.json"))
    check("task active", task.get("status") == "active", repr(task))
    hs, qs = users("holder"), users("querier")
    if len(hs) < 2 or len(qs) < 2:
        abort("requires >=2 holders and >=2 queriers")
    h0, h1 = hs[:2]
    q0, q1 = qs[:2]

    check("no live business ops", not live_ops(), repr(live_ops()))
    st = {h0: user_status(h0), h1: user_status(h1)}
    check("H0/H1 start NOT_JOINED",
          all(v == "NOT_JOINED" for v in st.values()), repr(st))
    if live_ops() or not all(v == "NOT_JOINED" for v in st.values()):
        abort("fresh pretreat required")

    ds0, ds1 = load_set(h0), load_set(h1)
    probe0 = ds0[min(1, len(ds0)-1)]
    probe1 = ds1[min(1, len(ds1)-1)]

    r0 = root_sig()
    check("fresh root files absent", roots_absent(r0), repr(r0))
    m0 = {r: meta(r) for r in ROLES}
    check("fresh leaf_num=0",
          all(m0[r].get("leaf_num") == 0 for r in ROLES),
          repr({r:m0[r].get("leaf_num") for r in ROLES}))
    check("fresh dirty_leaf empty", all_clean(),
          repr({r:m0[r].get("dirty_leaf") for r in ROLES}))
    if not roots_absent(r0):
        abort("fresh TreeCache root files are not absent")

    section("JOIN H0 + H1 without auto-merge")
    join(h0, ds0, "H0")
    check("JOIN H0 root unchanged", root_sig() == r0, "root changed before sync")
    check_dirty("after JOIN H0")

    join(h1, ds1, "H1")
    r_join_dirty = root_sig()
    check("JOIN H1 root still unchanged", r_join_dirty == r0, "root changed before sync")
    check_dirty("after JOIN H0+H1")
    leaves = {r: meta(r).get("leaf_num") for r in ROLES}
    check("leaf_num=2 before first sync",
          all(v == 2 for v in leaves.values()), repr(leaves))

    section("manual SYNC #1 — publish two JOINs")
    r_join = manual_sync(r_join_dirty, "SYNC #1")
    check("SYNC #1 created root files", roots_exist(r_join), repr(r_join))
    check_clean("after SYNC #1")
    query_true(q0, probe0, "H0 after SYNC #1")
    query_true(q1, probe1, "H1 after SYNC #1")
    check("QUERY leaves root unchanged", root_sig() == r_join, "QUERY mutated root")

    section("UPDATE H0 without auto-merge")
    ds0u = list(ds0)
    marker0 = f"c3-sync-h0-{seed}-{uuid.uuid4().hex}".encode()
    ds0u[0] = marker0
    before = root_sig()
    update(h0, ds0u, "H0")
    r_up_dirty = root_sig()
    check("UPDATE H0 root unchanged before sync",
          r_up_dirty == before, "UPDATE auto-merged")
    check_dirty("after UPDATE H0")

    section("manual SYNC #2 — publish H0 UPDATE")
    r_up = manual_sync(r_up_dirty, "SYNC #2")
    check("SYNC #2 changed root", r_up != r_up_dirty, "root unchanged")
    check_clean("after SYNC #2")
    query_true(q0, marker0, "new H0 marker")
    query_true(q1, probe1, "H1 survives H0 UPDATE")

    section("QUIT H0 without auto-merge")
    before = root_sig()
    quit_holder(h0, "H0")
    r_quit_dirty = root_sig()
    check("QUIT H0 root unchanged before sync",
          r_quit_dirty == before, "QUIT auto-merged")
    check_dirty("after QUIT H0")
    leaves = {r: meta(r).get("leaf_num") for r in ROLES}
    check("leaf_num=1 after H0 QUIT",
          all(v == 1 for v in leaves.values()), repr(leaves))

    section("manual SYNC #3 — publish H0 QUIT")
    r_quit = manual_sync(r_quit_dirty, "SYNC #3")
    check("SYNC #3 changed root", r_quit != r_quit_dirty, "root unchanged")
    check_clean("after SYNC #3")
    query_true(q0, probe1, "H1 survives H0 QUIT")

    section("priority: A BUSY, B/C QUEUED, then ms sync")
    priority_old_root = root_sig()

    ds1u = list(ds1)
    marker1 = f"c3-sync-priority-h1-{seed}-{uuid.uuid4().hex}".encode()
    ds1u[0] = marker1

    op_a, agents_a = begin_held_update(h1)
    op_b = queue_query(q0, "priority B QUERY")
    op_c = queue_query(q1, "priority C QUERY")
    a, b, c = op_by_id(op_a), op_by_id(op_b), op_by_id(op_c)
    check("setup A=BUSY B=QUEUED C=QUEUED",
          a and a["status"]=="BUSY" and b and b["status"]=="QUEUED"
          and c and c["status"]=="QUEUED",
          f"A={a} B={b} C={c}")
    if b and c and b.get("queue_pos") is not None and c.get("queue_pos") is not None:
        check("business FIFO B before C",
              b["queue_pos"] < c["queue_pos"],
              f"B={b['queue_pos']} C={c['queue_pos']}")
    check("held UPDATE has not changed root",
          root_sig() == priority_old_root, "root changed before client data")
    check_clean("while held UPDATE is BUSY")

    print("\n>>> PRIORITY MANUAL ACTION")
    print(">>> Current: A UPDATE=BUSY, B QUERY=QUEUED, C QUERY=QUEUED")
    print(">>> In Manager CLI type:  ms sync")
    print(">>> It must queue immediately after A, ahead of B/C.")
    print(">>> auto ms sync queued (ahead of B/C; runs after A is released)")
    _ST.ms_sync()

    check("SYNC did not run concurrently with BUSY A",
          root_sig() == priority_old_root and all_clean(),
          "root/dirty changed while A was still BUSY")

    print("  releasing held UPDATE A...", flush=True)
    t = time.monotonic()
    setholder_mpc(agents_a, ds1u)
    print(f"  [MPC] held UPDATE op={op_a} client-side "
          f"{time.monotonic()-t:.3f}s", flush=True)
    term_a = wait_terminal(op_a)
    check("priority A UPDATE DONE", term_a.get("status") == "DONE", repr(term_a))
    if term_a.get("status") != "DONE":
        abort("priority UPDATE failed")

    agents_b = wait_query_agents_after_sync(
        q0, op_b, priority_old_root, "priority B"
    )
    result_b = querier_mpc(agents_b, marker1)
    term_b = wait_terminal(op_b)
    check("priority B QUERY DONE", term_b.get("status") == "DONE", repr(term_b))
    check("priority B sees new marker (SYNC before B)",
          result_b == 1, f"result={result_b}")

    end = time.monotonic() + PROTOCOL_TIMEOUT
    agents_c = None
    while time.monotonic() < end:
        r = claim(q1, "QUERY", op_c)
        if r.get("status") == "WAITING":
            time.sleep(0.03); continue
        if r.get("status") == "BUSY" and r.get("agents"):
            agents_c = r["agents"]; break
        abort(f"priority C unexpected response: {r}")
    if agents_c is None:
        abort("priority C timeout")
    result_c = querier_mpc(agents_c, probe1)
    term_c = wait_terminal(op_c)
    check("priority C QUERY DONE", term_c.get("status") == "DONE", repr(term_c))
    check("priority C sees surviving H1 element", result_c == 1, f"result={result_c}")
    check("priority SYNC changed root",
          root_sig() != priority_old_root, "root unchanged")
    check_clean("after priority SYNC")

    section("final")
    check("no live business ops", not live_ops(), repr(live_ops()))
    task = read_json(str(APP / "task_status.json"))
    check("task remains active", task.get("status") == "active", repr(task))
    check("H0 remains QUITTED", user_status(h0) == "QUITTED", str(user_status(h0)))
    check("H0 tokens cleared", not has_three_tokens(h0), repr(token_rows(h0)))
    check("H1 remains JOINED", user_status(h1) == "JOINED", str(user_status(h1)))
    check("H1 has 3 tokens", has_three_tokens(h1), repr(token_rows(h1)))
    check_clean("final TreeCache")

    elapsed = time.monotonic() - T0
    print("\n" + "="*76)
    print(f"MS SYNC E2E RESULT: PASS={PASS} FAIL={FAIL} seed={seed} elapsed={elapsed:.1f}s")
    print("="*76)
    _ST.stop()
    if FAIL == 0:
        print("\nALL MS-SYNC APPLICATION-LAYER TESTS PASSED.")
        print("H1 is intentionally left JOINED; this test does not add a final-last-leaf QUIT case.")
    return 0 if FAIL == 0 else 1

if __name__ == "__main__":
    raise SystemExit(main())
