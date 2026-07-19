"""End-to-end: ADD2 ET (Leader+Querier) — full query chain terminal step.

ADD2 hash pipeline: tests/test_shr_add2.py (138 tests).
Rep3 dot: tests/test_correctness.py.
"""

import multiprocessing as mp
import time
import socket as _socket
import pytest
import mpmt
from mpmt.channels import Channel


SET_SIZE = 2 ** 10
FPR_MANTISSA = 1.0
FPR_EXPONENT = -3


def _alloc_port():
    s = _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)
    s.bind(("0.0.0.0", 0)); p = s.getsockname()[1]; s.close()
    return p


def _et_leader(q, port, ell_q, my_a, my_b):
    import traceback
    try:
        ch = Channel(port)
        et = mpmt.ShrAdd2(ell=ell_q, party=0)(ch)
        r = et.equality_test(my_a, my_b)
        q.put(("ok", r))
    except Exception as e:
        q.put(("err", f"{e}\n{traceback.format_exc()}"))


def _et_querier(q, port, ell_q, my_a, my_b):
    import traceback
    try:
        ch = Channel("127.0.0.1", port)
        et = mpmt.ShrAdd2(ell=ell_q, party=1)(ch)
        r = et.equality_test(my_a, my_b)
        q.put(("ok", r))
    except Exception as e:
        q.put(("err", f"{e}\n{traceback.format_exc()}"))


class TestQueryE2E:

    def test_add2_et_equal(self):
        """ADD2 ET: equal case. Leader(a,b) + Querier(0,0) where a==b."""
        mp.set_start_method("spawn", force=True)
        _, _, hf_num, ell_q = mpmt.bf_param(SET_SIZE, FPR_MANTISSA, FPR_EXPONENT)
        ell_q = max(1, ell_q)
        port = _alloc_port()

        q = mp.Queue()
        pL = mp.Process(target=_et_leader, args=(q, port, ell_q, hf_num, hf_num))
        pQ = mp.Process(target=_et_querier, args=(q, port, ell_q, 0, 0))
        pL.start(); time.sleep(0.3); pQ.start()

        results = [q.get(timeout=15) for _ in range(2)]
        pL.join(3); pQ.join(3)
        lr, qr = results[0], results[1]
        if lr[0] != "ok": lr, qr = qr, lr
        assert lr[0] == "ok", f"Failed: {lr}"
        result = mpmt.ring_add(ell_q, lr[1], qr[1])
        assert result == 1, f"ET equal: expected 1, got {result}"

    def test_add2_et_not_equal(self):
        """ADD2 ET: not-equal case."""
        mp.set_start_method("spawn", force=True)
        _, _, hf_num, ell_q = mpmt.bf_param(SET_SIZE, FPR_MANTISSA, FPR_EXPONENT)
        ell_q = max(1, ell_q)
        port = _alloc_port()

        q = mp.Queue()
        pL = mp.Process(target=_et_leader, args=(q, port, ell_q, hf_num, 0))
        pQ = mp.Process(target=_et_querier, args=(q, port, ell_q, 0, 0))
        pL.start(); time.sleep(0.3); pQ.start()

        results = [q.get(timeout=15) for _ in range(2)]
        pL.join(3); pQ.join(3)
        lr, qr = results[0], results[1]
        if lr[0] != "ok": lr, qr = qr, lr
        assert lr[0] == "ok", f"Failed: {lr}"
        result = mpmt.ring_add(ell_q, lr[1], qr[1])
        assert result == 0, f"ET not-equal: expected 0, got {result}"
