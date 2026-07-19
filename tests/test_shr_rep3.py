"""ShrRep3 (3-party replicated secret sharing) correctness tests.

Uses persistent-worker pools (see conftest.py). Each test runs on a
module-scoped RSS3 pool with reusable channels.
"""

import os
import random
import pytest
import mpmt
from conftest import (
    _share_scalar, _unpack_scalar, _share_vec, _sv_to_lists,
    _reconstruct_scalar, _reconstruct_vec,
)

# ——— module constants ———

ELLS = list(range(1, 9))
VEC_SIZES = [0, 1, 16, 100, 255, 1000]
NONZERO_SIZES = [s for s in VEC_SIZES if s > 0]

# Large-scale sizes that exercise chunked ring reshare (>TCP buffer).
# Run with: MPC_BENCH_LARGE=1 python -m pytest ... -k "test_hadamard_large"
LARGE_VEC_SIZES           = [1_000_000, 10_000_000]    # always run
LARGE_VEC_SIZES_STRESS    = [50_000_000, 100_000_000]  # MPC_BENCH_LARGE=1 only


# ==================================================================
#  Group A — crng (correlated randomness)
# ==================================================================

class TestCrng:

    @pytest.mark.parametrize("ell", ELLS)
    def test_crng_sum_zero(self, ell, rss3_pool):
        def party(pid, channels):
            inst = mpmt.ShrRep3(ell, pid)(channels["prev"], channels["next"])
            return inst.crng()

        results = rss3_pool.run(party)
        s = mpmt.ring_add(ell, mpmt.ring_add(ell, results[0], results[1]), results[2])
        assert s == 0, f"crng sum = {s}, expected 0 (ELL={ell})"

    @pytest.mark.parametrize("ell", ELLS)
    @pytest.mark.parametrize("n", VEC_SIZES)
    def test_crng_vec_sum_zero(self, ell, n, rss3_pool):
        def party(pid, channels):
            inst = mpmt.ShrRep3(ell, pid)(channels["prev"], channels["next"])
            Rv = mpmt.Rvector(ell)
            v = Rv(n)
            inst.crng_vec(v)
            return [v[i] for i in range(n)]

        results = rss3_pool.run(party)
        for i in range(n):
            s = mpmt.ring_add(ell, mpmt.ring_add(ell, results[0][i], results[1][i]), results[2][i])
            assert s == 0, f"crng_vec[{i}] sum = {s}, expected 0 (ELL={ell})"


# ==================================================================
#  Group B — share / recv_share
# ==================================================================

class TestShareScalar:

    @pytest.mark.parametrize("ell", ELLS)
    def test_share_reconstruct(self, ell, rss3_pool):
        m = mpmt.ring_mask(ell)
        secret = random.randint(0, m)

        def party(pid, channels):
            inst = mpmt.ShrRep3(ell, pid)(channels["prev"], channels["next"])
            return _unpack_scalar(_share_scalar(inst, pid, 0, secret))

        results = rss3_pool.run(party)
        assert _reconstruct_scalar(results, ell) == secret

    @pytest.mark.parametrize("ell", ELLS)
    def test_share_consistency(self, ell, rss3_pool):
        m = mpmt.ring_mask(ell)
        secret = random.randint(0, m)

        def party(pid, channels):
            inst = mpmt.ShrRep3(ell, pid)(channels["prev"], channels["next"])
            return _unpack_scalar(_share_scalar(inst, pid, 0, secret))

        results = rss3_pool.run(party)
        assert results[0][1] == results[1][0]
        assert results[1][1] == results[2][0]
        assert results[2][1] == results[0][0]


class TestShareVec:

    @pytest.mark.parametrize("ell", ELLS)
    @pytest.mark.parametrize("n", VEC_SIZES)
    def test_share_vec_reconstruct(self, ell, n, rss3_pool):
        m = mpmt.ring_mask(ell)
        secret = [random.randint(0, m) for _ in range(n)]
        SV = mpmt.ShrRep3ShareVec(ell)

        def party(pid, channels):
            inst = mpmt.ShrRep3(ell, pid)(channels["prev"], channels["next"])
            sv = SV(n)
            _share_vec(inst, pid, 0, ell, secret, sv)
            return _sv_to_lists(sv)

        results = rss3_pool.run(party)
        plain = _reconstruct_vec(results, ell, n)
        assert plain == secret

    @pytest.mark.parametrize("ell", ELLS)
    @pytest.mark.parametrize("n", NONZERO_SIZES)
    def test_share_vec_consistency(self, ell, n, rss3_pool):
        m = mpmt.ring_mask(ell)
        secret = [random.randint(0, m) for _ in range(n)]
        SV = mpmt.ShrRep3ShareVec(ell)

        def party(pid, channels):
            inst = mpmt.ShrRep3(ell, pid)(channels["prev"], channels["next"])
            sv = SV(n)
            _share_vec(inst, pid, 0, ell, secret, sv)
            return _sv_to_lists(sv)

        results = rss3_pool.run(party)
        assert results[0][1] == results[1][0]
        assert results[1][1] == results[2][0]
        assert results[2][1] == results[0][0]


# ==================================================================
#  Group C — add / sub
# ==================================================================

