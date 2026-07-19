"""Verify a SetHolder's BF can be RSS3-shared to 3 servers and
reconstructed correctly.

Tests the full pipeline: local BF construction → share → reconstruct.
Because all parties use the same hash seeds and elements, the locally
constructed plaintext BF must match the reconstructed result.
"""

import random
import pytest
import mpmt
from conftest import PartyPool, _setup_rep3


# ——— Test parameters ———

ELL = 1  # Bloom filters use ELL=1

# Use a moderate BF size so the test stays fast even with MPC rounds.
BF_SIZE_BITS = 10_000   # ~1.25 KB  — large enough to span multiple words
HF_NUM = 3               # 3 hash functions

ELEMENTS = [
    b"alice@example.com",
    b"bob@example.com",
    b"charlie@example.com",
]


# ——— Helpers ——————————————————————————————————————————————


def _make_hash_seeds(hf_num: int) -> list[bytes]:
    """Generate *hf_num* random 128-bit hash seeds."""
    seeds = []
    for _ in range(hf_num):
        seeds.append(mpmt.get_key_128bits())
    return seeds


def _build_bf_list(elements: list[bytes], hash_seeds: list[bytes],
                   bf_size: int) -> list[int]:
    """Construct a plaintext Bloom filter locally, return as list of ints.

    Plain Python list so it can be cloudpickled to worker processes.
    """
    Rv = mpmt.Rvector(ELL)
    bf = Rv(bf_size)
    bf.fill(0)
    for e in elements:
        for hs in hash_seeds:
            idx = mpmt.hash_aes_dm(preimage=e, key=hs, ell=ELL)
            bf[idx] = 1
    return [bf[i] for i in range(bf_size)]


# ===================================================================
#  Tests
# ===================================================================


class TestSetHolderShare:
    """Verify that RSS3-shared BF reconstructs to the expected plaintext."""

    def test_share_and_reconstruct(self, rss3_pool):
        """Party 0 acts as SetHolder, shares a BF, all 3 reconstruct."""
        hash_seeds = _make_hash_seeds(HF_NUM)
        expected_list = _build_bf_list(ELEMENTS, hash_seeds, BF_SIZE_BITS)

        def party(pid, channels):
            import mpmt as _mpmt
            ELL = 1
            inst = _mpmt.ShrRep3(ELL, pid)(channels["prev"], channels["next"])
            SV = _mpmt.ShrRep3ShareVec(ELL)
            sv = SV(BF_SIZE_BITS)

            if pid == 0:
                # Rebuild BF in-process (can't pickle Rvector across processes).
                Rv = _mpmt.Rvector(ELL)
                bf = Rv(BF_SIZE_BITS)
                bf.fill(0)
                for e in ELEMENTS:
                    for hs in hash_seeds:
                        idx = _mpmt.hash_aes_dm(preimage=e, key=hs, ell=ELL)
                        bf[idx] = 1
                aux = _mpmt.RvectorPack(ELL)(BF_SIZE_BITS)
                inst.share_vector(bf, sv, aux)
            else:
                aux = _mpmt.RvectorPack(ELL)(BF_SIZE_BITS)
                inst.recv_vector_share(sv, aux)

            # Reconstruct via reveal_vector.
            Rv = _mpmt.Rvector(ELL)
            out = Rv(BF_SIZE_BITS)
            aux2 = _mpmt.RvectorPack(ELL)(BF_SIZE_BITS)
            inst.reveal_vector(sv, out, aux2)
            return [out[i] for i in range(BF_SIZE_BITS)]

        results = rss3_pool.run(party)
        assert results[0] == results[1] == results[2]
        assert results[0] == expected_list

    def test_local_bf_matches_reconstructed(self, rss3_pool):
        """All 3 parties locally construct the BF and verify their
        shares reconstruct to the same result."""
        hash_seeds = _make_hash_seeds(HF_NUM)
        expected_list = _build_bf_list(ELEMENTS, hash_seeds, BF_SIZE_BITS)

        def party(pid, channels):
            import mpmt as _mpmt
            ELL = 1
            inst = _mpmt.ShrRep3(ELL, pid)(channels["prev"], channels["next"])
            SV = _mpmt.ShrRep3ShareVec(ELL)
            sv = SV(BF_SIZE_BITS)

            if pid == 0:
                Rv = _mpmt.Rvector(ELL)
                bf = Rv(BF_SIZE_BITS)
                bf.fill(0)
                for e in ELEMENTS:
                    for hs in hash_seeds:
                        idx = _mpmt.hash_aes_dm(preimage=e, key=hs, ell=ELL)
                        bf[idx] = 1
                aux = _mpmt.RvectorPack(ELL)(BF_SIZE_BITS)
                inst.share_vector(bf, sv, aux)
            else:
                aux = _mpmt.RvectorPack(ELL)(BF_SIZE_BITS)
                inst.recv_vector_share(sv, aux)

            Rv = _mpmt.Rvector(ELL)
            out = Rv(BF_SIZE_BITS)
            aux2 = _mpmt.RvectorPack(ELL)(BF_SIZE_BITS)
            inst.reveal_vector(sv, out, aux2)
            for i in range(BF_SIZE_BITS):
                if out[i] != expected_list[i]:
                    return False
            return True

        results = rss3_pool.run(party)
        assert all(results)
