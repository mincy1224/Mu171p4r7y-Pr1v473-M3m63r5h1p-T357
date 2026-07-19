"""Test bytes_sent / bytes_recv / clear_send_cnt / clear_recv_cnt for ShrRep3 and ShrAdd2."""

import array
import mpmt
import pytest


# ══════════════════════════════════════════════════════════════════
#  ShrRep3 (RSS3)  byte counters
# ══════════════════════════════════════════════════════════════════

ELL = 4


def _rep3_target(pid, channels):
    inst = mpmt.ShrRep3(ELL, pid)(channels["prev"], channels["next"])
    inst.clear_send_cnt()
    inst.clear_recv_cnt()
    assert inst.bytes_sent() == 0
    assert inst.bytes_recv() == 0

    # scalar share → both send and recv happen
    ss = inst.share_scalar(123)
    s1 = inst.bytes_sent()
    r1 = inst.bytes_recv()
    assert s1 > 0
    assert r1 > 0

    # scalar recv share
    ss2 = inst.recv_scalar_share()
    assert inst.bytes_sent() > s1
    assert inst.bytes_recv() > r1

    # reveal
    inst.reveal_scalar(ss)
    assert inst.bytes_sent() > s1
    assert inst.bytes_recv() > r1

    # clear → back to zero
    inst.clear_send_cnt()
    inst.clear_recv_cnt()
    assert inst.bytes_sent() == 0
    assert inst.bytes_recv() == 0

    # byte send/recv
    buf = bytearray(16)
    next_pid = (pid + 1) % 3
    inst.send_data(next_pid, buf)
    sent_bytes = inst.bytes_sent()
    assert sent_bytes == 16

    prev_pid = (pid + 2) % 3
    inst.recv_data(prev_pid, buf)
    recv_bytes = inst.bytes_recv()
    assert recv_bytes == 16

    return "ok"


class TestShrRep3ByteCounters:

    def test_rep3_counters(self, rss3_pool):
        results = rss3_pool.run(_rep3_target)
        assert results == ["ok", "ok", "ok"]


# ══════════════════════════════════════════════════════════════════
#  ShrAdd2  byte counters
# ══════════════════════════════════════════════════════════════════

ADD2_ELL = 20


def _add2_target(pid, channels):
    inst = mpmt.ShrAdd2(ADD2_ELL, pid)(channels["peer"])
    inst.clear_send_cnt()
    inst.clear_recv_cnt()
    assert inst.bytes_sent() == 0
    assert inst.bytes_recv() == 0

    # scalar send/recv — uint32_t = 4 bytes
    inst.send_data(0xDEAD)
    assert inst.bytes_sent() == 4
    val = inst.recv_data()
    assert inst.bytes_recv() == 4

    # scalar share
    if pid == 0:
        inst.share_scalar(100)
        assert inst.bytes_sent() > 4
    else:
        inst.recv_scalar_share()
        assert inst.bytes_recv() > 4

    # share_element → length-prefixed
    if pid == 0:
        elem = inst.share_element(b"hello")
        assert isinstance(elem, bytes)
    else:
        elem = inst.recv_element_share()
        assert isinstance(elem, bytes)

    sent_elem = inst.bytes_sent()
    recv_elem = inst.bytes_recv()
    if pid == 0:
        assert sent_elem > 4 + 8 + 5
    else:
        assert recv_elem > 4 + 8 + 5

    # share_key / recv_key_share — 16 bytes no prefix
    buf = bytearray(16)
    if pid == 0:
        import os
        share = inst.share_key(os.urandom(16))
        assert isinstance(share, bytes) and len(share) == 16
        assert inst.bytes_sent() >= sent_elem + 16
    else:
        inst.recv_key_share(buf)
        assert inst.bytes_recv() >= recv_elem + 16

    # clear → back to zero
    inst.clear_send_cnt()
    inst.clear_recv_cnt()
    assert inst.bytes_sent() == 0
    assert inst.bytes_recv() == 0

    return "ok"


class TestShrAdd2ByteCounters:

    def test_add2_counters(self, add2_pool):
        results = add2_pool.run(_add2_target)
        assert results == ["ok", "ok"]


# ══════════════════════════════════════════════════════════════════
#  Clear after heavy REP3 ops
# ══════════════════════════════════════════════════════════════════


def _rep3_heavy_target(pid, channels):
    inst = mpmt.ShrRep3(ELL, pid)(channels["prev"], channels["next"])
    inst.clear_send_cnt()
    inst.clear_recv_cnt()

    for _ in range(10):
        ss = inst.share_scalar(5)
        inst.reveal_scalar(ss)

    assert inst.bytes_sent() > 0
    assert inst.bytes_recv() > 0

    inst.clear_send_cnt()
    inst.clear_recv_cnt()
    assert inst.bytes_sent() == 0
    assert inst.bytes_recv() == 0

    # more ops after clear
    ss = inst.share_scalar(7)
    inst.reveal_scalar(ss)
    assert inst.bytes_sent() > 0
    assert inst.bytes_recv() > 0

    return "ok"


class TestCountersClear:

    def test_rep3_clear_after_ops(self, rss3_pool):
        results = rss3_pool.run(_rep3_heavy_target)
        assert results == ["ok", "ok", "ok"]
