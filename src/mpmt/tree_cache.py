"""Tree cache — complete binary tree for RSS3 BF-share aggregation.

φ bijection maps token ↔ node index.  ``_buf[i]`` ∈ {⊥, NOT_LOADED, ShareVec}.
Insert uses top-down splitting; remove uses heap-style deletion.
``get_merge_schedule`` returns a bottom-up ordered merge plan.

@author  mincy
@ref     Based on the binary-tree aggregation design, heap-style removal
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional

# Sentinel: share is clean but currently on disk (not in memory).
NOT_LOADED = object()


@dataclass(frozen=True)
class MergeStep:
    """One merge instruction.

    The parent node needs to be recomputed from its two children.
    Left child  = ``2 * parent + 1``
    Right child = ``2 * parent + 2``

    When only one child is available (after a quit created a structural
    hole), the merge executor simply copies the surviving child's value
    into *parent* (pass-through).
    """
    parent: int


class TreeCache:
    """Complete-binary-tree cache for RSS3 Bloom-filter shares.

    Parameters
    ----------
    max_holders : int
        Maximum number of set holders this tree can accommodate.
        Determines the tree height ``h = ⌈log₂(max_holders)⌉ + 1``
        and the maximum node count ``2ʰ − 1``.
    """

    def __init__(self, max_holders: int):
        if max_holders < 1:
            raise ValueError("max_holders must be >= 1")

        self._max_holders = max_holders
        self._h = math.ceil(math.log2(max_holders)) + 1
        self._max_nodes = (1 << self._h) - 1

        # Tree storage — see module docstring for the three states.
        self._buf: list[Optional[object]] = [None] * self._max_nodes

        # Bijection φ : token ↔ node index.
        # Only leaf nodes (holders) are tracked here.
        self._phi: dict[bytes, int] = {}
        self._phi_inv: dict[int, bytes] = {}

        # Number of *active* holders currently in the tree.
        self._count: int = 0

    # ------------------------------------------------------------------
    #  Read-only properties
    # ------------------------------------------------------------------

    @property
    def max_holders(self) -> int:
        """Maximum number of holders this tree can contain."""
        return self._max_holders

    @property
    def height(self) -> int:
        """Tree height (root = layer 0, deepest leaves = layer h-1)."""
        return self._h

    @property
    def max_nodes(self) -> int:
        """Total number of array slots (indices 0 … max_nodes-1)."""
        return self._max_nodes

    @property
    def count(self) -> int:
        """Current number of active holders in the tree."""
        return self._count

    @property
    def root_share(self):
        """The root share B(∪Xᵢ), or ``None`` if not yet computed."""
        return self._buf[0] if self._buf[0] is not None else None

    # ------------------------------------------------------------------
    #  φ mapping
    # ------------------------------------------------------------------

    def token_of(self, idx: int) -> Optional[bytes]:
        """Return the token stored at node *idx*, or ``None``."""
        return self._phi_inv.get(idx)

    def index_of(self, token: bytes) -> Optional[int]:
        """Return the node index for *token*, or ``None``."""
        return self._phi.get(token)

    def share_at(self, idx: int):
        """Return the share at node *idx*.

        May be ``None`` (⊥ / dirty), ``NOT_LOADED`` (clean, on disk),
        or a ``ShrRep3ShareVec`` (clean, in memory).
        """
        if idx < 0 or idx >= self._max_nodes:
            raise IndexError(f"index {idx} out of range [0, {self._max_nodes})")
        return self._buf[idx]

    # ------------------------------------------------------------------
    #  Insert  (Phase 3: top-down splitting   c = count − 1)
    # ------------------------------------------------------------------

    def insert(self, token: bytes, share) -> None:
        """Insert the next holder's share using Phase 3 top-down splitting.

        For the *k*-th holder (k ≥ 2), the node ``c = count − 1`` is
        split: its current content moves to the left child (2c+1), and
        the new share is placed at the right child (2c+2).  The parent
        *c* is then marked ⊥ and will be recomputed during the next
        aggregate.

        Parameters
        ----------
        token : bytes
            Holder's unique 16-byte identifier.
        share :
            RSS3 share (``ShrRep3ShareVec``) of the holder's Bloom filter.

        Raises
        ------
        RuntimeError
            If the tree is already full.
        KeyError
            If *token* is already present.
        """
        if self._count >= self._max_holders:
            raise RuntimeError(
                f"TreeCache is full ({self._max_holders} holders max)"
            )
        if token in self._phi:
            raise KeyError(f"token {token.hex()} already in tree")

        if self._count == 0:
            # First holder — place at root.
            self._buf[0] = share
            self._phi[token] = 0
            self._phi_inv[0] = token
            self._count = 1
            return

        # Phase 3 split: c = count − 1
        c = self._count - 1
        left = 2 * c + 1
        right = 2 * c + 2

        # Move old content to left child.
        self._buf[left] = self._buf[c]
        old_token = self._phi_inv.pop(c, None)
        if old_token is not None:
            self._phi[old_token] = left
            self._phi_inv[left] = old_token

        # Place new share at right child.
        self._buf[right] = share
        self._phi[token] = right
        self._phi_inv[right] = token

        # Parent is now an internal node → mark ⊥.
        self._buf[c] = None

        self._count += 1

    # ------------------------------------------------------------------
    #  Update  (replace leaf share)
    # ------------------------------------------------------------------

    def update(self, token: bytes, new_share) -> None:
        """Replace a holder's share and mark the path to root as dirty.

        Parameters
        ----------
        token : bytes
            Holder's unique identifier.
        new_share :
            New RSS3 share replacing the old one.

        Raises
        ------
        KeyError
            If *token* is not in the tree.
        """
        idx = self._phi.get(token)
        if idx is None:
            raise KeyError(f"token {token.hex()} not in tree")

        self._buf[idx] = new_share
        self._mark_path_dirty(idx)

    # ------------------------------------------------------------------
    #  Remove / Quit  (heap-style deletion)
    # ------------------------------------------------------------------

    def remove(self, token: bytes) -> None:
        """Remove a holder using heap-style deletion (Paper Quit protocol).

        1. Swap the last leaf with the deleted position.
        2. Delete the last leaf (shrinking the tree).
        3. Mark paths from both affected positions to root as ⊥.

        Parameters
        ----------
        token : bytes
            Holder's unique identifier to remove.

        Raises
        ------
        KeyError
            If *token* is not in the tree.
        """
        idx = self._phi.get(token)
        if idx is None:
            raise KeyError(f"token {token.hex()} not in tree")

        # Find the last active leaf.
        last_idx = max(self._phi_inv.keys())

        if idx == last_idx:
            # Removing the last leaf — simple case: just delete it.
            self._buf[last_idx] = None
            del self._phi[token]
            del self._phi_inv[last_idx]
            self._count -= 1

            # Parent of the removed leaf → ⊥.
            if last_idx > 0:
                self._mark_path_dirty(last_idx)
            return

        # Heap-style: move last leaf's content to the deleted position.
        last_token = self._phi_inv[last_idx]

        self._buf[idx] = self._buf[last_idx]
        self._phi[last_token] = idx
        self._phi_inv[idx] = last_token

        # Delete the last position.
        self._buf[last_idx] = None
        del self._phi_inv[last_idx]
        del self._phi[token]  # removed token

        self._count -= 1

        # Both positions' ancestors need recomputation.
        self._mark_path_dirty(idx)
        if last_idx > 0:
            self._mark_path_dirty(last_idx)

    # ------------------------------------------------------------------
    #  Merge schedule
    # ------------------------------------------------------------------

    def get_merge_schedule(self) -> list[MergeStep]:
        """Return the ordered list of merges for all ⊥ internal nodes.

        Scans from the deepest layer upward so that children are always
        recomputed before their parents.  Each step identifies a parent
        node whose children are non-⊥ (i.e. data is available).

        A parent with exactly one non-⊥ child still appears in the
        schedule — the merge executor handles this as a *pass-through*
        (copy the surviving child's value).

        Returns
        -------
        list[MergeStep]
            Ordered bottom-up; empty if the tree is clean.
        """
        schedule: list[MergeStep] = []
        scheduled: set[int] = set()  # nodes whose merge is already planned

        # Scan internal-node layers bottom-up: h-2 (deepest parents)
        # down to 0 (root).  A node at layer L has children at L+1.
        # Leaves (layer h-1) cannot be ⊥ parents — skip them.
        for layer in range(self._h - 2, -1, -1):
            layer_start = (1 << layer) - 1
            layer_end = min((1 << (layer + 1)) - 1, self._max_nodes)

            for node in range(layer_start, layer_end):
                if self._buf[node] is not None:
                    continue  # clean — nothing to do

                left = 2 * node + 1
                right = 2 * node + 2

                left_ok = (
                    left < self._max_nodes
                    and (self._buf[left] is not None or left in scheduled)
                )
                right_ok = (
                    right < self._max_nodes
                    and (self._buf[right] is not None or right in scheduled)
                )

                if left_ok or right_ok:
                    schedule.append(MergeStep(parent=node))
                    scheduled.add(node)

        return schedule

    # ------------------------------------------------------------------
    #  Leaf enumeration
    # ------------------------------------------------------------------

    def leaf_indices(self) -> list[int]:
        """Return the node index of every active holder (sorted by index)."""
        return sorted(self._phi_inv.keys())

    def dirty_nodes(self) -> list[int]:
        """Return indices of all ⊥ nodes eligible for merge (debug aid).

        These are the parents that :meth:`get_merge_schedule` would return.
        """
        schedule = self.get_merge_schedule()
        return [s.parent for s in schedule]

    # ------------------------------------------------------------------
    #  Helpers
    # ------------------------------------------------------------------

    def _mark_path_dirty(self, leaf_idx: int) -> None:
        """Mark all ancestors of *leaf_idx* as ⊥, up to (but not including)
        the root's parent (which doesn't exist)."""
        idx = leaf_idx
        while idx > 0:
            idx = (idx - 1) // 2  # parent
            self._buf[idx] = None
