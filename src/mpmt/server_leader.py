"""MpmtServerLeader — Rep3 Party 0 (Leader), Composition Root.

Creates all C++ protocol instances and injects them into ProtocolHandler.

@author  mincy
"""

from __future__ import annotations

import mpmt
from mpmt.tree_cache import TreeCache
from mpmt.query import QueryServer
from mpmt.protocol_handler import ProtocolHandler


class MpmtServerLeader:
    """Rep3 Leader (P0).  Creates all C++ instances, injects into Handler."""

    PID_IN_REP3 = 0

    def __init__(self, *, set_size: int, fpr_mantissa: float,
                 fpr_exponent: int, max_holders: int,
                 ch_prev, ch_next, ell_dpf_out: int = 4,
                 hash_seeds: list[bytes] | None = None):
        bf_size, ell_add2, hf_num, ell_query = mpmt.bf_param(
            set_size, fpr_mantissa, fpr_exponent)
        ell_query = max(1, ell_query)
        self.bf_size = bf_size
        self.hf_num = hf_num
        self.ell_query = ell_query

        # ——— C++ types ———
        self.rep3   = mpmt.ShrRep3(1, pid=0)
        self.rep3_q = mpmt.ShrRep3(ell_query, pid=0)

        # ——— Persistent Rep3 instances ———
        rep3_inst   = self.rep3(ch_prev, ch_next)
        rep3_q_inst = self.rep3_q(ch_prev, ch_next)

        # ——— Tree cache ———
        self.tc = TreeCache(max_holders=max_holders)

        # ——— Hash seeds ———
        if hash_seeds is None:
            hash_seeds = [mpmt.get_key_128bits() for _ in range(hf_num)]
        self.hash_seed_list = list(hash_seeds)

        # ——— Pre-allocated buffers ———
        sv = mpmt.ShrRep3ShareVec(1)
        self.aux_buf = mpmt.RvectorPack(1)(bf_size)
        self.sv_buf  = sv(bf_size)

        # ——— Query infrastructure ———
        self.add2_hash = mpmt.ShrAdd2(ell=ell_add2, party=0)
        self.add2_et   = mpmt.ShrAdd2(ell=ell_query, party=0)
        self.dpf       = mpmt.DpfDealer(ell_add2, ell_dpf_out)

        query_srv = QueryServer(
            party_id=0, rep3_q_inst=rep3_q_inst, tree_cache=self.tc,
            add2_hash=self.add2_hash, add2_et=self.add2_et,
            dpf_dealer=self.dpf, hf_num=hf_num, bf_size=bf_size,
        )

        # ——— Single entry point ———
        self.handler = ProtocolHandler(
            party_id=0, rep3_inst=rep3_inst, tree_cache=self.tc,
            hash_seeds=self.hash_seed_list, bf_size=bf_size,
            ch_prev=ch_prev, ch_next=ch_next,
            rep3_q_inst=rep3_q_inst, query_srv=query_srv,
        )
