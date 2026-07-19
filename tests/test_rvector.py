"""Rvector unit tests — mirrors tests/test_rvector.cpp"""

import random
import pytest
import mpmt

ELLS = list(range(1, 9))
SIZES = [0, 1, 7, 8, 9, 31, 32, 33, 63, 64, 65, 100, 127, 128, 129, 256, 1000]
ALGEBRA_SIZES = [1, 7, 8, 9, 32, 64, 65, 100, 256, 1000]


def random_elements(n, seed, ell):
    rng = random.Random(seed)
    return [rng.randint(0, mpmt.ring_mask(ell)) for _ in range(n)]


def rv(ell, size, val=None):
    """factory shorthand"""
    Rv = mpmt.Rvector(ell)
    return Rv(size, val) if val is not None else Rv(size)


def build_from_elements(ell, elems):
    Rv = mpmt.Rvector(ell)
    v = Rv(len(elems))
    v.fill()
    for i, e in enumerate(elems):
        v[i] = e
    return v


def to_elements(v):
    return [v[i] for i in range(len(v))]


def ref_add(ea, eb, ell):
    return [mpmt.ring_add(ell, a, b) for a, b in zip(ea, eb)]


def ref_sub(ea, eb, ell):
    return [mpmt.ring_sub(ell, a, b) for a, b in zip(ea, eb)]


def ref_hadamard(ea, eb, ell):
    return [mpmt.ring_mul(ell, a, b) for a, b in zip(ea, eb)]


def ref_add_scalar(ea, s, ell):
    return [mpmt.ring_add(ell, a, s) for a in ea]


def ref_sub_scalar(ea, s, ell):
    return [mpmt.ring_sub(ell, a, s) for a in ea]


def ref_mul_scalar(ea, s, ell):
    return [mpmt.ring_mul(ell, a, s) for a in ea]


def ref_dot(ea, eb, ell):
    r = 0
    for a, b in zip(ea, eb):
        r = mpmt.ring_add(ell, r, mpmt.ring_mul(ell, a, b))
    return r


def ref_reduce(ea, ell):
    r = 0
    for a in ea:
        r = mpmt.ring_add(ell, r, a)
    return r


def expect_elements_eq(actual, expected):
    got = to_elements(actual)
    assert got == expected


#  1. arithmetic correctness

class TestArith:
    @pytest.mark.parametrize("ell", ELLS)
    @pytest.mark.parametrize("n", SIZES)
    def test_add(self, ell, n):
        ea = random_elements(n, 0xADD000 + ell, ell)
        eb = random_elements(n, 0xADD100 + ell, ell)
        expected = ref_add(ea, eb, ell)
        a = build_from_elements(ell, ea)
        b = build_from_elements(ell, eb)
        out = rv(ell, n)
        mpmt.Rvector(ell).add(a, b, out)
        expect_elements_eq(out, expected)

    @pytest.mark.parametrize("ell", ELLS)
    @pytest.mark.parametrize("n", SIZES)
    def test_sub(self, ell, n):
        ea = random_elements(n, 0x5B0000 + ell, ell)
        eb = random_elements(n, 0x5B0100 + ell, ell)
        expected = ref_sub(ea, eb, ell)
        a = build_from_elements(ell, ea)
        b = build_from_elements(ell, eb)
        out = rv(ell, n)
        mpmt.Rvector(ell).sub(a, b, out)
        expect_elements_eq(out, expected)

    @pytest.mark.parametrize("ell", ELLS)
    @pytest.mark.parametrize("n", SIZES)
    def test_hadamard(self, ell, n):
        ea = random_elements(n, 0xCAD000 + ell, ell)
        eb = random_elements(n, 0xCAD100 + ell, ell)
        expected = ref_hadamard(ea, eb, ell)
        a = build_from_elements(ell, ea)
        b = build_from_elements(ell, eb)
        out = rv(ell, n)
        mpmt.Rvector(ell).hadamard(a, b, out)
        expect_elements_eq(out, expected)

    @pytest.mark.parametrize("ell", ELLS)
    @pytest.mark.parametrize("n", SIZES)
    def test_add_scalar(self, ell, n):
        m = mpmt.ring_mask(ell)
        ea = random_elements(n, 0x5CA000 + ell, ell)
        s = (0x5CA100 + ell) & m
        expected = ref_add_scalar(ea, s, ell)
        a = build_from_elements(ell, ea)
        out = rv(ell, n)
        mpmt.Rvector(ell).add_scalar(a, s, out)
        expect_elements_eq(out, expected)

    @pytest.mark.parametrize("ell", ELLS)
    @pytest.mark.parametrize("n", SIZES)
    def test_sub_scalar(self, ell, n):
        m = mpmt.ring_mask(ell)
        ea = random_elements(n, 0x5C0000 + ell, ell)
        s = (0x5C0100 + ell) & m
        expected = ref_sub_scalar(ea, s, ell)
        a = build_from_elements(ell, ea)
        out = rv(ell, n)
        mpmt.Rvector(ell).sub_scalar(a, s, out)
        expect_elements_eq(out, expected)

    @pytest.mark.parametrize("ell", ELLS)
    @pytest.mark.parametrize("n", SIZES)
    def test_mul_scalar(self, ell, n):
        m = mpmt.ring_mask(ell)
        ea = random_elements(n, 0xC5C000 + ell, ell)
        s = (0xC5C100 + ell) & m
        expected = ref_mul_scalar(ea, s, ell)
        a = build_from_elements(ell, ea)
        out = rv(ell, n)
        mpmt.Rvector(ell).mul_scalar(a, s, out)
        expect_elements_eq(out, expected)

    @pytest.mark.parametrize("ell", ELLS)
    @pytest.mark.parametrize("n", SIZES)
    def test_dot(self, ell, n):
        ea = random_elements(n, 0xD07000 + ell, ell)
        eb = random_elements(n, 0xD07100 + ell, ell)
        a = build_from_elements(ell, ea)
        b = build_from_elements(ell, eb)
        assert mpmt.Rvector(ell).dot(a, b) == ref_dot(ea, eb, ell)

    @pytest.mark.parametrize("ell", ELLS)
    @pytest.mark.parametrize("n", SIZES)
    def test_reduce(self, ell, n):
        ea = random_elements(n, 0xED0000 + ell, ell)
        a = build_from_elements(ell, ea)
        assert mpmt.Rvector(ell).reduce(a) == ref_reduce(ea, ell)


