"""Direct _TreeCache semantics — the last-leaf → no-tree path (no network).

Inserts mark the tree dirty and persist leaf files; removing the last leaf
drives leaf_num back to 0; execute_merge() then publishes a canonical no-tree
state: in-memory root zeroed, root files removed, meta valid.  The 3-party
merge path (leaf_num >= 2) is covered by test_setholder via sync_cache().
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

_t = os.path.dirname(os.path.abspath(__file__))
while _t and not os.path.isdir(os.path.join(_t, "common")):
    _t = os.path.dirname(_t)
sys.path.insert(0, _t)

import mpmt
from mpmt._tree_cache import _TreeCache
from common.assertions import Harness


def _dummy_merge(sva, svb, svout):
    raise AssertionError("merge_fn should not be called (no dirty 2+ leaf merge here)")


def _dummy_ring(sv, sv_out, ell_to):
    raise AssertionError("ring_conv_fn should not be called")


def main() -> int:
    harness = Harness()
    bf_size, _, _, ell_root = mpmt.bf_param(1024, 1.0, -3)
    storage = tempfile.mkdtemp(prefix="tc_unit_")
    tc = _TreeCache(storage_dir=storage, bf_size=bf_size, ell_root=ell_root,
                    merge_fn=_dummy_merge, ring_conv_fn=_dummy_ring)

    harness.check("fresh leaf_num=0", tc.leaf_num == 0)
    harness.check("fresh root files absent",
                  not Path(tc.root_this_path).exists()
                  and not Path(tc.root_nxt_path).exists())

    harness.section("insert one leaf")
    sv = mpmt.ShrRep3ShareVec(1)(bf_size)
    token = tc.insert(node=sv)
    harness.check("insert returns a token", isinstance(token, str) and len(token) > 0)
    harness.check("leaf_num=1 after insert", tc.leaf_num == 1)
    harness.check("insert writes leaf files",
                  Path(storage, f"{token}_this.mpmtrvp").exists()
                  and Path(storage, f"{token}_nxt.mpmtrvp").exists())
    harness.check("insert marks the leaf dirty", set(tc._dirty_leaf) == {1})
    meta = json.loads(Path(tc.meta_path).read_text())
    harness.check("meta state=valid after insert", meta.get("state") == "valid",
                  str(meta.get("state")))

    harness.section("remove the last leaf")
    tc.remove(del_token=token)
    harness.check("leaf_num=0 after last remove", tc.leaf_num == 0)
    harness.check("phi empty after last remove", not tc.phi_r and not tc.phi_f)
    harness.check("leaf files removed",
                  not Path(storage, f"{token}_this.mpmtrvp").exists())

    harness.section("execute_merge on the empty tree (canonical no-tree)")
    tc.execute_merge()
    harness.check("empty merge: dirty cleared", not tc._dirty_leaf)
    harness.check("empty merge: root files absent",
                  not Path(tc.root_this_path).exists()
                  and not Path(tc.root_nxt_path).exists())
    meta = json.loads(Path(tc.meta_path).read_text())
    harness.check("empty merge: meta state=valid", meta.get("state") == "valid",
                  str(meta.get("state")))
    harness.check("empty merge: meta leaf_num=0", meta.get("leaf_num") == 0)
    harness.check("empty merge: in-memory root zeroed",
                  all(int(tc.root_node.this_share[i]) == 0 for i in range(bf_size))
                  and all(int(tc.root_node.nxt_share[i]) == 0 for i in range(bf_size)))

    return harness.result()


if __name__ == "__main__":
    raise SystemExit(main())