class TestAddSubScalar:

    @pytest.mark.parametrize("ell", ELLS)
    def test_add_scalar(self, ell, rss3_pool):
        m = mpmt.ring_mask(ell)
        a, b = random.randint(0, m), random.randint(0, m)
        expected = mpmt.ring_add(ell, a, b)

        def party(pid, channels):
            inst = mpmt.ShrRep3(ell, pid)(channels["prev"], channels["next"])
            sa = _share_scalar(inst, pid, 0, a)
            sb = _share_scalar(inst, pid, 0, b)
            return _unpack_scalar(inst.add(sa, sb))

        results = rss3_pool.run(party)
        assert _reconstruct_scalar(results, ell) == expected

    @pytest.mark.parametrize("ell", ELLS)
    def test_sub_scalar(self, ell, rss3_pool):
        m = mpmt.ring_mask(ell)
        a, b = random.randint(0, m), random.randint(0, m)
        expected = mpmt.ring_sub(ell, a, b)

        def party(pid, channels):
            inst = mpmt.ShrRep3(ell, pid)(channels["prev"], channels["next"])
            sa = _share_scalar(inst, pid, 0, a)
            sb = _share_scalar(inst, pid, 0, b)
            return _unpack_scalar(inst.sub(sa, sb))

        results = rss3_pool.run(party)
        assert _reconstruct_scalar(results, ell) == expected


class TestAddSubVec:

    @pytest.mark.parametrize("ell", ELLS)
    @pytest.mark.parametrize("n", VEC_SIZES)
    def test_add_vec(self, ell, n, rss3_pool):
        m = mpmt.ring_mask(ell)
        ea = [random.randint(0, m) for _ in range(n)]
        eb = [random.randint(0, m) for _ in range(n)]
        expected = [mpmt.ring_add(ell, ea[i], eb[i]) for i in range(n)]
        SV = mpmt.ShrRep3ShareVec(ell)

        def party(pid, channels):
            inst = mpmt.ShrRep3(ell, pid)(channels["prev"], channels["next"])
            sa, sb, sout = SV(n), SV(n), SV(n)
            _share_vec(inst, pid, 0, ell, ea, sa)
            _share_vec(inst, pid, 0, ell, eb, sb)
            inst.add_vec(sa, sb, sout)
            return _sv_to_lists(sout)

        results = rss3_pool.run(party)
        plain = _reconstruct_vec(results, ell, n)
        assert plain == expected

    @pytest.mark.parametrize("ell", ELLS)
    @pytest.mark.parametrize("n", VEC_SIZES)
    def test_sub_vec(self, ell, n, rss3_pool):
        m = mpmt.ring_mask(ell)
        ea = [random.randint(0, m) for _ in range(n)]
        eb = [random.randint(0, m) for _ in range(n)]
        expected = [mpmt.ring_sub(ell, ea[i], eb[i]) for i in range(n)]
        SV = mpmt.ShrRep3ShareVec(ell)

        def party(pid, channels):
            inst = mpmt.ShrRep3(ell, pid)(channels["prev"], channels["next"])
            sa, sb, sout = SV(n), SV(n), SV(n)
            _share_vec(inst, pid, 0, ell, ea, sa)
            _share_vec(inst, pid, 0, ell, eb, sb)
            inst.sub_vec(sa, sb, sout)
            return _sv_to_lists(sout)

        results = rss3_pool.run(party)
        plain = _reconstruct_vec(results, ell, n)
        assert plain == expected

    @pytest.mark.parametrize("ell", ELLS)
    @pytest.mark.parametrize("n", NONZERO_SIZES)
    def test_add_vec_aliasing(self, ell, n, rss3_pool):
        m = mpmt.ring_mask(ell)
        ea = [random.randint(0, m) for _ in range(n)]
        expected = [(ea[i] * 2) & m for i in range(n)]
        SV = mpmt.ShrRep3ShareVec(ell)

        def party(pid, channels):
            inst = mpmt.ShrRep3(ell, pid)(channels["prev"], channels["next"])
            sa = SV(n)
            _share_vec(inst, pid, 0, ell, ea, sa)
            inst.add_vec(sa, sa, sa)
            return _sv_to_lists(sa)

        results = rss3_pool.run(party)
        plain = _reconstruct_vec(results, ell, n)
        assert plain == expected

    @pytest.mark.parametrize("ell", ELLS)
    @pytest.mark.parametrize("n", NONZERO_SIZES)
    def test_sub_vec_aliasing(self, ell, n, rss3_pool):
        m = mpmt.ring_mask(ell)
        ea = [random.randint(0, m) for _ in range(n)]
        SV = mpmt.ShrRep3ShareVec(ell)

        def party(pid, channels):
            inst = mpmt.ShrRep3(ell, pid)(channels["prev"], channels["next"])
            sa = SV(n)
            _share_vec(inst, pid, 0, ell, ea, sa)
            inst.sub_vec(sa, sa, sa)
            return _sv_to_lists(sa)

        results = rss3_pool.run(party)
        plain = _reconstruct_vec(results, ell, n)
        assert all(v == 0 for v in plain)


# ==================================================================
#  Group D — mul / hadamard / dot
# ==================================================================

