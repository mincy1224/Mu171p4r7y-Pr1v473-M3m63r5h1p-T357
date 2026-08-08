"""
SetHolder 
@author  mincy
"""

from __future__ import annotations
from array import array
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

    def _genbf_plain(
        self, *,
        set: list[bytes],
        hash_seed_list: list[bytes],
    ):        
        Rv = mpmt.Rvector(ell=1)
        bf = Rv(self._bf_size)
        bf.fill(0)
        batch_size = max(2 ** 20, self._bf_size // 128)
        batch_cap = batch_size * self._hf_num
        batch_idx = array("Q", [0]) * batch_cap
        pos = 0
        for e in set:
            for hs in hash_seed_list:
                batch_idx[pos] = mpmt.ring_mod(
                    self._ell_add2,
                    mpmt.hash_aes_dm(preimage=e, key=hs, ell=self._ell_add2),
                    self._bf_size,
                )
                pos += 1
            if pos >= batch_cap:
                bf.batch_set(indices=batch_idx, val=1)
                pos = 0
        if pos > 0:
            bf.batch_set(indices=batch_idx[:pos], val=1)
        return bf

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
        
        bf = self._genbf_plain(
            set=set,
            hash_seed_list=hash_seed_list
        )

        rep3_inst.share_vector(
            vec=bf,
            sv=self._sv_buf,
            auxBuf=self._pack_buf
        )

        rt_inst.send_vector(
            vec=self._sv_buf.this_share,
            auxBuf=self._pack_buf
        )

        rt_inst.send_vector(
            vec=self._sv_buf.nxt_share,
            auxBuf=self._pack_buf
        )
