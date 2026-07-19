"""Unit tests for TreeCache — pure local, no MPC network.

Tests: insert (Phase 3), update, remove (quit), merge schedule ordering,
φ mapping correctness, edge cases, and churn (insert/update/remove cycles).
"""

import pytest
from mpmt.tree_cache import TreeCache, MergeStep, NOT_LOADED


# ——— Helpers ——————————————————————————————————————————————

def _mock_share(tag: str):
    """Return a distinguishable placeholder for a share."""
    return tag


def _parents_of(idx: int) -> list[int]:
    """Return the ancestor chain of *idx* up to (and including) root."""
    path = []
    while idx > 0:
        idx = (idx - 1) // 2
        path.append(idx)
    return path


# ===================================================================
#  MergeStep
# ===================================================================

class TestMergeStep:
    def test_dataclass(self):
        s = MergeStep(parent=5)
        assert s.parent == 5
        assert s == MergeStep(5)


# ===================================================================
#  Construction
# ===================================================================

class TestConstruction:
    def test_invalid_max_holders(self):
        with pytest.raises(ValueError):
            TreeCache(0)

    def test_minimal_tree(self):
        tc = TreeCache(max_holders=1)
        assert tc.max_holders == 1
        assert tc.max_nodes == 1  # root only
        assert tc.count == 0
        assert tc.root_share is None

    def test_height_for_4_holders(self):
        tc = TreeCache(max_holders=4)
        # h = ceil(log2(4)) + 1 = 3 → max_nodes = 7
        assert tc.height == 3
        assert tc.max_nodes == 7


# ===================================================================
#  Insert — Phase 3
# ===================================================================

class TestInsert:
    def test_first_holder_goes_to_root(self):
        tc = TreeCache(max_holders=4)
        tc.insert(b"tk1", _mock_share("B1"))
        assert tc.count == 1
        assert tc.index_of(b"tk1") == 0
        assert tc.share_at(0) == "B1"
        assert tc.get_merge_schedule() == []  # single node, clean

    def test_second_holder_splits_root(self):
        tc = TreeCache(max_holders=4)
        tc.insert(b"tk1", _mock_share("B1"))
        tc.insert(b"tk2", _mock_share("B2"))

        assert tc.count == 2
        # Root split: old→left child (1), new→right child (2), root→⊥
        assert tc.share_at(0) is None          # ⊥
        assert tc.share_at(1) == "B1"          # old
        assert tc.share_at(2) == "B2"          # new
        assert tc.index_of(b"tk1") == 1
        assert tc.index_of(b"tk2") == 2

    def test_four_holders_tree_structure(self):
        """Reproduce auxread.md example with N_D=4."""
        tc = TreeCache(max_holders=4)
        tc.insert(b"tk1", _mock_share("B1"))
        tc.insert(b"tk2", _mock_share("B2"))
        tc.insert(b"tk3", _mock_share("B3"))
        tc.insert(b"tk4", _mock_share("B4"))

        assert tc.count == 4

        # Leaves from auxread: [3]=tk1, [4]=tk3, [5]=tk2, [6]=tk4
        assert tc.index_of(b"tk1") == 3
        assert tc.index_of(b"tk2") == 5
        assert tc.index_of(b"tk3") == 4
        assert tc.index_of(b"tk4") == 6

        # Internal nodes are ⊥
        assert tc.share_at(0) is None
        assert tc.share_at(1) is None
        assert tc.share_at(2) is None

    def test_duplicate_token_raises(self):
        tc = TreeCache(max_holders=4)
        tc.insert(b"tk1", _mock_share("B1"))
        with pytest.raises(KeyError):
            tc.insert(b"tk1", _mock_share("B1_again"))

    def test_full_tree_raises(self):
        tc = TreeCache(max_holders=2)
        tc.insert(b"tk1", _mock_share("B1"))
        tc.insert(b"tk2", _mock_share("B2"))
        with pytest.raises(RuntimeError, match="full"):
            tc.insert(b"tk3", _mock_share("B3"))


# ===================================================================
#  Merge schedule
# ===================================================================