class TestMulScalar:

    @pytest.mark.parametrize("ell", ELLS)
    def test_mul_scalar(self, ell, rss3_pool):
        m = mpmt.ring_mask(ell)
        a, b = random.randint(0, m), random.randint(0, m)
        expected = mpmt.ring_mul(ell, a, b)

        def party(pid, channels):
            inst = mpmt.ShrRep3(ell, pid)(channels["prev"], channels["next"])
            sa = _share_scalar(inst, pid, 0, a)
            sb = _share_scalar(inst, pid, 0, b)
            return _unpack_scalar(inst.mul(sa, sb))

        results = rss3_pool.run(party)
        assert _reconstruct_scalar(results, ell) == expected


class TestHadamard:

    @pytest.mark.parametrize("ell", ELLS)
    @pytest.mark.parametrize("n", VEC_SIZES)
    def test_hadamard(self, ell, n, rss3_pool):
        m = mpmt.ring_mask(ell)
        ea = [random.randint(0, m) for _ in range(n)]
        eb = [random.randint(0, m) for _ in range(n)]
        expected = [mpmt.ring_mul(ell, ea[i], eb[i]) for i in range(n)]
        SV = mpmt.ShrRep3ShareVec(ell)

        def party(pid, channels):
            inst = mpmt.ShrRep3(ell, pid)(channels["prev"], channels["next"])
            sa, sb, sout = SV(n), SV(n), SV(n)
            _share_vec(inst, pid, 0, ell, ea, sa)
            _share_vec(inst, pid, 0, ell, eb, sb)
            inst.hadamard(sa, sb, sout)
            return _sv_to_lists(sout)

        results = rss3_pool.run(party)
        plain = _reconstruct_vec(results, ell, n)
        assert plain == expected

    @pytest.mark.parametrize("ell", ELLS)
    @pytest.mark.parametrize("n", NONZERO_SIZES)
    def test_hadamard_alias_raises(self, ell, n, rss3_pool):
        def party(pid, channels):
            inst = mpmt.ShrRep3(ell, pid)(channels["prev"], channels["next"])
            SV = mpmt.ShrRep3ShareVec(ell)
            sa = SV(n)
            _share_vec(inst, pid, 0, ell, [1] * n, sa)
            with pytest.raises(ValueError, match=r"hadamard.*alias|out.*must not"):
                inst.hadamard(sa, sa, sa)
            return "ok"

        results = rss3_pool.run(party)
        assert all(r == "ok" for r in results)

    @pytest.mark.slow
    @pytest.mark.parametrize("ell", [1])
    @pytest.mark.parametrize("n", LARGE_VEC_SIZES)
    def test_hadamard_large(self, ell, n, rss3_pool):
        """Hadamard at scale — exercises chunked ring reshare (>TCP buffer).

        Only ell=1 to keep reconstruction cheap.  The 3-party ring
        *must* survive n=1 000 000 (~1 MB per share) without deadlock.
        """
        m = mpmt.ring_mask(ell)
        ea = [random.randint(0, m) for _ in range(n)]
        eb = [random.randint(0, m) for _ in range(n)]
        expected = [mpmt.ring_mul(ell, ea[i], eb[i]) for i in range(n)]
        SV = mpmt.ShrRep3ShareVec(ell)

        def party(pid, channels):
            inst = mpmt.ShrRep3(ell, pid)(channels["prev"], channels["next"])
            sa, sb, sout = SV(n), SV(n), SV(n)
            _share_vec(inst, pid, 0, ell, ea, sa)
            _share_vec(inst, pid, 0, ell, eb, sb)
            inst.hadamard(sa, sb, sout)
            return _sv_to_lists(sout)

        results = rss3_pool.run(party)
        plain = _reconstruct_vec(results, ell, n)
        assert plain == expected

    @pytest.mark.slow
    @pytest.mark.skipif(
        not os.environ.get("MPC_BENCH_LARGE"),
        reason="Set MPC_BENCH_LARGE=1 for stress hadamard (50M/100M elements)"
    )
    @pytest.mark.parametrize("ell", [1])
    @pytest.mark.parametrize("n", LARGE_VEC_SIZES_STRESS)
    def test_hadamard_stress(self, ell, n, rss3_pool):
        """Hadamard at extreme scale — no reconstruction, deadlock survival.

        Verifies chunked ring reshare survives 100M elements (~12.5 MB
        per party, far above TCP buffer).  Only runs with MPC_BENCH_LARGE=1.
        """
        SV = mpmt.ShrRep3ShareVec(ell)

        def party(pid, channels):
            inst = mpmt.ShrRep3(ell, pid)(channels["prev"], channels["next"])
            sa, sb, sout = SV(n), SV(n), SV(n)
            _share_vec(inst, pid, 0, ell, [0] * n, sa)
            _share_vec(inst, pid, 0, ell, [0] * n, sb)
            inst.hadamard(sa, sb, sout)
            return "ok"

        results = rss3_pool.run(party)
        assert all(r == "ok" for r in results)


class TestDot:

    @pytest.mark.parametrize("ell", ELLS)
    @pytest.mark.parametrize("n", VEC_SIZES)
    def test_dot(self, ell, n, rss3_pool):
        m = mpmt.ring_mask(ell)
        ea = [random.randint(0, m) for _ in range(n)]
        eb = [random.randint(0, m) for _ in range(n)]
        expected = sum(ea[i] * eb[i] for i in range(n)) & m
        SV = mpmt.ShrRep3ShareVec(ell)

        def party(pid, channels):
            inst = mpmt.ShrRep3(ell, pid)(channels["prev"], channels["next"])
            sa, sb = SV(n), SV(n)
            _share_vec(inst, pid, 0, ell, ea, sa)
            _share_vec(inst, pid, 0, ell, eb, sb)
            return _unpack_scalar(inst.dot(sa, sb))

        results = rss3_pool.run(party)
        assert _reconstruct_scalar(results, ell) == expected


