"""Query protocol — server-side operations and querier-side client.

QueryServer: crng → reshare → dot.  Used by Leader and Helpers.
QueryClient: hash → idx → dot → ET → result.  Pure client, no Rep3.

@author  mincy
@ref     BGI16 DPF (https://eprint.iacr.org/2016/622), ABY3 RSS3
"""

from __future__ import annotations

import mpmt


class QueryServer:
    """Server-side query protocol.  One ``execute`` entry point.

    The Composition Root (server class) injects C++ instances.
    """

    def __init__(
        self, *,
        party_id: int, rep3_q_inst, tree_cache,
        add2_hash=None, add2_et=None,
        dpf_dealer=None, dpf_eval=None,
        hf_num: int, bf_size: int,
    ):
        self._pid = party_id
        self._rep3_q = rep3_q_inst
        self._tc = tree_cache
        self._add2_hash = add2_hash
        self._add2_et = add2_et
        self._dpf_dealer = dpf_dealer
        self._dpf_eval = dpf_eval
        self._hf_num = hf_num
        self._bf_size = bf_size
        self._ell_query = rep3_q_inst.ell

    # ------------------------------------------------------------------
    #  Shared helpers  (used by 3 parties)
    # ------------------------------------------------------------------

    def genbf_additive_share(self) -> object:
        """crng → additive share component r_i  (sum r_i = 0)."""
        Rv_q = mpmt.Rvector(ell=self._ell_query)
        r = Rv_q(self._bf_size)
        self._rep3_q.crng_vec(r)
        return r

    def genbf_additive_share_helper(self, dpf_bf_2of2) -> object:
        """Helper: crng → add r_i to DPF 2-of-2 result → 3-of-3 component."""
        Rv_q = mpmt.Rvector(ell=self._ell_query)
        r = Rv_q(self._bf_size)
        self._rep3_q.crng_vec(r)
        out = Rv_q(self._bf_size)
        Rv_q.add(dpf_bf_2of2, r, out)
        return out

    def reshare_into_rep3(self, additive_share) -> object:
        """3-of-3 additive shares → Rep3 (2-of-3).

        Helpers send their shares to Leader via ring.  Leader reconstructs
        and shares back.  All three get Rep3 share.
        """
        SV_q = mpmt.ShrRep3ShareVec(ell=self._ell_query)
        aux = mpmt.RvectorPack(ell=self._ell_query)(self._bf_size)
        nbytes = len(additive_share.to_bytes())

        if self._pid == 0:
            buf = bytearray(nbytes)
            self._rep3_q.recv_data(1, buf)
            tmp_a = mpmt.Rvector(ell=self._ell_query)(self._bf_size)
            tmp_a.from_bytes(bytes(buf))
            self._rep3_q.recv_data(2, buf)
            tmp_b = mpmt.Rvector(ell=self._ell_query)(self._bf_size)
            tmp_b.from_bytes(bytes(buf))

            Rv_q = mpmt.Rvector(ell=self._ell_query)
            plain = Rv_q(self._bf_size)
            Rv_q.add(additive_share, tmp_a, plain)
            Rv_q.add(plain, tmp_b, plain)

            sv = SV_q(self._bf_size)
            self._rep3_q.share_vector(plain, sv, aux)
            return sv
        else:
            self._rep3_q.send_data(0, additive_share.to_bytes())
            sv = SV_q(self._bf_size)
            self._rep3_q.recv_vector_share(sv, aux)
            return sv

    def step_dot(self, bf_query_sv, rep3_inst=None) -> object:
        root_sv = self._tc.root_share
        if root_sv is None:
            raise RuntimeError("TreeCache root not ready — run aggregate first")
        if self._ell_query == 1:
            return self._rep3_q.dot(root_sv, bf_query_sv)
        # ring_conv: ELL=1 → ell_query
        SV_q = mpmt.ShrRep3ShareVec(ell=self._ell_query)
        root_q = SV_q(self._bf_size)
        if rep3_inst is None:
            raise RuntimeError("rep3_inst required for ring_conv when ell_query > 1")
        rep3_inst.ring_conv_vec(root_sv, root_q, self._ell_query)
        return self._rep3_q.dot(root_q, bf_query_sv)


class QueryClient:
    """Querier-side query protocol.  One ``execute`` entry point."""

    def __init__(
        self, *,
        add2_hash, add2_et,
        ell_add2: int, ell_query: int,
        hf_num: int, bf_size: int,
        hash_seed_list: list[bytes],
    ):
        self._add2_hash = add2_hash
        self._add2_et = add2_et
        self._ell_add2 = ell_add2
        self._ell_query = ell_query
        self._hf_num = hf_num
        self._bf_size = bf_size
        self._hash_seed_list = list(hash_seed_list)

    def execute(self, element: bytes,
                ch_leader, ch_helper_a, ch_helper_b) -> bool:
        """Run full query protocol → True if element is a member."""
        # —— 1. Hash (ADD2 with Leader) ——
        add2_i = self._add2_hash(ch_leader)
        e_share = add2_i.share_element(element)
        idx_list = []
        for hs in self._hash_seed_list:
            hs_buf = bytearray(16)
            add2_i.recv_key_share(hs_buf)
            hr = add2_i.hash(e_share, hs_buf)
            mr = add2_i.mod(hr, self._bf_size)
            idx_list.append(mr)

        # —— 2. Send idx to Helpers ——
        nbytes_idx = (self._ell_add2 + 7) // 8
        for idx in idx_list:
            if self._ell_add2 >= 9:
                rt_a = mpmt.RingTransport(ell=self._ell_add2)(ch_helper_a)
                rt_b = mpmt.RingTransport(ell=self._ell_add2)(ch_helper_b)
                rt_a.send_scalar(idx.this_share)
                rt_b.send_scalar(idx.nxt_share)
            else:
                ch_helper_a.send(idx.this_share.to_bytes(nbytes_idx, 'little'))
                ch_helper_b.send(idx.nxt_share.to_bytes(nbytes_idx, 'little'))

        # —— 3. Receive dot shares from Helpers ——
        nbytes_dot = (self._ell_query + 7) // 8
        if self._ell_query >= 9:
            rt_dot_a = mpmt.RingTransport(ell=self._ell_query)(ch_helper_a)
            rt_dot_b = mpmt.RingTransport(ell=self._ell_query)(ch_helper_b)
            dot_a = rt_dot_a.recv_scalar()
            dot_b = rt_dot_b.recv_scalar()
        else:
            buf = bytearray(nbytes_dot)
            ch_helper_a.recv(buf); dot_a = int.from_bytes(buf, 'little')
            ch_helper_b.recv(buf); dot_b = int.from_bytes(buf, 'little')
        dot_shr = mpmt.ring_add(self._ell_query, dot_a, dot_b)

        # —— 4. Equality test (ADD2 with Leader) ——
        # Querier passes (dot_shr, 0); Leader passes (s0, hf_num).
        add2_et_i = self._add2_et(ch_leader)
        et_shr = add2_et_i.equality_test(dot_shr, 0)

        # —— 5. Receive Leader's ET share → reveal ——
        if self._ell_query >= 9:
            rt_et = mpmt.RingTransport(ell=self._ell_query)(ch_leader)
            leader_et = rt_et.recv_scalar()
        else:
            buf = bytearray(nbytes_dot)
            ch_leader.recv(buf); leader_et = int.from_bytes(buf, 'little')
        result = mpmt.ring_add(self._ell_query, et_shr, leader_et)
        return result == 1
