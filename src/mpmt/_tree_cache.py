""" 
Tree Cache
@author  mincy
"""

import inspect
import json
import os

import mpmt
import secrets
from pathlib import Path

def _is_valid_merge_fn(merge_fn: object) -> bool:
    if not callable(merge_fn):
        return False

    try:
        signature = inspect.signature(merge_fn)
    except (TypeError, ValueError):
        return False

    parameters = tuple(signature.parameters.values())
    expected_names = ("sva", "svb", "svout")

    if len(parameters) != len(expected_names):
        return False

    return all(
        parameter.name == expected_name
        and parameter.kind is inspect.Parameter.KEYWORD_ONLY
        and parameter.default is inspect.Parameter.empty
        for parameter, expected_name in zip(parameters, expected_names)
    )

def _is_valid_ring_conv(ring_conv_fn: object) -> bool:
    if not callable(ring_conv_fn):
        return False

    try:
        signature = inspect.signature(ring_conv_fn)
    except (TypeError, ValueError):
        return False

    parameters = tuple(signature.parameters.values())
    expected_names = ("sv", "sv_out", "ell_to")

    if len(parameters) != len(expected_names):
        return False

    return all(
        parameter.name == expected_name
        and parameter.kind is inspect.Parameter.KEYWORD_ONLY
        and parameter.default is inspect.Parameter.empty
        for parameter, expected_name in zip(parameters, expected_names)
    )