# ==================================================================
#  Group E — ring_conv (ELL == 1 only)
# ==================================================================

@pytest.mark.parametrize("ell", [1])
class TestRingConv:

    def test_ring_conv_scalar(self, ell, rss3_pool):
        m1 = mpmt.ring_mask(1)
        bit = random.randint(0, m1)

        for ell_to in range(2, 9):
            m_to = mpmt.ring_mask(ell_to)
            expected = bit & m_to

            def party(pid, channels):
                inst = mpmt.ShrRep3(ell, pid)(channels["prev"], channels["next"])
                s = _share_scalar(inst, pid, 0, bit)
                return _unpack_scalar(inst.ring_conv(s, ell_to))

            results = rss3_pool.run(party)
            assert _reconstruct_scalar(results, ell_to) == expected

    @pytest.mark.parametrize("n", VEC_SIZES)
    def test_ring_conv_vec(self, ell, n, rss3_pool):
        m1 = mpmt.ring_mask(1)
        bits = [random.randint(0, m1) for _ in range(n)]
        SV1 = mpmt.ShrRep3ShareVec(ell)

        for ell_to in range(2, 9):
            m_to = mpmt.ring_mask(ell_to)

            def party(pid, channels):
                inst = mpmt.ShrRep3(ell, pid)(channels["prev"], channels["next"])
                sv = SV1(n)
                _share_vec(inst, pid, 0, ell, bits, sv)
                SV_OUT = mpmt.ShrRep3ShareVec(ell_to)
                sv_out = SV_OUT(n)
                inst.ring_conv_vec(sv, sv_out, ell_to)
                return _sv_to_lists(sv_out)

            results = rss3_pool.run(party)
            plain = _reconstruct_vec(results, ell_to, n)
            expected = [b & m_to for b in bits]
            assert plain == expected

    def test_ring_conv_bad_ell_to(self, ell, rss3_pool):
        def party(pid, channels):
            inst = mpmt.ShrRep3(ell, pid)(channels["prev"], channels["next"])
            s = _share_scalar(inst, pid, 0, 0)
            with pytest.raises(ValueError, match="ell_to"):
                inst.ring_conv(s, 9)
            return "ok"

        results = rss3_pool.run(party)
        assert all(r == "ok" for r in results)

    @pytest.mark.parametrize("n", NONZERO_SIZES)
    def test_ring_conv_vec_bad_ell_to(self, ell, n, rss3_pool):
        SV1 = mpmt.ShrRep3ShareVec(ell)

        def party(pid, channels):
            inst = mpmt.ShrRep3(ell, pid)(channels["prev"], channels["next"])
            sv = SV1(n)
            _share_vec(inst, pid, 0, ell, [0] * n, sv)
            SV_OUT = mpmt.ShrRep3ShareVec(2)
            sv_out = SV_OUT(n)
            with pytest.raises(ValueError, match="ell_to"):
                inst.ring_conv_vec(sv, sv_out, 9)
            return "ok"

        results = rss3_pool.run(party)
        assert all(r == "ok" for r in results)


# ==================================================================
#  Group F — factory / boundary
# ==================================================================

class TestFactory:

    def test_ell_out_of_range_low(self):
        with pytest.raises(ValueError, match="ell out of range"):
            mpmt.ShrRep3(0, 0)

    def test_ell_out_of_range_high(self):
        with pytest.raises(ValueError, match="ell out of range"):
            mpmt.ShrRep3(9, 0)

    def test_party_out_of_range(self):
        with pytest.raises(ValueError, match="party"):
            mpmt.ShrRep3(1, 3)

    @pytest.mark.parametrize("ell", ELLS)
    def test_properties_exist(self, ell):
        for p in [0, 1, 2]:
            cls = mpmt.ShrRep3(ell, p)
            assert hasattr(cls, "ell")
            assert hasattr(cls, "party")


# ==================================================================
#  Group G — edge cases
# ==================================================================

class TestEmptyVector:

    @pytest.mark.parametrize("ell", ELLS)
    def test_empty_share_vec(self, ell, rss3_pool):
        SV = mpmt.ShrRep3ShareVec(ell)

        def party(pid, channels):
            inst = mpmt.ShrRep3(ell, pid)(channels["prev"], channels["next"])
            sv = SV(0)
            _share_vec(inst, pid, 0, ell, [], sv)
            return _sv_to_lists(sv)

        results = rss3_pool.run(party)
        assert results[0][0] == [] and results[0][1] == []

    @pytest.mark.parametrize("ell", ELLS)
    def test_empty_add_sub_hadamard_dot(self, ell, rss3_pool):
        m = mpmt.ring_mask(ell)
        SV = mpmt.ShrRep3ShareVec(ell)

        def party(pid, channels):
            inst = mpmt.ShrRep3(ell, pid)(channels["prev"], channels["next"])
            sa, sb, out = SV(0), SV(0), SV(0)
            inst.add_vec(sa, sb, out)
            inst.sub_vec(sa, sb, out)
            inst.hadamard(sa, sb, out)
            return _unpack_scalar(inst.dot(sa, sb))

        results = rss3_pool.run(party)
        assert _reconstruct_scalar(results, ell) == 0


