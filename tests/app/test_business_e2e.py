#!/usr/bin/env python3
"""
C3 Ultra Business E2E — real MPC, all SetHolders + all Querier clients.

YOU start the servers:
    python3 application/run.py steward
    python3 application/run.py peer0
    python3 application/run.py peer1
    python3 application/run.py manage_server

THIS SCRIPT starts/acts as every SetHolder and Querier client.

Preconditions:
  * fresh pretreat
  * all 3 Agents + Manager already running
  * no live operations

This is intentionally NON-DESTRUCTIVE: it does not kill processes or
intentionally crack the task. Restart-persistence and CRACKED recovery must be
separate lifecycle tests because they require stopping/restarting servers.
"""
from __future__ import annotations

import argparse
import json
import os
import random
import sqlite3
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve()
for cand in [
    Path.cwd() / "application",
    HERE.parent / "application",
    HERE.parent.parent / "application",
    HERE.parent.parent.parent / "application",
]:
    if (cand / "config.json").is_file():
        APP_DIR = cand.resolve()
        break
else:
    raise SystemExit("cannot locate application/config.json; run from project root")

ROOT = APP_DIR.parent
sys.path.insert(0, str(APP_DIR))
sys.path.insert(0, str(ROOT))

from _c3_io import read_json
import mpmt
import os as _os
_t = _os.path.dirname(_os.path.abspath(__file__))
while _t and not _os.path.isdir(_os.path.join(_t, "common")):
    _t = _os.path.dirname(_t)
sys.path.insert(0, _t)
from common.stack import Stack

_ST: Stack | None = None
CFG = read_json(str(APP_DIR / "config.json"))
M = CFG["manage_server"]
URL = f"http://{M['server_ip']}:{M['http_port']}"
HTTP_TIMEOUT = max(10.0, float(CFG.get("timeout", 30.0)))
CONNECT_TIMEOUT = float(CFG.get("connect_timeout", 5.0))
E2E_TIMEOUT = max(180.0, float(CFG.get("protocol_timeout", 180.0)))
DB_PATH = APP_DIR / CFG["storage_root_dir"] / "manage_server" / M["db_name"]
PRESET = read_json(str(APP_DIR / "pretreat" / "pre.json"))

RESERVE_ROUTE = {
    "JOIN": "/reserve_join",
    "UPDATE": "/reserve_update",
    "QUIT": "/reserve_quit",
    "QUERY": "/reserve_query",
}
LIVE_STATUSES = ("RESERVED", "QUEUED", "ACTIVE", "BUSY")
TERMINAL_STATUSES = ("DONE", "FAILED", "REMOVED")

PASS = FAIL = 0
T0 = time.monotonic()


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS {name}", flush=True)
        return True
    FAIL += 1
    print(f"  FAIL {name}: {detail}", flush=True)
    return False


def section(name):
    print(f"\n{'=' * 16} {name} {'=' * 16}", flush=True)


def abort(msg):
    print(f"\nABORT: {msg}", flush=True)
    raise SystemExit(2)