class _TreeCache:
    def __init__(
        self,
        *,
        storage_dir: str,
        bf_size: int,
        ell_root:int,
        merge_fn,
        ring_conv_fn
    ):
        if not _is_valid_merge_fn(merge_fn):
            raise TypeError(
                "merge_fn must have signature (*, sva, svb, svout)"
            )

        if not _is_valid_ring_conv(ring_conv_fn):
            raise TypeError(
                "ring_conv_fn must have signature (*, sv, sv_out, ell_to)"
            )

        self._merge = merge_fn
        self._ring_conv = ring_conv_fn

        self._ell_root = ell_root
        self._bf_size = bf_size

        self.root_node = mpmt.ShrRep3ShareVec(
            ell=self._ell_root
        )(self._bf_size)

        storage_path = Path(storage_dir)
        storage_path.mkdir(parents=True, exist_ok=True)

        self.storage_dir = str(storage_path)
        self.meta_path = storage_path / "meta.json"

        self.root_this_path = storage_path / "root_this.mpmtrvp"
        self.root_nxt_path = storage_path / "root_nxt.mpmtrvp"

        self._corrupted = False
        self.phi_f: dict[str, int] = {}
        self.phi_r: dict[int, str] = {}

        self._dirty_leaf: set[int] = set()

        self.leaf_num = 0

        if not self.meta_path.exists():
            if any(storage_path.iterdir()):
                raise RuntimeError(
                    "meta.json is missing, but the storage directory is not empty; "
                    "the cache may be corrupted. Delete the directory contents "
                    "manually before creating a new TreeCache."
                )
            return

        try:
            with self.meta_path.open("r", encoding="utf-8") as f:
                meta_data = json.load(f)
        except Exception as e:
            self._corrupted = True
            raise RuntimeError(
                "meta.json is malformed or corrupt"
            ) from e

        if not isinstance(meta_data, dict):
            self._corrupted = True
            raise RuntimeError(
                "meta.json is not a JSON object"
            )

        if "bf_size" not in meta_data:
            self._corrupted = True
            raise RuntimeError(
                "meta.json is missing bf_size"
            )

        meta_bf_size = meta_data["bf_size"]

        if (
            isinstance(meta_bf_size, bool)
            or not isinstance(meta_bf_size, int)
        ):
            self._corrupted = True
            raise TypeError(
                "meta.json bf_size is not an integer"
            )

        if meta_bf_size != bf_size:
            self._corrupted = True
            raise ValueError(
                "bf_size mismatch: "
                f"meta.json contains {meta_bf_size}, "
                f"but {bf_size} was requested"
            )

        if "ell_root" not in meta_data:
            self._corrupted = True
            raise RuntimeError(
                "meta.json is missing ell_root"
            )

        meta_ell_root = meta_data["ell_root"]

        if (
            isinstance(meta_ell_root, bool)
            or not isinstance(meta_ell_root, int)
        ):
            self._corrupted = True
            raise TypeError(
                "meta.json ell_root is not an integer"
            )

        if meta_ell_root != ell_root:
            self._corrupted = True
            raise ValueError(
                "ell_root mismatch: "
                f"meta.json contains {meta_ell_root}, "
                f"but {ell_root} was requested"
            )

        required = {
            "state",
            "leaf_num",
            "phi_f",
            "phi_r",
            "dirty_leaf",
        }

        if not required.issubset(meta_data):
            self._corrupted = True
            raise RuntimeError(
                "meta.json is missing required fields"
            )

        if meta_data["state"] != "valid":
            self._corrupted = True
            raise RuntimeError(
                "TreeCache state is not valid"
            )

        leaf_num = meta_data["leaf_num"]
        phi_f = meta_data["phi_f"]
        phi_r = meta_data["phi_r"]

        raw_dirty_leaf = meta_data["dirty_leaf"]

        if (
            isinstance(leaf_num, bool)
            or not isinstance(leaf_num, int)
            or leaf_num < 0
        ):
            self._corrupted = True
            raise RuntimeError(
                "leaf_num is not a valid non-negative integer"
            )

        if not isinstance(phi_f, dict):
            self._corrupted = True
            raise RuntimeError(
                "phi_f is not a JSON object"
            )

        if not isinstance(phi_r, dict):
            self._corrupted = True
            raise RuntimeError(
                "phi_r is not a JSON object"
            )

        if not isinstance(raw_dirty_leaf, list):
            self._corrupted = True
            raise RuntimeError(
                "dirty_leaf is not a JSON array"
            )

        for node_id in raw_dirty_leaf:
            if (
                isinstance(node_id, bool)
                or not isinstance(node_id, int)
            ):
                self._corrupted = True
                raise RuntimeError(
                    "dirty_leaf contains a non-integer node_id"
                )

        new_dirty_leaf: set[int] = set(raw_dirty_leaf)

        if leaf_num == 0:
            if raw_dirty_leaf:
                self._corrupted = True
                raise RuntimeError(
                    "dirty_leaf must be empty when leaf_num is zero"
                )
        else:
            min_leaf_id = leaf_num
            max_leaf_id = 2 * leaf_num - 1

            if any(
                node_id < min_leaf_id or node_id > max_leaf_id
                for node_id in raw_dirty_leaf
            ):
                self._corrupted = True
                raise RuntimeError(
                    "dirty_leaf contains a non-leaf node_id"
                )

        new_phi_f: dict[str, int] = {}
        new_phi_r: dict[int, str] = {}
        used_node_ids: set[int] = set()

        for token, node_id in phi_f.items():
            if not isinstance(token, str):
                self._corrupted = True
                raise RuntimeError(
                    "phi_f key is not a string"
                )

            if (
                isinstance(node_id, bool)
                or not isinstance(node_id, int)
                or node_id < 0
            ):
                self._corrupted = True
                raise RuntimeError(
                    "phi_f value is not a valid non-negative integer"
                )

            if node_id in used_node_ids:
                self._corrupted = True
                raise RuntimeError(
                    "phi_f contains duplicate node_id"
                )

            used_node_ids.add(node_id)
            new_phi_f[token] = node_id

        for raw_node_id, token in phi_r.items():
            try:
                node_id = int(raw_node_id)
            except (TypeError, ValueError) as e:
                self._corrupted = True
                raise RuntimeError(
                    "phi_r key is not a valid integer"
                ) from e

            if node_id < 0:
                self._corrupted = True
                raise RuntimeError(
                    "phi_r key is negative"
                )

            if not isinstance(token, str):
                self._corrupted = True
                raise RuntimeError(
                    "phi_r value is not a string"
                )

            if node_id in new_phi_r:
                self._corrupted = True
                raise RuntimeError(
                    "phi_r contains duplicate node_id"
                )

            new_phi_r[node_id] = token

        if len(new_phi_f) != len(new_phi_r):
            self._corrupted = True
            raise RuntimeError(
                "phi_f and phi_r have different lengths"
            )

        for token, node_id in new_phi_f.items():
            if new_phi_r.get(node_id) != token:
                self._corrupted = True
                raise RuntimeError(
                    "phi_f and phi_r are not mutual inverses"
                )

        expected_node_count = (
            0 if leaf_num == 0
            else 2 * leaf_num - 1
        )

        expected_node_ids = set(
            range(1, expected_node_count + 1)
        )

        actual_node_ids = set(new_phi_r)

        if actual_node_ids != expected_node_ids:
            self._corrupted = True
            raise RuntimeError(
                "node_id sequence is invalid: expected consecutive "
                f"integers from 1 to {expected_node_count}"
            )

        self.leaf_num = leaf_num
        self.phi_f = new_phi_f
        self.phi_r = new_phi_r
        self._dirty_leaf = new_dirty_leaf
        self._corrupted = False

        if self.leaf_num > 0:
            root_this_exists = self.root_this_path.exists()
            root_nxt_exists = self.root_nxt_path.exists()

            if root_this_exists and root_nxt_exists:
                tmp_pack_buf = mpmt.RvectorPack(
                    ell=self._ell_root
                )(self._bf_size)

                self.root_node.this_share.load(
                    str(self.root_this_path),
                    tmp_pack_buf
                )

                self.root_node.nxt_share.load(
                    str(self.root_nxt_path),
                    tmp_pack_buf
                )
            else:
                root_token = self.phi_r[1]
                ell_root_1_this_path = storage_path / f"{root_token}_this.mpmtrvp"
                ell_root_1_nxt_path = storage_path / f"{root_token}_nxt.mpmtrvp"

                ell_root_1_this_exists = ell_root_1_this_path.exists()
                ell_root_1_nxt_exists = ell_root_1_nxt_path.exists()

                if ell_root_1_this_exists and ell_root_1_nxt_exists:
                    ell_root_1 = mpmt.ShrRep3ShareVec(
                        ell=1
                    )(self._bf_size)

                    pack_buf_ell_1 = mpmt.RvectorPack(
                        ell=1
                    )(self._bf_size)

                    pack_buf_ell_root = mpmt.RvectorPack(
                        ell=self._ell_root
                    )(self._bf_size)

                    ell_root_1.this_share.load(
                        str(ell_root_1_this_path),
                        pack_buf_ell_1
                    )

                    ell_root_1.nxt_share.load(
                        str(ell_root_1_nxt_path),
                        pack_buf_ell_1
                    )

                    self._ring_conv(
                        sv=ell_root_1,
                        sv_out=self.root_node,
                        ell_to=self._ell_root
                    )

                    self.root_node.this_share.save(
                        str(self.root_this_path),
                        pack_buf_ell_root
                    )

                    self.root_node.nxt_share.save(
                        str(self.root_nxt_path),
                        pack_buf_ell_root
                    )
                else:
                    if self.leaf_num == 1:
                        self._corrupted = True
                        raise RuntimeError(
                            "root cache and the only leaf cache are incomplete"
                        )

                    self._dirty_leaf.update(
                        range(self.leaf_num, 2 * self.leaf_num)
                    )

                
    def _mark_corrupted(self) -> None:
        self._corrupted = True
        meta_data = {
            "state": "dirty",
            "bf_size": self._bf_size,
            "ell_root": self._ell_root,
            "leaf_num": self.leaf_num,
            "phi_f": self.phi_f,
            "phi_r": {
                str(node_id): token
                for node_id, token in self.phi_r.items()
            },
            "dirty_leaf": sorted(self._dirty_leaf),
        }

        try:
            with open(self.meta_path, "w", encoding="utf-8") as f:
                json.dump(
                    meta_data,
                    f,
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
        except Exception:
            raise

    def _mark_valid(self) -> None:
        meta_data = {
            "state": "valid",
            "bf_size": self._bf_size,
            "ell_root": self._ell_root,
            "leaf_num": self.leaf_num,
            "phi_f": self.phi_f,
            "phi_r": {
                str(node_id): token
                for node_id, token in self.phi_r.items()
            },
            "dirty_leaf": sorted(self._dirty_leaf),
        }

        try:
            with open(self.meta_path, "w", encoding="utf-8") as f:
                json.dump(
                    meta_data,
                    f,
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
        except Exception:
            raise

        self._corrupted = False

    def _new_token(self):
        while (token := secrets.token_hex(16)) in self.phi_f:
            pass

        return token


    def has_inserted(
        self, *,
        token: str,
    ) -> bool:
        node_id = self.phi_f.get(token)
        return (
            node_id is not None
            and node_id >= self.leaf_num
        )

    def insert(
        self, *,
        node
    ) -> str:        
        if self._corrupted:
            raise RuntimeError(
                "TreeCache is corrupted and cannot be used"
            )

        self._mark_corrupted()

        aux_buf = mpmt.RvectorPack(ell=1)(self._bf_size)

        token = self._new_token()
        this_path = Path(
            self.storage_dir,
            f"{token}_this.mpmtrvp"
        )
        nxt_path = Path(
            self.storage_dir,
            f"{token}_nxt.mpmtrvp"
        )

        try:
            node.this_share.save(
                str(this_path),
                aux_buf
            )
            node.nxt_share.save(
                str(nxt_path),
                aux_buf
            )

        except Exception:
            try:
                this_path.unlink(missing_ok=True)
            except OSError:
                pass

            try:
                nxt_path.unlink(missing_ok=True)
            except OSError:
                pass

            raise

        if self.leaf_num == 0:
            self.leaf_num = 1
            self.phi_r[1] = token
            self.phi_f[token] = 1

        else:
            old_leaf_id = self.leaf_num

            lc_id = old_leaf_id * 2
            rc_id = lc_id + 1

            ori_root_token = self.phi_r[old_leaf_id]

            self.phi_r[rc_id] = token
            self.phi_f[token] = rc_id

            new_root_token = self._new_token()
            self.phi_r[old_leaf_id] = new_root_token
            self.phi_f[new_root_token] = old_leaf_id

            self.phi_r[lc_id] = ori_root_token
            self.phi_f[ori_root_token] = lc_id

            if old_leaf_id in self._dirty_leaf:
                self._dirty_leaf.remove(old_leaf_id)
                self._dirty_leaf.add(lc_id)

            self.leaf_num += 1

        self._dirty_leaf.add(self.phi_f[token])

        self._mark_valid()

        return token
    
    def update(
        self, *,
        token,
        new_node
    ):
        if self._corrupted:
            raise RuntimeError(
                "TreeCache is corrupted and cannot be used"
            )
        
        node_id = self.phi_f.get(token)

        if node_id is None:
            raise KeyError(f"Unknown token: {token!r}")

        if node_id < self.leaf_num:
            raise ValueError(f"Token {token!r} is an internal node")

        self._mark_corrupted()

        aux_buf = mpmt.RvectorPack(ell=1)(self._bf_size)

        this_path = os.path.join(self.storage_dir, f"{token}_this.mpmtrvp")
        nxt_path = os.path.join(self.storage_dir, f"{token}_nxt.mpmtrvp")
        new_node.this_share.save(this_path, aux_buf)
        new_node.nxt_share.save(nxt_path, aux_buf)

        self._dirty_leaf.add(node_id)

        self._mark_valid()

    def remove(
        self, *,
        del_token
    ):
        if self._corrupted:
            raise RuntimeError(
                "TreeCache is corrupted and cannot be used"
            )

        del_id = self.phi_f.get(del_token)

        if del_id is None:
            return

        old_leaf_num = self.leaf_num

        if del_id < old_leaf_num:
            raise ValueError(
                f"Token {del_token!r} is an internal node"
            )

        self._mark_corrupted()

        removed_internal_token = None

        if old_leaf_num == 1:
            del self.phi_f[del_token]
            del self.phi_r[1]

            self.leaf_num = 0
            self._dirty_leaf.clear()

        else:
            collapse_id = old_leaf_num - 1
            left_id = collapse_id * 2
            right_id = left_id + 1

            internal_token = self.phi_r[collapse_id]
            left_token = self.phi_r[left_id]
            right_token = self.phi_r[right_id]
            removed_internal_token = internal_token

            if del_id == left_id:
                promoted_token = right_token

            elif del_id == right_id:
                promoted_token = left_token

            else:
                self.phi_r[del_id] = right_token
                self.phi_f[right_token] = del_id
                promoted_token = left_token
                self._dirty_leaf.add(del_id)

            self.phi_r[collapse_id] = promoted_token
            self.phi_f[promoted_token] = collapse_id

            del self.phi_r[left_id]
            del self.phi_r[right_id]
            del self.phi_f[internal_token]
            del self.phi_f[del_token]

            self.leaf_num = old_leaf_num - 1

            self._dirty_leaf.discard(left_id)
            self._dirty_leaf.discard(right_id)
            self._dirty_leaf.add(collapse_id)

        this_path = Path(
            self.storage_dir,
            f"{del_token}_this.mpmtrvp"
        )
        nxt_path = Path(
            self.storage_dir,
            f"{del_token}_nxt.mpmtrvp"
        )

        this_path.unlink(missing_ok=True)
        nxt_path.unlink(missing_ok=True)

        if removed_internal_token is not None:
            internal_this_path = Path(
                self.storage_dir,
                f"{removed_internal_token}_this.mpmtrvp"
            )
            internal_nxt_path = Path(
                self.storage_dir,
                f"{removed_internal_token}_nxt.mpmtrvp"
            )

            internal_this_path.unlink(missing_ok=True)
            internal_nxt_path.unlink(missing_ok=True)

        self._mark_valid()

    def execute_merge(self):
        if self._corrupted:
            raise RuntimeError(
                "TreeCache is corrupted and cannot be used"
            )

        self._mark_corrupted()

        if self.leaf_num == 0:
            self._dirty_leaf.clear()
            self._mark_valid()
            return

        if self.leaf_num == 1:
            root_files_exist = (
                self.root_this_path.exists()
                and self.root_nxt_path.exists()
            )

            if self._dirty_leaf or not root_files_exist:
                leaf_token = self.phi_r[1]

                leaf_buf = mpmt.ShrRep3ShareVec(
                    ell=1
                )(self._bf_size)

                pack_buf_ell_1 = mpmt.RvectorPack(
                    ell=1
                )(self._bf_size)

                pack_buf_ell_root = mpmt.RvectorPack(
                    ell=self._ell_root
                )(self._bf_size)

                try:
                    leaf_buf.this_share.load(
                        os.path.join(
                            self.storage_dir,
                            f"{leaf_token}_this.mpmtrvp"
                        ),
                        pack_buf_ell_1
                    )

                    leaf_buf.nxt_share.load(
                        os.path.join(
                            self.storage_dir,
                            f"{leaf_token}_nxt.mpmtrvp"
                        ),
                        pack_buf_ell_1
                    )

                    self._ring_conv(
                        sv=leaf_buf,
                        sv_out=self.root_node,
                        ell_to=self._ell_root
                    )

                    self.root_node.this_share.save(
                        str(self.root_this_path),
                        pack_buf_ell_root
                    )

                    self.root_node.nxt_share.save(
                        str(self.root_nxt_path),
                        pack_buf_ell_root
                    )

                except Exception:
                    raise

            self._dirty_leaf.clear()

            self._mark_valid()
            return

        dirty_node: set[int] = set()

        for node in self._dirty_leaf:
            pnode = node // 2
            while pnode > 0:
                dirty_node.add(pnode)
                pnode //= 2

        self._dirty_leaf.clear()

        prefetch_buf = [
            mpmt.ShrRep3ShareVec(ell=1)(self._bf_size)
            for _ in range(3)
        ]

        pack_buf_ell_1 = mpmt.RvectorPack(
            ell=1
        )(self._bf_size)

        pack_buf_ell_root = mpmt.RvectorPack(
            ell=self._ell_root
        )(self._bf_size)

        try:
            for node in sorted(dirty_node, reverse=True):
                out_token = self.phi_r[node]
                out_buf_idx = 0

                lc_token = self.phi_r[node * 2]
                lc_buf_idx = 1

                rc_token = self.phi_r[node * 2 + 1]
                rc_buf_idx = 2

                prefetch_buf[lc_buf_idx].this_share.load(
                    os.path.join(
                        self.storage_dir,
                        f"{lc_token}_this.mpmtrvp"
                    ),
                    pack_buf_ell_1
                )

                prefetch_buf[lc_buf_idx].nxt_share.load(
                    os.path.join(
                        self.storage_dir,
                        f"{lc_token}_nxt.mpmtrvp"
                    ),
                    pack_buf_ell_1
                )

                prefetch_buf[rc_buf_idx].this_share.load(
                    os.path.join(
                        self.storage_dir,
                        f"{rc_token}_this.mpmtrvp"
                    ),
                    pack_buf_ell_1
                )

                prefetch_buf[rc_buf_idx].nxt_share.load(
                    os.path.join(
                        self.storage_dir,
                        f"{rc_token}_nxt.mpmtrvp"
                    ),
                    pack_buf_ell_1
                )

                self._merge(
                    sva=prefetch_buf[lc_buf_idx],
                    svb=prefetch_buf[rc_buf_idx],
                    svout=prefetch_buf[out_buf_idx]
                )

                if node == 1:
                    prefetch_buf[out_buf_idx].this_share.save(
                        os.path.join(
                            self.storage_dir,
                            f"{out_token}_this.mpmtrvp"
                        ),
                        pack_buf_ell_1
                    )

                    prefetch_buf[out_buf_idx].nxt_share.save(
                        os.path.join(
                            self.storage_dir,
                            f"{out_token}_nxt.mpmtrvp"
                        ),
                        pack_buf_ell_1
                    )

                    self._ring_conv(
                        sv=prefetch_buf[out_buf_idx],
                        sv_out=self.root_node,
                        ell_to=self._ell_root
                    )

                    self.root_node.this_share.save(
                        str(self.root_this_path),
                        pack_buf_ell_root
                    )

                    self.root_node.nxt_share.save(
                        str(self.root_nxt_path),
                        pack_buf_ell_root
                    )

                else:
                    prefetch_buf[out_buf_idx].this_share.save(
                        os.path.join(
                            self.storage_dir,
                            f"{out_token}_this.mpmtrvp"
                        ),
                        pack_buf_ell_1
                    )

                    prefetch_buf[out_buf_idx].nxt_share.save(
                        os.path.join(
                            self.storage_dir,
                            f"{out_token}_nxt.mpmtrvp"
                        ),
                        pack_buf_ell_1
                    )

        except Exception:
            raise

        self._mark_valid()