class TestEll1Bit:

    def test_share_bit_values(self, rss3_pool):
        ell, m = 1, mpmt.ring_mask(1)

        def party0(pid, channels):
            inst = mpmt.ShrRep3(ell, pid)(channels["prev"], channels["next"])
            return _unpack_scalar(_share_scalar(inst, pid, 0, 0))

        results0 = rss3_pool.run(party0)
        assert _reconstruct_scalar(results0, ell) == 0

        def party1(pid, channels):
            inst = mpmt.ShrRep3(ell, pid)(channels["prev"], channels["next"])
            return _unpack_scalar(_share_scalar(inst, pid, 0, 1))

        results1 = rss3_pool.run(party1)
        assert _reconstruct_scalar(results1, ell) == 1

    def test_add_is_xor(self, rss3_pool):
        ell, m = 1, mpmt.ring_mask(1)

        for a, b, exp in [(0, 0, 0), (0, 1, 1), (1, 0, 1), (1, 1, 0)]:
            def party(pid, channels):
                inst = mpmt.ShrRep3(ell, pid)(channels["prev"], channels["next"])
                sa = _share_scalar(inst, pid, 0, a)
                sb = _share_scalar(inst, pid, 0, b)
                return _unpack_scalar(inst.add(sa, sb))

            results = rss3_pool.run(party)
            assert _reconstruct_scalar(results, ell) == exp

    def test_mul_is_and(self, rss3_pool):
        ell, m = 1, mpmt.ring_mask(1)

        for a, b, exp in [(0, 0, 0), (0, 1, 0), (1, 0, 0), (1, 1, 1)]:
            def party(pid, channels):
                inst = mpmt.ShrRep3(ell, pid)(channels["prev"], channels["next"])
                sa = _share_scalar(inst, pid, 0, a)
                sb = _share_scalar(inst, pid, 0, b)
                return _unpack_scalar(inst.mul(sa, sb))

            results = rss3_pool.run(party)
            assert _reconstruct_scalar(results, ell) == exp


class TestMaxValue:

    @pytest.mark.parametrize("ell", ELLS)
    def test_max_value_share_reconstruct(self, ell, rss3_pool):
        m = mpmt.ring_mask(ell)
        if m == 0:
            pytest.skip()
        secret = m

        def party(pid, channels):
            inst = mpmt.ShrRep3(ell, pid)(channels["prev"], channels["next"])
            return _unpack_scalar(_share_scalar(inst, pid, 0, secret))

        results = rss3_pool.run(party)
        assert _reconstruct_scalar(results, ell) == secret

    @pytest.mark.parametrize("ell", ELLS)
    def test_max_value_add_overflow(self, ell, rss3_pool):
        m = mpmt.ring_mask(ell)
        if m == 0:
            pytest.skip()
        expected = mpmt.ring_mul(ell, m, 2)

        def party(pid, channels):
            inst = mpmt.ShrRep3(ell, pid)(channels["prev"], channels["next"])
            sa = _share_scalar(inst, pid, 0, m)
            sb = _share_scalar(inst, pid, 0, m)
            return _unpack_scalar(inst.add(sa, sb))

        results = rss3_pool.run(party)
        assert _reconstruct_scalar(results, ell) == expected


# ==================================================================
#  Group H — multi-sharer
# ==================================================================

class TestMultiSharer:

    @pytest.mark.parametrize("ell", ELLS)
    def test_p1_p2_share_add_mul_scalar(self, ell, rss3_pool):
        m = mpmt.ring_mask(ell)
        a = random.randint(0, m)
        b = random.randint(0, m)
        expected_add = mpmt.ring_add(ell, a, b)
        expected_mul = mpmt.ring_mul(ell, a, b)

        def party(pid, channels):
            inst = mpmt.ShrRep3(ell, pid)(channels["prev"], channels["next"])
            sa = _share_scalar(inst, pid, 1, a)
            sb = _share_scalar(inst, pid, 2, b)
            return (_unpack_scalar(inst.add(sa, sb)),
                    _unpack_scalar(inst.mul(sa, sb)))

        results = rss3_pool.run(party)
        add_plain = (results[0][0][0] + results[1][0][0] + results[2][0][0]) & m
        mul_plain = (results[0][1][0] + results[1][1][0] + results[2][1][0]) & m
        assert add_plain == expected_add
        assert mul_plain == expected_mul

    @pytest.mark.parametrize("ell", ELLS)
    @pytest.mark.parametrize("n", NONZERO_SIZES)
    def test_p1_p2_share_hadamard_vec(self, ell, n, rss3_pool):
        m = mpmt.ring_mask(ell)
        ea = [random.randint(0, m) for _ in range(n)]
        eb = [random.randint(0, m) for _ in range(n)]
        expected = [mpmt.ring_mul(ell, ea[i], eb[i]) for i in range(n)]
        SV = mpmt.ShrRep3ShareVec(ell)

        def party(pid, channels):
            inst = mpmt.ShrRep3(ell, pid)(channels["prev"], channels["next"])
            sa, sb, sout = SV(n), SV(n), SV(n)
            _share_vec(inst, pid, 1, ell, ea, sa)
            _share_vec(inst, pid, 2, ell, eb, sb)
            inst.hadamard(sa, sb, sout)
            return _sv_to_lists(sout)

        results = rss3_pool.run(party)
        plain = _reconstruct_vec(results, ell, n)
        assert plain == expected

    @pytest.mark.parametrize("ell", ELLS)
    @pytest.mark.parametrize("n", VEC_SIZES)
    def test_all_three_share_dot(self, ell, n, rss3_pool):
        m = mpmt.ring_mask(ell)
        ea = [random.randint(0, m) for _ in range(n)]
        eb = [random.randint(0, m) for _ in range(n)]
        c = random.randint(0, m)
        expected = (sum(ea[i] * eb[i] for i in range(n)) + c) & m
        SV = mpmt.ShrRep3ShareVec(ell)

        def party(pid, channels):
            inst = mpmt.ShrRep3(ell, pid)(channels["prev"], channels["next"])
            sa, sb = SV(n), SV(n)
            _share_vec(inst, pid, 0, ell, ea, sa)
            _share_vec(inst, pid, 1, ell, eb, sb)
            s_dot = inst.dot(sa, sb)
            sc = _share_scalar(inst, pid, 2, c)
            return _unpack_scalar(inst.add(s_dot, sc))

        results = rss3_pool.run(party)
        assert _reconstruct_scalar(results, ell) == expected


