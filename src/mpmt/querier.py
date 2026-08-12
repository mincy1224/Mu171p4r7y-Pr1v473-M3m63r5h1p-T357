"""
Querier
@author  mincy
"""

from __future__ import annotations
import mpmt

class Querier:

    def __init__(
        self, *,
        set_size,
        fpr_mantissa,
        fpr_exponent
    ):
        self._bf_size, self._ell_add2, self._hf_num, self._ell_root = mpmt.bf_param(
            set_size, fpr_mantissa, fpr_exponent,
        )

    def query(
        self, *, 
        element: bytes,
        ch_steward: mpmt.channels.Channel,
        ch_peer0: mpmt.channels.Channel,
        ch_peer1: mpmt.channels.Channel
    ) -> int:
        add2_inst_steward   = mpmt.ShrAdd2(ell=self._ell_add2, party=0)(ch_steward)
        add2_inst_peer0     = mpmt.ShrAdd2(ell=self._ell_add2, party=0)(ch_peer0)
        rt_inst_peer1       = mpmt.RingTransport(ell=self._ell_add2)(ch_peer1)

        element_share = add2_inst_steward.share_element(element)

        for _ in range(self._hf_num):
            key_share = add2_inst_steward.recv_element_share()
            h_share = add2_inst_steward.hash(element_share, key_share)
            idx_share = add2_inst_steward.mod(h_share, self._bf_size)
            rt_inst_peer1.send_scalar(
                add2_inst_peer0.share_scalar(idx_share)
            )

        rt_inst_peer1       = mpmt.RingTransport(ell=self._ell_root)(ch_peer1)
        dr_share = rt_inst_peer1.recv_scalar()

        add2_inst_steward   = mpmt.ShrAdd2(ell=self._ell_root, party=0)(ch_steward)
        qr_share = add2_inst_steward.equality_test(dr_share, self._hf_num)

        return mpmt.ring_add(
            ell=self._ell_root,
            a=add2_inst_steward.recv_data(),
            b=qr_share
        )
        