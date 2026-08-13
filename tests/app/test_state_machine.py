#!/usr/bin/env python3
"""Business-state-machine test — control-plane only, NO real MPC."""
from __future__ import annotations
import json, os, sqlite3, sys, urllib.request, urllib.error
from pathlib import Path

HERE = Path(__file__).resolve()
for cand in [Path.cwd() / "application", HERE.parent / "application",
             HERE.parent.parent / "application"]:
    if (cand / "config.json").is_file():
        APP_DIR = cand.resolve(); break
else:
    raise SystemExit("cannot locate application/config.json")
sys.path.insert(0, str(APP_DIR))
from _c3_io import read_json
import os as _os
_t = _os.path.dirname(_os.path.abspath(__file__))
while _t and not _os.path.isdir(_os.path.join(_t, "common")):
    _t = _os.path.dirname(_t)
sys.path.insert(0, _t)
from common.stack import Stack

CFG = read_json(str(APP_DIR / "config.json"))
M = CFG["manage_server"]
URL = f"http://{M['server_ip']}:{M['http_port']}"
TIMEOUT = max(10.0, float(CFG.get("timeout", 30.0)))
DB_PATH = APP_DIR / CFG["storage_root_dir"] / "manage_server" / M["db_name"]

PASS = FAIL = 0

def check(name, cond, detail=""):
    global PASS, FAIL
    if cond: PASS += 1; print(f"  PASS {name}")
    else: FAIL += 1; print(f"  FAIL {name}: {detail}")

def die(msg):
    print(f"\nPRECONDITION FAILED: {msg}", file=sys.stderr)
    raise SystemExit(2)

