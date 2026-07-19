"""Integration test: full Join → Aggregate → Update → Quit via ProtocolHandler.

Uses PartyPool to spawn 3 processes (no Flask), each running a
ProtocolHandler against a shared Rep3 ring.
"""

import multiprocessing as mp
import os
import sys
import time

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

import mpmt
from mpmt.tree_cache import TreeCache
from mpmt.protocol_handler import ProtocolHandler
from tests.conftest import PartyPool, _setup_rep3


# ——— Test parameters ———
SET_SIZE = 2 ** 10
FPR_MANTISSA = 1.0
FPR_EXPONENT = -3
MAX_HOLDERS = 4

ELEMENTS_1 = [b"alice", b"bob"]
ELEMENTS_2 = [b"charlie", b"dave"]


# ——————————————————————————————————————————————
#  Helpers
# ——————————————————————————————————————————————

def _make_bf_list(elements, hash_seeds, bf_size, hf_num, ell_add2):
    Rv = mpmt.Rvector(1)
    bf = Rv(bf_size)
    bf.fill(0)
    for e in elements:
        for hs in hash_seeds:
            idx = mpmt.ring_mod(ell_add2, mpmt.hash_aes_dm(preimage=e, key=hs, ell=ell_add2), bf_size)
            bf[idx] = 1
    return [bf[i] for i in range(bf_size)]


def _reconstruct_root(rep3_inst, tc, bf_size):
    root_sv = tc.root_share
    if root_sv is None:
        return None
    Rv = mpmt.Rvector(1)
    out = Rv(bf_size)
    rep3_inst.reveal_vector(root_sv, out, mpmt.RvectorPack(1)(bf_size))
    return [out[i] for i in range(bf_size)]


# ——————————————————————————————————————————————
#  Party function
# ——————————————————————————————————————————————

def _server_party(pid, channels, bf_size, hf_num, ell_add2,
                  hash_seeds, max_holders_val):
    inst = mpmt.ShrRep3(1, pid)(channels["prev"], channels["next"])
    tc = TreeCache(max_holders=max_holders_val)
    handler = ProtocolHandler(
        party_id=pid, rep3_inst=inst, tree_cache=tc,
        hash_seeds=hash_seeds, bf_size=bf_size,
    )
    return {"pid": pid, "handler": handler, "inst": inst, "tc": tc}


# ——————————————————————————————————————————————
#  Tests
# ——————————————————————————————————————————————

