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
        self._conns: dict[str, socket.socket] = {}
        self._bufs: dict[str, bytes] = {}
        for role, ac in agents_cfg.items():
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(timeout)
            s.connect((ac["ip"], ac["mgmt_port"]))
            self._conns[role] = s
            self._bufs[role] = b""

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
            if db.is_already_queued(user_id):
                return jsonify({"status": "ALREADY"})

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
            if db.is_already_queued(user_id):
                return jsonify({"status": "ALREADY"})

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
            if db.is_already_queued(user_id):
                return jsonify({"status": "ALREADY"})

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
            if db.is_already_queued(user_id):
                return jsonify({"status": "ALREADY"})

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
                                "reason": f"overtime_{op['overtime_count']}"})
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
                                    "ip": cfg["server_ip"],
                                    "port_steward": cached.get("STEWARD"),
                                    "port_peer0": cached.get("PEER0"),
                                    "port_peer1": cached.get("PEER1")})

                return jsonify({"status": "NOT_FOUND"})

        return app

    def _start_execute(self, op: dict):
        """Transition ACTIVE → BUSY, spawn protocol thread, return ports."""
        op_id = op["op_id"]
        prot_type = op["prot_type"]
        user_id = op["user_id"]
        cfg = self._cfg

        if prot_type == "QUIT":
            db.update_operation(op_id, "BUSY")
            threading.Thread(target=self._run_execute,
                             args=(op_id, user_id, prot_type),
                             daemon=True).start()
            return jsonify({"status": "BUSY"})

        ports = self._reserve_ports(op_id, user_id, prot_type)
        if ports is None:
            db.update_operation(op_id, "FAILED", queue_pos=None)
            return jsonify({"status": "FAILED", "reason": "reserve_ports"})

        self._ports[op_id] = ports
        db.update_operation(op_id, "BUSY")
        threading.Thread(target=self._run_execute,
                         args=(op_id, user_id, prot_type),
                         daemon=True).start()
        return jsonify({"status": "BUSY",
                        "ip": cfg["server_ip"],
                        **{f"port_{r.lower()}": ports[r]
                           for r in ("STEWARD", "PEER0", "PEER1")}})

    # Agent protocol

    def _reserve_ports(self, op_id: int, user_id: str, prot_type: str
                       ) -> dict[str, int] | None:
        req_id = self._next_request_id()
        for role in ("STEWARD", "PEER0", "PEER1"):
            self._agents.send(role, {"request_id": req_id,
                                     "cmd": "RESERVE",
                                     "user_id": user_id,
                                     "prot_type": prot_type})

        ports: dict[str, int] = {}
        for role in ("STEWARD", "PEER0", "PEER1"):
            try:
                ack = self._agents.recv(role)
            except (ConnectionError, socket.timeout):
                return None
            if ack.get("event") != "READY" or ack.get("request_id") != req_id:
                return None
            try:
                resp = self._agents.recv(role)
            except (ConnectionError, socket.timeout):
                return None
            if resp.get("event") != "RESERVED" or resp.get("request_id") != req_id:
                return None
            ports[role] = resp["port"]
        return ports

    def _run_execute(self, op_id: int, user_id: str, prot_type: str):
        req_id = self._next_request_id()
        deadline = time.monotonic() + self._timeout

        for role in ("STEWARD", "PEER0", "PEER1"):
            self._agents.send(role, {"request_id": req_id,
                                     "cmd": "EXECUTE",
                                     "user_id": user_id,
                                     "prot_type": prot_type})

        for role in ("STEWARD", "PEER0", "PEER1"):
            try:
                ack = self._agents.recv(role)
            except (ConnectionError, socket.timeout):
                self._handle_overtime(op_id)
                return
            if ack.get("event") != "READY" or ack.get("request_id") != req_id:
                self._handle_overtime(op_id)
                return

        steward_event = None
        for role in ("STEWARD", "PEER0", "PEER1"):
            try:
                if time.monotonic() > deadline:
                    self._handle_overtime(op_id)
                    return
                event = self._agents.recv(role)
            except (ConnectionError, socket.timeout):
                self._handle_overtime(op_id)
                return
            if event.get("event") == "ERROR":
                self._handle_overtime(op_id)
                return
            if event.get("request_id") != req_id:
                self._handle_overtime(op_id)
                return
            if role == "STEWARD":
                steward_event = event

        token = steward_event.get("agent_token", "") if steward_event else ""
        db.update_operation(op_id, "DONE", queue_pos=None)

        if prot_type == "JOIN":
            if token:
                db.set_token(user_id, token)
            db.update_user_status(user_id, "JOINED")
        elif prot_type == "QUIT":
            db.delete_token(user_id)
            db.update_user_status(user_id, "QUITTED")

    # queue

    def _handle_overtime(self, op_id: int):
        op = db.get_operation(op_id)
        if not op:
            return
        cnt = op["overtime_count"] + 1

        if cnt >= 3:
            db.remove_from_queue(op_id)
            return

        if cnt == 1:
            # move back 5 positions
            qsize = db.queue_size()
            old_pos = op.get("queue_pos") or 0
            new_pos = min(old_pos + 5, qsize + 1)
        else:
            # back of the queue
            new_pos = db.queue_size() + 1

        db.update_operation(op_id, "QUEUED", queue_pos=new_pos,
                            overtime_count=cnt)
        db.reorder_queue()

    def _try_advance_queue(self):
        if db.get_active() is None:
            db.promote_first_queued()

    # run

    def run(self):
        self._app.run(host=self._cfg["server_ip"],
                      port=self._cfg["http_port"], debug=False)


def run():
    C3ManageServer().run()