#  2. in-place operations

class TestInPlace:
    @pytest.mark.parametrize("ell", ELLS)
    @pytest.mark.parametrize("n", SIZES)
    def test_add(self, ell, n):
        ea = random_elements(n, 0x1AD000 + ell, ell)
        eb = random_elements(n, 0x1AD100 + ell, ell)
        a = build_from_elements(ell, ea)
        b = build_from_elements(ell, eb)
        mpmt.Rvector(ell).add(a, b, a)
        expect_elements_eq(a, ref_add(ea, eb, ell))

    @pytest.mark.parametrize("ell", ELLS)
    @pytest.mark.parametrize("n", SIZES)
    def test_mul_scalar(self, ell, n):
        m = mpmt.ring_mask(ell)
        ea = random_elements(n, 0xC1AD00 + ell, ell)
        s = (0xC1AD01 + ell) & m
        a = build_from_elements(ell, ea)
        mpmt.Rvector(ell).mul_scalar(a, s, a)
        expect_elements_eq(a, ref_mul_scalar(ea, s, ell))

    @pytest.mark.parametrize("ell", ELLS)
    @pytest.mark.parametrize("n", SIZES)
    def test_add_out_is_second_arg(self, ell, n):
        ea = random_elements(n, 0xADD200 + ell, ell)
        eb = random_elements(n, 0xADD300 + ell, ell)
        a = build_from_elements(ell, ea)
        b = build_from_elements(ell, eb)
        mpmt.Rvector(ell).add(a, b, b)
        expect_elements_eq(b, ref_add(ea, eb, ell))

    @pytest.mark.parametrize("ell", ELLS)
    @pytest.mark.parametrize("n", SIZES)
    def test_hadamard_all_same(self, ell, n):
        ea = random_elements(n, 0xADD400 + ell, ell)
        a = build_from_elements(ell, ea)
        mpmt.Rvector(ell).hadamard(a, a, a)
        expect_elements_eq(a, ref_hadamard(ea, ea, ell))


#  3. boundary: max ring values

class TestMaxValue:
    @pytest.mark.parametrize("ell", ELLS)
    @pytest.mark.parametrize("n", [1, 7, 64, 100])
    def test_roundtrip(self, ell, n):
        m = mpmt.ring_mask(ell)
        if m == 0:
            pytest.skip()
        a = build_from_elements(ell, [m] * n)
        out = rv(ell, n)
        mpmt.Rvector(ell).add(a, a, out)
        expect_elements_eq(out, [mpmt.ring_add(ell, m, m)] * n)


