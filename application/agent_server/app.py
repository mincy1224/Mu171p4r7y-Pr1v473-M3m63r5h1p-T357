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
        self._connect_timeout = cfg.get("connect_timeout", 5.0)
        self._conn: socket.socket | None = None
        self._reserved: dict[str, tuple[int, socket.socket]] = {}
        self._send_lock = threading.Lock()
        self._reserved_lock = threading.Lock()
        self._generation = 0  # incremented per Manager connection
        self._exec_lock = threading.Lock()
        self._exec_thread = None  # non-None while protocol is executing

        # save config for deferred init in run()
        self._app_dir = os.path.join(_dir, "..")
        self._cfg_root = cfg
        self._preset = read_json(os.path.join(self._app_dir, "pretreat", "pre.json"))

        _NEXT = {"steward": "peer0", "peer0": "peer1", "peer1": "steward"}
        self._nxt_role = _NEXT[role]
        self._nxt_cfg = cfg[self._nxt_role]

        _ROLE_MAP = {"steward": mpmt.ServerRole.STEWARD,
                     "peer0":   mpmt.ServerRole.PEER0,
                     "peer1":   mpmt.ServerRole.PEER1}
        self._server_role = _ROLE_MAP[role]

        self._storage_dir = os.path.join(self._app_dir, cfg["storage_root_dir"], role)
        ensure_dir(self._storage_dir)

        self.prot_inst = None  # created in run() after channels are ready

    # reserve key
    def _reserve_key(self, user_id: str, prot_type: str) -> str:
        return f"{user_id}||{prot_type}"

    # command handlers

    def cmd_reserve(self, user_id: str, prot_type: str,
                    request_id: str = "", _send_gen: int = 0,
                    token: str = "") -> None:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind((self._cfg["ip"], 0))
            s.listen(1)
            port = s.getsockname()[1]
            # guard: stale worker from previous Manager session — discard
            if _send_gen != self._generation:
                s.close()
                return
            key = self._reserve_key(user_id, prot_type)
            with self._reserved_lock:
                old = self._reserved.get(key)
                if old is not None:
                    try:
                        old[1].close()
                    except OSError:
                        pass
                self._reserved[key] = (port, s)
            self._send({"event": "RESERVED", "user_id": user_id,
                        "port": port, "request_id": request_id}, _gen=_send_gen)
        except Exception as e:
            self._send({"event": "ERROR", "user_id": user_id,
                        "msg": str(e), "request_id": request_id}, _gen=_send_gen)

    def cmd_execute(self, user_id: str, prot_type: str,
                    request_id: str = "", _send_gen: int = 0,
                    token: str = "") -> None:
        """token is supplied by Manager for UPDATE/QUIT (from DB)."""
        # guard against concurrent protocol execution
        with self._exec_lock:
            if self._exec_thread is not None:
                self._send({"event": "ERROR", "user_id": user_id,
                            "msg": "agent busy (previous operation still running)",
                            "request_id": request_id}, _gen=_send_gen)
                return
            self._exec_thread = threading.current_thread()
        try:
            match prot_type:
                case "JOIN" | "UPDATE" | "QUERY":
                    if prot_type in ("JOIN", "UPDATE", "QUERY"):
                        pass  # handled below per-type
                case "QUIT":
                    if not token:
                        self._send({"event": "ERROR", "user_id": user_id,
                                    "msg": "not joined yet",
                                    "request_id": request_id}, _gen=_send_gen)
                        return
                    self.prot_inst.response_share_bf(
                        prot_type=mpmt.ProtType.QUIT,
                        token=token,
                    )
                    self._send({"event": "DONE", "user_id": user_id,
                                "request_id": request_id}, _gen=_send_gen)
                    return

                case _:
                    self._send({"event": "ERROR", "user_id": user_id,
                                "msg": f"unknown prot_type: {prot_type}",
                                "request_id": request_id}, _gen=_send_gen)
                    return

            # --- prot_type in (JOIN, UPDATE, QUERY): accept-based path ---
            if prot_type == "UPDATE" and not token:
                with self._reserved_lock:
                    entry = self._reserved.pop(
                        self._reserve_key(user_id, "UPDATE"), None)
                if entry is not None:
                    try:
                        entry[1].close()
                    except OSError:
                        pass
                self._send({"event": "ERROR", "user_id": user_id,
                            "msg": "not joined yet",
                            "request_id": request_id}, _gen=_send_gen)
                return

            with self._reserved_lock:
                entry = self._reserved.pop(
                    self._reserve_key(user_id, prot_type), None)
            if entry is None:
                self._send({"event": "ERROR", "user_id": user_id,
                            "msg": "no reserved port",
                            "request_id": request_id}, _gen=_send_gen)
                return
            _port, reserved_sock = entry

            conn = None
            try:
                reserved_sock.settimeout(self._timeout)
                conn, _ = reserved_sock.accept()
            except socket.timeout:
                self._send({"event": "ERROR", "user_id": user_id,
                            "msg": "client did not connect in time",
                            "request_id": request_id}, _gen=_send_gen)
                return
            finally:
                try:
                    reserved_sock.close()
                except OSError:
                    pass

            if prot_type in ("JOIN", "UPDATE"):
                ch_set_holder = mpmt.Channel(sock=conn)
                if prot_type == "JOIN":
                    agent_token = self.prot_inst.response_share_bf(
                        prot_type=mpmt.ProtType.JOIN,
                        ch_set_holder=ch_set_holder,
                    )
                    self._send({"event": "DONE", "user_id": user_id,
                                "agent_token": agent_token,
                                "request_id": request_id}, _gen=_send_gen)
                else:  # UPDATE
                    self.prot_inst.response_share_bf(
                        prot_type=mpmt.ProtType.UPDATE,
                        ch_set_holder=ch_set_holder,
                        token=token,
                    )
                    self._send({"event": "DONE", "user_id": user_id,
                                "request_id": request_id}, _gen=_send_gen)
            else:  # QUERY
                ch_querier = mpmt.Channel(sock=conn)
                self.prot_inst.response_query(ch_querier=ch_querier)
                self._send({"event": "DONE", "user_id": user_id,
                            "request_id": request_id}, _gen=_send_gen)

        except Exception as e:
            self._send({"event": "ERROR", "user_id": user_id,
                        "msg": str(e), "request_id": request_id}, _gen=_send_gen)
        finally:
            with self._exec_lock:
                self._exec_thread = None

    _INSTRUCTIONS: dict[str, str] = {
        "RESERVE": "cmd_reserve",
        "EXECUTE": "cmd_execute",
    }

    # network

    def run(self) -> None:
        cfg_root = self._cfg_root
        role_cfg = self._cfg

        print(f"[{self._role}] Agent starting up ...", flush=True)

        # 1. open ch_prev (listen) in background thread —
        #    mpmt.Channel(port) blocks until the peer connects,
        #    so we must NOT block the main thread here.
        prev_result: dict = {}
        prev_error: dict = {}

        def _open_prev() -> None:
            try:
                prev_result["channel"] = mpmt.Channel(role_cfg["ch_prev_port"])
            except Exception as e:
                prev_error["error"] = e

        print(f"[{self._role}] opening listen channel on :{role_cfg['ch_prev_port']} "
              f"(background) ...", flush=True)
        prev_thread = threading.Thread(target=_open_prev, daemon=True)
        prev_thread.start()

        # 2. main thread: connect to next peer with retry
        print(f"[{self._role}] connecting to {self._nxt_role} "
              f"at {self._nxt_cfg['ip']}:{role_cfg['ch_nxt_port']} ...",
              flush=True)
        ch_nxt = None
        attempt = 0
        while ch_nxt is None:
            attempt += 1
            try:
                ch_nxt = mpmt.Channel(self._nxt_cfg["ip"],
                                      role_cfg["ch_nxt_port"],
                                      retry_timeout=self._connect_timeout)
            except (TimeoutError, OSError):
                print(f"\r[{self._role}] waiting for {self._nxt_role} "
                      f"(attempt {attempt}) ...",
                      end="", flush=True)
        print(f"\n[{self._role}] connected to {self._nxt_role}", flush=True)

        # 3. wait for ch_prev to be ready
        print(f"[{self._role}] waiting for incoming connection from peer ...",
              flush=True)
        prev_thread.join()
        if prev_error:
            raise prev_error["error"]
        ch_prev = prev_result["channel"]
        print(f"[{self._role}] incoming connection established", flush=True)

        # 4. create AgentServer (TreeCache init)
        print(f"[{self._role}] initialising TreeCache ...", flush=True)
        preset = self._preset
        kwargs: dict = {
            "server_role": self._server_role,
            "set_size": preset["set_size"],
            "fpr_mantissa": preset["fpr_mantissa"],
            "fpr_exponent": preset["fpr_exponent"],
            "storage_dir": self._storage_dir,
            "ch_prev": ch_prev,
            "ch_nxt": ch_nxt,
            "cores": cfg_root["dpf_cores"],
        }
        if self._role == "steward":
            kwargs["hash_seed_list"] = [bytes.fromhex(h)
                                        for h in preset["hash_seed_list"]]
        self.prot_inst = mpmt.AgentServer(**kwargs)
        print(f"[{self._role}] TreeCache initialised", flush=True)

        # 5. start management listener
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind((self._cfg["ip"], self._cfg["mgmt_port"]))
        srv.listen(1)
        print(f"[{self._role}] management listening on "
              f"{self._cfg['ip']}:{self._cfg['mgmt_port']}", flush=True)

        while True:
            self._conn, addr = srv.accept()
            self._generation += 1
            # clean up reservations from previous Manager session
            with self._reserved_lock:
                for _key, (_port, sock) in self._reserved.items():
                    try:
                        sock.close()
                    except OSError:
                        pass
                self._reserved.clear()
            print(f"[{self._role}] management connected from {addr}", flush=True)
            try:
                self._recv_loop()
            except (ConnectionError, OSError) as e:
                print(f"[{self._role}] management disconnected: {e}", flush=True)
            except Exception as e:
                print(f"[{self._role}] management error: {e}", flush=True)

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
        token = cmd.get("token", "")
        gen = self._generation  # capture so stale workers drop results
        self._send({"event": "READY", "user_id": user_id,
                    "request_id": request_id})
        method = getattr(self, method_name)
        threading.Thread(target=method,
                         args=(user_id, prot_type, request_id, gen, token),
                         daemon=True).start()

    def _send(self, msg: dict, *, _gen: int | None = None) -> None:
        with self._send_lock:
            if _gen is not None and _gen != self._generation:
                return  # stale worker from previous Manager session, drop
            if self._conn is None:
                return
            try:
                self._conn.sendall(json.dumps(msg).encode() + b"\n")
            except OSError:
                pass  # connection already dead
