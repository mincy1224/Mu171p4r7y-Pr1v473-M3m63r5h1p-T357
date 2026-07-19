"""MpmtServerHelper — Rep3 Party 1 or 2 (Helper), Composition Root.

Creates all C++ protocol instances and injects them into ProtocolHandler.

@author  mincy
"""

from __future__ import annotations

import mpmt
from mpmt.tree_cache import TreeCache
from mpmt.query import QueryServer
from mpmt.protocol_handler import ProtocolHandler


class MpmtServerHelper:
    """Rep3 Helper (P1 or P2).  Creates all C++ instances, injects into Handler."""

    def __init__(self, *, server_id: int, set_size: int, fpr_mantissa: float,
                 fpr_exponent: int, max_holders: int,
                 ch_prev, ch_next, ell_dpf_out: int = 4):
        if server_id not in (1, 2):
            raise ValueError("server_id must be 1 or 2")
        bf_size, ell_add2, hf_num, ell_query = mpmt.bf_param(
            set_size, fpr_mantissa, fpr_exponent)
        ell_query = max(1, ell_query)
        self.bf_size = bf_size
        self.hf_num = hf_num

        # ——— C++ types ———
        self.rep3   = mpmt.ShrRep3(1, pid=server_id)
        self.rep3_q = mpmt.ShrRep3(ell_query, pid=server_id)

        # ——— Persistent Rep3 instances ———
        rep3_inst   = self.rep3(ch_prev, ch_next)
        rep3_q_inst = self.rep3_q(ch_prev, ch_next)

        # ——— Tree cache ———
        self.tc = TreeCache(max_holders=max_holders)

        # ——— Pre-allocated buffers ———
        sv = mpmt.ShrRep3ShareVec(1)
        self.aux_buf = mpmt.RvectorPack(1)(bf_size)
        self.sv_buf  = sv(bf_size)

        # ——— Query infrastructure ———
        dpf_eval = mpmt.DpfEvaluator(ell_add2, ell_dpf_out,
                                      party=(0 if server_id == 1 else 1))

        query_srv = QueryServer(
            party_id=server_id, rep3_q_inst=rep3_q_inst, tree_cache=self.tc,
            dpf_eval=dpf_eval, hf_num=hf_num, bf_size=bf_size,
        )

        # ——— Single entry point ———
        self.handler = ProtocolHandler(
            party_id=server_id, rep3_inst=rep3_inst, tree_cache=self.tc,
            hash_seeds=[], bf_size=bf_size,
            ch_prev=ch_prev, ch_next=ch_next,
            rep3_q_inst=rep3_q_inst, query_srv=query_srv,
        )