#  4. ring-value validation

class TestRingCheck:
    @pytest.mark.parametrize("ell", [e for e in ELLS if e != 8])
    def test_construct_rejects_out_of_range(self, ell):
        ok, bad = (1 << ell) - 1, (1 << ell)
        v = rv(ell, 10)
        v.fill(ok)
        with pytest.raises(ValueError):
            v.fill(bad)

    @pytest.mark.parametrize("ell", [e for e in ELLS if e != 8])
    def test_scalar_ops_reject_out_of_range(self, ell):
        ok, bad = (1 << ell) - 1, (1 << ell)
        v = rv(ell, 16)
        out = rv(ell, 16)
        Rv = mpmt.Rvector(ell)
        Rv.add_scalar(v, ok, out)
        Rv.sub_scalar(v, ok, out)
        Rv.mul_scalar(v, ok, out)
        for op in [Rv.add_scalar, Rv.sub_scalar, Rv.mul_scalar]:
            with pytest.raises(ValueError):
                op(v, bad, out)

    def test_ell8_accepts_all_uint8(self):
        """ELL=8 accepts full uint8 range (0xFF) where other ELL reject >= 2^ELL."""
        v = rv(8, 10)
        v.fill(0xFF)          # must not raise
        assert v[0] == 0xFF
        v2 = rv(8, 16)
        v2.fill(0xFF)
        out = rv(8, 16)
        Rv = mpmt.Rvector(8)
        Rv.add_scalar(v2, 0xFF, out)   # must not raise
        Rv.sub_scalar(v2, 0xFF, out)   # must not raise
        Rv.mul_scalar(v2, 0xFF, out)   # must not raise


#  5. size-mismatch errors

class TestSizeErrors:
    @pytest.mark.parametrize("ell", ELLS)
    def test_vec_vec(self, ell):
        a, b, out = rv(ell, 10), rv(ell, 20), rv(ell, 10)
        Rv = mpmt.Rvector(ell)
        for op in [Rv.add, Rv.sub, Rv.hadamard]:
            with pytest.raises(RuntimeError):
                op(a, b, out)
        with pytest.raises(RuntimeError):
            Rv.dot(a, b)

    @pytest.mark.parametrize("ell", ELLS)
    def test_out_size(self, ell):
        a, out = rv(ell, 10), rv(ell, 20)
        Rv = mpmt.Rvector(ell)
        for op in [Rv.add, Rv.sub, Rv.hadamard,
                   Rv.add_scalar, Rv.sub_scalar, Rv.mul_scalar]:
            with pytest.raises(RuntimeError):
                if op in [Rv.add, Rv.sub, Rv.hadamard]:
                    op(a, a, out)
                else:
                    op(a, 0, out)


#  6. identity operations

class TestIdentity:
    @pytest.mark.parametrize("ell", ELLS)
    def test_add_zero(self, ell):
        v = rv(ell, 100, 1)
        zero = rv(ell, 100)
        zero.fill()
        out = rv(ell, 100)
        mpmt.Rvector(ell).add(v, zero, out)
        assert to_elements(out) == to_elements(v)

    @pytest.mark.parametrize("ell", ELLS)
    def test_sub_self(self, ell):
        init = [1, 3, 5, 7, 11, 13, 17, 19]
        v = rv(ell, 100, init[ell - 1])
        out = rv(ell, 100)
        mpmt.Rvector(ell).sub(v, v, out)
        assert all(x == 0 for x in to_elements(out))

    @pytest.mark.parametrize("ell", ELLS)
    def test_hadamard_one(self, ell):
        init = [1, 2, 3, 4, 5, 6, 7, 8]
        v = rv(ell, 100, init[ell - 1])
        one = rv(ell, 100, 1)
        out = rv(ell, 100)
        mpmt.Rvector(ell).hadamard(v, one, out)
        expect_elements_eq(out, to_elements(v))


#  7. ELL=1 specifics