def post(route, body):
    req = urllib.request.Request(
        f"{URL}{route}",
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
        abort(f"HTTP {route} failed: {type(e).__name__}: {e}")


def db():
    c = sqlite3.connect(DB_PATH, timeout=10.0)
    c.row_factory = sqlite3.Row
    return c


def operation_count():
    with db() as c:
        return int(c.execute("SELECT COUNT(*) AS n FROM operations").fetchone()["n"])


def op_by_id(op_id):
    with db() as c:
        r = c.execute("SELECT * FROM operations WHERE op_id=?", (op_id,)).fetchone()
        return dict(r) if r else None


def latest_op(user_id, prot_type):
    with db() as c:
        r = c.execute(
            "SELECT * FROM operations WHERE user_id=? AND prot_type=? "
            "ORDER BY op_id DESC LIMIT 1",
            (user_id, prot_type),
        ).fetchone()
        return dict(r) if r else None


def live_ops(user_id=None, prot_type=None):
    sql = "SELECT * FROM operations WHERE status IN ('RESERVED','QUEUED','ACTIVE','BUSY')"
    args = []
    if user_id is not None:
        sql += " AND user_id=?"; args.append(user_id)
    if prot_type is not None:
        sql += " AND prot_type=?"; args.append(prot_type)
    sql += " ORDER BY op_id"
    with db() as c:
        return [dict(r) for r in c.execute(sql, args).fetchall()]


def user_status(user_id):
    with db() as c:
        r = c.execute("SELECT status FROM users WHERE user_id=?", (user_id,)).fetchone()
        return r["status"] if r else None


def token_rows(user_id):
    with db() as c:
        return [dict(r) for r in c.execute(
            "SELECT agent_role,token FROM user_agent_tokens WHERE user_id=? ORDER BY agent_role",
            (user_id,),
        ).fetchall()]


def has_three_tokens(user_id):
    roles = {r["agent_role"] for r in token_rows(user_id) if r.get("token")}
    return roles == {"STEWARD", "PEER0", "PEER1"}


def get_users(role):
    name = "set_holder_users.json" if role == "set_holder" else "querier_users.json"
    return list(read_json(str(APP_DIR / "pretreat" / name)).keys())


def load_dataset(user_id):
    p = APP_DIR / CFG["storage_root_dir"] / f"set_holder_{user_id}" / "set.npy"
    if not p.is_file():
        abort(f"dataset missing: {p}")
    return [str(x).encode() for x in np.load(str(p))]


def wait_terminal_db(op_id, timeout=E2E_TIMEOUT):
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        last = op_by_id(op_id)
        if last is None:
            return {"status": "NOT_FOUND", "_op_id": op_id}
        if last["status"] in TERMINAL_STATUSES:
            return {
                "status": last["status"],
                "reason": last.get("error_code"),
                "_op_id": op_id,
            }
        time.sleep(0.20)
    return {
        "status": "TIMEOUT", "_op_id": op_id,
        "db_status": last["status"] if last else None,
        "error_code": last.get("error_code") if last else None,
    }


def set_holder_mpc(agents, data_set):
    sh = mpmt.SetHolder(
        set_size=PRESET["set_size"],
        fpr_mantissa=PRESET["fpr_mantissa"],
        fpr_exponent=PRESET["fpr_exponent"],
    )
    ch = []
    try:
        for role in ("STEWARD", "PEER0", "PEER1"):
            info = agents[role]
            ch.append(mpmt.Channel.connect(
                info["ip"], int(info["port"]), timeout=CONNECT_TIMEOUT
            ))
        sh.share_bf(
            set=data_set,
            hash_seed_list=[bytes.fromhex(h) for h in PRESET["hash_seed_list"]],
            ch_steward=ch[0], ch_peer0=ch[1], ch_peer1=ch[2],
        )
    finally:
        ch.clear()


def querier_mpc(agents, element):
    q = mpmt.Querier(
        set_size=PRESET["set_size"],
        fpr_mantissa=PRESET["fpr_mantissa"],
        fpr_exponent=PRESET["fpr_exponent"],
    )
    ch = []
    try:
        for role in ("STEWARD", "PEER0", "PEER1"):
            info = agents[role]
            ch.append(mpmt.Channel.connect(
                info["ip"], int(info["port"]), timeout=CONNECT_TIMEOUT
            ))
        return q.query(
            element=element,
            ch_steward=ch[0], ch_peer0=ch[1], ch_peer1=ch[2],
        )
    finally:
        ch.clear()


def membership_true(v):
    return isinstance(v, (bool, int)) and int(v) != 0


def e2e_body(user_id, prot_type, op_id=None):
    b = {"user_id": user_id, "prot_type": prot_type}
    if op_id is not None:
        b["op_id"] = op_id
    return b


def claim_once(user_id, prot_type, op_id=None):
    return post("/execute", e2e_body(user_id, prot_type, op_id))


def reserve(user_id, prot_type, expected="SUCCESSFUL"):
    r = post(RESERVE_ROUTE[prot_type], {"user_id": user_id})
    check(
        f"reserve {prot_type} {user_id[:10]}… -> {expected}",
        r.get("status") == expected,
        repr(r),
    )
    return r


def execute_mpc(user_id, prot_type, mpc_fn, mpc_args=(), op_id=None):
    internal = op_id
    while True:
        r = claim_once(user_id, prot_type, internal)
        if "_op_id" in r:
            internal = int(r["_op_id"])
        st = r.get("status")

        if st in ("DONE", "FAILED", "REMOVED", "NOT_FOUND", "NOT_RESERVED", "REJECTED"):
            return r, internal, None

        if st == "WAITING":
            time.sleep(float(r.get("retry_after", 0.25)))
            continue

        if st == "BUSY":
            agents = r.get("agents")
            if agents is None:
                if internal is None:
                    abort(f"BUSY without agents/_op_id: {r}")
                return wait_terminal_db(internal), internal, None
            if internal is None:
                abort(f"BUSY+agents without _op_id: {r}")

            t = time.monotonic()
            try:
                result = mpc_fn(agents, *mpc_args)
            except BaseException as e:
                abort(
                    f"{prot_type} client MPC raised {type(e).__name__}: {e}; "
                    "protocol may be uncertain, stop all + pretreat"
                )
            print(
                f"  [MPC] {prot_type} op={internal} client-side "
                f"{time.monotonic()-t:.3f}s", flush=True
            )
            return wait_terminal_db(internal), internal, result

        abort(f"unexpected /execute response: {r}")


def join_holder(uid, ds, label, already_reserved=False):
    if not already_reserved:
        reserve(uid, "JOIN")
    term, op_id, _ = execute_mpc(uid, "JOIN", set_holder_mpc, (ds,))
    ok = check(f"{label} JOIN DONE", term.get("status") == "DONE", repr(term))
    check(f"{label} status JOINED", user_status(uid) == "JOINED", str(user_status(uid)))
    check(f"{label} 3 tokens", has_three_tokens(uid), repr(token_rows(uid)))
    check(f"{label} JOIN no longer live", not live_ops(uid, "JOIN"), repr(live_ops(uid, "JOIN")))
    if not ok:
        abort(f"{label} JOIN failed")
    return op_id


def update_holder(uid, ds, label, already_reserved=False):
    if not already_reserved:
        reserve(uid, "UPDATE")
    term, op_id, _ = execute_mpc(uid, "UPDATE", set_holder_mpc, (ds,))
    ok = check(f"{label} UPDATE DONE", term.get("status") == "DONE", repr(term))
    check(f"{label} remains JOINED", user_status(uid) == "JOINED", str(user_status(uid)))
    check(f"{label} tokens remain", has_three_tokens(uid), repr(token_rows(uid)))
    check(f"{label} UPDATE no longer live", not live_ops(uid, "UPDATE"), repr(live_ops(uid, "UPDATE")))
    if not ok:
        abort(f"{label} UPDATE failed")
    return op_id


def quit_holder(uid, label, already_reserved=False):
    if not already_reserved:
        reserve(uid, "QUIT")
    term, op_id, _ = execute_mpc(uid, "QUIT", lambda *_: None)
    ok = check(f"{label} QUIT DONE", term.get("status") == "DONE", repr(term))
    check(f"{label} status QUITTED", user_status(uid) == "QUITTED", str(user_status(uid)))
    check(f"{label} tokens cleared", not has_three_tokens(uid), repr(token_rows(uid)))
    check(f"{label} QUIT no longer live", not live_ops(uid, "QUIT"), repr(live_ops(uid, "QUIT")))
    if not ok:
        abort(f"{label} QUIT failed")
    return op_id


def query_present(qid, element, label):
    _ST.ms_sync()
    reserve(qid, "QUERY")
    term, op_id, result = execute_mpc(qid, "QUERY", querier_mpc, (element,))
    ok1 = check(f"{label}: QUERY DONE", term.get("status") == "DONE", repr(term))
    ok2 = check(f"{label}: membership=true", membership_true(result), repr(result))
    row = op_by_id(op_id) if op_id is not None else None
    check(f"{label}: DB op DONE", row is not None and row["status"] == "DONE", repr(row))
    check(f"{label}: QUERY no longer live", not live_ops(qid, "QUERY"), repr(live_ops(qid, "QUERY")))
    if not (ok1 and ok2):
        abort(f"query failed: {label}")
    return op_id


def test_execute_without_reserve(qid):
    section("execute without reserve")
    before = operation_count()
    r = claim_once(qid, "QUERY")
    after = operation_count()
    check("QUERY execute without reserve -> NOT_RESERVED", r.get("status") == "NOT_RESERVED", repr(r))
    check("no DB auto-create", before == after, f"before={before} after={after}")


def test_holder_preconditions(uid, ds):
    section("same holder reserves JOIN + UPDATE + QUIT")
    reserve(uid, "JOIN")
    reserve(uid, "JOIN", "ALREADY")
    reserve(uid, "UPDATE")
    reserve(uid, "QUIT")

    live = live_ops(uid)
    check(
        "three simultaneous service reservations",
        len(live) == 3 and {r["prot_type"] for r in live} == {"JOIN", "UPDATE", "QUIT"},
        repr(live),
    )
    for prot in ("JOIN", "UPDATE", "QUIT"):
        row = latest_op(uid, prot)
        check(
            f"{prot} RESERVED queue_pos NULL",
            row is not None and row["status"] == "RESERVED" and row["queue_pos"] is None,
            repr(row),
        )

    r = claim_once(uid, "UPDATE")
    check("UPDATE before JOIN -> REJECTED", r.get("status") == "REJECTED", repr(r))
    row = latest_op(uid, "UPDATE")
    check("rejected UPDATE stays RESERVED", row and row["status"] == "RESERVED" and row["queue_pos"] is None, repr(row))

    r = claim_once(uid, "QUIT")
    check("QUIT before JOIN -> REJECTED", r.get("status") == "REJECTED", repr(r))
    row = latest_op(uid, "QUIT")
    check("rejected QUIT stays RESERVED", row and row["status"] == "RESERVED" and row["queue_pos"] is None, repr(row))

    join_holder(uid, ds, "H0", already_reserved=True)

    ds2 = list(ds)
    marker = f"c3-ultra-precondition-{uuid.uuid4().hex}".encode()
    ds2[0] = marker
    update_holder(uid, ds2, "H0 pre-reserved", already_reserved=True)

    qrow = latest_op(uid, "QUIT")
    check("pre-reserved QUIT remains RESERVED", qrow and qrow["status"] == "RESERVED" and qrow["queue_pos"] is None, repr(qrow))
    return ds2, marker


def test_concurrent_reserve(qid, element, racers):
    section(f"{racers}-way duplicate QUERY reserve race")
    results, errors = [], []
    lock = threading.Lock()
    gate = threading.Barrier(racers)

    def worker():
        try:
            gate.wait(timeout=10)
            r = post("/reserve_query", {"user_id": qid})
            with lock:
                results.append(r)
        except BaseException as e:
            with lock:
                errors.append(repr(e))

    ts = [threading.Thread(target=worker, daemon=True) for _ in range(racers)]
    for t in ts: t.start()
    for t in ts: t.join(20)

    sts = [r.get("status") for r in results]
    check("all reserve racers returned", len(results) == racers and not errors, f"results={len(results)} errors={errors}")
    check("exactly one SUCCESSFUL", sts.count("SUCCESSFUL") == 1, repr(sts))
    check("all others ALREADY", sts.count("ALREADY") == racers - 1, repr(sts))
    check("exactly one live QUERY generation", len(live_ops(qid, "QUERY")) == 1, repr(live_ops(qid, "QUERY")))

    term, _, result = execute_mpc(qid, "QUERY", querier_mpc, (element,))
    check("reserve-race generation DONE", term.get("status") == "DONE", repr(term))
    check("reserve-race query true", membership_true(result), repr(result))


def test_duplicate_executor(qid, element):
    section("duplicate executor / agents-once")
    reserve(qid, "QUERY")
    results, errors = [], []
    lock = threading.Lock()
    gate = threading.Barrier(2)

    def worker():
        try:
            gate.wait(timeout=10)
            r = claim_once(qid, "QUERY")
            with lock: results.append(r)
        except BaseException as e:
            with lock: errors.append(repr(e))

    ts = [threading.Thread(target=worker, daemon=True) for _ in range(2)]
    for t in ts: t.start()
    for t in ts: t.join(20)

    check("both execute requests returned", len(results) == 2 and not errors, f"results={results} errors={errors}")
    winners = [r for r in results if r.get("status") == "BUSY" and r.get("agents")]
    losers = [r for r in results if r not in winners]
    check("exactly one BUSY+agents winner", len(winners) == 1, repr(results))
    check("loser gets no agents", len(losers) == 1 and not losers[0].get("agents"), repr(results))
    if len(winners) != 1:
        abort("duplicate executor invariant failed")

    w = winners[0]
    op_id = int(w["_op_id"])
    result = querier_mpc(w["agents"], element)
    term = wait_terminal_db(op_id)
    check("winning generation DONE", term.get("status") == "DONE", repr(term))
    check("winning query true", membership_true(result), repr(result))
    old = claim_once(qid, "QUERY", op_id)
    check("pinned DONE observable", old.get("status") == "DONE", repr(old))
    return op_id


def test_generation_isolation(qid, element, old_id):
    section("generation isolation")
    reserve(qid, "QUERY")
    new = latest_op(qid, "QUERY")
    if not new:
        abort("new QUERY generation missing")
    new_id = int(new["op_id"])
    check("new generation differs", new_id != old_id, f"old={old_id} new={new_id}")
    check("new generation starts RESERVED", new["status"] == "RESERVED" and new["queue_pos"] is None, repr(new))

    old = claim_once(qid, "QUERY", old_id)
    check("old generation still DONE", old.get("status") == "DONE", repr(old))
    after = op_by_id(new_id)
    check("old poll cannot enqueue new generation", after and after["status"] == "RESERVED" and after["queue_pos"] is None, repr(after))

    term, got, result = execute_mpc(qid, "QUERY", querier_mpc, (element,))
    check("new generation executes own op_id", term.get("status") == "DONE" and got == new_id, f"term={term} got={got} expected={new_id}")
    check("new generation query true", membership_true(result), repr(result))


def test_fifo(q0, q1, elem0, elem1):
    section("global single-active + FIFO")
    reserve(q0, "QUERY")
    reserve(q1, "QUERY")

    first = claim_once(q0, "QUERY")
    if not (first.get("status") == "BUSY" and first.get("agents") and first.get("_op_id") is not None):
        abort(f"first FIFO claim not BUSY+agents: {first}")
    op0 = int(first["_op_id"])

    second = claim_once(q1, "QUERY")
    check("second WAITING while first BUSY", second.get("status") == "WAITING", repr(second))
    if second.get("_op_id") is None:
        abort(f"WAITING response missing _op_id: {second}")
    op1 = int(second["_op_id"])

    a, b = op_by_id(op0), op_by_id(op1)
    check("first DB BUSY", a and a["status"] == "BUSY", repr(a))
    check("second DB QUEUED", b and b["status"] == "QUEUED", repr(b))
    check("FIFO op_id order", op1 > op0, f"first={op0} second={op1}")

    r0 = querier_mpc(first["agents"], elem0)
    t0 = wait_terminal_db(op0)
    check("FIFO first DONE", t0.get("status") == "DONE", repr(t0))
    check("FIFO first query true", membership_true(r0), repr(r0))

    t1, got1, r1 = execute_mpc(q1, "QUERY", querier_mpc, (elem1,), op_id=op1)
    check("FIFO second DONE", t1.get("status") == "DONE" and got1 == op1, f"term={t1} got={got1}")
    check("FIFO second query true", membership_true(r1), repr(r1))


def main():
    global PASS, FAIL
    global PRESET
    global _ST
    ap = argparse.ArgumentParser()
    ap.add_argument("--query-rounds", type=int, default=16, help="extra mixed QUERY rounds")
    ap.add_argument("--reserve-racers", type=int, default=16, help="concurrent duplicate reserve threads")
    ap.add_argument("--max-holders", type=int, default=0, help="0 = all pretreat holders")
    args = ap.parse_args()

    _ST = Stack(mode="full", fresh=True)
    _ST.start()
    PRESET = read_json(str(APP_DIR / "pretreat" / "pre.json"))

    seed = int(os.environ.get("TEST_SEED", "42"))
    random.seed(seed)

    print("=== C3 ULTRA E2E (real MPC clients) ===")
    print(f"project={ROOT}")
    print(f"manager={URL}")
    print(f"seed={seed} query_rounds={args.query_rounds} reserve_racers={args.reserve_racers}")

    section("preflight")
    if not DB_PATH.is_file():
        abort(f"Manager DB missing: {DB_PATH}")
    task = read_json(str(APP_DIR / "task_status.json"))
    check("task active", task.get("status") == "active", repr(task))
    if task.get("status") != "active":
        abort("task not active")

    holders = get_users("set_holder")
    queriers = get_users("querier")
    if args.max_holders > 0:
        holders = holders[:args.max_holders]
    if len(holders) < 2 or len(queriers) < 2:
        abort("need >=2 holders and >=2 queriers")

    pre_live = live_ops()
    check("no live ops before suite", not pre_live, repr(pre_live))
    if pre_live:
        abort("fresh pretreat required")

    bad = {u: user_status(u) for u in holders if user_status(u) != "NOT_JOINED"}
    check("all holders start NOT_JOINED", not bad, repr(bad))
    if bad:
        abort("fresh pretreat required")

    datasets, probes = {}, {}
    for i, u in enumerate(holders):
        ds = load_dataset(u)
        if not ds:
            abort(f"empty dataset: {u}")
        datasets[u] = ds
        probes[u] = ds[min(i, len(ds)-1)]

    print(f"holders={len(holders)} queriers={len(queriers)}")
    for i, u in enumerate(holders):
        print(f"  H{i}={u[:16]}… n={len(datasets[u])} probe={probes[u]!r}")
    for i, q in enumerate(queriers):
        print(f"  Q{i}={q[:16]}…")

    h0, q0, q1 = holders[0], queriers[0], queriers[1]

    test_execute_without_reserve(q0)
    datasets[h0], probes[h0] = test_holder_preconditions(h0, datasets[h0])
    query_present(q0, probes[h0], "H0 present after precondition UPDATE")

    test_concurrent_reserve(q0, probes[h0], args.reserve_racers)
    old = test_duplicate_executor(q0, probes[h0])
    test_generation_isolation(q0, probes[h0], old)

    section("JOIN every remaining holder")
    for i, u in enumerate(holders[1:], start=1):
        join_holder(u, datasets[u], f"H{i}")
        query_present(queriers[i % len(queriers)], probes[u], f"H{i} immediate present")
        if i % 2 == 0:
            query_present(queriers[(i+1) % len(queriers)], probes[h0], f"H0 survives JOIN H{i}")

    section("representative QUERY for every holder")
    for i, u in enumerate(holders):
        query_present(queriers[i % len(queriers)], probes[u], f"H{i} representative")

    test_fifo(q0, q1, probes[holders[0]], probes[holders[1]])

    section("UPDATE every holder + verify fresh marker")
    for i, u in enumerate(holders):
        ds2 = list(datasets[u])
        marker = f"c3-ultra-H{i}-{seed}-{uuid.uuid4().hex}".encode()
        ds2[0] = marker
        update_holder(u, ds2, f"H{i}")
        datasets[u] = ds2
        probes[u] = marker
        query_present(queriers[i % len(queriers)], marker, f"H{i} update marker")
        other = holders[(i+1) % len(holders)]
        if other != u:
            query_present(queriers[(i+1) % len(queriers)], probes[other], f"H{(i+1)%len(holders)} survives UPDATE H{i}")

    section(f"mixed QUERY stress x{args.query_rounds}")
    for rnd in range(args.query_rounds):
        u = random.choice(holders)
        q = queriers[rnd % len(queriers)]
        query_present(q, probes[u], f"stress {rnd} H{holders.index(u)}")

    section("QUIT every holder + survivor query")
    remaining = list(holders)
    for i, u in enumerate(holders):
        quit_holder(u, f"H{i}", already_reserved=(u == h0))
        remaining.remove(u)
        if remaining:
            survivor = remaining[-1]
            query_present(
                queriers[i % len(queriers)],
                probes[survivor],
                f"survivor H{holders.index(survivor)} after QUIT H{i}",
            )

    section("final invariants")
    final_live = live_ops()
    check("no live operations at end", not final_live, repr(final_live))

    wrong = {u: user_status(u) for u in holders if user_status(u) != "QUITTED"}
    check("all holders end QUITTED", not wrong, repr(wrong))

    residual = {u: token_rows(u) for u in holders if has_three_tokens(u)}
    check("all holder tokens cleared", not residual, repr(residual))

    with db() as c:
        nonterminal = [dict(r) for r in c.execute(
            "SELECT * FROM operations WHERE status NOT IN ('DONE','FAILED','REMOVED') ORDER BY op_id"
        ).fetchall()]
    check("every DB operation terminal", not nonterminal, repr(nonterminal[:10]))

    elapsed = time.monotonic() - T0
    print("\n" + "="*72)
    print(f"ULTRA RESULT: PASS={PASS} FAIL={FAIL} seed={seed} elapsed={elapsed:.1f}s")
    print("="*72)
    if FAIL:
        return 1
    print("ALL NON-DESTRUCTIVE ULTRA E2E CHECKS PASSED.")
    _ST.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