class TestMergeSchedule:
    def test_empty_tree_no_schedule(self):
        tc = TreeCache(max_holders=4)
        assert tc.get_merge_schedule() == []

    def test_single_holder_no_schedule(self):
        tc = TreeCache(max_holders=4)
        tc.insert(b"tk1", _mock_share("B1"))
        assert tc.get_merge_schedule() == []

    def test_two_holders_one_merge(self):
        tc = TreeCache(max_holders=4)
        tc.insert(b"tk1", _mock_share("B1"))
        tc.insert(b"tk2", _mock_share("B2"))

        schedule = tc.get_merge_schedule()
        assert len(schedule) == 1
        assert schedule[0].parent == 0  # root needs merge(1, 2)

    def test_four_holders_bottom_up_order(self):
        tc = TreeCache(max_holders=4)
        for i in range(1, 5):
            tc.insert(f"tk{i}".encode(), _mock_share(f"B{i}"))

        schedule = tc.get_merge_schedule()
        parents = [s.parent for s in schedule]

        # Bottom-up: layer-2 nodes first, then layer-1, then root.
        # Nodes 1 and 2 are at layer 1 (parents of 3,4 and 5,6).
        # Node 0 is root (layer 0).
        # Layer check: indices of node 1,2 MUST appear before node 0.
        idx1 = parents.index(1)
        idx2 = parents.index(2)
        idx0 = parents.index(0)
        assert idx1 < idx0
        assert idx2 < idx0
        assert len(schedule) == 3  # 0, 1, 2

    def test_schedule_is_idempotent(self):
        tc = TreeCache(max_holders=4)
        tc.insert(b"tk1", _mock_share("B1"))
        tc.insert(b"tk2", _mock_share("B2"))
        s1 = tc.get_merge_schedule()
        s2 = tc.get_merge_schedule()
        assert s1 == s2


# ===================================================================
#  Update
# ===================================================================

class TestUpdate:
    def test_update_marks_path_dirty(self):
        tc = TreeCache(max_holders=4)
        tc.insert(b"tk1", _mock_share("B1"))
        tc.insert(b"tk2", _mock_share("B2"))

        # Manually "merge" node 0 to clean the tree first.
        tc._buf[0] = _mock_share("B12")
        assert tc.get_merge_schedule() == []

        # Now update tk1 (at index 1)
        tc.update(b"tk1", _mock_share("B1_new"))

        assert tc.share_at(1) == "B1_new"
        # Path 1→0: node 0 should be ⊥
        assert tc.share_at(0) is None
        schedule = tc.get_merge_schedule()
        assert MergeStep(parent=0) in schedule

    def test_update_unknown_token_raises(self):
        tc = TreeCache(max_holders=4)
        tc.insert(b"tk1", _mock_share("B1"))
        with pytest.raises(KeyError):
            tc.update(b"tk_unknown", _mock_share("X"))


# ===================================================================
#  Remove / Quit
# ===================================================================

