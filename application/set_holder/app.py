# draft
import json
import os
import time
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
        with urllib.request.urlopen(req, timeout=self._timeout) as resp:
            return json.loads(resp.read())

    # protocol
    def run_protocol(self, agents: dict, data_set: list[bytes]) -> None:
        ch_steward = mpmt.Channel(agents["STEWARD"]["ip"],
                                  agents["STEWARD"]["port"],
                                  retry_timeout=self._connect_timeout)
        ch_peer0 = mpmt.Channel(agents["PEER0"]["ip"],
                                agents["PEER0"]["port"],
                                retry_timeout=self._connect_timeout)
        ch_peer1 = mpmt.Channel(agents["PEER1"]["ip"],
                                agents["PEER1"]["port"],
                                retry_timeout=self._connect_timeout)

        self._prot_inst.share_bf(
            set=data_set,
            hash_seed_list=self._hash_seed_list,
            ch_steward=ch_steward,
            ch_peer0=ch_peer0,
            ch_peer1=ch_peer1,
        )

    # flow
    def _reserve_and_execute(self, prot_type: str,
                             data_set: list[bytes] | None = None) -> dict:
        resp = self._post(f"/reserve_{prot_type.lower()}",
                          {"user_id": self._user_id})
        if resp.get("status") == "REJECTED":
            return resp
        if resp.get("status") not in ("SUCCESSFUL", "ALREADY"):
            return resp
        op_id = resp.get("op_id")
        if not op_id:
            return {"status": "FAILED", "reason": "no op_id in response"}

        protocol_started = False
        while True:
            resp = self._post("/execute", {"op_id": op_id})
            status = resp.get("status")

            if status == "DONE":
                return resp
            if status in ("REMOVED", "FAILED", "NOT_FOUND"):
                return resp
            if status == "WAITING":
                time.sleep(1)
                continue
            if status == "BUSY":
                if prot_type == "QUIT":
                    time.sleep(1)
                    continue
                if not protocol_started:
                    protocol_started = True
                    self.run_protocol(resp["agents"], data_set or [])
                time.sleep(1)
                continue

            return resp

    def join(self, data_set: list[bytes]) -> dict:
        return self._reserve_and_execute("JOIN", data_set)

    def update(self, data_set: list[bytes]) -> dict:
        return self._reserve_and_execute("UPDATE", data_set)

    def quit(self) -> dict:
        return self._reserve_and_execute("QUIT")

    # CLI
    @classmethod
    def run_cli(cls, args: list[str] | None = None) -> None:
        if args is None:
            args = []

        if not args:
            print("usage: python run.py set_holder <user_id>")
            return

        user_id = args[0]

        # load pretreat-generated dataset
        cfg_root = read_json(os.path.join(_APP_DIR, "config.json"))
        storage_root = cfg_root["storage_root_dir"]
        npy_path = os.path.join(_APP_DIR, storage_root,
                                f"set_holder_{user_id}", "set.npy")
        if os.path.isfile(npy_path):
            arr = np.load(npy_path)
            data_set = [str(x).encode() for x in arr]
            print(f"[set_holder] loaded {len(data_set)} elements from {npy_path}")
        else:
            print(f"error: {npy_path} not found")
            return

        sh = cls(user_id)
        result = sh.join(data_set=data_set)
        print(json.dumps(result, indent=2))
