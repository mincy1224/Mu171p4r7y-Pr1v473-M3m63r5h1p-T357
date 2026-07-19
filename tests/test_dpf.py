"""Dpf unit tests — mirrors tests/test_bgi16.cpp"""

import json
import random
import pytest
import mpmt

ELL_IN_RANGE  = list(range(20, 23))
ELL_OUT_RANGE = list(range(2, 7))

NUM_RANDOM_TRIALS = 10


def _test_cases(ell_in, ell_out, seed):
    max_alpha = (1 << ell_in) - 1
    max_beta  = (1 << ell_out) - 1

    cases = [
        (0, 0),
        (0, max_beta),
        (max_alpha, 0),
        (max_alpha, max_beta),
        (1, 1),
        (max_alpha // 2, max_beta // 2),
    ]

    rng = random.Random(seed)
    for _ in range(NUM_RANDOM_TRIALS):
        cases.append((rng.randint(0, max_alpha),
                       rng.randint(0, max_beta)))

    return cases


def _verify(er0, er1, alpha, beta, ell_out):
    """er0 + er1 == point function at alpha (all C++ ops, no Python [] loop)."""
    Rv = mpmt.Rvector(ell_out)
    n  = len(er0)

    result   = Rv(n)
    expected = Rv(n, 0)

    Rv.add(er0, er1, result)
    expected[alpha] = beta

    assert result == expected


class TestDpfFullEval:

    @pytest.mark.parametrize("ell_in", ELL_IN_RANGE)
    @pytest.mark.parametrize("ell_out", ELL_OUT_RANGE)
    def test_full_eval(self, ell_in, ell_out):
        Dealer = mpmt.DpfDealer(ell_in, ell_out)
        Eval0  = mpmt.DpfEvaluator(ell_in, ell_out, 0)
        Eval1  = mpmt.DpfEvaluator(ell_in, ell_out, 1)
        vec_len = 1 << ell_in

        for alpha, beta in _test_cases(ell_in, ell_out,
                                       (ell_in << 16) | ell_out):
            k0, k1 = Dealer.gen(alpha, beta)

            er0 = mpmt.Rvector(ell_out)(vec_len)
            er1 = mpmt.Rvector(ell_out)(vec_len)
            Eval0.eval(k0, er0, 8)
            Eval1.eval(k1, er1, 8)

            _verify(er0, er1, alpha, beta, ell_out)

    @pytest.mark.parametrize("cores", [1, 4, 8, 16])
    def test_full_eval_cores(self, cores):
        """Cores parametrisation — single representative (ELL_IN=20, ELL_OUT=4)."""
        ell_in, ell_out = 20, 4
        Dealer = mpmt.DpfDealer(ell_in, ell_out)
        Eval0  = mpmt.DpfEvaluator(ell_in, ell_out, 0)
        Eval1  = mpmt.DpfEvaluator(ell_in, ell_out, 1)
        vec_len = 1 << ell_in

        for alpha, beta in _test_cases(ell_in, ell_out, 0xDEAD):
            k0, k1 = Dealer.gen(alpha, beta)

            er0 = mpmt.Rvector(ell_out)(vec_len)
            er1 = mpmt.Rvector(ell_out)(vec_len)
            Eval0.eval(k0, er0, cores)
            Eval1.eval(k1, er1, cores)

            _verify(er0, er1, alpha, beta, ell_out)


# ---------------------------------------------------------------------------
#  Range eval helpers
# ---------------------------------------------------------------------------

def _global_idx(bg: int, ed: int, vec_len: int, offset: int) -> int:
    """Map output buffer offset back to the global DPF index.

    Circular (ed < bg):  buf[0..len1-1] = [bg..VEC_LEN), buf[len1..] = [0..ed]
    Linear  (bg ≤ ed):   buf[i] = bg + i
    """
    if ed < bg:
        len1 = vec_len - bg
        if offset < len1:
            return bg + offset
        else:
            return offset - len1
    else:
        return bg + offset


def _range_len(bg: int, ed: int, vec_len: int) -> int:
    """Range length for either circular or linear."""
    if ed < bg:
        return (vec_len - bg) + ed + 1
    else:
        return ed - bg + 1


def _in_range(global_idx: int, bg: int, ed: int) -> bool:
    """Check whether *global_idx* falls inside the range."""
    if ed < bg:
        return global_idx >= bg or global_idx <= ed
    else:
        return bg <= global_idx <= ed


def _verify_range_vs_full(r0, r1, full0, full1, bg, ed, ell_out, vec_len):
    """Compare rangeEval result against fullEval, element by element."""
    Rv = mpmt.Rvector(ell_out)
    rlen = len(r0)
    expected_rlen = _range_len(bg, ed, vec_len)
    assert rlen == expected_rlen, f"buf len {rlen} != expected {expected_rlen}"

    # verify each output slot against the full-eval reference
    for i in range(rlen):
        g = _global_idx(bg, ed, vec_len, i)
        exp = mpmt.ring_add(ell_out, full0[g], full1[g])
        got = mpmt.ring_add(ell_out, r0[i], r1[i])
        assert got == exp, f"offset {i} → global {g}: expected {exp}, got {got}"

    # verify the "modular pointer" view: whole r0+r1 combined is exactly
    # the relevant slice of full0+full1, in order.
    combined_full = Rv(rlen)
    combined_r    = Rv(rlen)
    for i in range(rlen):
        g = _global_idx(bg, ed, vec_len, i)
        combined_full[i] = mpmt.ring_add(ell_out, full0[g], full1[g])
        combined_r[i]    = mpmt.ring_add(ell_out, r0[i], r1[i])
    assert combined_r == combined_full


# ═══════════════════════════════════════════════════════════════════════════
#  Linear range tests  (bg ≤ ed)
# ═══════════════════════════════════════════════════════════════════════════

class TestDpfRangeEvalLinear:
    """Range eval — linear (closed) intervals [bg, ed]."""

    ELL_IN  = 16
    ELL_OUT = 4
    VEC_LEN = 1 << ELL_IN

    @pytest.mark.parametrize("bg, ed, desc", [
        # single points
        (0, 0, "single at 0"),
        (VEC_LEN // 2, VEC_LEN // 2, "single midpoint"),
        (VEC_LEN - 1, VEC_LEN - 1, "single at end"),
        # small ranges
        (0, 7, "beginning"),
        (100, 199, "mid-low"),
        (VEC_LEN - 100, VEC_LEN - 1, "end"),
        # large ranges
        (0, VEC_LEN - 1, "full domain"),
        (1000, 2000, "mid range"),
    ])
    def test_linear_vs_full_eval(self, bg, ed, desc):
        """Every linear range matches fullEval at the corresponding indices."""
        Dealer = mpmt.DpfDealer(self.ELL_IN, self.ELL_OUT)
        Eval0  = mpmt.DpfEvaluator(self.ELL_IN, self.ELL_OUT, 0)
        Eval1  = mpmt.DpfEvaluator(self.ELL_IN, self.ELL_OUT, 1)
        Rv = mpmt.Rvector(self.ELL_OUT)

        for alpha, beta in _test_cases(self.ELL_IN, self.ELL_OUT, 0xAA_BB):
            k0, k1 = Dealer.gen(alpha, beta)

            full0 = Rv(self.VEC_LEN)
            full1 = Rv(self.VEC_LEN)
            Eval0.eval(k0, full0, 4)
            Eval1.eval(k1, full1, 4)

            rlen = _range_len(bg, ed, self.VEC_LEN)
            r0 = Rv(rlen)
            r1 = Rv(rlen)
            Eval0.eval_range(k0, r0, bg, ed, cores=4)
            Eval1.eval_range(k1, r1, bg, ed, cores=4)
            _verify_range_vs_full(r0, r1, full0, full1, bg, ed, self.ELL_OUT, self.VEC_LEN)

    @pytest.mark.parametrize("ell_in, ell_out", [(20, 4)])
    def test_range_eval_cores(self, ell_in, ell_out):
        """Cores parametrisation for linear rangeEval."""
        Dealer = mpmt.DpfDealer(ell_in, ell_out)
        Eval0  = mpmt.DpfEvaluator(ell_in, ell_out, 0)
        Eval1  = mpmt.DpfEvaluator(ell_in, ell_out, 1)
        bg, ed = 100, 500
        Rv = mpmt.Rvector(ell_out)
        vec_len = 1 << ell_in
        rlen = _range_len(bg, ed, vec_len)

        for alpha, beta in _test_cases(ell_in, ell_out, 0xBEEF):
            k0, k1 = Dealer.gen(alpha, beta)
            full0 = Rv(vec_len)
            full1 = Rv(vec_len)
            Eval0.eval(k0, full0, 1)
            Eval1.eval(k1, full1, 1)
            for cores in [1, 4, 8, 16, 32]:
                r0 = Rv(rlen)
                r1 = Rv(rlen)
                Eval0.eval_range(k0, r0, bg, ed, cores=cores)
                Eval1.eval_range(k1, r1, bg, ed, cores=cores)
                _verify_range_vs_full(r0, r1, full0, full1, bg, ed, ell_out, vec_len)

    @pytest.mark.parametrize("ell_in, ell_out", [(20, 4)])
    def test_alpha_outside_range_all_zero(self, ell_in, ell_out):
        """When alpha is outside the range, all outputs must be zero."""
        Dealer = mpmt.DpfDealer(ell_in, ell_out)
        Eval0  = mpmt.DpfEvaluator(ell_in, ell_out, 0)
        Eval1  = mpmt.DpfEvaluator(ell_in, ell_out, 1)
        alpha, beta = 42, 7
        bg, ed = 1000, 2000  # ← alpha outside
        Rv = mpmt.Rvector(ell_out)
        vec_len = 1 << ell_in
        rlen = _range_len(bg, ed, vec_len)

        k0, k1 = Dealer.gen(alpha, beta)
        r0 = Rv(rlen)
        r1 = Rv(rlen)
        Eval0.eval_range(k0, r0, bg, ed, cores=4)
        Eval1.eval_range(k1, r1, bg, ed, cores=4)

        result = Rv(rlen)
        Rv.add(r0, r1, result)
        expected = Rv(rlen, 0)
        assert result == expected, "all outputs should be 0 when alpha outside range"


# ═══════════════════════════════════════════════════════════════════════════
#  Circular range tests  (ed < bg)
# ═══════════════════════════════════════════════════════════════════════════

class TestDpfRangeEvalCircular:
    """Range eval — circular intervals [bg, VEC_LEN) ∪ [0, ed]."""

    ELL_IN  = 16
    ELL_OUT = 4
    VEC_LEN = 1 << ELL_IN

    @pytest.mark.parametrize("bg, ed, desc", [
        # minimal circular: wraps by exactly 1
        (VEC_LEN - 1, 0, "wraparound [N-1, N) ∪ [0, 0]"),
        (VEC_LEN - 1, VEC_LEN - 2, "almost full circle (gap of 1 at N-2)"),
        (1, 0, "full domain except index 0"),
        # small wraps
        (VEC_LEN - 5, 5, "wrap with ~10 elements"),
        (VEC_LEN - 100, 100, "wrap with ~200 elements"),
        # medium / large wraps
        (VEC_LEN // 2, VEC_LEN // 2 - 1, "wrap at midpoint"),
        (VEC_LEN - 10, VEC_LEN - 20, "big wrap, small gap"),
    ])
    def test_circular_vs_full_eval(self, bg, ed, desc):
        """Every circular range matches fullEval, element by element."""
        Dealer = mpmt.DpfDealer(self.ELL_IN, self.ELL_OUT)
        Eval0  = mpmt.DpfEvaluator(self.ELL_IN, self.ELL_OUT, 0)
        Eval1  = mpmt.DpfEvaluator(self.ELL_IN, self.ELL_OUT, 1)
        Rv = mpmt.Rvector(self.ELL_OUT)

        for alpha, beta in _test_cases(self.ELL_IN, self.ELL_OUT, 0xCC_DD):
            k0, k1 = Dealer.gen(alpha, beta)

            full0 = Rv(self.VEC_LEN)
            full1 = Rv(self.VEC_LEN)
            Eval0.eval(k0, full0, 4)
            Eval1.eval(k1, full1, 4)

            rlen = _range_len(bg, ed, self.VEC_LEN)
            r0 = Rv(rlen)
            r1 = Rv(rlen)
            Eval0.eval_range(k0, r0, bg, ed, cores=4)
            Eval1.eval_range(k1, r1, bg, ed, cores=4)
            _verify_range_vs_full(r0, r1, full0, full1, bg, ed, self.ELL_OUT, self.VEC_LEN)

    @pytest.mark.parametrize("bg, ed, alpha, desc", [
        (60000, 100, 60010, "alpha in first segment [bg, VEC_LEN)"),
        (60000, 100, 50, "alpha in second segment [0, ed]"),
        (60000, 100, 50, "alpha at middle of wrap"),
        (VEC_LEN - 1, 0, VEC_LEN - 1, "alpha at bg (first element)"),
        (VEC_LEN - 1, 0, 0, "alpha at ed (wrapped element)"),
        (1, 0, 1, "alpha at bg=1 when ed=0"),
        (1, 0, 0, "alpha at ed=0 when bg=1"),
    ])
    def test_circular_alpha_inside_range(self, bg, ed, alpha, desc):
        """Alpha inside the circular range → beta appears at the correct offset."""
        beta = 7
        Dealer = mpmt.DpfDealer(self.ELL_IN, self.ELL_OUT)
        Eval0  = mpmt.DpfEvaluator(self.ELL_IN, self.ELL_OUT, 0)
        Eval1  = mpmt.DpfEvaluator(self.ELL_IN, self.ELL_OUT, 1)
        Rv = mpmt.Rvector(self.ELL_OUT)

        k0, k1 = Dealer.gen(alpha, beta)
        full0 = Rv(self.VEC_LEN)
        full1 = Rv(self.VEC_LEN)
        Eval0.eval(k0, full0, 4)
        Eval1.eval(k1, full1, 4)

        rlen = _range_len(bg, ed, self.VEC_LEN)
        r0 = Rv(rlen)
        r1 = Rv(rlen)
        Eval0.eval_range(k0, r0, bg, ed, cores=4)
        Eval1.eval_range(k1, r1, bg, ed, cores=4)
        _verify_range_vs_full(r0, r1, full0, full1, bg, ed, self.ELL_OUT, self.VEC_LEN)

        # spot-check: the alpha position must carry beta
        result = Rv(rlen)
        Rv.add(r0, r1, result)
        for offset in range(rlen):
            g = _global_idx(bg, ed, self.VEC_LEN, offset)
            if g == alpha:
                assert result[offset] == beta, \
                    f"offset {offset} (global {g}) expected beta={beta}, got {result[offset]}"

    @pytest.mark.parametrize("bg, ed, alpha, desc", [
        (60000, 100, 500, "alpha in gap between ed+1 and bg-1"),
        (VEC_LEN - 1, 0, VEC_LEN // 2, "alpha far from wraparound points"),
        (VEC_LEN - 10, VEC_LEN - 20, VEC_LEN - 15, "alpha in gap between ed and bg"),
    ])
    def test_circular_alpha_outside_range(self, bg, ed, alpha, desc):
        """Alpha in the gap (ed, bg) → all outputs zero."""
        beta = 7
        Dealer = mpmt.DpfDealer(self.ELL_IN, self.ELL_OUT)
        Eval0  = mpmt.DpfEvaluator(self.ELL_IN, self.ELL_OUT, 0)
        Eval1  = mpmt.DpfEvaluator(self.ELL_IN, self.ELL_OUT, 1)
        Rv = mpmt.Rvector(self.ELL_OUT)

        k0, k1 = Dealer.gen(alpha, beta)
        rlen = _range_len(bg, ed, self.VEC_LEN)
        r0 = Rv(rlen)
        r1 = Rv(rlen)
        Eval0.eval_range(k0, r0, bg, ed, cores=4)
        Eval1.eval_range(k1, r1, bg, ed, cores=4)

        result = Rv(rlen)
        Rv.add(r0, r1, result)
        expected = Rv(rlen, 0)
        assert result == expected, (
            f"alpha={alpha} in gap ({ed}, {bg}) → all outputs must be 0"
        )

    @pytest.mark.parametrize("ell_in, ell_out", [(20, 4)])
    def test_circular_cores(self, ell_in, ell_out):
        """Cores parametrisation for circular rangeEval."""
        Dealer = mpmt.DpfDealer(ell_in, ell_out)
        Eval0  = mpmt.DpfEvaluator(ell_in, ell_out, 0)
        Eval1  = mpmt.DpfEvaluator(ell_in, ell_out, 1)
        vec_len = 1 << ell_in
        bg, ed = vec_len - 100, 50  # circular: wrap with ~150 elements
        Rv = mpmt.Rvector(ell_out)
        rlen = _range_len(bg, ed, vec_len)

        for alpha, beta in _test_cases(ell_in, ell_out, 0xCAFE):
            k0, k1 = Dealer.gen(alpha, beta)
            full0 = Rv(vec_len)
            full1 = Rv(vec_len)
            Eval0.eval(k0, full0, 1)
            Eval1.eval(k1, full1, 1)
            for cores in [1, 4, 8, 16, 32]:
                r0 = Rv(rlen)
                r1 = Rv(rlen)
                Eval0.eval_range(k0, r0, bg, ed, cores=cores)
                Eval1.eval_range(k1, r1, bg, ed, cores=cores)
                _verify_range_vs_full(r0, r1, full0, full1, bg, ed, ell_out, vec_len)


# ═══════════════════════════════════════════════════════════════════════════
#  Error paths
# ═══════════════════════════════════════════════════════════════════════════

class TestDpfRangeEvalErrors:
    """Error paths for eval_range."""

    @pytest.mark.parametrize("ell_in, ell_out", [(20, 4)])
    def test_bg_out_of_range_raises(self, ell_in, ell_out):
        Dealer = mpmt.DpfDealer(ell_in, ell_out)
        Eval0  = mpmt.DpfEvaluator(ell_in, ell_out, 0)
        k0, _ = Dealer.gen(0, 0)
        vec_len = 1 << ell_in
        buf = mpmt.Rvector(ell_out)(10)
        with pytest.raises(ValueError, match="bg out of range"):
            Eval0.eval_range(k0, buf, vec_len, 0)

    @pytest.mark.parametrize("ell_in, ell_out", [(20, 4)])
    def test_ed_out_of_range_raises(self, ell_in, ell_out):
        Dealer = mpmt.DpfDealer(ell_in, ell_out)
        Eval0  = mpmt.DpfEvaluator(ell_in, ell_out, 0)
        k0, _ = Dealer.gen(0, 0)
        vec_len = 1 << ell_in
        buf = mpmt.Rvector(ell_out)(10)
        with pytest.raises(ValueError, match="ed out of range"):
            Eval0.eval_range(k0, buf, 0, vec_len)

    @pytest.mark.parametrize("ell_in, ell_out", [(20, 4)])
    def test_buf_size_mismatch_raises(self, ell_in, ell_out):
        Dealer = mpmt.DpfDealer(ell_in, ell_out)
        Eval0  = mpmt.DpfEvaluator(ell_in, ell_out, 0)
        k0, _ = Dealer.gen(0, 0)
        vec_len = 1 << ell_in
        # linear: bg=10, ed=20 → rangeLen = 20-10+1 = 11
        bad_buf = mpmt.Rvector(ell_out)(100)
        with pytest.raises(RuntimeError, match="fdresBuf.size\\(\\) must equal"):
            Eval0.eval_range(k0, bad_buf, 10, 20)
        # circular: bg=vec_len-10, ed=5 → rangeLen = 10 + 5 + 1 = 16
        bad_buf2 = mpmt.Rvector(ell_out)(100)
        with pytest.raises(RuntimeError, match="fdresBuf.size\\(\\) must equal"):
            Eval0.eval_range(k0, bad_buf2, vec_len - 10, 5)


class TestDpfSerialization:
    """Key serialization: gen returns JSON, eval accepts JSON."""

    @pytest.mark.parametrize("ell_in", ELL_IN_RANGE)
    @pytest.mark.parametrize("ell_out", ELL_OUT_RANGE)
    def test_gen_returns_valid_json(self, ell_in, ell_out):
        """gen() keys must be parseable JSON and eval must reproduce results."""
        Dealer = mpmt.DpfDealer(ell_in, ell_out)
        alpha, beta = 42, 7
        k0, k1 = Dealer.gen(alpha, beta)

        # Both keys must be valid JSON strings
        d0 = json.loads(k0)
        d1 = json.loads(k1)
        assert isinstance(d0, dict), f"k0 not a JSON object: {type(d0)}"
        assert isinstance(d1, dict), f"k1 not a JSON object: {type(d1)}"

        # Re-serialized JSON → eval must produce the same result
        k0_rt = json.dumps(d0)
        k1_rt = json.dumps(d1)
        vec_len = 1 << ell_in
        Eval0 = mpmt.DpfEvaluator(ell_in, ell_out, 0)
        Eval1 = mpmt.DpfEvaluator(ell_in, ell_out, 1)
        er0 = mpmt.Rvector(ell_out)(vec_len)
        er1 = mpmt.Rvector(ell_out)(vec_len)
        Eval0.eval(k0_rt, er0, 1)
        Eval1.eval(k1_rt, er1, 1)
        _verify(er0, er1, alpha, beta, ell_out)


class TestDpfErrors:

    def test_ell_in_too_low(self):
        with pytest.raises(ValueError):
            mpmt.DpfDealer(8, 4)

    def test_ell_in_too_high(self):
        with pytest.raises(ValueError):
            mpmt.DpfDealer(32, 4)

    def test_ell_out_too_low(self):
        with pytest.raises(ValueError):
            mpmt.DpfDealer(20, 1)

    def test_ell_out_too_high(self):
        with pytest.raises(ValueError):
            mpmt.DpfDealer(20, 9)

    def test_bad_party(self):
        with pytest.raises(ValueError):
            mpmt.DpfEvaluator(20, 4, 2)

    def test_buf_size_mismatch(self):
        Dealer = mpmt.DpfDealer(20, 4)
        Eval0  = mpmt.DpfEvaluator(20, 4, 0)
        k0, _ = Dealer.gen(0, 0)
        bad_buf = mpmt.Rvector(4)(100)
        with pytest.raises(RuntimeError):
            Eval0.eval(k0, bad_buf)


class TestDpfNetwork:
    """End-to-end network test: gen → send_key → recv_key → eval → reveal."""

    @pytest.mark.parametrize("ell_in, ell_out", [(20, 4), (20, 6), (22, 4)])
    def test_send_recv_key_reveal(self, ell_in, ell_out, dpf_pool):
        alpha, beta = 42, 7  # beta must fit in Z_{2^ELL_OUT}: 7 < 16
        vec_len = 1 << ell_in
        Rv = mpmt.Rvector(ell_out)

        def party(pid, channels):
            if pid == 0:  # Dealer
                DealerCls = mpmt.DpfDealer(ell_in, ell_out)
                dealer = DealerCls(channels["eval0"], channels["eval1"])
                k0, k1 = DealerCls.gen(alpha, beta)
                dealer.send_key(k0, 0)
                dealer.send_key(k1, 1)
                out = Rv(vec_len)
                dealer.reveal(out)
                assert out[alpha] == beta, f"out[{alpha}]={out[alpha]}, expected {beta}"
                assert out[0] == 0 and out[vec_len - 1] == 0, "endpoints should be 0"
                return "ok"
            else:  # Evaluator
                EvalCls = mpmt.DpfEvaluator(ell_in, ell_out, pid - 1)
                evaluator = EvalCls(channels["dealer"])
                key_json = evaluator.recv_key()
                buf = Rv(vec_len)
                EvalCls.eval(key_json, buf, cores=1)
                evaluator.reveal(buf)
                return "ok"

        results = dpf_pool.run(party)
        assert results == ["ok", "ok", "ok"]