# ==================================================================
#  Group I — multi-round
# ==================================================================

class TestMultiRound:

    @pytest.mark.parametrize("ell", ELLS)
    def test_share_mul_add_chain(self, ell, rss3_pool):
        m = mpmt.ring_mask(ell)
        a = random.randint(0, m)
        b = random.randint(0, m)
        c = random.randint(0, m)
        expected = (a * b + c) & m

        def party(pid, channels):
            inst = mpmt.ShrRep3(ell, pid)(channels["prev"], channels["next"])
            sa = _share_scalar(inst, pid, 0, a)
            sb = _share_scalar(inst, pid, 0, b)
            s_ab = inst.mul(sa, sb)
            sc = _share_scalar(inst, pid, 0, c)
            return _unpack_scalar(inst.add(s_ab, sc))

        results = rss3_pool.run(party)
        assert _reconstruct_scalar(results, ell) == expected

    @pytest.mark.parametrize("ell", ELLS)
    @pytest.mark.parametrize("n", NONZERO_SIZES)
    def test_share_hadamard_add_chain(self, ell, n, rss3_pool):
        m = mpmt.ring_mask(ell)
        ea = [random.randint(0, m) for _ in range(n)]
        eb = [random.randint(0, m) for _ in range(n)]
        ec = [random.randint(0, m) for _ in range(n)]
        expected = [(mpmt.ring_mul(ell, ea[i], eb[i]) + ec[i]) & m for i in range(n)]
        SV = mpmt.ShrRep3ShareVec(ell)

        def party(pid, channels):
            inst = mpmt.ShrRep3(ell, pid)(channels["prev"], channels["next"])
            sa, sb, sc, sab, sout = SV(n), SV(n), SV(n), SV(n), SV(n)
            _share_vec(inst, pid, 0, ell, ea, sa)
            _share_vec(inst, pid, 0, ell, eb, sb)
            inst.hadamard(sa, sb, sab)
            _share_vec(inst, pid, 0, ell, ec, sc)
            inst.add_vec(sab, sc, sout)
            return _sv_to_lists(sout)

        results = rss3_pool.run(party)
        plain = _reconstruct_vec(results, ell, n)
        assert plain == expected

    def test_share_mul_ring_conv_chain(self, rss3_pool):
        ell = 1
        m1 = mpmt.ring_mask(1)
        bit_a = random.randint(0, m1)
        bit_b = random.randint(0, m1)
        ell_to = 4
        m_to = mpmt.ring_mask(ell_to)
        expected = mpmt.ring_mul(ell, bit_a, bit_b)

        def party(pid, channels):
            inst = mpmt.ShrRep3(ell, pid)(channels["prev"], channels["next"])
            sa = _share_scalar(inst, pid, 0, bit_a)
            sb = _share_scalar(inst, pid, 0, bit_b)
            s_and = inst.mul(sa, sb)
            return _unpack_scalar(inst.ring_conv(s_and, ell_to))

        results = rss3_pool.run(party)
        assert _reconstruct_scalar(results, ell_to) == expected


# ==================================================================
#  Group I2 — revealAll
# ==================================================================

