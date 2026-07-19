"""MpmtSetHolder — client that builds a local BF and RSS3-shares it to the servers.

Uses reserve/connect callback pattern — no HTTP dependency.

@author  mincy
"""

from __future__ import annotations

import inspect
import math
from array import array

import mpmt


class MpmtSetHolder:
    """Set holder — shares its Bloom filter via RSS3."""

    PID_IN_REP3 = 0

    def __init__(
        self, *,
        set_size: int, fpr_mantissa: float, fpr_exponent: int,
        reserve_fn, connect_fn,
    ):
        self.bf_size, self.ell_add2, self.hf_num, _ = mpmt.bf_param(
            set_size, fpr_mantissa, fpr_exponent,
        )

        self.rv     = mpmt.Rvector(ell=1)
        self.rvpack = mpmt.RvectorPack(ell=1)
        self.sv     = mpmt.ShrRep3ShareVec(ell=1)
        self.rep3   = mpmt.ShrRep3(ell=1, party=MpmtSetHolder.PID_IN_REP3)
        self.rt     = mpmt.RingTransport(ell=1)

        self.token  = mpmt.get_key_128bits()

        self.aux_buf = self.rvpack(self.bf_size)
        self.bf_buf  = self.rv(self.bf_size)
        self.sv_buf  = self.sv(self.bf_size)

        # —— Reserve callback ——
        inspect.signature(reserve_fn).bind(token=b"\x00" * 16, action="join")
        _raw_reserve = reserve_fn

        def _checked_reserve(token: bytes, action: str) -> list[dict]:
            results = _raw_reserve(token=token, action=action)
            if not isinstance(results, list) or len(results) != 3:
                raise TypeError("reserve_fn must return list of 3 dicts")
            for i, r in enumerate(results):
                if not isinstance(r, dict):
                    raise TypeError(f"reserve_fn result[{i}] must be dict")
                if action != "quit" and not isinstance(r.get("port"), int):
                    raise TypeError(f"reserve_fn result[{i}] missing 'port'")
            return results

        self._reserve = _checked_reserve

        # —— Connect callback ——
        inspect.signature(connect_fn).bind(
            token=b"\x00" * 16, action="join",
            leader_port=0, helper_a_port=0, helper_b_port=0,
        )
        self._connect = connect_fn

    # ------------------------------------------------------------------
    #  Join / Update
    # ------------------------------------------------------------------

    def _do_share_flow(self, action: str,
                       server_leader_addr: str,
                       elements: list) -> None:
        """Reserve → connect → build BF → Rep3 share → send to Leader."""
        results = self._reserve(self.token, action)
        leader_port   = results[0]["port"]
        helper_a_port = results[1]["port"]
        helper_b_port = results[2]["port"]

        self._connect(token=self.token, action=action,
                      leader_port=leader_port,
                      helper_a_port=helper_a_port,
                      helper_b_port=helper_b_port)

        # —— Channels ——
        ch_leader = mpmt.Channel(server_leader_addr, leader_port)
        rt_inst   = self.rt(ch_leader)
        from mpmt.channels import _build_rep3_channels
        ch_rep3 = _build_rep3_channels(
            prev_port=helper_a_port,
            next_host=server_leader_addr,
            next_port=helper_b_port,
            party_id=MpmtSetHolder.PID_IN_REP3,
        )
        rep3_inst = self.rep3(ch_rep3["prev"], ch_rep3["next"])

        # —— Receive hash seeds from Leader ——
        hash_seed_list: list[bytes] = []
        for _ in range(self.hf_num):
            buf = bytearray(16)
            ch_leader.recv(buf)
            hash_seed_list.append(bytes(buf))

        # —— Build BF locally ——
        self.bf_buf.fill(0)
        batch_size = max(2 ** 20, math.floor(self.bf_size / 128))
        batch_cap = batch_size * self.hf_num
        batch_idx = array("Q", [0]) * batch_cap
        pos = 0
        for e in elements:
            for hs in hash_seed_list:
                batch_idx[pos] = mpmt.ring_mod(
                    self.ell_add2,
                    mpmt.hash_aes_dm(preimage=e, key=hs, ell=self.ell_add2),
                    self.bf_size,
                )
                pos += 1
            if pos >= batch_cap:
                self.bf_buf.batch_set(indices=batch_idx, val=1)
                pos = 0
        if pos > 0:
            self.bf_buf.batch_set(indices=batch_idx[:pos], val=1)

        # —— Rep3 share → send to Leader via RingTransport ——
        rep3_inst.share_vector(vec=self.bf_buf, sv=self.sv_buf,
                               aux_buf=self.aux_buf)
        rt_inst.send_vector(vec=self.sv_buf.this_share, auxBuf=self.aux_buf)
        rt_inst.send_vector(vec=self.sv_buf.nxt_share, auxBuf=self.aux_buf)

    # ------------------------------------------------------------------
    #  Public API
    # ------------------------------------------------------------------

    def join(self, server_leader_addr: str, elements: list) -> None:
        self._do_share_flow("join", server_leader_addr, elements)

    def update(self, server_leader_addr: str, elements: list) -> None:
        self._do_share_flow("update", server_leader_addr, elements)

    def quit(self) -> None:
        results = self._reserve(self.token, "quit")
        for r in results:
            if r.get("status") != "ok":
                raise RuntimeError(f"quit failed: {r}")
