"""Fake management listeners for the manager_only stack mode.

The control-plane test (app/test_state_machine.py) never triggers a
Manager→Agent management command: it only exercises the reserve/execute HTTP
routes that return early (NOT_RESERVED / REJECTED) plus direct DB assertions.
Verified against tests/app/test_state_machine.py and the Manager's command path — no
RESERVE/EXECUTE is ever issued to an Agent, so these fakes only need to accept
the Manager's management connection and hold it open.

If a future control-plane test DOES trigger an Agent management request, the
fake must grow the minimal request/response set that test needs — do not guess.
"""
from __future__ import annotations

import socket
import threading


def _serve(ip: str, port: int, holder: dict) -> None:
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((ip, port))
    srv.listen(8)
    holder["ready"] = True
    conns: list[socket.socket] = []
    try:
        while not holder.get("stop"):
            try:
                srv.settimeout(0.3)
                conn, _ = srv.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            conns.append(conn)
    finally:
        for c in conns:
            try:
                c.close()
            except OSError:
                pass
        try:
            srv.close()
        except OSError:
            pass


class FakeAgent:
    def __init__(self, ip: str, port: int) -> None:
        self._holder = {"ready": False, "stop": False}
        self._thread = threading.Thread(
            target=_serve, args=(ip, port, self._holder), daemon=True)
        self._thread.start()

    def ready(self) -> bool:
        return self._holder["ready"]

    def stop(self) -> None:
        self._holder["stop"] = True


def start_fakes(ip: str, ports: list[int]) -> list[FakeAgent]:
    return [FakeAgent(ip, p) for p in ports]
