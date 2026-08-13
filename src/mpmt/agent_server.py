""" 
Agent Server

"""

from __future__ import annotations

from enum import IntEnum
from typing import Literal, overload

import mpmt

from ._tree_cache import _TreeCache


_VALID_DPF_CORES = {1, 2, 4, 8, 16, 32}


class ProtType(IntEnum):
    JOIN = 1
    UPDATE = 2
    QUIT = 3


class ServerRole(IntEnum):
    STEWARD = 0
    PEER0 = 1
    PEER1 = 2
    # Topology: STEWARD → PEER0 → PEER1 → STEWARD


class AgentServer:
    def __init__(
        self, *,
        server_role: ServerRole,
        set_size: int,
        fpr_mantissa: float,
        fpr_exponent: int,
        storage_dir: str,
        ch_prev,
        ch_nxt,
        hash_seed_list: list[bytes] | None = None,
        cores: int,
    ):
        if not isinstance(server_role, ServerRole):
            raise TypeError(
                f"server_role must be ServerRole, "
                f"got {type(server_role).__name__}"
            )

        if isinstance(cores, bool) or not isinstance(cores, int):
            raise TypeError("cores must be an integer")
        
        if cores not in _VALID_DPF_CORES:
            raise ValueError(
                f"cores must be one of {sorted(_VALID_DPF_CORES)}, got {cores}"
            )

        if not isinstance(storage_dir, str) or not storage_dir:
            raise TypeError("storage_dir must be a non-empty string")

        for name, channel in (("ch_prev", ch_prev), ("ch_nxt", ch_nxt)):
            if channel is None or not callable(getattr(channel, "acquire", None)):
                raise TypeError(
                    f"{name} must be a Channel-like object with acquire()"
                )

        if server_role is ServerRole.STEWARD:
            if hash_seed_list is None:
                raise ValueError(
                    "hash_seed_list is required when server_role is STEWARD"
                )
            if not isinstance(hash_seed_list, list):
                raise TypeError("hash_seed_list must be a list of 16-byte values")
            for i, seed in enumerate(hash_seed_list):
                if not isinstance(seed, bytes) or len(seed) != 16:
                    raise ValueError(
                        f"hash_seed_list[{i}] must be exactly 16 bytes"
                    )
        elif hash_seed_list is not None:
            raise ValueError(
                "hash_seed_list is only valid when server_role is STEWARD"
            )

        self._cores = cores
        self._server_role = server_role
        self._ch_prev = ch_prev
        self._ch_nxt = ch_nxt

        self._bf_size, self._ell_add2, self._hf_num, self._ell_root = (
            mpmt.bf_param(set_size, fpr_mantissa, fpr_exponent)
        )

        if self._server_role is ServerRole.STEWARD:
            self._hash_seed_list = hash_seed_list
            if self._hf_num != len(self._hash_seed_list):
                raise ValueError(
                    "Hash function count mismatch: "
                    f"self._hf_num={self._hf_num}, "
                    f"len(hash_seed_list)={len(self._hash_seed_list)}"
                )

        # IMPORTANT:
        # Do not insert any custom barrier/handshake here.  ShrRep3 itself
        # performs its own key exchange.  Extra bytes on these channels would
        # corrupt the protocol stream.
        self._rep3_inst_ell1 = self._init_step(
            "ShrRep3(ell=1)",
            lambda: mpmt.ShrRep3(
                ell=1,
                party=int(self._server_role),
            )(self._ch_prev, self._ch_nxt),
        )

        self._rep3_inst_ell_root = self._init_step(
            f"ShrRep3(ell={self._ell_root})",
            lambda: mpmt.ShrRep3(
                ell=self._ell_root,
                party=int(self._server_role),
            )(self._ch_prev, self._ch_nxt),
        )

        self._server_dpf_inst = self._init_step(
            "DPF",
            self._build_dpf,
        )

        self._pack_buf = self._init_step(
            f"RvectorPack(ell=1, n={self._bf_size})",
            lambda: mpmt.RvectorPack(ell=1)(self._bf_size),
        )

        self._merge_buf = self._init_step(
            f"ShrRep3ShareVec(ell=1, n={self._bf_size})",
            lambda: mpmt.ShrRep3ShareVec(ell=1)(self._bf_size),
        )

        self._query_buf = self._init_step(
            f"ShrRep3ShareVec(ell={self._ell_root}, n={self._bf_size})",
            lambda: mpmt.ShrRep3ShareVec(
                ell=self._ell_root
            )(self._bf_size),
        )

        self._tc = self._init_step(
            "TreeCache",
            lambda: _TreeCache(
                storage_dir=storage_dir,
                bf_size=self._bf_size,
                ell_root=self._ell_root,
                merge_fn=self._merge,
                ring_conv_fn=self._rep3_inst_ell1.ring_conv_vec,
            ),
        )

    # initialisation helpers

    def _init_step(self, name: str, factory):
        return factory()

    def _build_dpf(self):
        if self._server_role is ServerRole.STEWARD:
            return mpmt.DpfDealer(
                ell_in=self._ell_add2,
                ell_out=self._ell_root,
            )(self._ch_nxt, self._ch_prev)

        if self._server_role is ServerRole.PEER0:
            return mpmt.DpfEvaluator(
                ell_in=self._ell_add2,
                ell_out=self._ell_root,
                party=0,
            )(self._ch_prev)

        return mpmt.DpfEvaluator(
            ell_in=self._ell_add2,
            ell_out=self._ell_root,
            party=1,
        )(self._ch_nxt)

    # TreeCache merge primitive

    def _merge(self, *, sva, svb, svout):
        self._rep3_inst_ell1.add_vec(sva, svb, svout)
        self._rep3_inst_ell1.hadamard(
            sva,
            svb,
            self._merge_buf,
        )
        self._rep3_inst_ell1.sub_vec(
            svout,
            self._merge_buf,
            svout,
        )

    # set-holder protocol

    @overload
    def response_share_bf(
        self,
        *,
        prot_type: Literal[ProtType.JOIN],
        ch_set_holder: mpmt.channels.Channel,
    ) -> str:
        ...

    @overload
    def response_share_bf(
        self,
        *,
        prot_type: Literal[ProtType.UPDATE],
        ch_set_holder: mpmt.channels.Channel,
        token: str,
    ) -> None:
        ...

    @overload
    def response_share_bf(
        self,
        *,
        prot_type: Literal[ProtType.QUIT],
        token: str,
    ) -> None:
        ...

    def response_share_bf(
        self,
        *,
        prot_type: ProtType,
        ch_set_holder: mpmt.channels.Channel | None = None,
        token: str | None = None,
    ) -> str | None:
        if not isinstance(prot_type, ProtType):
            raise TypeError(
                f"prot_type must be ProtType, "
                f"got {type(prot_type).__name__}"
            )

        if prot_type is ProtType.QUIT:
            if token is None:
                raise TypeError("QUIT requires keyword argument 'token'")

            if not self._tc.has_inserted(token=token):
                raise KeyError(
                    f"Unknown TreeCache token: {token!r}"
                )

            self._tc.remove(del_token=token)
            return None

        if ch_set_holder is None:
            raise TypeError(
                f"{prot_type.name} requires keyword argument 'ch_set_holder'"
            )

        if prot_type is ProtType.UPDATE:
            if token is None:
                raise TypeError(
                    "UPDATE requires keyword argument 'token'"
                )
            if not self._tc.has_inserted(token=token):
                raise KeyError(
                    f"Unknown TreeCache token: {token!r}"
                )

        # Receive the new Bloom-filter share.
        if self._server_role is ServerRole.STEWARD:
            rt_inst = mpmt.RingTransport(ell=1)(ch_set_holder)
            rt_inst.recv_vector(
                self._merge_buf.this_share,
                self._pack_buf,
            )
            rt_inst.recv_vector(
                self._merge_buf.nxt_share,
                self._pack_buf,
            )
        else:
            if self._server_role is ServerRole.PEER0:
                ch_prev = ch_set_holder
                ch_nxt = self._ch_nxt
            else:
                ch_prev = self._ch_prev
                ch_nxt = ch_set_holder

            rep3_inst = mpmt.ShrRep3(
                ell=1,
                party=int(self._server_role),
            )(ch_prev, ch_nxt)
            rep3_inst.recv_vector_share(
                self._merge_buf,
                self._pack_buf,
            )

        if prot_type is ProtType.JOIN:
            new_token = self._tc.insert(
                node=self._merge_buf
            )
            # The new leaf is marked dirty; the tree merge is deferred to
            # an explicit Manager SYNC (see sync_cache).
            return new_token

        if prot_type is ProtType.UPDATE:
            self._tc.update(
                token=token,
                new_node=self._merge_buf,
            )
            # The updated leaf is marked dirty; merge deferred to explicit SYNC.
            return None

        raise ValueError(
            f"Unsupported prot_type: {prot_type!r}"
        )

    def sync_cache(self) -> None:
        """Explicitly merge the TreeCache (three-party, lockstep on all
        three Agents).  JOIN/UPDATE/QUIT only mark the cache dirty; the
        actual tree merge is deferred until the Manager issues a SYNC.
        """
        self._tc.execute_merge()

    def response_query(
        self, *,
        ch_querier: mpmt.channels.Channel,
    ) -> None:
        _rt_inst_for_idx_prev = mpmt.RingTransport(ell=self._ell_add2)(self._ch_prev)
        _rt_inst_for_idx_nxt = mpmt.RingTransport(ell=self._ell_add2)(self._ch_nxt)
        def _reshare_idx_share(val: int):
            ss = mpmt.ShrRep3ShareScalar()
            ss.this_share = val
            if self._server_role == ServerRole.STEWARD:
                _rt_inst_for_idx_prev.send_scalar(val)
                ss.nxt_share = _rt_inst_for_idx_nxt.recv_scalar()

            elif self._server_role == ServerRole.PEER0:
                _rt_inst_for_idx_prev.send_scalar(val)
                ss.nxt_share = _rt_inst_for_idx_nxt.recv_scalar()

            elif self._server_role == ServerRole.PEER1:
                ss.nxt_share = _rt_inst_for_idx_nxt.recv_scalar()
                _rt_inst_for_idx_prev.send_scalar(val)

            return ss

        if self._server_role == ServerRole.STEWARD:
            add2_inst = mpmt.ShrAdd2(ell=self._ell_add2, party=1)(ch_querier)
            element_share = add2_inst.recv_element_share()

            idx_shares = []

            for key in self._hash_seed_list:
                key_share = add2_inst.share_element(key)
                h_share = add2_inst.hash(element_share, key_share)
                idx_shares.append(add2_inst.mod(h_share, self._bf_size))

            alpha_set = []
            for ishare in idx_shares:
                idx_rep_share = _reshare_idx_share(val=ishare)
                alpha = mpmt.ring_add(
                            ell=self._ell_add2,
                            a=idx_rep_share.this_share,
                            b=idx_rep_share.nxt_share
                        )
                alpha_set.append(alpha)

            keylist_e0 = []
            keylist_e1 = []
            for alpha in alpha_set:
                key_e0, key_e1 = self._server_dpf_inst.gen(alpha=alpha, beta=1)
                keylist_e0.append(key_e0)
                keylist_e1.append(key_e1)

            for key_e0, key_e1 in zip(keylist_e0, keylist_e1):
                self._server_dpf_inst.send_key(key_e0, party=0)
                self._server_dpf_inst.send_key(key_e1, party=1)

            pack_buf = mpmt.RvectorPack(ell=self._ell_root)(self._bf_size)
            self._rep3_inst_ell_root.crng_vec(self._query_buf.nxt_share)

            self._rep3_inst_ell_root.reshare_vector(
                vec=self._query_buf.nxt_share,
                sv=self._query_buf,
                aux_buf=pack_buf
            )

            dr_rep_share = self._rep3_inst_ell_root.dot(
                sv1 = self._query_buf,
                sv2 = self._tc.root_node
            )

            dr_share = mpmt.ring_add(
                ell=self._ell_root,
                a=dr_rep_share.this_share,
                b=dr_rep_share.nxt_share
            )

            add2_inst = mpmt.ShrAdd2(ell=self._ell_root, party=1)(ch_querier)
            qr_share = add2_inst.equality_test(dr_share, self._hf_num)
            add2_inst.send_data(val = qr_share)

        else:
            pack_buf = mpmt.RvectorPack(ell=self._ell_root)(self._bf_size)
            self._rep3_inst_ell_root.crng_vec(self._query_buf.nxt_share)

            idx_shares = []
            if self._server_role == ServerRole.PEER0:
                add2_inst = mpmt.ShrAdd2(ell=self._ell_add2, party=1)(ch_querier)
                for _ in range(self._hf_num):
                    idx_shares.append(add2_inst.recv_scalar_share())

            elif self._server_role == ServerRole.PEER1:
                rt_inst = mpmt.RingTransport(ell=self._ell_add2)(ch_querier)
                for _ in range(self._hf_num):
                    idx_shares.append(rt_inst.recv_scalar())

            bias_set = []
            for ishare in idx_shares:
                idx_rep_share = _reshare_idx_share(val=ishare)
                if self._server_role == ServerRole.PEER0:
                    bias_set.append(idx_rep_share.nxt_share)
                elif self._server_role == ServerRole.PEER1:
                    bias_set.append(idx_rep_share.this_share)

            keylist = []
            for _ in range(self._hf_num):
                keylist.append(self._server_dpf_inst.recv_key())

            for key, bias in zip(keylist, bias_set):
                eval_bg = mpmt.ring_sub(ell=self._ell_add2, a=0, b=bias)
                eval_ed = mpmt.ring_add(ell=self._ell_add2, a=eval_bg, b=self._bf_size)
                eval_ed = mpmt.ring_sub(ell=self._ell_add2, a=eval_ed, b=1)

                self._server_dpf_inst.eval_range(
                    key_json=key,
                    buf=self._query_buf.this_share,
                    bg=eval_bg,
                    ed=eval_ed,
                    cores=self._cores
                )

                mpmt.Rvector(ell=self._ell_root).add(
                    a=self._query_buf.nxt_share,
                    b=self._query_buf.this_share,
                    out=self._query_buf.nxt_share
                )

            self._rep3_inst_ell_root.reshare_vector(
                vec=self._query_buf.nxt_share,
                sv=self._query_buf,
                aux_buf=pack_buf
            )

            dr_rep_share = self._rep3_inst_ell_root.dot(
                sv1 = self._query_buf,
                sv2 = self._tc.root_node
            )

            if self._server_role == ServerRole.PEER1:
                rt_inst = mpmt.RingTransport(ell=self._ell_root)(ch_querier)
                rt_inst.send_scalar(val=dr_rep_share.this_share)