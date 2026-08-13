
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
        return self._post("/reserve_query", {"user_id": self._user_id})

    def execute(self) -> dict:
        """Poll /execute until DONE.  Pins internal _op_id after first call."""
        internal_op_id = None
        result = None
        protocol_started = False
        while True:
            body = {"user_id": self._user_id, "prot_type": "QUERY"}
            if internal_op_id is not None:
                body["op_id"] = internal_op_id
            resp = self._post("/execute", body)
            status = resp.get("status")
            if "_op_id" in resp:
                internal_op_id = resp["_op_id"]

            if status == "DONE":
                resp["result"] = result
                return resp
            if status in ("REMOVED", "FAILED", "NOT_FOUND", "NOT_RESERVED"):
                return resp
            if status == "REJECTED":
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
                agents = resp.get("agents")
                if agents is not None and not protocol_started:
                    protocol_started = True
                    print("\r[query] executing query protocol..."
                          + " " * 30)
                    result = self.run_protocol(agents)
                time.sleep(1)
                continue
            return resp

    # CLI
    @classmethod
    def run_cli(cls, args: list[str] | None = None) -> None:
        if args is None:
            args = []

        if len(args) < 2:
            print("usage: python3 run.py querier -r QUERY <user_id>")
            print("       python3 run.py querier -e QUERY <user_id> <element>")
            return

        flag = args[0]
        if flag not in ("-r", "-e"):
            print("error: must specify -r (reserve) or -e (execute)")
            return

        if args[1] != "QUERY":
            print("error: querier only supports QUERY")
            return

        user_id = args[2] if len(args) > 2 else ""
        if not user_id:
            print("error: user_id required")
            return

        if flag == "-r":
            q = cls(user_id, b"")  # element not needed for reserve
            result = q.reserve()
            print(json.dumps(result, indent=2))
            return

        # -e QUERY <user_id> [element]
        element = args[3] if len(args) > 3 else input("element: ").strip()
        element = element.encode()
        q = cls(user_id, element)
        result = q.execute()
        print(json.dumps(result, indent=2))