class TestEll1:
    @pytest.mark.parametrize("n", SIZES)
    def test_add_scalar_zero_is_copy(self, n):
        ea = random_elements(n, 0xE1A000, 1)
        a = build_from_elements(1, ea)
        out = rv(1, n)
        mpmt.Rvector(1).add_scalar(a, 0, out)
        expect_elements_eq(out, ea)

    @pytest.mark.parametrize("n", SIZES)
    def test_mul_scalar_one_is_copy(self, n):
        ea = random_elements(n, 0xE1C000, 1)
        a = build_from_elements(1, ea)
        out = rv(1, n)
        mpmt.Rvector(1).mul_scalar(a, 1, out)
        expect_elements_eq(out, ea)

    def test_add_scalar_one_is_not(self):
        a = build_from_elements(1, [1, 1, 0, 0])
        out = rv(1, 4)
        mpmt.Rvector(1).add_scalar(a, 1, out)
        assert to_elements(out) == [0, 0, 1, 1]


#  8. serialization

class TestSerialization:
    @pytest.mark.parametrize("ell", ELLS)
    def test_roundtrip(self, ell):
        ea = random_elements(128, 0x5E0000 + ell, ell)
        a = build_from_elements(ell, ea)
        b = rv(ell, 128)
        b.fill()
        b.from_bytes(a.to_bytes())
        assert to_elements(b) == ea

    @pytest.mark.parametrize("ell", [e for e in ELLS if e != 1])
    def test_partial(self, ell):
        v = rv(ell, 10)
        v.fill()
        v.from_bytes(b"\x01\x02\x03")
        assert v[0] == 1
        assert v[1] == 2
        assert v[2] == 3
        assert v[3] == 0

    @pytest.mark.parametrize("ell", [e for e in ELLS if e != 1])
    def test_input_longer_than_buffer(self, ell):
        m = mpmt.ring_mask(ell)
        v = rv(ell, 3)
        v.fill()
        v.from_bytes(bytes([1 & m, 2 & m, 3 & m, 4, 5]))
        assert v[0] == (1 & m)
        assert v[1] == (2 & m)
        assert v[2] == (3 & m)

    @pytest.mark.parametrize("ell", [e for e in ELLS if e != 1])
    def test_input_shorter_than_buffer(self, ell):
        """Short from_bytes only overwrites prefix; tail stays as-is."""
        v = rv(ell, 10)
        v.fill(0xAB & mpmt.ring_mask(ell))
        v.from_bytes(b"\x01\x02\x03")
        assert v[0] == 1
        assert v[1] == 2
        assert v[2] == 3
        assert v[3] == (0xAB & mpmt.ring_mask(ell))
        assert v[9] == (0xAB & mpmt.ring_mask(ell))


#  9. ELL=1 partial word masking

class TestPartialWordMasking:
    @pytest.mark.parametrize("n", SIZES)
    def test_padding_is_zero(self, n):
        ea = random_elements(n, 0x9F0000, 1)
        v = build_from_elements(1, ea)
        got = to_elements(v)
        for i in range(n):
            assert got[i] == ea[i], f"mismatch at {i}"


#  10. empty vectors

class TestEmptyVector:
    @pytest.mark.parametrize("ell", ELLS)
    def test_all_ops(self, ell):
        a, b, out = rv(ell, 0), rv(ell, 0), rv(ell, 0)
        Rv = mpmt.Rvector(ell)
        Rv.add(a, b, out)
        Rv.sub(a, b, out)
        Rv.hadamard(a, b, out)
        Rv.add_scalar(a, 0, out)
        Rv.sub_scalar(a, 0, out)
        Rv.mul_scalar(a, 0, out)
        assert Rv.dot(a, b) == 0
        assert Rv.reduce(a) == 0


#  11. algebra