def post(route, body):
    data = json.dumps(body).encode()
    req = urllib.request.Request(f"{URL}{route}", data=data,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        try: return json.loads(e.read())
        except json.JSONDecodeError: return {"status": f"http_{e.code}"}

def db():
    c = sqlite3.connect(DB_PATH, timeout=10.0); c.row_factory = sqlite3.Row; return c

def live_ops(user_id=None, prot_type=None):
    sql = "SELECT * FROM operations WHERE status IN ('RESERVED','QUEUED','ACTIVE','BUSY')"
    args = []
    if user_id: sql += " AND user_id=?"; args.append(user_id)
    if prot_type: sql += " AND prot_type=?"; args.append(prot_type)
    sql += " ORDER BY op_id"
    with db() as c:
        return [dict(r) for r in c.execute(sql, args).fetchall()]

def get_user(role, idx=0):
    fname = "set_holder_users.json" if role == "set_holder" else "querier_users.json"
    users = read_json(str(APP_DIR / "pretreat" / fname))
    k = list(users.keys())
    if idx >= len(k): die(f"need {idx+1} {role} users")
    return k[idx]

if __name__ == '__main__':
    st = Stack(mode="manager_only", fresh=True)
    st.start()
    print("=== Business State Machine (control-plane only) ===\n")
    print("(manager_only stack auto-started: fresh pretreat + fake mgmt listeners + Manager)")

    sh_a = get_user("set_holder", 0)
    sh_b = get_user("set_holder", 1)
    qr   = get_user("querier", 0)

    r1 = post("/reserve_update", {"user_id": sh_a})
    check("reserve UPDATE #1 → SUCCESSFUL",
          r1.get("status") == "SUCCESSFUL", str(r1))
    op1 = live_ops(user_id=sh_a, prot_type="UPDATE")
    check("DB: one live UPDATE", len(op1) == 1, str(op1))
    op1_id = op1[0]["op_id"]
    check("DB: UPDATE RESERVED", op1[0]["status"] == "RESERVED")
    check("DB: UPDATE queue_pos IS NULL", op1[0]["queue_pos"] is None)

    r2 = post("/reserve_update", {"user_id": sh_a})
    check("reserve UPDATE duplicate → ALREADY",
          r2.get("status") == "ALREADY", str(r2))
    op1b = live_ops(user_id=sh_a, prot_type="UPDATE")
    check("DB: still exactly one live UPDATE", len(op1b) == 1, str(op1b))

    r3 = post("/reserve_join", {"user_id": sh_a})
    check("reserve JOIN → SUCCESSFUL", r3.get("status") == "SUCCESSFUL", str(r3))
    r4 = post("/reserve_quit", {"user_id": sh_a})
    check("reserve QUIT → SUCCESSFUL", r4.get("status") == "SUCCESSFUL", str(r4))
    all_live = live_ops(user_id=sh_a)
    live_types = {(o["user_id"], o["prot_type"], o["status"]) for o in all_live}
    check("3 live reservations for same user",
          live_types == {(sh_a, "JOIN", "RESERVED"),
                         (sh_a, "UPDATE", "RESERVED"),
                         (sh_a, "QUIT", "RESERVED")},
          str(live_types))
    for o in all_live:
        check(f"{o['prot_type']} queue_pos IS NULL", o["queue_pos"] is None)

    q1 = post("/reserve_query", {"user_id": qr})
    check("reserve QUERY → SUCCESSFUL", q1.get("status") == "SUCCESSFUL", str(q1))
    q2 = post("/reserve_query", {"user_id": qr})
    check("reserve QUERY duplicate → ALREADY", q2.get("status") == "ALREADY", str(q2))
    q_ops = live_ops(user_id=qr, prot_type="QUERY")
    check("DB: one live QUERY", len(q_ops) == 1, str(q_ops))
    check("DB: QUERY RESERVED", q_ops[0]["status"] == "RESERVED")
    check("DB: QUERY queue_pos IS NULL", q_ops[0]["queue_pos"] is None)

    e0 = post("/execute", {"user_id": sh_b, "prot_type": "UPDATE"})
    check("execute without reserve → NOT_RESERVED",
          e0.get("status") == "NOT_RESERVED", str(e0))
    ops_after = live_ops(user_id=sh_b)
    check("DB: no auto-creation", len(ops_after) == 0, str(ops_after))

    e1 = post("/execute", {"user_id": sh_a, "prot_type": "UPDATE"})
    check("execute UPDATE before JOIN → REJECTED",
          e1.get("status") == "REJECTED", str(e1))
    up_after = live_ops(user_id=sh_a, prot_type="UPDATE")
    check("UPDATE still RESERVED after reject",
          len(up_after) == 1 and up_after[0]["status"] == "RESERVED"
          and up_after[0]["op_id"] == op1_id,
          str(up_after))
    check("UPDATE queue_pos still NULL after reject",
          up_after[0]["queue_pos"] is None)

    with db() as c:
        qd = c.execute("SELECT COUNT(*) AS n FROM operations "
                       "WHERE status IN ('QUEUED','ACTIVE','BUSY')").fetchone()
    check("no ops in queue (RESERVED excluded)", qd["n"] == 0,
          f"found {qd['n']}")

    with db() as c:
        idxs = [r["name"] for r in
                c.execute("SELECT name FROM sqlite_master WHERE type='index'").fetchall()]
    check("old idx_one_live_op_per_user removed",
          "idx_one_live_op_per_user" not in idxs)
    check("new per-(user,service) index exists",
          "idx_one_live_op_per_user_service" in idxs)
    try:
        with db() as c:
            c.execute("INSERT INTO operations (user_id,prot_type,status,queue_pos,"
                      "overtime_count,created_at,updated_at) VALUES "
                      "(?,'UPDATE','RESERVED',NULL,0,datetime('now'),datetime('now'))",
                      (sh_a,))
            c.commit()
        check("DB UNIQUE constraint on (user_id,prot_type) for live ops",
              False, "no violation raised")
    except sqlite3.IntegrityError:
        check("DB UNIQUE constraint on (user_id,prot_type) for live ops", True)

    with db() as c:
        c.execute("UPDATE operations SET status='FAILED', queue_pos=NULL, "
                  "error_code='TEST_CLEANUP', updated_at=datetime('now') "
                  "WHERE status IN ('RESERVED','QUEUED','ACTIVE','BUSY')")
        c.commit()
        remaining = c.execute("SELECT COUNT(*) AS n FROM operations "
                              "WHERE status IN ('RESERVED','QUEUED','ACTIVE','BUSY')"
                              ).fetchone()["n"]
    check("cracked cleanup covers RESERVED", remaining == 0,
          f"{remaining} ops still live")

    print(f"\n  PASS={PASS}  FAIL={FAIL}")
    st.stop()
    raise SystemExit(0 if FAIL == 0 else 1)
