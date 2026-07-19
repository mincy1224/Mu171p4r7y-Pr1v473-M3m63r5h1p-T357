"""Correctness: aggregate (BF merge via TreeCache) and query (Rep3 dot).

Uses real ``hash_aes_dm`` → ``ring_mod`` for BF construction.
Runs via direct multiprocessing.Process — no PartyPool, no HTTP.
"""

import multiprocessing as mp
import time
import random
import pytest
import mpmt
from mpmt.channels import _build_rep3_channels
from mpmt.tree_cache import TreeCache
from mpmt.protocol_handler import ProtocolHandler
from mpmt.query import QueryServer


# ===================================================================
#  Param grids
# ===================================================================

AGG_PARAMS = [
    (2 ** 10, 1.0, -2, 4),
    (2 ** 14, 1.0, -3, 16),
    (2 ** 16, 1.0, -3, 32),
]

QUERY_PARAMS = [
    (2 ** 10, 1.0, -2, 4,   b"alice"),
    (2 ** 14, 1.0, -3, 16,  b"charlie"),
    (2 ** 16, 1.0, -3, 32,  b"frank"),
]


# ===================================================================
#  Shared helpers
# ===================================================================

def _local_bf(elements, seeds, bf_size, ell_add2, additive=False, ell_q=1):
    bf = [0] * bf_size
    for e in elements:
        for s in seeds:
            h = mpmt.hash_aes_dm(preimage=e, key=s, ell=ell_add2)
            idx = mpmt.ring_mod(ell_add2, h, bf_size)
            if additive:
                bf[idx] = mpmt.ring_add(ell_q, bf[idx], 1)
            else:
                bf[idx] = 1
    return bf


def _local_dot(bf_a, bf_b, ell_q):
    s = 0
    for i in range(len(bf_a)):
        s = mpmt.ring_add(ell_q, s, mpmt.ring_add(ell_q, 0, bf_a[i] * bf_b[i]))
    return s


def _run_3p(fn, fn_args, base_port, timeout=30):
    """Spawn 3 processes, collect results."""
    q = mp.Queue()
    procs = []
    for pid in range(3):
        p = mp.Process(target=fn, args=(pid, q, base_port, *fn_args))
        procs.append(p)
        p.start()

    results = []
    deadline = time.monotonic() + timeout
    while len(results) < 3:
        if time.monotonic() > deadline:
            for p in procs: p.terminate()
            return results
        try:
            results.append(q.get(timeout=0.5))
        except Exception:
            pass
    for p in procs:
        p.terminate()
        p.join(2)
    return results


# ===================================================================
#  Worker functions
# ===================================================================

def _agg_worker(pid, q, base_port, bf_size, hf_num, ell_add2, n_holders,
                seeds, holder_sets):
    """One aggregate-test worker."""
    import traceback
    try:
        ch = _build_rep3_channels(prev_port=base_port + pid,
                                   next_host="127.0.0.1",
                                   next_port=base_port + (pid + 1) % 3,
                                   party_id=pid)
        inst = mpmt.ShrRep3(1, pid)(ch["prev"], ch["next"])
        tc = TreeCache(max_holders=n_holders)
        handler = ProtocolHandler(party_id=pid, rep3_inst=inst, tree_cache=tc, hash_seeds=[], bf_size=bf_size)

        SV = mpmt.ShrRep3ShareVec(1)
        aux = mpmt.RvectorPack(1)(bf_size)
        Rv = mpmt.Rvector(1)

        for h, elems in enumerate(holder_sets):
            tok = f"holder_{h}".encode() + b"\x00" * 9
            handler.prepare_join(tok)
            if pid == 0:
                bf = Rv(bf_size); bf.fill(0)
                for e in elems:
                    for s in seeds:
                        idx = mpmt.ring_mod(ell_add2,
                            mpmt.hash_aes_dm(preimage=e, key=s, ell=ell_add2), bf_size)
                        bf[idx] = 1
                sv = SV(bf_size); inst.share_vector(bf, sv, aux)
            else:
                sv = SV(bf_size); inst.recv_vector_share(sv, aux)
            handler.place_share(tok, sv)

        handler.aggregate()
        out = Rv(bf_size)
        inst.reveal_vector(tc.root_share, out, aux)
        q.put([out[i] for i in range(bf_size)])
    except Exception:
        q.put(f"ERR P{pid}\n{traceback.format_exc()}")