class TestIntegration:

    @staticmethod
    def _make_shared_state(rss3_pool):
        bf_size, ell_add2, hf_num, _ = mpmt.bf_param(
            SET_SIZE, FPR_MANTISSA, FPR_EXPONENT
        )
        hash_seeds = [mpmt.get_key_128bits() for _ in range(hf_num)]

        def party(pid, channels):
            return _server_party(pid, channels, bf_size, hf_num, ell_add2,
                                 hash_seeds, MAX_HOLDERS)

        results = rss3_pool.run(party)
        return results, bf_size, hf_num, ell_add2, hash_seeds

    def test_reserve_responses(self, rss3_pool):
        bf_size, ell_add2, hf_num, _ = mpmt.bf_param(
            SET_SIZE, FPR_MANTISSA, FPR_EXPONENT
        )
        hash_seeds = [mpmt.get_key_128bits() for _ in range(hf_num)]

        def party(pid, channels):
            import mpmt as _mpmt
            ELL = 1
            inst = _mpmt.ShrRep3(ELL, pid)(channels["prev"], channels["next"])
            tc = TreeCache(max_holders=MAX_HOLDERS)
            handler = ProtocolHandler(
                party_id=pid, rep3_inst=inst, tree_cache=tc,
                hash_seeds=hash_seeds, bf_size=bf_size,
            )
            results = {}

            # reserve flow: confirm + prepare
            handler.three_way_confirm()
            handler.prepare_join(b"tk01")
            results["join_count"] = (tc.count == 1)

            handler.three_way_confirm()
            handler.prepare_join(b"tk02")
            results["join2_count"] = (tc.count == 2)

            try:
                handler.prepare_join(b"tk01")
                results["dup_join_raises"] = False
            except ValueError:
                results["dup_join_raises"] = True

            handler.three_way_confirm()
            handler.check_token(b"tk01", "update")
            results["update_ok"] = True

            try:
                handler.check_token(b"tk_unknown", "update")
                results["update_unknown_raises"] = False
            except ValueError:
                results["update_unknown_raises"] = True

            try:
                handler.check_token(b"tk_unknown", "quit")
                results["quit_unknown_raises"] = False
            except ValueError:
                results["quit_unknown_raises"] = True

            handler.three_way_confirm()
            handler.check_token(b"tk01", "quit")
            handler.do_quit(b"tk01")
            results["quit_ok"] = True

            handler.aggregate()
            results["agg_ok"] = True

            return results

        all_results = rss3_pool.run(party)
        keys = all_results[0].keys()
        for k in keys:
            vals = [r[k] for r in all_results]
            assert all(vals), f"{k}: not all parties agree (values={vals})"

    def test_construct_mpmt_classes(self):
        """Smoke test: MpmtSetHolder can be instantiated."""
        def dummy_reserve(token, action):
            return [{"port": 9999}, {"port": 9998}, {"port": 9997}]
        def dummy_connect(token, action, **ports):
            pass

        sh = mpmt.MpmtSetHolder(
            set_size=2**10, fpr_mantissa=1.0, fpr_exponent=-3,
            reserve_fn=dummy_reserve, connect_fn=dummy_connect,
        )
        assert sh.token is not None
        assert sh.bf_size == 14723
        assert sh.hf_num == 10

    def test_direct_share_then_aggregate(self, rss3_pool):
        bf_size, ell_add2, hf_num, _ = mpmt.bf_param(
            SET_SIZE, FPR_MANTISSA, FPR_EXPONENT
        )
        hash_seeds = [mpmt.get_key_128bits() for _ in range(hf_num)]

        bf_1_list = _make_bf_list(ELEMENTS_1, hash_seeds, bf_size, hf_num, ell_add2)
        bf_2_list = _make_bf_list(ELEMENTS_2, hash_seeds, bf_size, hf_num, ell_add2)

        def party(pid, channels):
            import mpmt as _mpmt
            ELL = 1
            inst = _mpmt.ShrRep3(ELL, pid)(channels["prev"], channels["next"])
            tc = TreeCache(max_holders=MAX_HOLDERS)
            handler = ProtocolHandler(
                party_id=pid, rep3_inst=inst, tree_cache=tc,
                hash_seeds=hash_seeds, bf_size=bf_size,
            )
            SV = _mpmt.ShrRep3ShareVec(ELL)
            Rv = _mpmt.Rvector(ELL)

            def share_bf(elements):
                if pid == 0:
                    bf = Rv(bf_size); bf.fill(0)
                    for e in elements:
                        for hs in hash_seeds:
                            idx = _mpmt.ring_mod(ell_add2, mpmt.hash_aes_dm(preimage=e, key=hs, ell=ell_add2), bf_size)
                            bf[idx] = 1
                    aux = _mpmt.RvectorPack(ELL)(bf_size)
                    sv = SV(bf_size)
                    inst.share_vector(bf, sv, aux)
                else:
                    aux = _mpmt.RvectorPack(ELL)(bf_size)
                    sv = SV(bf_size)
                    inst.recv_vector_share(sv, aux)
                last_idx = max(tc._phi_inv.keys())
                tc._buf[last_idx] = sv
                tc._mark_path_dirty(last_idx)

            handler.three_way_confirm()
            handler.prepare_join(b"tk01")
            share_bf(ELEMENTS_1)

            handler.three_way_confirm()
            handler.prepare_join(b"tk02")
            share_bf(ELEMENTS_2)

            handler.aggregate()

            root = tc.root_share
            assert root is not None, f"P{pid}: root is None"

            out = Rv(bf_size)
            inst.reveal_vector(root, out, _mpmt.RvectorPack(ELL)(bf_size))
            root_list = [out[i] for i in range(bf_size)]
            expected = [bf_1_list[i] | bf_2_list[i] for i in range(bf_size)]

            return {"pid": pid, "root_ok": root_list == expected}

        results = rss3_pool.run(party)
        for r in results:
            assert r["root_ok"], f"P{r['pid']}: root mismatch"

    def test_full_lifecycle(self, rss3_pool):
        bf_size, ell_add2, hf_num, _ = mpmt.bf_param(
            SET_SIZE, FPR_MANTISSA, FPR_EXPONENT
        )
        hash_seeds = [mpmt.get_key_128bits() for _ in range(hf_num)]

        bf_1_list = _make_bf_list(ELEMENTS_1, hash_seeds, bf_size, hf_num, ell_add2)
        bf_2_list = _make_bf_list(ELEMENTS_2, hash_seeds, bf_size, hf_num, ell_add2)
        bf_1_new_list = _make_bf_list(ELEMENTS_2, hash_seeds, bf_size, hf_num, ell_add2)

        def party(pid, channels):
            import mpmt as _mpmt
            ELL = 1
            inst = _mpmt.ShrRep3(ELL, pid)(channels["prev"], channels["next"])
            tc = TreeCache(max_holders=MAX_HOLDERS)
            handler = ProtocolHandler(
                party_id=pid, rep3_inst=inst, tree_cache=tc,
                hash_seeds=hash_seeds, bf_size=bf_size,
            )
            SV = _mpmt.ShrRep3ShareVec(ELL)
            Rv = _mpmt.Rvector(ELL)
            RvP = _mpmt.RvectorPack(ELL)

            def share_bf(elements, token=None):
                if pid == 0:
                    bf = Rv(bf_size); bf.fill(0)
                    for e in elements:
                        for hs in hash_seeds:
                            idx = _mpmt.ring_mod(ell_add2, mpmt.hash_aes_dm(preimage=e, key=hs, ell=ell_add2), bf_size)
                            bf[idx] = 1
                    aux = RvP(bf_size)
                    sv = SV(bf_size)
                    inst.share_vector(bf, sv, aux)
                else:
                    aux = RvP(bf_size)
                    sv = SV(bf_size)
                    inst.recv_vector_share(sv, aux)
                idx = tc.index_of(token) if token else max(tc._phi_inv.keys())
                tc._buf[idx] = sv
                tc._mark_path_dirty(idx)

            def reveal_root():
                root = tc.root_share
                out = Rv(bf_size)
                inst.reveal_vector(root, out, RvP(bf_size))
                return [out[i] for i in range(bf_size)]

            handler.three_way_confirm()
            handler.prepare_join(b"tk_01")
            share_bf(ELEMENTS_1)
            handler.three_way_confirm()
            handler.prepare_join(b"tk_02")
            share_bf(ELEMENTS_2)

            handler.aggregate()
            r1 = reveal_root()
            expected_12 = [bf_1_list[i] | bf_2_list[i] for i in range(bf_size)]
            assert r1 == expected_12, f"P{pid}: join aggregate wrong"

            handler.three_way_confirm()
            handler.check_token(b"tk_01", "update")
            share_bf(ELEMENTS_2, token=b"tk_01")

            handler.aggregate()
            r2 = reveal_root()
            expected_22 = [bf_2_list[i] for i in range(bf_size)]
            assert r2 == expected_22, f"P{pid}: update aggregate wrong"

            handler.three_way_confirm()
            handler.check_token(b"tk_01", "quit")
            handler.do_quit(b"tk_01")

            handler.aggregate()
            r3 = reveal_root()
            assert r3 == bf_2_list, f"P{pid}: quit aggregate wrong"

            assert tc.count == 1
            assert tc.index_of(b"tk_01") is None
            assert tc.index_of(b"tk_02") is not None

            return "ok"

        results = rss3_pool.run(party)
        assert all(r == "ok" for r in results)
