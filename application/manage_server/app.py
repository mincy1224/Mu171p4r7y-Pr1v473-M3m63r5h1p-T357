# draft
import json
import math
import os
import socket
import threading
import time

from flask import Flask, request, jsonify
from _c3_io import read_json
from _c3_task_status import write as write_task_status
from .db import db

_APP_DIR = os.path.join(os.path.dirname(__file__), "..")


def _load_users(filename: str) -> dict:
    path = os.path.join(_APP_DIR, "pretreat", filename)
    if not os.path.isfile(path):
        return {}
    return read_json(path)


SET_HOLDER_USERS = _load_users("set_holder_users.json")
QUERIER_USERS = _load_users("querier_users.json")


class AgentManager:
    def __init__(self, agents_cfg: dict[str, dict], timeout: float):
        self._cfg = agents_cfg
        self._timeout = timeout
        self._conns: dict[str, socket.socket] = {}
        self._bufs: dict[str, bytes] = {}
        # connections are established lazily — call connect_all()

    def _connect(self, role: str) -> None:
        """Create a fresh connection to *role*, replacing any old one."""
        old = self._conns.pop(role, None)
        if old:
            try:
                old.close()
            except OSError:
                pass
        ac = self._cfg[role]
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(self._timeout)
        s.connect((ac["ip"], ac["mgmt_port"]))
        self._conns[role] = s
        self._bufs[role] = b""
        print(f"[manage] connected to {role} at {ac['ip']}:{ac['mgmt_port']}", flush=True)

    def reconnect(self, role: str) -> None:
        """Public: reconnect a dropped Agent."""
        self._connect(role)

    def settimeout(self, role: str, timeout: float) -> None:
        """Set socket timeout for *role* (used for per-recv deadline)."""
        conn = self._conns.get(role)
        if conn is not None:
            conn.settimeout(timeout)

    def send(self, role: str, msg: dict) -> None:
        conn = self._conns.get(role)
        if conn is None:
            raise ConnectionError(f"{role} not connected")
        conn.sendall(json.dumps(msg).encode() + b"\n")

    def recv(self, role: str) -> dict:
        conn = self._conns.get(role)
        if conn is None:
            raise ConnectionError(f"{role} not connected")
        buf = self._bufs[role]
        while b"\n" not in buf:
            chunk = conn.recv(4096)
            if not chunk:
                raise ConnectionError(f"{role} disconnected")
            buf += chunk
        line, buf = buf.split(b"\n", 1)
        self._bufs[role] = buf
        return json.loads(line)


