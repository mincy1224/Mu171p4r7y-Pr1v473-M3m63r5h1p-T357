# draft
import json
import os
import time
import urllib.error
import urllib.request

import numpy as np
import mpmt

from _c3_io import read_json

_APP_DIR = os.path.join(os.path.dirname(__file__), "..")
SET_HOLDER_USERS = os.path.join(_APP_DIR, "pretreat", "set_holder_users.json")


class C3SetHolder:
    def __init__(self, user_id: str):
        _dir = os.path.dirname(__file__)
        cfg_root = read_json(os.path.join(_dir, "..", "config.json"))
        self._cfg = cfg_root["manage_server"]
        self._timeout = cfg_root["timeout"]
        self._connect_timeout = cfg_root.get("connect_timeout", 5.0)
        preset = read_json(os.path.join(_dir, "..", "pretreat", "pre.json"))

        self._user_id = user_id
        self._manage_url = (f"http://{self._cfg['server_ip']}:"
                            f"{self._cfg['http_port']}")

        self._prot_inst = mpmt.SetHolder(
            set_size=preset["set_size"],
            fpr_mantissa=preset["fpr_mantissa"],
            fpr_exponent=preset["fpr_exponent"],
        )
        self._hash_seed_list = [bytes.fromhex(h) for h in preset["hash_seed_list"]]

    def _post(self, route: str, body: dict) -> dict:
        url = f"{self._manage_url}{route}"
        data = json.dumps(body).encode()
        req = urllib.request.Request(url, data=data,
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as e:
            try:
                return json.loads(e.read())
            except json.JSONDecodeError:
                return {"status": "FAILED", "reason": f"http_{e.code}"}

    # protocol
    def run_protocol(self, agents: dict, data_set: list[bytes]) -> None:
        ch_steward = mpmt.Channel.connect(agents["STEWARD"]["ip"],
                                          agents["STEWARD"]["port"],
                                          timeout=self._connect_timeout)
        ch_peer0 = mpmt.Channel.connect(agents["PEER0"]["ip"],
                                        agents["PEER0"]["port"],
                                        timeout=self._connect_timeout)
        ch_peer1 = mpmt.Channel.connect(agents["PEER1"]["ip"],
                                        agents["PEER1"]["port"],
                                        timeout=self._connect_timeout)

        self._prot_inst.share_bf(
            set=data_set,
            hash_seed_list=self._hash_seed_list,
            ch_steward=ch_steward,
            ch_peer0=ch_peer0,
            ch_peer1=ch_peer1,
        )

    # flow
    def reserve(self, prot_type: str) -> dict:
        return self._post(f"/reserve_{prot_type.lower()}",
                          {"user_id": self._user_id})

    def execute(self, prot_type: str,
                data_set: list[bytes] | None = None) -> dict:
        """Poll /execute until DONE.  Pins internal _op_id after first call."""
        internal_op_id = None
        protocol_started = False
        tag = prot_type.lower()
        while True:
            body = {"user_id": self._user_id, "prot_type": prot_type}
            if internal_op_id is not None:
                body["op_id"] = internal_op_id
            resp = self._post("/execute", body)
            status = resp.get("status")
            # pin internal op_id for subsequent polls
            if "_op_id" in resp:
                internal_op_id = resp["_op_id"]

            if status == "DONE":
                return resp
            if status in ("REMOVED", "FAILED", "NOT_FOUND", "NOT_RESERVED"):
                return resp
            if status == "REJECTED":
                return resp
            if status == "WAITING":
                ahead = resp.get("ahead", 0)
                delay = float(resp.get("retry_after", 1.0))
                print(f"\r[{tag}] server: {ahead} task(s) ahead "
                      f"— retrying in {delay:.1f}s",
                      end="", flush=True)
                end = time.monotonic() + delay
                while time.monotonic() < end:
                    remaining = end - time.monotonic()
                    print(f"\r[{tag}] server: {ahead} task(s) ahead "
                          f"— retrying in {remaining:.1f}s",
                          end="", flush=True)
                    time.sleep(min(0.1, remaining))
                print(f"\r[{tag}] contacting server..."
                      + " " * 30, end="", flush=True)
                continue
            if status == "BUSY":
                if prot_type == "QUIT":
                    time.sleep(1)
                    continue
                agents = resp.get("agents")
                if agents is not None and not protocol_started:
                    protocol_started = True
                    print(f"\r[{tag}] executing protocol..."
                          + " " * 30)
                    self.run_protocol(agents, data_set or [])
                time.sleep(1)
                continue
            return resp

    # CLI
    @classmethod
    def _load_dataset(cls, user_id: str) -> list[bytes] | None:
        cfg_root = read_json(os.path.join(_APP_DIR, "config.json"))
        storage_root = cfg_root["storage_root_dir"]
        npy_path = os.path.join(_APP_DIR, storage_root,
                                f"set_holder_{user_id}", "set.npy")
        if not os.path.isfile(npy_path):
            print(f"error: {npy_path} not found")
            return None
        arr = np.load(npy_path)
        data_set = [str(x).encode() for x in arr]
        print(f"[set_holder] loaded {len(data_set)} elements from {npy_path}")
        return data_set

    @classmethod
    def run_cli(cls, args: list[str] | None = None) -> None:
        if args is None:
            args = []

        if len(args) < 3:
            print("usage: python3 run.py set_holder -r <SERVICE> <user_id>")
            print("       python3 run.py set_holder -e <SERVICE> <user_id>")
            print("  SERVICE: JOIN | UPDATE | QUIT")
            return

        flag = args[0]
        if flag not in ("-r", "-e"):
            print("error: must specify -r (reserve) or -e (execute)")
            return

        prot_type = args[1]
        if prot_type not in ("JOIN", "UPDATE", "QUIT"):
            print(f"error: unknown service '{prot_type}'")
            return

        user_id = args[2]
        sh = cls(user_id)

        if flag == "-r":
            result = sh.reserve(prot_type)
            print(json.dumps(result, indent=2))
            return

        # -e: execute
        if prot_type == "QUIT":
            data_set = None
        else:
            data_set = cls._load_dataset(user_id)
            if data_set is None:
                return

        result = sh.execute(prot_type, data_set=data_set)
        print(json.dumps(result, indent=2))