class TestAlgebra:
    @pytest.mark.parametrize("ell", ELLS)
    @pytest.mark.parametrize("n", ALGEBRA_SIZES)
    def test_add_commutative(self, ell, n):
        ea = random_elements(n, 0xACC000 + ell, ell)
        eb = random_elements(n, 0xACC100 + ell, ell)
        a = build_from_elements(ell, ea)
        b = build_from_elements(ell, eb)
        ab, ba = rv(ell, n), rv(ell, n)
        Rv = mpmt.Rvector(ell)
        Rv.add(a, b, ab)
        Rv.add(b, a, ba)
        assert to_elements(ab) == to_elements(ba)

    @pytest.mark.parametrize("ell", ELLS)
    @pytest.mark.parametrize("n", ALGEBRA_SIZES)
    def test_hadamard_commutative(self, ell, n):
        ea = random_elements(n, 0xACC200 + ell, ell)
        eb = random_elements(n, 0xACC300 + ell, ell)
        a = build_from_elements(ell, ea)
        b = build_from_elements(ell, eb)
        ab, ba = rv(ell, n), rv(ell, n)
        Rv = mpmt.Rvector(ell)
        Rv.hadamard(a, b, ab)
        Rv.hadamard(b, a, ba)
        assert to_elements(ab) == to_elements(ba)

    @pytest.mark.parametrize("ell", ELLS)
    @pytest.mark.parametrize("n", ALGEBRA_SIZES)
    def test_dot_symmetric(self, ell, n):
        ea = random_elements(n, 0xACC400 + ell, ell)
        eb = random_elements(n, 0xACC500 + ell, ell)
        a = build_from_elements(ell, ea)
        b = build_from_elements(ell, eb)
        Rv = mpmt.Rvector(ell)
        assert Rv.dot(a, b) == Rv.dot(b, a)

    @pytest.mark.parametrize("ell", ELLS)
    @pytest.mark.parametrize("n", ALGEBRA_SIZES)
    def test_dot_self(self, ell, n):
        ea = random_elements(n, 0xACC600 + ell, ell)
        a = build_from_elements(ell, ea)
        assert mpmt.Rvector(ell).dot(a, a) == ref_dot(ea, ea, ell)


#  12. large sizes