class TestRevealAll:

    @pytest.mark.parametrize("ell", ELLS)
    def test_reveal_all_scalar(self, ell, rss3_pool):
        m = mpmt.ring_mask(ell)
        secret = random.randint(0, m)

        def party(pid, channels):
            inst = mpmt.ShrRep3(ell, pid)(channels["prev"], channels["next"])
            ss = _share_scalar(inst, pid, 0, secret)
            return inst.reveal_scalar(ss)

        results = rss3_pool.run(party)
        assert results[0] == secret
        assert results[1] == secret
        assert results[2] == secret

    @pytest.mark.parametrize("ell", ELLS)
    def test_reveal_all_all_sharers(self, ell, rss3_pool):
        m = mpmt.ring_mask(ell)
        for sharer in [0, 1, 2]:
            secret = random.randint(0, m)

            def party(pid, channels):
                inst = mpmt.ShrRep3(ell, pid)(channels["prev"], channels["next"])
                ss = _share_scalar(inst, pid, sharer, secret)
                return inst.reveal_scalar(ss)

            results = rss3_pool.run(party)
            assert results[0] == secret, f"sharer=P{sharer}"
            assert results[1] == secret, f"sharer=P{sharer}"
            assert results[2] == secret, f"sharer=P{sharer}"

    @pytest.mark.parametrize("ell", ELLS)
    @pytest.mark.parametrize("n", VEC_SIZES)
    def test_reveal_all_vec(self, ell, n, rss3_pool):
        m = mpmt.ring_mask(ell)
        secret = [random.randint(0, m) for _ in range(n)]
        SV = mpmt.ShrRep3ShareVec(ell)
        Rv = mpmt.Rvector(ell)

        def party(pid, channels):
            inst = mpmt.ShrRep3(ell, pid)(channels["prev"], channels["next"])
            sv = SV(n)
            _share_vec(inst, pid, 0, ell, secret, sv)
            out = Rv(n)
            auxBuf = mpmt.RvectorPack(ell=ell)(n=n)
            inst.reveal_vector(sv, out, auxBuf)
            return [out[i] for i in range(n)]

        results = rss3_pool.run(party)
        assert results[0] == secret
        assert results[1] == secret
        assert results[2] == secret

    @pytest.mark.parametrize("ell", ELLS)
    def test_reveal_all_after_add(self, ell, rss3_pool):
        m = mpmt.ring_mask(ell)
        a, b = random.randint(0, m), random.randint(0, m)
        expected = mpmt.ring_add(ell, a, b)

        def party(pid, channels):
            inst = mpmt.ShrRep3(ell, pid)(channels["prev"], channels["next"])
            sa = _share_scalar(inst, pid, 0, a)
            sb = _share_scalar(inst, pid, 0, b)
            return inst.reveal_scalar(inst.add(sa, sb))

        results = rss3_pool.run(party)
        assert results[0] == expected
        assert results[1] == expected
        assert results[2] == expected

    @pytest.mark.parametrize("ell", ELLS)
    def test_reveal_all_after_mul(self, ell, rss3_pool):
        m = mpmt.ring_mask(ell)
        a, b = random.randint(0, m), random.randint(0, m)
        expected = mpmt.ring_mul(ell, a, b)

        def party(pid, channels):
            inst = mpmt.ShrRep3(ell, pid)(channels["prev"], channels["next"])
            sa = _share_scalar(inst, pid, 0, a)
            sb = _share_scalar(inst, pid, 0, b)
            return inst.reveal_scalar(inst.mul(sa, sb))

        results = rss3_pool.run(party)
        assert results[0] == expected
        assert results[1] == expected
        assert results[2] == expected

    @pytest.mark.parametrize("ell", ELLS)
    def test_reveal_all_after_dot(self, ell, rss3_pool):
        m = mpmt.ring_mask(ell)
        n = 16
        ea = [random.randint(0, m) for _ in range(n)]
        eb = [random.randint(0, m) for _ in range(n)]
        expected = 0
        for i in range(n):
            expected = mpmt.ring_add(ell, expected, mpmt.ring_mul(ell, ea[i], eb[i]))
        SV = mpmt.ShrRep3ShareVec(ell)

        def party(pid, channels):
            inst = mpmt.ShrRep3(ell, pid)(channels["prev"], channels["next"])
            sa, sb = SV(n), SV(n)
            _share_vec(inst, pid, 0, ell, ea, sa)
            _share_vec(inst, pid, 0, ell, eb, sb)
            return inst.reveal_scalar(inst.dot(sa, sb))

        results = rss3_pool.run(party)
        assert results[0] == expected
        assert results[1] == expected
        assert results[2] == expected


#  Group J — send / recv (plain data transfer)
# ==================================================================

class TestSendRecvScalar:

    @pytest.mark.parametrize("ell", ELLS)
    def test_send_recv_p0_to_p1(self, ell, rss3_pool):
        m = mpmt.ring_mask(ell)
        val = 0xAB & m

        def party(pid, channels):
            inst = mpmt.ShrRep3(ell, pid)(channels["prev"], channels["next"])
            if pid == 0:
                inst.send_data(1, val)
            elif pid == 1:
                return inst.recv_data(0)

        results = rss3_pool.run(party)
        assert results[1] == val

    @pytest.mark.parametrize("ell", ELLS)
    def test_send_recv_p0_to_p2(self, ell, rss3_pool):
        m = mpmt.ring_mask(ell)
        val = 0xCD & m

        def party(pid, channels):
            inst = mpmt.ShrRep3(ell, pid)(channels["prev"], channels["next"])
            if pid == 0:
                inst.send_data(2, val)
            elif pid == 2:
                return inst.recv_data(0)

        results = rss3_pool.run(party)
        assert results[2] == val

    @pytest.mark.parametrize("ell", ELLS)
    def test_send_recv_p1_to_p2(self, ell, rss3_pool):
        m = mpmt.ring_mask(ell)
        val = 0xEF & m

        def party(pid, channels):
            inst = mpmt.ShrRep3(ell, pid)(channels["prev"], channels["next"])
            if pid == 1:
                inst.send_data(2, val)
            elif pid == 2:
                return inst.recv_data(1)

        results = rss3_pool.run(party)
        assert results[2] == val


