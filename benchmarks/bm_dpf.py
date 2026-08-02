"""DPF benchmarks — mirrors benchmarks/bm_bgi16.cpp

Offline: gen() and eval() are static methods requiring no network.
Keys are JSON strings passed in-process.
"""

import pytest
import mpmt

ELL_IN_RANGE  = list(range(25, 32))   # 25..31
ELL_OUT       = 6
CORES         = [8, 16, 32]


@pytest.mark.parametrize("ell_in", ELL_IN_RANGE)
@pytest.mark.parametrize("cores", CORES)
def bm_dpf_full_eval(benchmark, ell_in, cores):
    Dealer = mpmt.DpfDealer(ell_in, ELL_OUT)
    Eval0  = mpmt.DpfEvaluator(ell_in, ELL_OUT, 0)
    k0, _ = Dealer.gen(42, 1)
    vec_len = 1 << ell_in
    buf = mpmt.Rvector(ELL_OUT)(vec_len)

    def run():
        Eval0.eval(k0, buf, cores)

    benchmark(run)
