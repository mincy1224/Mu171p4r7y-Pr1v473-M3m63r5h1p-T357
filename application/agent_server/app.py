# draft
import json
import os
import socket
import threading

import mpmt

from _c3_io import ensure_dir, read_json

MAX_FRAME_BYTES = 1_048_576  # 1 MiB


class C3AgentServer:
    def __init__(self, role: str):
        if role not in ("steward", "peer0", "peer1"):
            raise ValueError(f"unknown role: {role}")

        _dir = os.path.dirname(__file__)
        cfg = read_json(os.path.join(_dir, "..", "config.json"))

        self._role = role
        self._cfg = cfg[role]
        self._timeout = cfg["timeout"]
        self._conn: socket.socket | None = None
        self._reserved: dict[str, tuple[int, socket.socket]] = {}
        self._user_id_to_token: dict[str, str] = {}
        self._send_lock = threading.Lock()

        # mpmt protocol instance
        _NEXT = {"steward": "peer0", "peer0": "peer1", "peer1": "steward"}
        nxt_role = _NEXT[role]
        nxt_cfg = cfg[nxt_role]
        role_cfg = self._cfg
        _app_dir = os.path.join(_dir, "..")

        preset = read_json(os.path.join(_app_dir, "pretreat", "pre.json"))

        ch_prev = mpmt.Channel(role_cfg["ch_prev_port"])
        ch_nxt = mpmt.Channel(nxt_cfg["ip"], role_cfg["ch_nxt_port"])

        storage_dir = os.path.join(_app_dir, cfg["storage_root_dir"], role)
        ensure_dir(storage_dir)

        _ROLE_MAP = {"steward": mpmt.ServerRole.STEWARD,
                     "peer0":   mpmt.ServerRole.PEER0,
                     "peer1":   mpmt.ServerRole.PEER1}
        server_role = _ROLE_MAP[role]

        if role == "steward":
            hash_seed_list = [bytes.fromhex(h) for h in preset["hash_seed_list"]]
            self.prot_inst = mpmt.AgentServer(
                server_role=server_role,
                set_size=preset["set_size"],
                fpr_mantissa=preset["fpr_mantissa"],
                fpr_exponent=preset["fpr_exponent"],
                storage_dir=storage_dir,
                ch_prev=ch_prev,
                ch_nxt=ch_nxt,
                hash_seed_list=hash_seed_list,
                cores=cfg["dpf_cores"],
            )
        else:
            self.prot_inst = mpmt.AgentServer(
                server_role=server_role,
                set_size=preset["set_size"],
                fpr_mantissa=preset["fpr_mantissa"],
                fpr_exponent=preset["fpr_exponent"],
                storage_dir=storage_dir,
                ch_prev=ch_prev,
                ch_nxt=ch_nxt,
                cores=cfg["dpf_cores"],
            )

    # reserve key
    def _reserve_key(self, user_id: str, prot_type: str) -> str:
        return f"{user_id}||{prot_type}"

    # command handlers

    def cmd_reserve(self, user_id: str, prot_type: str,
                    request_id: str = "") -> None:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind((self._cfg["ip"], 0))
            s.listen(1)
            port = s.getsockname()[1]
            self._reserved[self._reserve_key(user_id, prot_type)] = (port, s)
            self._send({"event": "RESERVED", "user_id": user_id,
                        "port": port, "request_id": request_id})
        except Exception as e:
            self._send({"event": "ERROR", "user_id": user_id,
                        "msg": str(e), "request_id": request_id})

    def cmd_execute(self, user_id: str, prot_type: str,
                    request_id: str = "") -> None:
        try:
            match prot_type:
                case "JOIN":
                    entry = self._reserved.pop(
                        self._reserve_key(user_id, "JOIN"), None)
                    if entry is None:
                        self._send({"event": "ERROR", "user_id": user_id,
                                    "msg": "no reserved port",
                                    "request_id": request_id})
                        return
                    port, reserved_sock = entry
                    reserved_sock.close()
                    ch_set_holder = mpmt.Channel(port=port)
                    agent_token = self.prot_inst.response_share_bf(
                        prot_type=mpmt.ProtType.JOIN,
                        ch_set_holder=ch_set_holder,
                    )
                    self._user_id_to_token[user_id] = agent_token
                    self._send({"event": "DONE", "user_id": user_id,
                                "agent_token": agent_token,
                                "request_id": request_id})

                case "UPDATE":
                    entry = self._reserved.pop(
                        self._reserve_key(user_id, "UPDATE"), None)
                    if entry is None:
                        self._send({"event": "ERROR", "user_id": user_id,
                                    "msg": "no reserved port",
                                    "request_id": request_id})
                        return
                    if user_id not in self._user_id_to_token:
                        self._send({"event": "ERROR", "user_id": user_id,
                                    "msg": "not joined yet",
                                    "request_id": request_id})
                        return
                    port, reserved_sock = entry
                    reserved_sock.close()
                    ch_set_holder = mpmt.Channel(port=port)
                    token = self._user_id_to_token[user_id]
                    self.prot_inst.response_share_bf(
                        prot_type=mpmt.ProtType.UPDATE,
                        ch_set_holder=ch_set_holder,
                        token=token,
                    )
                    self._send({"event": "DONE", "user_id": user_id,
                                "request_id": request_id})

                case "QUERY":
                    entry = self._reserved.pop(
                        self._reserve_key(user_id, "QUERY"), None)
                    if entry is None:
                        self._send({"event": "ERROR", "user_id": user_id,
                                    "msg": "no reserved port",
                                    "request_id": request_id})
                        return
                    port, reserved_sock = entry
                    reserved_sock.close()
                    ch_querier = mpmt.Channel(port=port)
                    self.prot_inst.response_query(ch_querier=ch_querier)
                    self._send({"event": "DONE", "user_id": user_id,
                                "request_id": request_id})

                case "QUIT":
                    if user_id not in self._user_id_to_token:
                        self._send({"event": "ERROR", "user_id": user_id,
                                    "msg": "not joined yet",
                                    "request_id": request_id})
                        return
                    token = self._user_id_to_token.pop(user_id)
                    self.prot_inst.response_share_bf(
                        prot_type=mpmt.ProtType.QUIT,
                        token=token,
                    )
                    self._send({"event": "DONE", "user_id": user_id,
                                "request_id": request_id})

                case _:
                    self._send({"event": "ERROR", "user_id": user_id,
                                "msg": f"unknown prot_type: {prot_type}",
                                "request_id": request_id})
        except Exception as e:
            self._send({"event": "ERROR", "user_id": user_id,
                        "msg": str(e), "request_id": request_id})

    _INSTRUCTIONS: dict[str, str] = {
        "RESERVE": "cmd_reserve",
        "EXECUTE": "cmd_execute",
    }

    # network

    def run(self) -> None:
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind((self._cfg["ip"], self._cfg["mgmt_port"]))
        srv.listen(1)
        print(f"[{self._role}] listening on :{self._cfg['mgmt_port']}")

        while True:
            self._conn, addr = srv.accept()
            print(f"[{self._role}] management connected from {addr}")
            try:
                self._recv_loop()
            except (ConnectionError, OSError) as e:
                print(f"[{self._role}] management disconnected: {e}")
            except Exception as e:
                print(f"[{self._role}] management error: {e}")

    def _recv_loop(self) -> None:
        buf = b""
        while True:
            chunk = self._conn.recv(4096)
            if not chunk:
                return
            buf += chunk
            if len(buf) > MAX_FRAME_BYTES:
                raise ConnectionError("frame too large")
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                try:
                    msg = json.loads(line)
                except json.JSONDecodeError:
                    continue
                self._dispatch(msg)

    def _dispatch(self, cmd: dict) -> None:
        name = cmd.get("cmd", "")
        method_name = self._INSTRUCTIONS.get(name)
        if method_name is None:
            self._send({"event": "ERROR",
                        "user_id": cmd.get("user_id", ""),
                        "request_id": cmd.get("request_id", ""),
                        "msg": f"unknown cmd: {name}"})
            return

        user_id = cmd.get("user_id", "")
        prot_type = cmd.get("prot_type", "")
        request_id = cmd.get("request_id", "")
        self._send({"event": "READY", "user_id": user_id,
                    "request_id": request_id})
        method = getattr(self, method_name)
        threading.Thread(target=method,
                         args=(user_id, prot_type, request_id),
                         daemon=True).start()

    def _send(self, msg: dict) -> None:
        with self._send_lock:
            self._conn.sendall(json.dumps(msg).encode() + b"\n")