class TestSendRecvBytes:

    @pytest.mark.parametrize("ell", ELLS)
    @pytest.mark.parametrize("n", NONZERO_SIZES)
    def test_send_recv_bytes(self, ell, n, rss3_pool):
        m = mpmt.ring_mask(ell)
        vals = [random.randint(0, m) for _ in range(n)]
        Rv = mpmt.Rvector(ell)

        def party(pid, channels):
            inst = mpmt.ShrRep3(ell, pid)(channels["prev"], channels["next"])
            if pid == 0:
                vec = Rv(n)
                for i, v in enumerate(vals):
                    vec[i] = v
                auxBuf = mpmt.RvectorPack(ell=ell)(n=n)
                mpmt.rvector_pack(vec, auxBuf)
                inst.send_data(1, auxBuf)
            elif pid == 1:
                auxBuf = mpmt.RvectorPack(ell=ell)(n=n)
                inst.recv_data(0, auxBuf)
                out = Rv(n)
                mpmt.rvector_unpack(auxBuf, out)
                return [out[i] for i in range(n)]

        results = rss3_pool.run(party)
        assert results[1] == vals


class TestSendRecvErrors:

    @pytest.mark.parametrize("ell", ELLS)
    def test_send_to_self_raises(self, ell, rss3_pool):
        def party(pid, channels):
            inst = mpmt.ShrRep3(ell, pid)(channels["prev"], channels["next"])
            with pytest.raises(ValueError, match="cannot send to self"):
                inst.send_data(pid, 0)
            return "ok"

        results = rss3_pool.run(party)
        assert all(r == "ok" for r in results)

    @pytest.mark.parametrize("ell", ELLS)
    def test_recv_from_self_raises(self, ell, rss3_pool):
        def party(pid, channels):
            inst = mpmt.ShrRep3(ell, pid)(channels["prev"], channels["next"])
            with pytest.raises(ValueError, match="cannot recv from self"):
                inst.recv_data(pid)
            return "ok"

        results = rss3_pool.run(party)
        assert all(r == "ok" for r in results)

    @pytest.mark.parametrize("ell", ELLS)
    def test_send_bad_to_raises(self, ell, rss3_pool):
        def party(pid, channels):
            inst = mpmt.ShrRep3(ell, pid)(channels["prev"], channels["next"])
            with pytest.raises(ValueError, match="to must be in"):
                inst.send_data(3, 0)
            return "ok"

        results = rss3_pool.run(party)
        assert all(r == "ok" for r in results)

    @pytest.mark.parametrize("ell", ELLS)
    def test_recv_bad_from_raises(self, ell, rss3_pool):
        def party(pid, channels):
            inst = mpmt.ShrRep3(ell, pid)(channels["prev"], channels["next"])
            with pytest.raises(ValueError, match="from must be in"):
                inst.recv_data(3)
            return "ok"

        results = rss3_pool.run(party)
        assert all(r == "ok" for r in results)

    @pytest.mark.parametrize("ell", ELLS)
    def test_send_bytes_to_self_raises(self, ell, rss3_pool):
        def party(pid, channels):
            inst = mpmt.ShrRep3(ell, pid)(channels["prev"], channels["next"])
            buf = mpmt.RvectorPack(ell=ell)(n=1)
            with pytest.raises(ValueError, match="cannot send to self"):
                inst.send_data(pid, buf)
            return "ok"

        results = rss3_pool.run(party)
        assert all(r == "ok" for r in results)

    @pytest.mark.parametrize("ell", ELLS)
    def test_recv_bytes_from_self_raises(self, ell, rss3_pool):
        def party(pid, channels):
            inst = mpmt.ShrRep3(ell, pid)(channels["prev"], channels["next"])
            buf = mpmt.RvectorPack(ell=ell)(n=1)
            with pytest.raises(ValueError, match="cannot recv from self"):
                inst.recv_data(pid, buf)
            return "ok"

        results = rss3_pool.run(party)
        assert all(r == "ok" for r in results)

    @pytest.mark.parametrize("ell", ELLS)
    def test_send_bytes_bad_to_raises(self, ell, rss3_pool):
        def party(pid, channels):
            inst = mpmt.ShrRep3(ell, pid)(channels["prev"], channels["next"])
            buf = mpmt.RvectorPack(ell=ell)(n=1)
            with pytest.raises(ValueError, match="to must be in"):
                inst.send_data(3, buf)
            return "ok"

        results = rss3_pool.run(party)
        assert all(r == "ok" for r in results)

    @pytest.mark.parametrize("ell", ELLS)
    def test_recv_bytes_bad_from_raises(self, ell, rss3_pool):
        def party(pid, channels):
            inst = mpmt.ShrRep3(ell, pid)(channels["prev"], channels["next"])
            buf = mpmt.RvectorPack(ell=ell)(n=1)
            with pytest.raises(ValueError, match="from must be in"):
                inst.recv_data(3, buf)
            return "ok"

        results = rss3_pool.run(party)
        assert all(r == "ok" for r in results)
