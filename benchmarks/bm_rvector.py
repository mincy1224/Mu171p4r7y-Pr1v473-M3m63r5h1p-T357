"""Rvector benchmarks — mirrors benchmarks/bm_rvector.cpp

All vector construction uses C++-side rand_fill() so that even 2G-element
vectors (≈ 2.1 GB of uint8_t on the C++ heap) fit in memory.  Python lists
are never materialised at benchmark sizes.
"""

import pytest
import mpmt

ELLS = [1, 2, 3, 4, 5, 6, 7, 8]
SIZES = [1024, 65536, 1048576, 1 << 25, 1 << 31]
RANDOM_SIZES = [1 << 20, 1 << 24, 1 << 28]


def _make_vecs(ell, n):
    """Return (a, b, out) — three RiRvector<ell> filled via rand_fill()."""
    a = mpmt.Rvector(ell)(n)
    b = mpmt.Rvector(ell)(n)
    out = mpmt.Rvector(ell)(n)
    a.rand_fill()
    b.rand_fill()
    out.fill(0)  # pre-touch pages: avoid page-fault noise on first write
    return a, b, out


# ——— add ———

@pytest.mark.parametrize("ell", ELLS)
@pytest.mark.parametrize("n", SIZES)
def bm_add(benchmark, ell, n):
    a, b, out = _make_vecs(ell, n)
    benchmark(lambda: mpmt.Rvector(ell).add(a, b, out))


# ——— sub ———

@pytest.mark.parametrize("ell", ELLS)
@pytest.mark.parametrize("n", SIZES)
def bm_sub(benchmark, ell, n):
    a, b, out = _make_vecs(ell, n)
    benchmark(lambda: mpmt.Rvector(ell).sub(a, b, out))


# ——— hadamard ———

@pytest.mark.parametrize("ell", ELLS)
@pytest.mark.parametrize("n", SIZES)
def bm_hadamard(benchmark, ell, n):
    a, b, out = _make_vecs(ell, n)
    benchmark(lambda: mpmt.Rvector(ell).hadamard(a, b, out))


# ——— dot ———

@pytest.mark.parametrize("ell", ELLS)
@pytest.mark.parametrize("n", SIZES)
def bm_dot(benchmark, ell, n):
    a, b, _ = _make_vecs(ell, n)
    benchmark(lambda: mpmt.Rvector(ell).dot(a, b))


# ——— add_scalar ———

@pytest.mark.parametrize("ell", ELLS)
@pytest.mark.parametrize("n", SIZES)
def bm_add_scalar(benchmark, ell, n):
    a, _, out = _make_vecs(ell, n)
    scalar = a[0] | 1  # non-zero element from a
    benchmark(lambda: mpmt.Rvector(ell).add_scalar(a, scalar, out))


# ——— mul_scalar ———

@pytest.mark.parametrize("ell", ELLS)
@pytest.mark.parametrize("n", SIZES)
def bm_mul_scalar(benchmark, ell, n):
    a, _, out = _make_vecs(ell, n)
    scalar = a[0] | 1
    benchmark(lambda: mpmt.Rvector(ell).mul_scalar(a, scalar, out))


# ——— rand_fill ———

@pytest.mark.parametrize("ell", ELLS)
@pytest.mark.parametrize("n", RANDOM_SIZES)
def bm_rand_fill(benchmark, ell, n):
    v = mpmt.Rvector(ell)(n)
    benchmark(v.rand_fill)
