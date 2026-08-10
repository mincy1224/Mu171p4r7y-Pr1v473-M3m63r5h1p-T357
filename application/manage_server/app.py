# draft
import json
import os
import socket
import threading
import time

from flask import Flask, request, jsonify
from _c3_io import read_json
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
        for role in agents_cfg:
            self._connect(role)

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

    def reconnect(self, role: str) -> None:
        """Public: reconnect a dropped Agent."""
        self._connect(role)

    def send(self, role: str, msg: dict) -> None:
        self._conns[role].sendall(json.dumps(msg).encode() + b"\n")

    def recv(self, role: str) -> dict:
        buf = self._bufs[role]
        while b"\n" not in buf:
            chunk = self._conns[role].recv(4096)
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

        # seed users from pretreat into DB
        pretreat_dir = os.path.join(_dir, "..", "pretreat")
        db.seed_users(pretreat_dir)

        # reconcile ACTIVE/BUSY operations left over from a previous crash
        reconciled = db.reconcile_on_startup()
        if reconciled:
            print(f"[manage] reconciled {reconciled} stale operation(s) → FAILED")

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
        self._app = self._create_app()

    def _next_request_id(self) -> str:
        self._req_counter += 1
        return f"r{self._req_counter}"

    # Flask app

    def _create_app(self) -> Flask:
        app = Flask(__name__)
        cfg = self._cfg

        # reserve

        @app.route("/reserve_join", methods=["POST"])
        def reserve_join():
            data = request.get_json(silent=True) or {}
            user_id = data.get("user_id")
            if not user_id:
                return jsonify({"status": "FAILED"}), 400
            if user_id not in SET_HOLDER_USERS:
                return jsonify({"status": "REJECTED",
                                "reason": "unknown user_id"})

            user = db.get_user(user_id)
            if user and user["status"] == "JOINED":
                return jsonify({"status": "REJECTED",
                                "reason": "already joined"})
            with self._lock:
                active = db.get_active_operation_by_user(user_id)
                if active:
                    return jsonify({"status": "ALREADY",
                                    "op_id": active["op_id"]})
                pos = db.next_queue_pos()
                op_id = db.create_operation(user_id, "JOIN", pos)
            return jsonify({"status": "SUCCESSFUL", "op_id": op_id})

        @app.route("/reserve_update", methods=["POST"])
        def reserve_update():
            data = request.get_json(silent=True) or {}
            user_id = data.get("user_id")
            if not user_id:
                return jsonify({"status": "FAILED"}), 400
            if user_id not in SET_HOLDER_USERS:
                return jsonify({"status": "REJECTED",
                                "reason": "unknown user_id"})

            user = db.get_user(user_id)
            if not user or user["status"] != "JOINED":
                return jsonify({"status": "REJECTED",
                                "reason": "not joined yet"})
            with self._lock:
                active = db.get_active_operation_by_user(user_id)
                if active:
                    return jsonify({"status": "ALREADY",
                                    "op_id": active["op_id"]})
                pos = db.next_queue_pos()
                op_id = db.create_operation(user_id, "UPDATE", pos)
            return jsonify({"status": "SUCCESSFUL", "op_id": op_id})

        @app.route("/reserve_query", methods=["POST"])
        def reserve_query():
            data = request.get_json(silent=True) or {}
            user_id = data.get("user_id")
            if not user_id:
                return jsonify({"status": "FAILED"}), 400
            if user_id not in QUERIER_USERS:
                return jsonify({"status": "REJECTED",
                                "reason": "unknown user_id"})
            with self._lock:
                active = db.get_active_operation_by_user(user_id)
                if active:
                    return jsonify({"status": "ALREADY",
                                    "op_id": active["op_id"]})
                pos = db.next_queue_pos()
                op_id = db.create_operation(user_id, "QUERY", pos)
            return jsonify({"status": "SUCCESSFUL", "op_id": op_id})

        @app.route("/reserve_quit", methods=["POST"])
        def reserve_quit():
            data = request.get_json(silent=True) or {}
            user_id = data.get("user_id")
            if not user_id:
                return jsonify({"status": "FAILED"}), 400
            if user_id not in SET_HOLDER_USERS:
                return jsonify({"status": "REJECTED",
                                "reason": "unknown user_id"})

            user = db.get_user(user_id)
            if not user or user["status"] != "JOINED":
                return jsonify({"status": "REJECTED",
                                "reason": "not joined yet"})
            with self._lock:
                active = db.get_active_operation_by_user(user_id)
                if active:
                    return jsonify({"status": "ALREADY",
                                    "op_id": active["op_id"]})
                pos = db.next_queue_pos()
                op_id = db.create_operation(user_id, "QUIT", pos)
            return jsonify({"status": "SUCCESSFUL", "op_id": op_id})

        # execute

        @app.route("/execute", methods=["POST"])
        def execute():
            data = request.get_json(silent=True) or {}
            op_id = data.get("op_id")
            if not op_id:
                return jsonify({"status": "NOT_FOUND"}), 400

            op = db.get_operation(op_id)
            if not op:
                return jsonify({"status": "NOT_FOUND"})

            user = db.get_user(op["user_id"])
            if not user:
                return jsonify({"status": "NOT_FOUND"})
            role = user["role"]
            prot_type = op["prot_type"]
            if role == "set_holder" and prot_type not in ("JOIN", "UPDATE", "QUIT"):
                return jsonify({"status": "REJECTED",
                                "reason": "unauthorized"})
            if role == "querier" and prot_type != "QUERY":
                return jsonify({"status": "REJECTED",
                                "reason": "unauthorized"})

            status = op["status"]
            if status == "DONE":
                return jsonify({"status": "DONE"})
            if status == "FAILED":
                return jsonify({"status": "FAILED",
                                "reason": op.get("error_code", "unknown")})
            if status == "REMOVED":
                return jsonify({"status": "REMOVED"})

            with self._lock:
                op = db.get_operation(op_id)
                status = op["status"]

                if status == "QUEUED":
                    self._try_advance_queue()
                    op = db.get_operation(op_id)
                    if op["status"] == "ACTIVE":
                        return self._start_execute(op)
                    pos = db.queue_position_of(op_id)
                    return jsonify({"status": "WAITING",
                                    "position": pos,
                                    "remaining": pos - 1 if pos else 0})

                if status == "ACTIVE":
                    return self._start_execute(op)

                if status == "BUSY":
                    cached = self._ports.get(op_id, {})
                    return jsonify({"status": "BUSY",
                                    "agents": {
                                        "STEWARD": {
                                            "ip": self._agent_ips["steward"],
                                            "port": cached.get("STEWARD")},
                                        "PEER0": {
                                            "ip": self._agent_ips["peer0"],
                                            "port": cached.get("PEER0")},
                                        "PEER1": {
                                            "ip": self._agent_ips["peer1"],
                                            "port": cached.get("PEER1")},
                                    }})

                return jsonify({"status": "NOT_FOUND"})

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
        req_id = self._next_request_id()
        try:
            for role in ("STEWARD", "PEER0", "PEER1"):
                self._agents.send(role, {"request_id": req_id,
                                         "cmd": "RESERVE",
                                         "user_id": user_id,
                                         "prot_type": prot_type})
        except (OSError, ConnectionError):
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
                continue
            if (resp.get("event") != "RESERVED"
                    or resp.get("request_id") != req_id):
                failed = True
                continue
            ports[role] = resp["port"]

        if failed:
            return None
        return ports

    def _run_execute(self, op_id: int, user_id: str, prot_type: str):
        req_id = self._next_request_id()
        deadline = time.monotonic() + self._timeout

        # --- send EXECUTE to all three (with per-agent token for UPDATE/QUIT) ---
        try:
            for role in ("STEWARD", "PEER0", "PEER1"):
                msg = {"request_id": req_id,
                       "cmd": "EXECUTE",
                       "user_id": user_id,
                       "prot_type": prot_type}
                if prot_type in ("UPDATE", "QUIT"):
                    tok = db.get_agent_token(user_id, role)
                    if tok:
                        msg["token"] = tok
                self._agents.send(role, msg)
        except (OSError, ConnectionError):
            self._ports.pop(op_id, None)
            db.fail_operation(op_id, "AGENT_DISCONNECTED")
            return

        # --- collect READY acks ---
        ready_ok = True
        for role in ("STEWARD", "PEER0", "PEER1"):
            ack = None
            for _ in range(self._MAX_SKIP):
                try:
                    ack = self._agents.recv(role)
                except (ConnectionError, socket.timeout):
                    ready_ok = False
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
            self._ports.pop(op_id, None)
            db.fail_operation(op_id, "EXECUTE_READY_FAILED")
            return

        # --- collect terminal events from all three agents ---
        tokens: dict[str, str] = {}
        all_done = True
        for role in ("STEWARD", "PEER0", "PEER1"):
            if time.monotonic() > deadline:
                all_done = False
                break
            event = None
            for _ in range(self._MAX_SKIP):
                try:
                    event = self._agents.recv(role)
                except (ConnectionError, socket.timeout):
                    all_done = False
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
            self._ports.pop(op_id, None)
            db.fail_operation(op_id, "PROTOCOL_FAILED")
            return

        # --- success: update DB atomically ---
        self._ports.pop(op_id, None)
        if prot_type == "JOIN":
            db.complete_join(op_id, user_id, tokens)
        elif prot_type == "QUIT":
            db.complete_quit(op_id, user_id)
        else:
            db.update_operation(op_id, "DONE", queue_pos=None)

    # queue

    def _handle_overtime(self, op_id: int):
        """Mark an operation FAILED after timeout.
        No automatic requeue — the old Agent protocol thread cannot be
        safely cancelled, so retrying would risk double execution."""
        self._ports.pop(op_id, None)
        db.fail_operation(op_id, "AGENT_TIMEOUT")

    def _try_advance_queue(self):
        if db.get_active() is None:
            db.promote_first_queued()

    # run

    def run(self):
        self._app.run(host=self._cfg["server_ip"],
                      port=self._cfg["http_port"], debug=False)


def run():
    C3ManageServer().run()
