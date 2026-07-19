"""Verify query protocol: crng+reshare, additive shares, dot product."""

import pytest
import mpmt
from mpmt.tree_cache import TreeCache
from mpmt.query import QueryServer


SET_SIZE = 2 ** 10
FPR_MANTISSA = 1.0
FPR_EXPONENT = -3
MAX_HOLDERS = 4


class TestQueryGenBF:

    def test_query_primitives(self, rss3_pool):
        """All query primitives: crng sum-zero, reshare, dot product."""
        bf_size, _, hf_num, ell_q = mpmt.bf_param(SET_SIZE, FPR_MANTISSA, FPR_EXPONENT)
        ell_q = max(1, ell_q)

        def party(pid, channels):
            import mpmt as _mpmt, traceback
            try:
                rep3_1 = _mpmt.ShrRep3(1, pid)(channels["prev"], channels["next"])
                rep3_q = _mpmt.ShrRep3(ell_q, pid)(channels["prev"], channels["next"])
                tc = TreeCache(max_holders=MAX_HOLDERS)
                srv = QueryServer(party_id=pid, rep3_q_inst=rep3_q,
                                  tree_cache=tc, hf_num=hf_num, bf_size=bf_size)

                # 1. crng additive share
                additive = srv.genbf_additive_share()
                first8 = [additive[i] for i in range(min(8, bf_size))]

                # 2. reshare into Rep3 → reveal must be zero (no DPF input)
                sv = srv.reshare_into_rep3(additive)
                Rv_q = _mpmt.Rvector(ell=ell_q)
                out = Rv_q(bf_size)
                rep3_q.reveal_vector(sv, out, _mpmt.RvectorPack(ell=ell_q)(bf_size))
                total = sum(out[i] for i in range(bf_size))

                # 3. set root to zero (ELL=1) → dot with query BF = 0
                SV1 = _mpmt.ShrRep3ShareVec(1)
                aux1 = _mpmt.RvectorPack(1)(bf_size)
                if pid == 0:
                    zv = _mpmt.Rvector(1)(bf_size); zv.fill(0)
                    root_sv = SV1(bf_size)
                    rep3_1.share_vector(zv, root_sv, aux1)
                else:
                    root_sv = SV1(bf_size)
                    rep3_1.recv_vector_share(root_sv, aux1)
                tc._buf[0] = root_sv

                dot = srv.step_dot(sv, rep3_inst=rep3_1)
                return {"first8": first8, "total": total,
                        "dot": (dot.this_share, dot.nxt_share)}
            except Exception:
                return f"ERR: {traceback.format_exc()}"

        results = rss3_pool.run(party)

        # Verify crng sums to zero
        for i, r in enumerate(results):
            if isinstance(r, str):
                raise RuntimeError(f"Worker P{i}: {r}")
        a0, a1, a2 = [r["first8"] for r in results]
        for i in range(len(a0)):
            s = mpmt.ring_add(ell_q, mpmt.ring_add(ell_q, a0[i], a1[i]), a2[i])
            assert s == 0, f"crng[{i}]: {a0[i]}+{a1[i]}+{a2[i]} = {s}"

        # Verify reshare result is all zeros
        for r in results:
            assert r["total"] == 0, f"total={r['total']}, expected 0"

        # Verify dot with zero root = 0
        dot_val = mpmt.ring_add(ell_q, results[0]["dot"][0],
                                mpmt.ring_add(ell_q, results[1]["dot"][0],
                                              results[2]["dot"][0]))
        assert dot_val == 0, f"dot={dot_val}, expected 0"
