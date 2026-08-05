import json
import os

import mpmt
import secrets
from pathlib import Path

class TreeCache:
    def __init__(
        self,
        *,
        storage_dir: str,
        bf_size: int,
        prefetch_num: int,
    ):
        storage_path = Path(storage_dir)
        storage_path.mkdir(parents=True, exist_ok=True)

        self.storage_dir = str(storage_path)
        self.meta_path = storage_path / "meta.json"
        self.corrupted = False
        self.phi_f: dict[str, int] = {}
        self.phi_r: dict[int, str] = {}
        self.leaf_num = 0

        if not self.meta_path.exists():
            self.prefetch_buf = [
                mpmt.Rvector(ell=1)(bf_size)
                for _ in range(prefetch_num)
            ]
            return

        try:
            with self.meta_path.open("r", encoding="utf-8") as f:
                meta_data = json.load(f)
        except Exception as e:
            self.corrupted = True
            raise RuntimeError(
                "meta.json is malformed or corrupt"
            ) from e

        if not isinstance(meta_data, dict):
            self.corrupted = True
            raise RuntimeError(
                "meta.json is not a JSON object"
            )

        required = {
            "state",
            "leaf_num",
            "phi_f",
            "phi_r",
        }

        if not required.issubset(meta_data):
            self.corrupted = True
            raise RuntimeError(
                "meta.json is missing required fields"
            )

        if meta_data["state"] != "valid":
            self.corrupted = True
            raise RuntimeError(
                "TreeCache state is not valid"
            )

        leaf_num = meta_data["leaf_num"]
        phi_f = meta_data["phi_f"]
        phi_r = meta_data["phi_r"]

        if (
            isinstance(leaf_num, bool)
            or not isinstance(leaf_num, int)
            or leaf_num < 0
        ):
            self.corrupted = True
            raise RuntimeError(
                "leaf_num is not a valid non-negative integer"
            )

        if not isinstance(phi_f, dict):
            self.corrupted = True
            raise RuntimeError(
                "phi_f is not a JSON object"
            )

        if not isinstance(phi_r, dict):
            self.corrupted = True
            raise RuntimeError(
                "phi_r is not a JSON object"
            )

        new_phi_f: dict[str, int] = {}
        new_phi_r: dict[int, str] = {}
        used_node_ids: set[int] = set()

        for token, node_id in phi_f.items():
            if not isinstance(token, str):
                self.corrupted = True
                raise RuntimeError(
                    "phi_f key is not a string"
                )

            if (
                isinstance(node_id, bool)
                or not isinstance(node_id, int)
                or node_id < 0
            ):
                self.corrupted = True
                raise RuntimeError(
                    "phi_f value is not a valid non-negative integer"
                )

            if node_id in used_node_ids:
                self.corrupted = True
                raise RuntimeError(
                    "phi_f contains duplicate node_id"
                )

            used_node_ids.add(node_id)
            new_phi_f[token] = node_id

        for raw_node_id, token in phi_r.items():
            try:
                node_id = int(raw_node_id)
            except (TypeError, ValueError) as e:
                self.corrupted = True
                raise RuntimeError(
                    "phi_r key is not a valid integer"
                ) from e

            if node_id < 0:
                self.corrupted = True
                raise RuntimeError(
                    "phi_r key is negative"
                )

            if not isinstance(token, str):
                self.corrupted = True
                raise RuntimeError(
                    "phi_r value is not a string"
                )

            if node_id in new_phi_r:
                self.corrupted = True
                raise RuntimeError(
                    "phi_r contains duplicate node_id"
                )

            new_phi_r[node_id] = token

        if len(new_phi_f) != len(new_phi_r):
            self.corrupted = True
            raise RuntimeError(
                "phi_f and phi_r have different lengths"
            )

        for token, node_id in new_phi_f.items():
            if new_phi_r.get(node_id) != token:
                self.corrupted = True
                raise RuntimeError(
                    "phi_f and phi_r are not mutual inverses"
                )

        expected_node_count = 0 if leaf_num == 0 else 2 * leaf_num - 1
        expected_node_ids = set(range(1, expected_node_count + 1))
        actual_node_ids = set(new_phi_r)

        if actual_node_ids != expected_node_ids:
            self.corrupted = True
            raise RuntimeError(
                "node_id sequence is invalid: expected consecutive "
                f"integers from 1 to {expected_node_count}"
            )

        self.leaf_num = leaf_num
        self.phi_f = new_phi_f
        self.phi_r = new_phi_r
        self.corrupted = False

        self.prefetch_buf = [
            mpmt.Rvector(ell=1)(bf_size)
            for _ in range(prefetch_num)
        ]

    def _mark_corrupted(self) -> None:
        self.corrupted = True
        meta_data = {
            "state": "dirty",
            "leaf_num": self.leaf_num,
            "phi_f": self.phi_f,
            "phi_r": {
                str(node_id): token
                for node_id, token in self.phi_r.items()
            },
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
            "leaf_num": self.leaf_num,
            "phi_f": self.phi_f,
            "phi_r": {
                str(node_id): token
                for node_id, token in self.phi_r.items()
            },
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

        self.corrupted = False

    def _new_token(self):
        while (token := secrets.token_hex(16)) in self.phi_f:
            pass

        return token

    def insert(
        self, *,
        node,
        aux_packbuf
    )->str:
        if self.corrupted:
            raise RuntimeError(
                "TreeCache is corrupted and cannot be used"
            )

        self._mark_corrupted()
        
        token = self._new_token()
        this_path = Path(self.storage_dir, f"{token}_this.mpmtrvp")
        nxt_path = Path(self.storage_dir, f"{token}_nxt.mpmtrvp")

        try:
            node.this_share.save(str(this_path), aux_packbuf)
            node.nxt_share.save(str(nxt_path),aux_packbuf)

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
            self.leaf_num += 1
            self.phi_r[self.leaf_num] = token
            self.phi_f[token] = self.leaf_num
        else:
            lc_id = self.leaf_num * 2         
            rc_id = lc_id + 1
            ori_root_token = self.phi_r[self.leaf_num]
            self.phi_r[rc_id] = token
            self.phi_f[token] = rc_id
            new_root_token = self._new_token()
            self.phi_r[self.leaf_num] = new_root_token
            self.phi_f[new_root_token] = self.leaf_num
            self.phi_r[lc_id] = ori_root_token
            self.phi_f[ori_root_token] = lc_id
            self.leaf_num += 1

        self._mark_valid()

        return token
    
    
    def update(
        self, *,
        token,
        new_node,
        aux_packbuf
    ):
        if self.corrupted:
            raise RuntimeError(
                "TreeCache is corrupted and cannot be used"
            )
        
        self._mark_corrupted()
        node_id = self.phi_f.get(token)

        if node_id is None:
            self._mark_valid()
            raise KeyError(f"Unknown token: {token!r}")

        if node_id < self.leaf_num:
            self._mark_valid()
            raise ValueError(f"Token {token!r} is an internal node")

        this_path = os.path.join(self.storage_dir, f"{token}_this.mpmtrvp")
        nxt_path = os.path.join(self.storage_dir, f"{token}_nxt.mpmtrvp")
        new_node.this_share.save(this_path, aux_packbuf)
        new_node.nxt_share.save(nxt_path, aux_packbuf)
        self._mark_valid()


    def remove(            
        self, *,
        del_token
    ):
        if self.corrupted:
            raise RuntimeError(
                "TreeCache is corrupted and cannot be used"
            )

        self._mark_corrupted()
        del_id = self.phi_f.get(del_token)

        if del_id is None:
            self._mark_valid()
            return
        
        old_leaf_num = self.leaf_num

        if del_id < old_leaf_num:
            self._mark_valid()
            raise ValueError(
                f"Token {del_token!r} is an internal node"
            )

        if old_leaf_num == 1:
            del self.phi_f[del_token]
            del self.phi_r[1]

            self.leaf_num = 0
        else:
            collapse_id = old_leaf_num - 1
            left_id = collapse_id * 2
            right_id = left_id + 1

            internal_token = self.phi_r[collapse_id]
            left_token = self.phi_r[left_id]
            right_token = self.phi_r[right_id]

            if del_id == left_id:
                promoted_token = right_token

            elif del_id == right_id:
                promoted_token = left_token

            else:
                self.phi_r[del_id] = right_token
                self.phi_f[right_token] = del_id
                promoted_token = left_token

            self.phi_r[collapse_id] = promoted_token
            self.phi_f[promoted_token] = collapse_id

            del self.phi_r[left_id]
            del self.phi_r[right_id]
            del self.phi_f[internal_token]
            del self.phi_f[del_token]
            self.leaf_num = old_leaf_num - 1

        this_path = Path(self.storage_dir, f"{del_token}_this.mpmtrvp")
        nxt_path = Path(self.storage_dir, f"{del_token}_nxt.mpmtrvp")

        this_path.unlink(missing_ok=True)
        nxt_path.unlink(missing_ok=True)

        self._mark_valid()