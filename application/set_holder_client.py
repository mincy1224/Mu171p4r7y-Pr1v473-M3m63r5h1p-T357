"""SetHolder CLI — join / update / quit against the 3 MPMT servers.

Usage::

    python application/set_holder_client.py join --elements alice,bob,charlie

@author  mincy
"""

import argparse
import json
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import requests


# ——— Default server addresses ———
SERVERS = [
    {"addr": "127.0.0.1", "port": 5000},   # Leader
    {"addr": "127.0.0.1", "port": 5001},   # Helper A
    {"addr": "127.0.0.1", "port": 5002},   # Helper B
]


def _post_all(endpoint: str, payloads: list[dict]) -> list[dict]:
    """POST to all 3 servers sequentially, return parsed JSON."""
    results = []
    for i, srv in enumerate(SERVERS):
        url = f"http://{srv['addr']}:{srv['port']}/api/v1/{endpoint}"
        resp = requests.post(url, json=payloads[i], timeout=30)
        resp.raise_for_status()
        results.append(resp.json())
    return results


def do_join(token_hex: str, elements: list[str]) -> None:
    """Full join flow: reserve → connect."""
    payload = {"token": token_hex, "action": "join"}

    # Phase 1 — reserve (all 3 servers return {"port": ...})
    reserves = _post_all("reserve", [payload] * 3)
    leader_port   = reserves[0]["port"]
    helper_a_port = reserves[1]["port"]
    helper_b_port = reserves[2]["port"]

    # Phase 2 — connect
    connect_payloads = [
        {"token": token_hex, "action": "join", "port": leader_port},
        {"token": token_hex, "action": "join", "port": helper_a_port},
        {"token": token_hex, "action": "join", "port": helper_b_port},
    ]
    _post_all("connect", connect_payloads)


def do_update(token_hex: str, elements: list[str]) -> None:
    """Full update flow: reserve → connect."""
    payload = {"token": token_hex, "action": "update"}

    reserves = _post_all("reserve", [payload] * 3)
    leader_port   = reserves[0]["port"]
    helper_a_port = reserves[1]["port"]
    helper_b_port = reserves[2]["port"]

    connect_payloads = [
        {"token": token_hex, "action": "update", "port": leader_port},
        {"token": token_hex, "action": "update", "port": helper_a_port},
        {"token": token_hex, "action": "update", "port": helper_b_port},
    ]
    _post_all("connect", connect_payloads)


def do_quit(token_hex: str) -> None:
    """Quit flow: reserve only (no connect phase)."""
    payload = {"token": token_hex, "action": "quit"}
    results = _post_all("reserve", [payload] * 3)
    for r in results:
        assert r["status"] == "ok", f"quit failed: {r}"


def main():
    parser = argparse.ArgumentParser(description="MPMT SetHolder client")
    parser.add_argument("action", choices=["join", "update", "quit"])
    parser.add_argument("--token", type=str, help="32-char hex token")
    parser.add_argument("--elements", type=str,
                        help="comma-separated list of elements")
    args = parser.parse_args()

    if args.token:
        token_hex = args.token
    else:
        import mpmt
        token_hex = mpmt.get_key_128bits().hex()

    elements = []
    if args.elements:
        elements = [s.strip().encode() for s in args.elements.split(",")]

    print(f"[client] action={args.action}  token={token_hex}")
    print(f"[client] elements={[e.decode() for e in elements]}")

    if args.action == "join":
        do_join(token_hex, elements)
    elif args.action == "update":
        do_update(token_hex, elements)
    else:
        do_quit(token_hex)

    print("[client] done.")


if __name__ == "__main__":
    main()