class TestRemove:
    def test_remove_simple(self):
        """Remove the only holder → empty tree."""
        tc = TreeCache(max_holders=4)
        tc.insert(b"tk1", _mock_share("B1"))
        tc.remove(b"tk1")
        assert tc.count == 0
        assert tc.index_of(b"tk1") is None
        assert tc.leaf_indices() == []

    def test_remove_last_leaf_direct(self):
        """Removing the last-indexed holder directly (no swap needed)."""
        tc = TreeCache(max_holders=4)
        tc.insert(b"tk1", _mock_share("B1"))
        tc.insert(b"tk2", _mock_share("B2"))
        # tk2 is at index 2 (the last leaf)
        tc.remove(b"tk2")
        assert tc.count == 1
        assert tc.index_of(b"tk1") == 1  # tk1 still at index 1 (moved from 0)
        # Wait, tk1 was at index 0 originally, then split: now at index 1
        # tk2 was at index 2 (last). After removal:
        # buf[2] = None, phi_inv has only tk1 at index 1
        assert tc.index_of(b"tk2") is None
        # Path 2→0 now ⊥
        assert tc.share_at(0) is None

    def test_remove_with_swap(self):
        """Remove a non-last holder → triggers heap-style swap."""
        tc = TreeCache(max_holders=4)
        tc.insert(b"tk1", _mock_share("B1"))
        tc.insert(b"tk2", _mock_share("B2"))
        tc.insert(b"tk3", _mock_share("B3"))

        # Leaves: tk1 at 3, tk3 at 4, tk2 at ? Wait, need to trace.
        # i=1: buf[0]=B1  (tk1 at 0)
        # i=2: c=0, buf[1]=B1, buf[2]=B2, buf[0]=⊥  (tk1 at 1, tk2 at 2)
        # i=3: c=1, buf[3]=B1, buf[4]=B3, buf[1]=⊥  (tk1 at 3, tk2 at 2, tk3 at 4)
        # count=3
        # Last leaf: max(phi_inv) = max(2, 3, 4) = 4

        last_idx = max(tc._phi_inv.keys())
        assert last_idx == 4  # tk3 is last

        # Remove tk2 (at index 2, not last)
        tc.remove(b"tk2")

        assert tc.count == 2
        # tk3 should have moved to index 2
        assert tc.index_of(b"tk3") == 2
        assert tc.share_at(2) == "B3"
        # Old tk3 position (4) should be gone
        assert tc.share_at(4) is None
        # tk2 should be gone
        assert tc.index_of(b"tk2") is None
        # Both paths (2→0 and 4→0) are ⊥
        schedule = tc.get_merge_schedule()
        parents = {s.parent for s in schedule}
        assert 0 in parents  # root dirty
        assert 1 in parents  # parent of 3,4 dirty

    def test_remove_unknown_token_raises(self):
        tc = TreeCache(max_holders=4)
        tc.insert(b"tk1", _mock_share("B1"))
        with pytest.raises(KeyError):
            tc.remove(b"tk_unknown")


# ===================================================================
#  NOT_LOADED state
# ===================================================================

class TestNotLoaded:
    def test_not_loaded_is_distinct_from_none(self):
        assert NOT_LOADED is not None

    def test_not_loaded_still_clean(self):
        """NOT_LOADED is distinct from None — it is clean, on disk.

        A node whose child is NOT_LOADED is still mergeable; the merge
        executor is responsible for loading from disk before computing.
        """
        tc = TreeCache(max_holders=4)
        tc.insert(b"tk1", _mock_share("B1"))
        tc.insert(b"tk2", _mock_share("B2"))

        # Simulate eviction: node 1 is clean but on disk.
        tc._buf[1] = NOT_LOADED
        assert tc.share_at(1) is NOT_LOADED

        # Node 0 IS in schedule — NOT_LOADED ≠ ⊥.
        schedule = tc.get_merge_schedule()
        assert MergeStep(parent=0) in schedule

    def test_dirty_one_child_pass_through(self):
        """When one child is ⊥ and the other clean, parent still merges.

        This is the pass-through case — common after quit, where a parent
        lost one child and simply propagates the surviving child's value.
        """
        tc = TreeCache(max_holders=4)
        tc.insert(b"tk1", _mock_share("B1"))
        tc.insert(b"tk2", _mock_share("B2"))

        # Mark left child dirty → parent still mergeable via right child
        tc._buf[1] = None
        schedule = tc.get_merge_schedule()
        assert MergeStep(parent=0) in schedule  # pass-through from node 2

    def test_both_children_none_no_merge(self):
        """A ⊥ parent whose BOTH children are ⊥ and not scheduled
        cannot be merged yet."""
        tc = TreeCache(max_holders=4)
        tc.insert(b"tk1", _mock_share("B1"))
        tc.insert(b"tk2", _mock_share("B2"))

        # Both children ⊥ — no data available
        tc._buf[1] = None
        tc._buf[2] = None
        schedule = tc.get_merge_schedule()
        assert schedule == []


# ===================================================================
#  Edge cases
# ===================================================================

