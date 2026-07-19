"""ProtocolHandler — single facade for join / update / quit / aggregate / query.

Receives C++ instances from the Composition Root (server class).
Application layer (HTTP routes, tests) talks exclusively to this object.

@author  mincy
"""

from __future__ import annotations

import mpmt
from mpmt.tree_cache import TreeCache, NOT_LOADED


class ProtocolHandler:
    """Facade for join / update / quit / aggregate / query.

    Receives C++ instances from the Composition Root (server class).
    Application layer (HTTP routes, tests) talks only to this object.
    """

    PID_LEADER   = 0
    PID_HELPER_A = 1
    PID_HELPER_B = 2

    def __init__(
        self,
        *,
        party_id: int,
        rep3_inst,                          # ELL=1, aggregation ring
        tree_cache: TreeCache,
        hash_seeds: list[bytes],
        bf_size: int,
        ch_prev=None,
        ch_next=None,
        # Query support (optional)
        rep3_q_inst=None,                   # ELL=ell_query
        query_srv=None,                     # QueryServer instance
    ):
        if party_id not in (0, 1, 2):
            raise ValueError("party_id must be 0, 1, or 2")
        self._pid = party_id
        self._rep3 = rep3_inst
        self._tc = tree_cache
        self._hash_seeds = list(hash_seeds)
        self._bf_size = bf_size
        self._ch_prev = ch_prev
        self._ch_next = ch_next
        self._rep3_q = rep3_q_inst
        self._query_srv = query_srv

        # Merge scratch buffer
        SV = mpmt.ShrRep3ShareVec(1)
        self._sv_tmp  = SV(bf_size)
        self._aux_buf = mpmt.RvectorPack(1)(bf_size)
        self._tc_sv   = SV(bf_size)

    # ==================================================================
    #  Merge primitive  (paper Eq. 3.3)
    # ==================================================================

    def _merge(self, sv1, sv2, out):
        self._rep3.add_vec(sv1, sv2, out)
        self._rep3.hadamard(sv1, sv2, self._sv_tmp)
        self._rep3.sub_vec(out, self._sv_tmp, out)

    # ==================================================================
    #  Three-way confirmation
    # ==================================================================

    def three_way_confirm(self) -> None:
        if self._pid == self.PID_LEADER:
            self._rep3.share_scalar(0)
        else:
            self._rep3.recv_scalar_share()

    # ==================================================================
    #  TreeCache helpers
    # ==================================================================

    def prepare_join(self, token: bytes) -> None:
        if token in self._tc._phi:
            raise ValueError(f"token {token.hex()} already in tree")
        self._tc.insert(token, None)

    def check_token(self, token: bytes, action: str) -> None:
        if action in ("update", "quit"):
            if token not in self._tc._phi:
                raise ValueError(f"token {token.hex()} not found for {action}")

    def place_share(self, token: bytes, sv) -> None:
        idx = self._tc.index_of(token)
        if idx is None:
            idx = max(self._tc._phi_inv.keys())
        self._tc._buf[idx] = sv
        self._tc._mark_path_dirty(idx)

    # ==================================================================
    #  Aggregate
    # ==================================================================

    def aggregate(self) -> None:
        schedule = self._tc.get_merge_schedule()
        if not schedule:
            return
        SV = mpmt.ShrRep3ShareVec(1)
        for step in schedule:
            left = 2 * step.parent + 1
            right = 2 * step.parent + 2
            left_sv = self._tc.share_at(left)
            right_sv = self._tc.share_at(right)
            if left_sv is None and right_sv is None:
                continue
            if left_sv is None:
                self._tc._buf[step.parent] = right_sv; continue
            if right_sv is None:
                self._tc._buf[step.parent] = left_sv; continue
            cur = self._tc._buf[step.parent]
            if cur is None or cur is NOT_LOADED:
                self._tc._buf[step.parent] = SV(self._bf_size)
            self._merge(left_sv, right_sv, self._tc._buf[step.parent])

    def do_quit(self, token: bytes) -> None:
        self._tc.remove(token)

    # ==================================================================
    #  SetHolder Connect  (join / update)
    # ==================================================================

    def connect_leader(self, listen_port: int) -> None:
        ch = mpmt.Channel(listen_port)
        rt = mpmt.RingTransport(1)(ch)
        for hs in self._hash_seeds:
            ch.send(bytearray(hs))
        rt.recv_vector(self._tc_sv.this_share, self._aux_buf)
        rt.recv_vector(self._tc_sv.nxt_share, self._aux_buf)
        idx = max(self._tc._phi_inv.keys())
        self._tc._buf[idx] = self._tc_sv
        self._tc._mark_path_dirty(idx)

    def connect_helper(self, *, port: int = 0) -> None:
        if self._pid == self.PID_HELPER_A:
            ch_prev = self._ch_next
            ch_next = mpmt.Channel(port)
        else:
            ch_prev = mpmt.Channel(port)
            ch_next = self._ch_prev
        inst = mpmt.ShrRep3(1, self._pid)(ch_prev, ch_next)
        sv = mpmt.ShrRep3ShareVec(1)(self._bf_size)
        inst.recv_vector_share(sv, self._aux_buf)
        idx = max(self._tc._phi_inv.keys())
        self._tc._buf[idx] = sv
        self._tc._mark_path_dirty(idx)

    # ==================================================================
    #  Query  (Leader + Helpers)
    # ==================================================================

    def query(self, *, element=None, ch_querier=None,
              hash_seeds=None, rt_to_helpers=None) -> dict | None:
        """Execute the query protocol on this server.

        Leader path: hash → DPF gen → crng → reshare → dot → ET → send.
        Helper path: recv idx → DPF eval → crng → reshare → dot → send dot.
        """
        if self._query_srv is None:
            raise RuntimeError("QueryServer not injected")

        srv = self._query_srv

        if self._pid == self.PID_LEADER:
            if ch_querier is None or hash_seeds is None:
                raise ValueError("Leader query requires ch_querier and hash_seeds")
            # — Hash (ADD2) —
            add2_h = self._query_srv._add2_hash(ch_querier)
            e_share = add2_h.recv_element_share()
            idx_list = []
            for hs in hash_seeds:
                add2_h.share_key(hs)
                hr = add2_h.hash(e_share, hs)
                mr = add2_h.mod(hr, self._bf_size)
                idx_list.append(mr)

            # — DPF gen + send keys —
            # (DPF keys go to helpers via rt_to_helpers if available)
            for idx_shr in idx_list:
                self._query_srv._dpf_dealer.gen(
                    idx_shr.this_share, idx_shr.nxt_share)

            # — crng + reshare + dot —
            additive = srv.genbf_additive_share()
            sv_q = srv.reshare_into_rep3(additive)
            dot = srv.step_dot(sv_q, rep3_inst=self._rep3)

            # — ET (ADD2) —
            add2_et = self._query_srv._add2_et(ch_querier)
            et_shr = add2_et.equality_test(dot, self._query_srv._hf_num)
            return {"dot": dot, "et": et_shr}
        else:
            # Helper path
            additive = srv.genbf_additive_share()
            sv_q = srv.reshare_into_rep3(additive)
            dot = srv.step_dot(sv_q, rep3_inst=self._rep3)
            return {"dot": dot}
