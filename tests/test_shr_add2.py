"""ShrAdd2 (2-party additive secret sharing) correctness tests.

Uses persistent-worker pools (see conftest.py).
"""

import random
import pytest
import mpmt
from conftest import _unpack_scalar, _reconstruct_scalar

ELLS = list(range(20, 32))


# ==================================================================
#  Group S — share / recvShare / reveal
# ==================================================================

class TestShareReveal:

    @pytest.mark.parametrize("ell", ELLS)
    def test_share_recv_reconstruct(self, ell, add2_pool):
        m = mpmt.ring_mask(ell)
        value = random.randint(0, m)

        def party(pid, channels):
            inst = mpmt.ShrAdd2(ell, pid)(channels["peer"])
            if pid == 0:
                return inst.share_scalar(value)
            else:
                return inst.recv_scalar_share()

        results = add2_pool.run(party)
        plain = mpmt.ring_add(ell, results[0], results[1])
        assert plain == value

    @pytest.mark.parametrize("ell", ELLS)
    def test_manual_reveal(self, ell, add2_pool):
        m = mpmt.ring_mask(ell)
        value = random.randint(0, m)

        def party(pid, channels):
            inst = mpmt.ShrAdd2(ell, pid)(channels["peer"])
            if pid == 0:
                my_share = inst.share_scalar(value)
                inst.send_data(my_share)
                return inst.recv_data()
            else:
                my_share = inst.recv_scalar_share()
                other = inst.recv_data()
                plain = mpmt.ring_add(ell, my_share, other)
                inst.send_data(plain)
                return plain

        results = add2_pool.run(party)
        assert results[0] == value
        assert results[1] == value

    @pytest.mark.parametrize("ell", ELLS)
    @pytest.mark.parametrize("value_label", ["zero", "one", "max", "half", "arbitrary"])
    def test_share_manual_reveal_roundtrip(self, ell, value_label, add2_pool):
        m = mpmt.ring_mask(ell)
        value = {"zero": 0, "one": 1, "max": m, "half": m // 2, "arbitrary": 42 & m}[value_label]

        def party(pid, channels):
            inst = mpmt.ShrAdd2(ell, pid)(channels["peer"])
            if pid == 0:
                my_share = inst.share_scalar(value)
                inst.send_data(my_share)
                return inst.recv_data()
            else:
                my_share = inst.recv_scalar_share()
                other = inst.recv_data()
                plain = mpmt.ring_add(ell, my_share, other)
                inst.send_data(plain)
                return plain

        results = add2_pool.run(party)
        assert results[0] == value and results[1] == value

    @pytest.mark.parametrize("ell", ELLS)
    def test_share_manual_reveal_zero(self, ell, add2_pool):
        def party(pid, channels):
            inst = mpmt.ShrAdd2(ell, pid)(channels["peer"])
            if pid == 0:
                my_share = inst.share_scalar(0)
                inst.send_data(my_share)
                return inst.recv_data()
            else:
                my_share = inst.recv_scalar_share()
                other = inst.recv_data()
                plain = mpmt.ring_add(ell, my_share, other)
                inst.send_data(plain)
                return plain

        results = add2_pool.run(party)
        assert results[0] == 0 and results[1] == 0

    @pytest.mark.parametrize("ell", ELLS)
    def test_share_manual_reveal_max(self, ell, add2_pool):
        m = mpmt.ring_mask(ell)

        def party(pid, channels):
            inst = mpmt.ShrAdd2(ell, pid)(channels["peer"])
            if pid == 0:
                my_share = inst.share_scalar(m)
                inst.send_data(my_share)
                return inst.recv_data()
            else:
                my_share = inst.recv_scalar_share()
                other = inst.recv_data()
                plain = mpmt.ring_add(ell, my_share, other)
                inst.send_data(plain)
                return plain

        results = add2_pool.run(party)
        assert results[0] == m and results[1] == m


# ==================================================================
#  Group H — hash
# ==================================================================

class TestHash:

    @pytest.mark.parametrize("ell", ELLS)
    def test_hash_consistency(self, ell, add2_pool):
        pt_bytes = b"hello world! test"
        key_bytes = b"0123456789abcdef"

        def run_hash(pt_local, ky_local):
            def party(pid, channels):
                inst = mpmt.ShrAdd2(ell, pid)(channels["peer"])
                if pid == 0:
                    lo_pt = inst.share_element(pt_bytes)
                    lo_ky = inst.share_key(key_bytes)
                else:
                    lo_pt = inst.recv_element_share()
                    _ky_buf = bytearray(16)
                    inst.recv_key_share(_ky_buf)
                    lo_ky = bytes(_ky_buf)
                return inst.hash(lo_pt, lo_ky)

            results = add2_pool.run(party)
            return mpmt.ring_add(ell, results[0], results[1])

        s1 = run_hash(pt_bytes, key_bytes)
        s2 = run_hash(pt_bytes, key_bytes)
        assert s1 == s2

    @pytest.mark.parametrize("ell", ELLS)
    def test_hash_same_as_local(self, ell, add2_pool):
        pt_bytes = b"preimage test!!"
        key_bytes = b"16-byte-key-here"

        expected = mpmt.hash_aes_dm(pt_bytes, key_bytes, ell)

        def party(pid, channels):
            inst = mpmt.ShrAdd2(ell, pid)(channels["peer"])
            if pid == 0:
                lo_pt = inst.share_element(pt_bytes)
                lo_ky = inst.share_key(key_bytes)
            else:
                lo_pt = inst.recv_element_share()
                _ky_buf = bytearray(16)
                inst.recv_key_share(_ky_buf)
                lo_ky = bytes(_ky_buf)
            return inst.hash(lo_pt, lo_ky)

        results = add2_pool.run(party)
        plain = mpmt.ring_add(ell, results[0], results[1])
        assert plain == expected


# ==================================================================
#  Group I — mod
# ==================================================================

class TestMod:

    @pytest.mark.parametrize("ell", ELLS)
    def test_mod(self, ell, add2_pool):
        sm = mpmt.ring_mask(ell)
        A = 500
        mv = 7
        a_0 = random.randint(0, sm)
        a_1 = mpmt.ring_sub(ell, A, a_0)
        expected = A % mv

        def party(pid, channels):
            inst = mpmt.ShrAdd2(ell, pid)(channels["peer"])
            my_a = a_0 if pid == 0 else a_1
            return inst.mod(my_a, mv)

        results = add2_pool.run(party)
        plain = mpmt.ring_add(ell, results[0], results[1])
        assert plain == expected

    @pytest.mark.parametrize("ell", ELLS)
    def test_mod_randomized(self, ell, add2_pool):
        sm = mpmt.ring_mask(ell)
        A = random.randint(0, sm)
        mv = random.randint(2, max(sm - 1, 3))
        expected = A % mv
        a_0 = random.randint(0, sm)
        a_1 = mpmt.ring_sub(ell, A, a_0)

        def party(pid, channels):
            inst = mpmt.ShrAdd2(ell, pid)(channels["peer"])
            my_a = a_0 if pid == 0 else a_1
            return inst.mod(my_a, mv)

        results = add2_pool.run(party)
        plain = mpmt.ring_add(ell, results[0], results[1])
        assert plain == expected

    @pytest.mark.parametrize("ell", ELLS)
    def test_mod_zero(self, ell, add2_pool):
        mv = 997

        def party(pid, channels):
            inst = mpmt.ShrAdd2(ell, pid)(channels["peer"])
            return inst.mod(0, mv)

        results = add2_pool.run(party)
        plain = mpmt.ring_add(ell, results[0], results[1])
        assert plain == 0

    @pytest.mark.parametrize("ell", ELLS)
    def test_mod_large_A(self, ell, add2_pool):
        sm = mpmt.ring_mask(ell)
        A = 1234567 & sm
        mv = 997
        a_0 = random.randint(0, sm)
        a_1 = mpmt.ring_sub(ell, A, a_0)
        expected = A % mv

        def party(pid, channels):
            inst = mpmt.ShrAdd2(ell, pid)(channels["peer"])
            my_a = a_0 if pid == 0 else a_1
            return inst.mod(my_a, mv)

        results = add2_pool.run(party)
        plain = mpmt.ring_add(ell, results[0], results[1])
        assert plain == expected

    @pytest.mark.parametrize("ell", ELLS)
    def test_mod_boundary_mv(self, ell, add2_pool):
        sm = mpmt.ring_mask(ell)
        mv = sm
        A = 12345 & sm
        expected = A % mv

        def party(pid, channels):
            inst = mpmt.ShrAdd2(ell, pid)(channels["peer"])
            my_a = A if pid == 0 else 0
            return inst.mod(my_a, mv)

        results = add2_pool.run(party)
        plain = mpmt.ring_add(ell, results[0], results[1])
        assert plain == expected


# ==================================================================
#  Group I2 — equality_test
# ==================================================================

class TestEquality:

    @pytest.mark.parametrize("ell", ELLS)
    def test_equality_equal(self, ell, add2_pool):
        A = 500
        a_0, a_1 = 300, A - 300

        def party(pid, channels):
            inst = mpmt.ShrAdd2(ell, pid)(channels["peer"])
            my_a0 = a_0 if pid == 0 else a_1
            my_a1 = a_0 if pid == 0 else a_1
            return inst.equality_test(my_a0, my_a1)

        results = add2_pool.run(party)
        plain = mpmt.ring_add(ell, results[0], results[1])
        assert plain == 1

    @pytest.mark.parametrize("ell", ELLS)
    def test_equality_not_equal(self, ell, add2_pool):
        a_0, a_1 = 300, 200
        b_0, b_1 = 200, 100

        def party(pid, channels):
            inst = mpmt.ShrAdd2(ell, pid)(channels["peer"])
            my_a = a_0 if pid == 0 else a_1
            my_b = b_0 if pid == 0 else b_1
            return inst.equality_test(my_a, my_b)

        results = add2_pool.run(party)
        plain = mpmt.ring_add(ell, results[0], results[1])
        assert plain == 0

    @pytest.mark.parametrize("ell", ELLS)
    def test_equality_zero(self, ell, add2_pool):
        def party(pid, channels):
            inst = mpmt.ShrAdd2(ell, pid)(channels["peer"])
            return inst.equality_test(0, 0)

        results = add2_pool.run(party)
        plain = mpmt.ring_add(ell, results[0], results[1])
        assert plain == 1

    @pytest.mark.parametrize("ell", ELLS)
    def test_equality_zero_vs_max(self, ell, add2_pool):
        sm = mpmt.ring_mask(ell)

        def party(pid, channels):
            inst = mpmt.ShrAdd2(ell, pid)(channels["peer"])
            my_a = sm if pid == 0 else 0
            my_b = 0
            return inst.equality_test(my_a, my_b)

        results = add2_pool.run(party)
        plain = mpmt.ring_add(ell, results[0], results[1])
        assert plain == 0


# ==================================================================
#  Group J — send / recv
# ==================================================================

class TestSendRecv:

    @pytest.mark.parametrize("ell", ELLS)
    def test_send_recv_p0_to_p1(self, ell, add2_pool):
        val = 0x12345678 & mpmt.ring_mask(ell)

        def party(pid, channels):
            inst = mpmt.ShrAdd2(ell, pid)(channels["peer"])
            if pid == 0:
                inst.send_data(val)
            else:
                return inst.recv_data()

        results = add2_pool.run(party)
        assert results[1] == val

    @pytest.mark.parametrize("ell", ELLS)
    def test_send_recv_p1_to_p0(self, ell, add2_pool):
        val = 0x9ABCDEF0 & mpmt.ring_mask(ell)

        def party(pid, channels):
            inst = mpmt.ShrAdd2(ell, pid)(channels["peer"])
            if pid == 1:
                inst.send_data(val)
            else:
                return inst.recv_data()

        results = add2_pool.run(party)
        assert results[0] == val

    @pytest.mark.parametrize("ell", ELLS)
    def test_send_recv_bidirectional(self, ell, add2_pool):
        m = mpmt.ring_mask(ell)
        v0, v1 = 42 & m, 99 & m

        def party(pid, channels):
            inst = mpmt.ShrAdd2(ell, pid)(channels["peer"])
            if pid == 0:
                inst.send_data(v0)
                return inst.recv_data()
            else:
                r0 = inst.recv_data()
                inst.send_data(v1)
                return r0

        results = add2_pool.run(party)
        assert results[0] == v1
        assert results[1] == v0


# ==================================================================
#  Group J2 — interaction
# ==================================================================

class TestInteraction:

    @pytest.mark.parametrize("ell", ELLS)
    def test_share_then_send_cross_check(self, ell, add2_pool):
        m = mpmt.ring_mask(ell)
        value = random.randint(0, m)

        def party(pid, channels):
            inst = mpmt.ShrAdd2(ell, pid)(channels["peer"])
            if pid == 0:
                my_share = inst.share_scalar(value)
                inst.send_data(my_share)
            else:
                complement = inst.recv_scalar_share()
                p0_share = inst.recv_data()
                return mpmt.ring_add(ell, complement, p0_share)

        results = add2_pool.run(party)
        assert results[1] == value

    @pytest.mark.parametrize("ell", ELLS)
    def test_reveal_via_send_recv(self, ell, add2_pool):
        m = mpmt.ring_mask(ell)
        value = random.randint(0, m)

        def party(pid, channels):
            inst = mpmt.ShrAdd2(ell, pid)(channels["peer"])
            if pid == 0:
                my_share = inst.share_scalar(value)
                inst.send_data(my_share)
                return inst.recv_data()
            else:
                my_share = inst.recv_scalar_share()
                other = inst.recv_data()
                plain = mpmt.ring_add(ell, my_share, other)
                inst.send_data(plain)
                return plain

        results = add2_pool.run(party)
        assert results[0] == value
        assert results[1] == value


# ==================================================================
#  Group K — factory / boundary
# ==================================================================

class TestFactory:

    def test_ell_too_low(self):
        with pytest.raises(ValueError, match="ell out of range"):
            mpmt.ShrAdd2(12, 0)  # ADD2_ELL_MIN=13 in bind_common.hpp

    def test_ell_too_high(self):
        with pytest.raises(ValueError, match="ell out of range"):
            mpmt.ShrAdd2(32, 0)

    def test_party_out_of_range(self):
        with pytest.raises(ValueError, match="party"):
            mpmt.ShrAdd2(20, 2)

    @pytest.mark.parametrize("ell", ELLS)
    def test_properties_exist(self, ell):
        for p in [0, 1]:
            cls = mpmt.ShrAdd2(ell, p)
            assert hasattr(cls, "ell")
            assert hasattr(cls, "party")


# ==================================================================
#  Group L — ELL boundary
# ==================================================================

class TestBoundary:

    BOUNDARY_ELLS = [20, 31]

    @pytest.mark.parametrize("ell", BOUNDARY_ELLS)
    def test_share_manual_reveal(self, ell, add2_pool):
        m = mpmt.ring_mask(ell)

        def party(pid, channels):
            inst = mpmt.ShrAdd2(ell, pid)(channels["peer"])
            if pid == 0:
                s = inst.share_scalar(m)
                inst.send_data(s)
                return inst.recv_data()
            else:
                s = inst.recv_scalar_share()
                other = inst.recv_data()
                plain = mpmt.ring_add(ell, s, other)
                inst.send_data(plain)
                return plain

        results = add2_pool.run(party)
        assert results[0] == m
        assert results[1] == m

    @pytest.mark.parametrize("ell", BOUNDARY_ELLS)
    def test_hash(self, ell, add2_pool):
        pt_bytes = b"bdry"
        key_bytes = b"0123456789abcdef"
        expected = mpmt.hash_aes_dm(pt_bytes, key_bytes, ell)

        def party(pid, channels):
            inst = mpmt.ShrAdd2(ell, pid)(channels["peer"])
            if pid == 0:
                lo_pt = inst.share_element(pt_bytes)
                lo_ky = inst.share_key(key_bytes)
            else:
                lo_pt = inst.recv_element_share()
                _ky_buf = bytearray(16)
                inst.recv_key_share(_ky_buf)
                lo_ky = bytes(_ky_buf)
            return inst.hash(lo_pt, lo_ky)

        results = add2_pool.run(party)
        plain = mpmt.ring_add(ell, results[0], results[1])
        assert plain == expected

    @pytest.mark.parametrize("ell", BOUNDARY_ELLS)
    def test_mod(self, ell, add2_pool):
        m = mpmt.ring_mask(ell)

        def party(pid, channels):
            inst = mpmt.ShrAdd2(ell, pid)(channels["peer"])
            my_a = m if pid == 0 else 0
            return inst.mod(my_a, m)

        results = add2_pool.run(party)
        plain = mpmt.ring_add(ell, results[0], results[1])
        assert plain == m % m

    @pytest.mark.parametrize("ell", BOUNDARY_ELLS)
    def test_equality(self, ell, add2_pool):
        m = mpmt.ring_mask(ell)

        def party(pid, channels):
            inst = mpmt.ShrAdd2(ell, pid)(channels["peer"])
            my_a = m if pid == 0 else 0
            my_b = m if pid == 0 else 0
            return inst.equality_test(my_a, my_b)

        results = add2_pool.run(party)
        plain = mpmt.ring_add(ell, results[0], results[1])
        assert plain == 1

    @pytest.mark.parametrize("ell", BOUNDARY_ELLS)
    def test_send_recv(self, ell, add2_pool):
        m = mpmt.ring_mask(ell)

        def party(pid, channels):
            inst = mpmt.ShrAdd2(ell, pid)(channels["peer"])
            if pid == 0:
                inst.send_data(m)
            else:
                return inst.recv_data()

        results = add2_pool.run(party)
        assert results[1] == m
