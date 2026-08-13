"""
SetHolder 

"""

from __future__ import annotations
import mpmt


class SetHolder:
    def __init__(
        self, *,
        set_size: int,
        fpr_mantissa: float,
        fpr_exponent: int,
    ):
        self._bf_size, self._ell_add2, self._hf_num, _ = mpmt.bf_param(
            set_size, fpr_mantissa, fpr_exponent,
        )

        self._pack_buf = mpmt.RvectorPack(ell=1)(self._bf_size)
        self._sv_buf = mpmt.ShrRep3ShareVec(ell=1)(self._bf_size)

    def share_bf(
        self, *,
        set: list[bytes],
        hash_seed_list: list[bytes],   
        ch_steward: mpmt.channels.Channel,
        ch_peer0: mpmt.channels.Channel,
        ch_peer1: mpmt.channels.Channel
    ) -> None:
        if len(hash_seed_list) != self._hf_num:
            raise ValueError(
                f"Hash function count mismatch: "
                f"expected {self._hf_num}, "
                f"got {len(hash_seed_list)}"
            )
        
        rep3_inst = mpmt.ShrRep3(ell=1, party=0)(
            ch_peer1,  # prev
            ch_peer0,  # nxt
        )

        rt_inst = mpmt.RingTransport(ell=1)(ch_steward)
        
        bf = mpmt.gen_bf(
            ell=1,
            set=set,
            hash_seed_list=hash_seed_list,
            bf_size=self._bf_size,
            hf_num=self._hf_num,
            ell_add2=self._ell_add2,
        )

        rep3_inst.share_vector(
            vec=bf,
            sv=self._sv_buf,
            aux_buf=self._pack_buf
        )

        rt_inst.send_vector(
            vec=self._sv_buf.this_share,
            aux_buf=self._pack_buf
        )

        rt_inst.send_vector(
            vec=self._sv_buf.nxt_share,
            aux_buf=self._pack_buf
        )
