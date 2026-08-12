# draft
import json
import os
import time
import urllib.error
import urllib.request

import mpmt

from _c3_io import read_json

_APP_DIR = os.path.join(os.path.dirname(__file__), "..")
QUERIER_USERS = os.path.join(_APP_DIR, "pretreat", "querier_users.json")


class C3Querier:
    def __init__(self, user_id: str, element: bytes):
        _dir = os.path.dirname(__file__)
        cfg_root = read_json(os.path.join(_dir, "..", "config.json"))
        self._cfg = cfg_root["manage_server"]
        self._timeout = cfg_root["timeout"]
        self._connect_timeout = cfg_root.get("connect_timeout", 5.0)
        preset = read_json(os.path.join(_dir, "..", "pretreat", "pre.json"))

        self._user_id = user_id
        self._manage_url = (f"http://{self._cfg['server_ip']}:"
                            f"{self._cfg['http_port']}")
        self._element = element

        self._prot_inst = mpmt.Querier(
            set_size=preset["set_size"],
            fpr_mantissa=preset["fpr_mantissa"],
            fpr_exponent=preset["fpr_exponent"],
        )

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
    def run_protocol(self, agents: dict) -> int:
        ch_steward = mpmt.Channel.connect(agents["STEWARD"]["ip"],
                                          agents["STEWARD"]["port"],
                                          timeout=self._connect_timeout)
        ch_peer0 = mpmt.Channel.connect(agents["PEER0"]["ip"],
                                        agents["PEER0"]["port"],
                                        timeout=self._connect_timeout)
        ch_peer1 = mpmt.Channel.connect(agents["PEER1"]["ip"],
                                        agents["PEER1"]["port"],
                                        timeout=self._connect_timeout)

        return self._prot_inst.query(
            element=self._element,
            ch_steward=ch_steward,
            ch_peer0=ch_peer0,
            ch_peer1=ch_peer1,
        )

    # flow
    def reserve(self) -> dict:
        """Reserve only.  Returns the server response including op_id."""
        return self._post("/reserve_query", {"user_id": self._user_id})

    def execute(self, op_id: str) -> dict:
        """Execute with a previously-reserved op_id."""
        result = None
        protocol_started = False
        while True:
            resp = self._post("/execute", {"op_id": op_id})
            status = resp.get("status")

            if status == "DONE":
                resp["result"] = result
                return resp
            if status in ("REMOVED", "FAILED", "NOT_FOUND"):
                return resp
            if status == "WAITING":
                ahead = resp.get("ahead", 0)
                delay = float(resp.get("retry_after", 1.0))
                print(f"\r[query] server: {ahead} task(s) ahead "
                      f"— retrying in {delay:.1f}s",
                      end="", flush=True)
                end = time.monotonic() + delay
                while time.monotonic() < end:
                    remaining = end - time.monotonic()
                    print(f"\r[query] server: {ahead} task(s) ahead "
                          f"— retrying in {remaining:.1f}s",
                          end="", flush=True)
                    time.sleep(min(0.1, remaining))
                print("\r[query] contacting server..."
                      + " " * 30, end="", flush=True)
                continue
            if status == "BUSY":
                if not protocol_started:
                    protocol_started = True
                    print("\r[query] executing query protocol..."
                          + " " * 30)
                    result = self.run_protocol(resp["agents"])
                time.sleep(1)
                continue
            return resp

    def query(self) -> dict:
        resp = self.reserve()
        if resp.get("status") == "REJECTED":
            return resp
        if resp.get("status") == "CONFLICT":
            return resp
        if resp.get("status") not in ("SUCCESSFUL", "ALREADY"):
            return resp
        op_id = resp.get("op_id")
        if not op_id:
            return {"status": "FAILED", "reason": "no op_id in response"}
        return self.execute(op_id)

    # CLI
    @classmethod
    def run_cli(cls, args: list[str] | None = None) -> None:
        if args is None:
            args = []

        if not args:
            print("usage: python run.py querier <user_id> [-r | -e <op_id>] [<element>]")
            return

        mode = "full"
        op_id = None
        element = None

        user_id = args[0]
        i = 1
        while i < len(args):
            a = args[i]
            if a == "-r":
                mode = "reserve"
                i += 1
            elif a == "-e":
                mode = "execute"
                if i + 1 < len(args) and not args[i + 1].startswith("-"):
                    op_id = args[i + 1]
                    i += 2
                else:
                    print("usage: querier <user_id> -e <op_id> [<element>]")
                    return
            else:
                element = a
                i += 1

        if mode != "reserve":
            if element is None:
                element = input("element: ").strip()
            element = element.encode()

        q = cls(user_id, element)

        if mode == "reserve":
            result = q.reserve()
        elif mode == "execute":
            if op_id is None:
                print("error: -e requires <op_id>")
                return
            result = q.execute(op_id)
        else:
            result = q.query()

        print(json.dumps(result, indent=2))