class TestEdgeCases:
    def test_max_holders_one(self):
        tc = TreeCache(max_holders=1)
        tc.insert(b"tk1", _mock_share("B1"))
        assert tc.count == 1
        assert tc.root_share == "B1"
        with pytest.raises(RuntimeError):
            tc.insert(b"tk2", _mock_share("B2"))

    def test_token_across_insert_and_remove(self):
        """Same token can't be re-used after quit → insert is fine."""
        tc = TreeCache(max_holders=4)
        tc.insert(b"tk1", _mock_share("B1"))
        tc.remove(b"tk1")
        # Re-insert same token should work (token identity is flat)
        tc.insert(b"tk1", _mock_share("B1_v2"))
        assert tc.count == 1

    def test_many_holders(self):
        """Insert 16 holders — basic sanity."""
        tc = TreeCache(max_holders=32)
        for i in range(16):
            tc.insert(f"tk{i:02d}".encode(), _mock_share(f"B{i}"))
        assert tc.count == 16
        schedule = tc.get_merge_schedule()
        # After Phase 3, all internal nodes are ⊥
        assert len(schedule) == 15  # 16 leaves → 15 internal nodes


# ===================================================================
#  Churn  (insert / update / remove cycle)
# ===================================================================

class TestChurn:
    def test_insert_remove_insert_cycle(self):
        """Repeated insert→remove→insert without merge in between."""
        tc = TreeCache(max_holders=8)
        tc.insert(b"tk1", _mock_share("B1"))
        tc.insert(b"tk2", _mock_share("B2"))
        tc.insert(b"tk3", _mock_share("B3"))

        tc.remove(b"tk2")     # heap-swap
        tc.insert(b"tk4", _mock_share("B4"))  # Phase 3 with c=count-1

        assert tc.count == 3
        # Verify all tokens map to distinct nodes
        indices = {tc.index_of(b"tk1"), tc.index_of(b"tk3"), tc.index_of(b"tk4")}
        assert len(indices) == 3
        assert None not in indices

    def test_full_cycle_merge_update_remove(self):
        """Insert → merge → update → merge → remove → merge."""
        tc = TreeCache(max_holders=4)

        # Insert 3 holders
        tc.insert(b"tk1", _mock_share("B1"))
        tc.insert(b"tk2", _mock_share("B2"))
        tc.insert(b"tk3", _mock_share("B3"))

        # Merge all
        schedule = tc.get_merge_schedule()
        for s in schedule:
            left = 2 * s.parent + 1
            right = 2 * s.parent + 2
            left_share = tc.share_at(left)
            right_share = tc.share_at(right)
            if left_share is not None and right_share is not None:
                tc._buf[s.parent] = f"M({left_share},{right_share})"
            elif left_share is not None:
                tc._buf[s.parent] = left_share
            else:
                tc._buf[s.parent] = right_share

        assert tc.get_merge_schedule() == []
        assert tc.root_share is not None

        # Update one holder
        tc.update(b"tk1", _mock_share("B1_new"))
        assert len(tc.get_merge_schedule()) > 0  # path dirty

        # Merge again
        schedule = tc.get_merge_schedule()
        for s in schedule:
            left = 2 * s.parent + 1
            right = 2 * s.parent + 2
            left_share = tc.share_at(left)
            right_share = tc.share_at(right)
            if left_share is not None and right_share is not None:
                tc._buf[s.parent] = f"M({left_share},{right_share})"
            elif left_share is not None:
                tc._buf[s.parent] = left_share
            else:
                tc._buf[s.parent] = right_share

        assert tc.get_merge_schedule() == []  # clean again

        # Remove
        tc.remove(b"tk2")
        assert tc.count == 2
        assert len(tc.get_merge_schedule()) > 0  # affected paths dirty

    def test_stress_churn(self):
        """Random-ish insert/update/remove for many iterations."""
        tc = TreeCache(max_holders=32)
        active = []

        for i in range(50):
            token = f"tk{i:02d}".encode()
            tc.insert(token, _mock_share(f"B{i}"))
            active.append(token)

            if len(active) > 4:
                # Remove a random holder
                to_remove = active.pop(2)  # remove middle
                tc.remove(to_remove)

            if len(active) > 2:
                # Update a holder
                tc.update(active[0], _mock_share(f"B{active[0].decode()}_v{i}"))

        # All active tokens should have valid indices
        for t in active:
            idx = tc.index_of(t)
            assert idx is not None
            assert tc.share_at(idx) is not None

        assert tc.count == len(active)