def _query_worker(pid, q, base_port, bf_size, hf_num, ell_add2, ell_query,
                  root_bf, query_bf):
    """One query-test worker."""
    import traceback
    try:
        ch = _build_rep3_channels(prev_port=base_port + pid,
                                   next_host="127.0.0.1",
                                   next_port=base_port + (pid + 1) % 3,
                                   party_id=pid)
        rep3 = mpmt.ShrRep3(1, pid)(ch["prev"], ch["next"])
        rep3_q = mpmt.ShrRep3(ell_query, pid)(ch["prev"], ch["next"])
        tc = TreeCache(max_holders=4)
        srv = QueryServer(party_id=pid, rep3_q_inst=rep3_q,
                          tree_cache=tc, hf_num=hf_num, bf_size=bf_size)

        # Share root BF
        SV1 = mpmt.ShrRep3ShareVec(1)
        aux1 = mpmt.RvectorPack(1)(bf_size)
        if pid == 0:
            Rv1 = mpmt.Rvector(1); v = Rv1(bf_size)
            for i, val in enumerate(root_bf):
                if val: v[i] = 1
            sv = SV1(bf_size); rep3.share_vector(v, sv, aux1)
        else:
            sv = SV1(bf_size); rep3.recv_vector_share(sv, aux1)
        tc._buf[0] = sv

        # Share query BF
        SV_q = mpmt.ShrRep3ShareVec(ell=ell_query)
        aux_q = mpmt.RvectorPack(ell=ell_query)(bf_size)
        if pid == 0:
            Rv_q = mpmt.Rvector(ell=ell_query); qv = Rv_q(bf_size)
            for i, val in enumerate(query_bf):
                qv[i] = val % (1 << ell_query)
            sv_q = SV_q(bf_size); rep3_q.share_vector(qv, sv_q, aux_q)
        else:
            sv_q = SV_q(bf_size); rep3_q.recv_vector_share(sv_q, aux_q)

        dot = srv.step_dot(sv_q, rep3_inst=rep3 if ell_query > 1 else None)
        q.put((dot.this_share, dot.nxt_share))
    except Exception:
        q.put(f"ERR P{pid}\n{traceback.format_exc()}")


# ===================================================================
#  Tests
# ===================================================================

class TestAggregateCorrectness:

    @pytest.mark.parametrize("set_size,fpr_m,fpr_e,n_holders", AGG_PARAMS)
    def test_aggregate_matches_local_union(self, set_size, fpr_m, fpr_e,
                                           n_holders):
        mp.set_start_method("spawn", force=True)
        bf_size, ell_add2, hf_num, _ = mpmt.bf_param(set_size, fpr_m, fpr_e)
        seeds = [mpmt.get_key_128bits() for _ in range(hf_num)]
        random.seed(42)

        all_elements = []
        holder_sets = []
        for h in range(n_holders):
            elems = [f"h{h}_e{i}".encode() for i in range(3)]
            holder_sets.append(elems)
            all_elements.extend(elems)

        expected = _local_bf(all_elements, seeds, bf_size, ell_add2)

        port = 17500 + (hash(f"agg{set_size}") % 1000)
        results = _run_3p(_agg_worker,
                          (bf_size, hf_num, ell_add2, n_holders, seeds, holder_sets),
                          port, timeout=120)

        assert len(results) == 3, f"Not enough results: {results}"
        for r in results:
            assert r == expected, "aggregate root BF != expected union"


class TestQueryCorrectness:

    @pytest.mark.parametrize("set_size,fpr_m,fpr_e,n_holders,member_elem",
                             QUERY_PARAMS)
    def test_dot_matches_local(self, set_size, fpr_m, fpr_e, n_holders,
                                member_elem):
        mp.set_start_method("spawn", force=True)
        bf_size, ell_add2, hf_num, ell_q = mpmt.bf_param(set_size, fpr_m, fpr_e)
        ell_q = max(1, ell_q)
        seeds = [mpmt.get_key_128bits() for _ in range(hf_num)]
        random.seed(42)

        root_elems = [member_elem]
        for i in range(50):
            root_elems.append(f"elem_{i}".encode())
        root_bf = _local_bf(root_elems, seeds, bf_size, ell_add2)

        for query_elem in [member_elem, b"definitely_not_in_set"]:
            query_bf = _local_bf([query_elem], seeds, bf_size, ell_add2,
                                 additive=True, ell_q=ell_q)
            expected_dot = _local_dot(root_bf, query_bf, ell_q)

            port = 18500 + (hash(f"q{set_size}{str(query_elem)}") % 2000)
            results = _run_3p(_query_worker,
                              (bf_size, hf_num, ell_add2, ell_q, root_bf, query_bf),
                              port, timeout=120)

            assert len(results) == 3, f"Not enough results: {results}"
            for r in results:
                assert isinstance(r, tuple), f"Worker error: {r}"
            actual = mpmt.ring_add(ell_q, results[0][0],
                                   mpmt.ring_add(ell_q, results[1][0],
                                                 results[2][0]))

            is_member = (query_elem == member_elem)
            label = "member" if is_member else "non-member"
            if is_member:
                assert actual == expected_dot, \
                    f"[member] dot={actual} != expected={expected_dot}"
                assert actual == hf_num, f"[member] dot={actual} != hf_num={hf_num}"
            else:
                assert actual != hf_num, \
                    f"[non-member] dot={actual} == hf_num (false positive)"