class C3ManageServer:
    def __init__(self):
        _dir = os.path.dirname(__file__)
        cfg = read_json(os.path.join(_dir, "..", "config.json"))
        self._cfg = cfg["manage_server"]
        self._timeout = cfg["timeout"]
        self._queue_lease_timeout = cfg.get("queue_lease_timeout", 120)

        print("[manage] Manager starting up ...", flush=True)

        # seed users from pretreat into DB
        pretreat_dir = os.path.join(_dir, "..", "pretreat")
        db.seed_users(pretreat_dir)
        print("[manage] DB seeded from pretreat", flush=True)

        # reconcile ACTIVE/BUSY operations left over from a previous crash
        result = db.reconcile_on_startup()
        if result["count"]:
            print(f"[manage] reconciled {result['count']} stale operation(s) → FAILED", flush=True)
        if result["uncertain"]:
            write_task_status(
                "cracked",
                "Manager restarted while a BUSY operation existed — "
                "EXECUTE was already in flight, protocol state uncertain",
            )
            raise RuntimeError(
                "Uncertain protocol state after restart: BUSY operation found. "
                "Stop all C3 processes, run pretreat, then restart all components."
            )

        self._agents = AgentManager({
            "STEWARD": {"ip": cfg["steward"]["ip"],
                        "mgmt_port": self._cfg["mgmt_port_steward"]},
            "PEER0":   {"ip": cfg["peer0"]["ip"],
                        "mgmt_port": self._cfg["mgmt_port_peer0"]},
            "PEER1":   {"ip": cfg["peer1"]["ip"],
                        "mgmt_port": self._cfg["mgmt_port_peer1"]},
        }, timeout=self._timeout)

        self._req_counter = 0
        self._ports: dict[int, dict[str, int]] = {}  # op_id → {role: port}
        self._agent_ips = {r: cfg[r]["ip"]
                          for r in ("steward", "peer0", "peer1")}
        self._lock = threading.Lock()
        self._task_cracked = False
        self._agents_available = True
        self._app = self._create_app()

    def _next_request_id(self) -> str:
        self._req_counter += 1
        return f"r{self._req_counter}"

    def _reconnect_all(self) -> bool:
        """Reconnect all agents. Returns True iff all three succeeded.
        Updates _agents_available to reflect current connectivity."""
        ok = True
        for role in ("STEWARD", "PEER0", "PEER1"):
            try:
                self._agents.reconnect(role)
            except Exception as e:
                ok = False
                print(f"[manage] reconnect {role} failed: {e}")
        self._agents_available = ok
        return ok

    def _crack_task(self, op_id: int, error_code: str, info: str = "") -> None:
        """Halt the entire task: agents uncertain, task unrecoverable.
        Order: block new ops FIRST, then record, then best-effort cleanup.
        No self-recovery — cracked requires full process restart + pretreat."""
        with self._lock:
            self._task_cracked = True
        try:
            db.fail_operation(op_id, error_code)
        except Exception as e:
            print(f"[manage] fail_operation error: {e}")
        try:
            write_task_status("cracked", info or f"{error_code}: op_id={op_id}")
        except Exception as e:
            print(f"[manage] write_task_status error: {e}")
        try:
            db.fail_all_live_operations("TASK_CRACKED")
        except Exception as e:
            print(f"[manage] fail_all_live_operations error: {e}")
        self._ports.clear()

    # Flask app

    def _create_app(self) -> Flask:
        app = Flask(__name__)
        cfg = self._cfg

        # reserve

        def _reserve_impl(user_id: str, prot_type: str, allowed_users: dict):
            if not user_id:
                return jsonify({"status": "FAILED"}), 400
            if user_id not in allowed_users:
                return jsonify({"status": "REJECTED",
                                "reason": "unknown user_id"})
            with self._lock:
                live = db.get_live_operation(user_id, prot_type)
                if live:
                    return jsonify({"status": "ALREADY",
                                    "_op_id": live["op_id"]})
                op_id = db.create_reserved_operation(user_id, prot_type)
                return jsonify({"status": "SUCCESSFUL",
                                "_op_id": op_id})

        @app.route("/reserve_join", methods=["POST"])
        def reserve_join():
            if self._task_cracked:
                return jsonify({"status": "FAILED",
                                "reason": "TASK_CRACKED"}), 503
            if not self._agents_available:
                return jsonify({"status": "FAILED",
                                "reason": "AGENT_UNAVAILABLE"}), 503
            data = request.get_json(silent=True) or {}
            return _reserve_impl(data.get("user_id"), "JOIN", SET_HOLDER_USERS)

        @app.route("/reserve_update", methods=["POST"])
        def reserve_update():
            if self._task_cracked:
                return jsonify({"status": "FAILED",
                                "reason": "TASK_CRACKED"}), 503
            if not self._agents_available:
                return jsonify({"status": "FAILED",
                                "reason": "AGENT_UNAVAILABLE"}), 503
            data = request.get_json(silent=True) or {}
            return _reserve_impl(data.get("user_id"), "UPDATE", SET_HOLDER_USERS)

        @app.route("/reserve_query", methods=["POST"])
        def reserve_query():
            if self._task_cracked:
                return jsonify({"status": "FAILED",
                                "reason": "TASK_CRACKED"}), 503
            if not self._agents_available:
                return jsonify({"status": "FAILED",
                                "reason": "AGENT_UNAVAILABLE"}), 503
            data = request.get_json(silent=True) or {}
            return _reserve_impl(data.get("user_id"), "QUERY", QUERIER_USERS)

        @app.route("/reserve_quit", methods=["POST"])
        def reserve_quit():
            if self._task_cracked:
                return jsonify({"status": "FAILED",
                                "reason": "TASK_CRACKED"}), 503
            if not self._agents_available:
                return jsonify({"status": "FAILED",
                                "reason": "AGENT_UNAVAILABLE"}), 503
            data = request.get_json(silent=True) or {}
            return _reserve_impl(data.get("user_id"), "QUIT", SET_HOLDER_USERS)

        # execute

        def _check_biz_precondition(user_id: str, prot_type: str) -> str | None:
            """Return rejection reason or None if precondition is met."""
            user = db.get_user(user_id)
            if not user:
                return "unknown user_id"
            if prot_type == "JOIN":
                if user["status"] == "JOINED":
                    return "already joined"
            elif prot_type in ("UPDATE", "QUIT"):
                if user["status"] != "JOINED":
                    return "not joined yet"
            return None

        @app.route("/execute", methods=["POST"])
        def execute():
            data = request.get_json(silent=True) or {}
            user_id = data.get("user_id")
            prot_type = data.get("prot_type")
            op_id = data.get("op_id")  # optional, for internal pinning

            if not user_id or not prot_type:
                return jsonify({"status": "NOT_FOUND"}), 400

            # ——— resolve operation ———
            if op_id is not None:
                # pinned: verify op_id still matches user+service
                op = db.get_operation(op_id)
                if not op or op["user_id"] != user_id or op["prot_type"] != prot_type:
                    return jsonify({"status": "NOT_FOUND"})
            else:
                op = db.get_live_operation(user_id, prot_type)
                if not op:
                    return jsonify({"status": "NOT_RESERVED"})
                op_id = op["op_id"]

            # ——— terminal states ———
            status = op["status"]
            if status == "DONE":
                return jsonify({"status": "DONE"})
            if status == "FAILED":
                return jsonify({"status": "FAILED",
                                "reason": op.get("error_code", "unknown")})
            if status == "REMOVED":
                return jsonify({"status": "REMOVED"})

            if self._task_cracked:
                return jsonify({"status": "FAILED",
                                "reason": "TASK_CRACKED"}), 503
            if not self._agents_available:
                return jsonify({"status": "FAILED",
                                "reason": "AGENT_UNAVAILABLE"}), 503

            # ——— RESERVED: enqueue + business check ———
            if status == "RESERVED":
                reason = _check_biz_precondition(user_id, prot_type)
                if reason:
                    return jsonify({"status": "REJECTED", "reason": reason})

                with self._lock:
                    # re-read in case of race
                    op2 = db.get_operation(op_id)
                    if op2["status"] == "RESERVED":
                        db.enqueue_reserved(op_id)
                    # fall through to re-read status below
                    op = db.get_operation(op_id)
                    status = op["status"]

            with self._lock:
                if self._task_cracked:
                    return jsonify({"status": "FAILED",
                                    "reason": "TASK_CRACKED"}), 503
                if not self._agents_available:
                    return jsonify({"status": "FAILED",
                                    "reason": "AGENT_UNAVAILABLE"}), 503
                op = db.get_operation(op_id)
                status = op["status"]

                if status == "QUEUED":
                    db.touch_operation(op_id)

                    # check again — a concurrent execute might have transitioned
                    if op["status"] == "QUEUED":
                        activated = self._try_activate(op_id)
                        if activated:
                            op = db.get_operation(op_id)
                            if op["status"] == "ACTIVE":
                                return self._start_execute(op)
                    pos = db.queue_position_of(op_id)
                    active_op = db.get_active()
                    ahead = (pos - 1 if pos else 0) + (1 if active_op else 0)
                    retry_after = min(8.0, max(1.0, 1.5 * math.sqrt(ahead + 1)))
                    return jsonify({"status": "WAITING",
                                    "position": pos,
                                    "ahead": ahead,
                                    "retry_after": round(retry_after, 1),
                                    "_op_id": op_id})

                if status == "ACTIVE":
                    return self._start_execute(op)

                if status == "BUSY":
                    # Only the caller whose _start_execute succeeded gets agents.
                    # Subsequent callers see BUSY without agents.
                    cached = self._ports.get(op_id)
                    if cached:
                        return jsonify({"status": "BUSY",
                                        "agents": {
                                            "STEWARD": {
                                                "ip": self._agent_ips["steward"],
                                                "port": cached["STEWARD"]},
                                            "PEER0": {
                                                "ip": self._agent_ips["peer0"],
                                                "port": cached["PEER0"]},
                                            "PEER1": {
                                                "ip": self._agent_ips["peer1"],
                                                "port": cached["PEER1"]},
                                        }})
                    return jsonify({"status": "BUSY"})

                return jsonify({"status": op["status"],
                                "_op_id": op_id})

        return app

    def _start_execute(self, op: dict):
        """Transition ACTIVE → BUSY, spawn protocol thread, return ports."""
        op_id = op["op_id"]
        prot_type = op["prot_type"]
        user_id = op["user_id"]

        if prot_type == "QUIT":
            db.update_operation(op_id, "BUSY")
            threading.Thread(target=self._run_execute,
                             args=(op_id, user_id, prot_type),
                             daemon=True).start()
            return jsonify({"status": "BUSY"})

        ports = self._reserve_ports(op_id, user_id, prot_type)
        if ports is None:
            db.fail_operation(op_id, "RESERVE_FAILED")
            return jsonify({"status": "FAILED", "reason": "reserve_ports"})

        self._ports[op_id] = ports
        db.update_operation(op_id, "BUSY")
        threading.Thread(target=self._run_execute,
                         args=(op_id, user_id, prot_type),
                         daemon=True).start()
        return jsonify({"status": "BUSY",
                        "agents": {
                            "STEWARD": {
                                "ip": self._agent_ips["steward"],
                                "port": ports["STEWARD"]},
                            "PEER0": {
                                "ip": self._agent_ips["peer0"],
                                "port": ports["PEER0"]},
                            "PEER1": {
                                "ip": self._agent_ips["peer1"],
                                "port": ports["PEER1"]},
                        }})

    # Agent protocol

    # max stale messages to skip per role before giving up
    _MAX_SKIP = 32

    def _reserve_ports(self, op_id: int, user_id: str, prot_type: str
                       ) -> dict[str, int] | None:
        # reset socket timeouts (previous op may have shrunk them for deadline)
        for role in ("STEWARD", "PEER0", "PEER1"):
            try:
                self._agents.settimeout(role, self._timeout)
            except Exception:
                pass
        req_id = self._next_request_id()
        try:
            for role in ("STEWARD", "PEER0", "PEER1"):
                self._agents.send(role, {"request_id": req_id,
                                         "cmd": "RESERVE",
                                         "user_id": user_id,
                                         "prot_type": prot_type})
        except (OSError, ConnectionError):
            self._reconnect_all()
            return None

        ports: dict[str, int] = {}
        failed = False
        for role in ("STEWARD", "PEER0", "PEER1"):
            # drain stale messages from previous failed operations
            ack = None
            for _ in range(self._MAX_SKIP):
                try:
                    ack = self._agents.recv(role)
                except (ConnectionError, socket.timeout):
                    failed = True
                    try:
                        self._agents.reconnect(role)
                    except Exception:
                        pass
                    break
                if ack.get("request_id") == req_id:
                    break
                # stale message (wrong request_id) — discard and continue
            else:
                failed = True  # exhausted skip limit

            if failed:
                # drain second response too before moving to next role
                try:
                    self._agents.recv(role)
                except (ConnectionError, socket.timeout):
                    pass
                continue

            if ack.get("event") != "READY":
                failed = True
                continue

            try:
                resp = self._agents.recv(role)
            except (ConnectionError, socket.timeout):
                failed = True
                try:
                    self._agents.reconnect(role)
                except Exception:
                    pass
                continue
            if (resp.get("event") != "RESERVED"
                    or resp.get("request_id") != req_id):
                failed = True
                continue
            ports[role] = resp["port"]

        if failed:
            self._reconnect_all()
            return None
        return ports

    def _run_execute(self, op_id: int, user_id: str, prot_type: str):
        req_id = self._next_request_id()
        deadline = time.monotonic() + self._timeout

        # --- preflight: verify all tokens present for UPDATE/QUIT ---
        agent_tokens: dict[str, str] = {}
        if prot_type in ("UPDATE", "QUIT"):
            for role in ("STEWARD", "PEER0", "PEER1"):
                tok = db.get_agent_token(user_id, role)
                if not tok:
                    self._ports.pop(op_id, None)
                    db.fail_operation(op_id, f"MISSING_TOKEN_{role}")
                    return
                agent_tokens[role] = tok

        # --- send EXECUTE to all three ---
        # Once EXECUTE is sent, any failure here is UNCERTAIN → cracked.
        try:
            for role in ("STEWARD", "PEER0", "PEER1"):
                msg = {"request_id": req_id,
                       "cmd": "EXECUTE",
                       "user_id": user_id,
                       "prot_type": prot_type}
                if prot_type in ("UPDATE", "QUIT"):
                    msg["token"] = agent_tokens[role]
                self._agents.send(role, msg)
        except (OSError, ConnectionError):
            self._crack_task(op_id, "AGENT_DISCONNECTED",
                f"EXECUTE dispatch uncertain: op={op_id} user={user_id}")
            return

        # --- collect READY acks (per-recv deadline timeout) ---
        ready_ok = True
        for role in ("STEWARD", "PEER0", "PEER1"):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                ready_ok = False
                break
            self._agents.settimeout(role, min(self._timeout, max(0.1, remaining)))
            ack = None
            for _ in range(self._MAX_SKIP):
                try:
                    ack = self._agents.recv(role)
                except (ConnectionError, socket.timeout):
                    ready_ok = False
                    try:
                        self._agents.reconnect(role)
                    except Exception:
                        pass
                    break
                if ack.get("request_id") == req_id:
                    break
            else:
                ready_ok = False
            if not ready_ok:
                break
            if ack.get("event") != "READY":
                ready_ok = False
                break

        if not ready_ok:
            self._crack_task(op_id, "EXECUTE_READY_FAILED",
                f"READY not received from all agents: op={op_id}")
            return

        # --- collect terminal events from all three agents (per-recv deadline) ---
        tokens: dict[str, str] = {}
        all_done = True
        for role in ("STEWARD", "PEER0", "PEER1"):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                all_done = False
                break
            self._agents.settimeout(role, min(self._timeout, max(0.1, remaining)))
            event = None
            for _ in range(self._MAX_SKIP):
                try:
                    event = self._agents.recv(role)
                except (ConnectionError, socket.timeout):
                    all_done = False
                    try:
                        self._agents.reconnect(role)
                    except Exception:
                        pass
                    break
                if event.get("request_id") == req_id:
                    break
            else:
                all_done = False
            if not all_done:
                break
            if event.get("event") != "DONE":
                all_done = False
                break
            tok = event.get("agent_token", "")
            if tok:
                tokens[role] = tok

        if not all_done:
            self._crack_task(op_id, "PROTOCOL_FAILED",
                f"{prot_type} protocol incomplete: op={op_id} user={user_id}")
            return

        # --- success: update DB atomically ---
        self._ports.pop(op_id, None)
        if prot_type == "JOIN":
            if set(tokens.keys()) != {"STEWARD", "PEER0", "PEER1"}:
                self._crack_task(op_id, "INCOMPLETE_TOKENS",
                    f"JOIN op_id={op_id} user={user_id} "
                    f"got tokens from {sorted(tokens.keys())}")
                return
            try:
                db.complete_join(op_id, user_id, tokens)
            except Exception as e:
                self._crack_task(op_id, "DB_COMMIT_FAILED",
                    f"JOIN DB commit failed: op={op_id}: {e}")
        elif prot_type == "QUIT":
            try:
                db.complete_quit(op_id, user_id)
            except Exception as e:
                self._crack_task(op_id, "DB_COMMIT_FAILED",
                    f"QUIT DB commit failed: op={op_id}: {e}")
        else:
            try:
                db.update_operation(op_id, "DONE", queue_pos=None)
            except Exception as e:
                self._crack_task(op_id, "DB_COMMIT_FAILED",
                    f"{prot_type} completed on agents but DB update failed: {e}")

    # queue

    def _evict_stale_queue_heads(self):
        """Remove QUEUED ops whose lease has expired (dead client at head)."""
        now_ts = time.time()
        while True:
            first = db.first_queued()
            if first is None:
                return
            try:
                updated_ts = time.mktime(
                    time.strptime(first["updated_at"], "%Y-%m-%dT%H:%M:%S"))
            except (ValueError, OverflowError):
                break
            if now_ts - updated_ts > self._queue_lease_timeout:
                db.remove_from_queue(first["op_id"])
                continue
            break

    def _try_activate(self, op_id: int) -> bool:
        """Promote *op_id* QUEUED → ACTIVE only if it is at the head of the queue
        and no other operation is ACTIVE/BUSY.  Returns True on success."""
        if self._task_cracked:
            return False
        if db.get_active() is not None:
            return False
        self._evict_stale_queue_heads()
        first = db.first_queued()
        if first is None:
            return False
        if first["op_id"] != op_id:
            return False
        db.promote_first_queued()
        return True

    # run

    def run(self):
        print(f"[manage] trying to connect to agents ...", flush=True)
        self._reconnect_all()
        if self._agents_available:
            print("[manage] all agents connected", flush=True)
        else:
            print("[manage] WARNING: some agents are not reachable — "
                  "requests will get AGENT_UNAVAILABLE until agents start", flush=True)
        print(f"[manage] listening on http://{self._cfg['server_ip']}:{self._cfg['http_port']}", flush=True)
        self._app.run(host=self._cfg['server_ip'],
                      port=self._cfg['http_port'], debug=False)


def run():
    C3ManageServer().run()