class TestLarge:
    @pytest.mark.parametrize("ell", [1, 2, 4, 8])
    def test_add(self, ell):
        m, n = mpmt.ring_mask(ell), 1 << 25
        v = rv(ell, n, 1)
        out = rv(ell, n)
        mpmt.Rvector(ell).add(v, v, out)
        for i in [0, n // 2, n - 1]:
            assert out[i] == ((1 + 1) & m)

    @pytest.mark.parametrize("ell", [1, 2, 4, 8])
    def test_hadamard(self, ell):
        m, n = mpmt.ring_mask(ell), 1 << 25
        val = 3 if ell > 1 else 1
        v = rv(ell, n, val)
        out = rv(ell, n)
        mpmt.Rvector(ell).hadamard(v, v, out)
        expected = (val * val) & m
        for i in [0, n // 2, n - 1]:
            assert out[i] == expected


#  ───────────────────────────────────────────────────────────────────
#  13.  batch_set / batch_get  (zero-copy via array('Q'))
#  ───────────────────────────────────────────────────────────────────

class TestBatchOps:
    @pytest.mark.parametrize("ell", ELLS)
    @pytest.mark.parametrize("n", [1, 7, 64, 100, 1000])
    def test_batch_set_then_get(self, ell, n):
        import array
        indices = array.array('Q', range(n))
        val = (0xAB + ell) & mpmt.ring_mask(ell)
        v = rv(ell, n)
        v.fill(0)
        v.batch_set(indices, val)
        # verify every element
        for i in range(n):
            assert v[i] == val
        # batch_get back
        out = rv(ell, n)
        out.fill()
        v.batch_get(indices, out)
        for i in range(n):
            assert out[i] == val

    @pytest.mark.parametrize("ell", ELLS)
    def test_batch_set_sparse(self, ell):
        import array
        n = 256
        v = rv(ell, n)
        v.fill(0)
        indices = array.array('Q', [10, 50, 100, 150, 200, 250])
        v.batch_set(indices, 1)
        for i in range(n):
            assert v[i] == (1 if i in indices else 0)

    @pytest.mark.parametrize("ell", ELLS)
    def test_batch_get_sparse(self, ell):
        import array
        n = 1000
        expected = [i & mpmt.ring_mask(ell) for i in range(n)]
        v = build_from_elements(ell, expected)
        indices = array.array('Q', [0, 1, 15, 16, 255, 256, 511, 512, 999])
        out = rv(ell, len(indices))
        v.batch_get(indices, out)
        for k, idx in enumerate(indices):
            assert out[k] == expected[idx]

    @pytest.mark.parametrize("ell", ELLS)
    def test_batch_set_empty(self, ell):
        import array
        v = rv(ell, 100)
        v.fill(0)
        indices = array.array('Q', [])  # empty
        v.batch_set(indices, 1)  # no-op

    @pytest.mark.parametrize("ell", ELLS)
    def test_batch_get_out_too_small_raises(self, ell):
        import array
        v = rv(ell, 100)
        indices = array.array('Q', [1, 2, 3])
        out = rv(ell, 2)  # too small
        with pytest.raises((ValueError, RuntimeError)):
            v.batch_get(indices, out)

    @pytest.mark.parametrize("ell", ELLS)
    def test_batch_set_index_out_of_range_raises(self, ell):
        import array
        v = rv(ell, 50)
        indices = array.array('Q', [49, 50])  # 50 >= 50
        with pytest.raises(IndexError, match=">= 50"):
            v.batch_set(indices, 1)

    @pytest.mark.parametrize("ell", ELLS)
    def test_batch_set_val_out_of_range_raises(self, ell):
        if ell == 8:
            pytest.skip("ELL=8 has no out-of-range values")
        import array
        v = rv(ell, 10)
        bad = 1 << ell
        indices = array.array('Q', [0])
        with pytest.raises(ValueError):
            v.batch_set(indices, bad)

    def test_batch_set_wrong_buffer_type_raises(self):
        v = rv(1, 10)
        # bytes is not uint64
        with pytest.raises((RuntimeError, TypeError, ValueError)):
            v.batch_set(b"abcdefgh", 1)


#  ───────────────────────────────────────────────────────────────────
#  14.  save / load  (file I/O via RvectorPack)
#  ───────────────────────────────────────────────────────────────────

import os
import shutil

_TEST_TMP = "test_tmp"


class TestFileIO:

    @pytest.fixture(autouse=True)
    def _cleanup(self):
        """Ensure test_tmp is clean before and after each test."""
        if os.path.exists(_TEST_TMP):
            shutil.rmtree(_TEST_TMP)
        os.makedirs(_TEST_TMP, exist_ok=True)
        yield
        if os.path.exists(_TEST_TMP):
            shutil.rmtree(_TEST_TMP)

    @pytest.mark.parametrize("ell", ELLS)
    @pytest.mark.parametrize("n", [0, 1, 64, 128, 1000])
    def test_save_load_roundtrip(self, ell, n):
        path = os.path.join(_TEST_TMP, f"test_{ell}_{n}.mpmtrvp")
        v = rv(ell, n)
        v.rand_fill()
        auxSave = mpmt.RvectorPack(ell=ell)(n=n)
        v.save(path, auxSave)

        v2 = rv(ell, n)
        auxLoad = mpmt.RvectorPack(ell=ell)(n=n)
        v2.load(path, auxLoad)
        assert to_elements(v) == to_elements(v2)

    @pytest.mark.parametrize("ell", ELLS)
    def test_save_bad_extension_raises(self, ell):
        v = rv(ell, 10)
        aux = mpmt.RvectorPack(ell=ell)(n=10)
        with pytest.raises(RuntimeError, match="extension"):
            v.save("test_tmp/test.txt", aux)

    @pytest.mark.parametrize("ell", ELLS)
    def test_load_ell_mismatch_raises(self, ell):
        # save with ell, try to load with a different ell — should fail
        n = 32
        path = os.path.join(_TEST_TMP, "mismatch.mpmtrvp")
        v = rv(ell, n)
        v.fill(1)
        aux = mpmt.RvectorPack(ell=ell)(n=n)
        v.save(path, aux)

        other_ell = ell + 1 if ell < 7 else ell - 1
        v2 = rv(other_ell, n)
        aux2 = mpmt.RvectorPack(ell=other_ell)(n=n)
        with pytest.raises(RuntimeError, match="ELL mismatch"):
            v2.load(path, aux2)

    @pytest.mark.parametrize("ell", ELLS)
    def test_load_size_mismatch_raises(self, ell):
        n_save, n_load = 32, 64
        path = os.path.join(_TEST_TMP, "size_mismatch.mpmtrvp")
        v = rv(ell, n_save)
        v.fill(1)
        aux = mpmt.RvectorPack(ell=ell)(n=n_save)
        v.save(path, aux)

        v2 = rv(ell, n_load)
        aux2 = mpmt.RvectorPack(ell=ell)(n=n_load)
        with pytest.raises(RuntimeError, match="size mismatch"):
            v2.load(path, aux2)

    @pytest.mark.parametrize("ell", ELLS)
    def test_load_bad_magic_raises(self, ell):
        path = os.path.join(_TEST_TMP, "bad_magic.mpmtrvp")
        # write non-MPMT content
        with open(path, "wb") as f:
            f.write(b"NOT A VALID MPMT FILE.....")
        v = rv(ell, 10)
        aux = mpmt.RvectorPack(ell=ell)(n=10)
        with pytest.raises(RuntimeError, match="bad magic"):
            v.load(path, aux)
