"""HTTP interface test: verify route dispatching and response format.

Uses mocked ProtocolHandler — no real MPC, no Rep3 ring.
"""

import multiprocessing as mp
import os
import sys
import time
from unittest.mock import MagicMock
from concurrent.futures import ThreadPoolExecutor, as_completed

import pytest
import requests

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

import mpmt
from mpmt.protocol_handler import ProtocolHandler


HTTP_PORTS = [15000, 15001, 15002]


def _post_all_parallel(endpoint: str, payloads: list[dict],
                       timeout: int = 10) -> list:
    urls = [f"http://127.0.0.1:{p}/api/v1/{endpoint}" for p in HTTP_PORTS]
    results = [None] * 3
    with ThreadPoolExecutor(max_workers=3) as ex:
        futures = {
            ex.submit(requests.post, urls[i], json=payloads[i],
                      timeout=timeout): i
            for i in range(3)
        }
        for f in as_completed(futures):
            i = futures[f]
            results[i] = f.result()
    return results


def _server_worker(party_id: int, http_port: int, ready_ev: mp.Event):
    """Start a Flask server with a mocked handler."""
    try:
        app = __import__("flask").Flask(__name__)

        # Build a lightweight handler with mocks
        mock_handler = MagicMock(spec=ProtocolHandler)
        mock_handler.three_way_confirm.return_value = None
        mock_handler.prepare_join.return_value = None
        mock_handler.check_token.return_value = None
        mock_handler.do_quit.return_value = None
        mock_handler.aggregate.return_value = None
        mock_handler.connect_leader.return_value = None
        mock_handler.connect_helper.return_value = None

        mock_server = MagicMock()

        from application.server_routes import make_blueprint
        bp = make_blueprint(mock_server, mock_handler, party_id)
        app.register_blueprint(bp)

        import threading as _th
        import wsgiref.simple_server
        server = wsgiref.simple_server.make_server("127.0.0.1", http_port, app)
        t = _th.Thread(target=server.serve_forever, daemon=True)
        t.start()
        ready_ev.set()
        t.join()
    except Exception:
        import traceback
        with open(f"/tmp/mpmt_mock_server_{party_id}.log", "w") as f:
            traceback.print_exc(file=f)
        ready_ev.set()


class TestHTTPInterface:
    """Mocked HTTP interface — no MPC, no Rep3 ring."""

    @pytest.fixture(scope="class")
    def servers(self):
        """Start 3 Flask servers with mocked handlers."""
        mp.set_start_method("spawn", force=True)

        procs, ready_events = [], []
        for pid, http_p in enumerate(HTTP_PORTS):
            ev = mp.Event()
            ready_events.append(ev)
            p = mp.Process(target=_server_worker, args=(pid, http_p, ev))
            p.start()
            procs.append(p)

        for ev in ready_events:
            ev.wait(timeout=15)
        time.sleep(0.3)

        yield

        for p in procs:
            p.terminate()
            p.join(timeout=3)

    def test_reserve_join_returns_port(self, servers):
        """POST /reserve join → 200, status=ok, port is int."""
        token_hex = b"tk_test_01".hex()
        payload = {"token": token_hex, "action": "join"}
        results = _post_all_parallel("reserve", [payload] * 3, timeout=10)
        for i, resp in enumerate(results):
            assert resp.status_code == 200, f"P{i}: {resp.text}"
            data = resp.json()
            assert data["status"] == "ok"
            assert isinstance(data["port"], int)

    def test_reserve_quit(self, servers):
        """POST /reserve quit → 200, status=ok (no port needed)."""
        payload = {"token": b"tk_quit".hex(), "action": "quit"}
        results = _post_all_parallel("reserve", [payload] * 3, timeout=10)
        for resp in results:
            assert resp.status_code == 200
            assert resp.json()["status"] == "ok"

    def test_reserve_bad_action(self, servers):
        """Unknown action → 400."""
        payload = {"token": b"tk01".hex(), "action": "dance"}
        results = _post_all_parallel("reserve", [payload] * 3, timeout=10)
        for resp in results:
            assert resp.status_code == 400

    def test_aggregate(self, servers):
        """POST /aggregate → 200, status=ok."""
        results = _post_all_parallel("aggregate", [{}] * 3, timeout=10)
        for resp in results:
            assert resp.status_code == 200
            assert resp.json() == {"status": "ok"}

    def test_connect_bad_action(self, servers):
        """POST /connect with quit → 400."""
        payload = {"token": b"tk01".hex(), "action": "quit"}
        results = _post_all_parallel("connect", [payload] * 3, timeout=10)
        for resp in results:
            assert resp.status_code == 400
