"""High-level SetHolder → AgentServer protocol flows (real mpmt objects).

Uses mpmt.SetHolder.share_bf as the client and mpmt.AgentServer
response_share_bf on three parties, with an explicit sync_cache() merge.
Verifies the merged, revealed root against a plaintext BF OR-aggregate.

Scenarios:
  1. JOIN one holder  → root == gen_bf(A)
  2. JOIN two holders → root == OR(A, B)
  3. JOIN A,B then UPDATE A→A' → root == OR(A', B)  (and != OR(A, B))
"""
from __future__ import annotations

import os
import secrets
import sys

_t = os.path.dirname(os.path.abspath(__file__))
while _t and not os.path.isdir(os.path.join(_t, "common")):
    _t = os.path.dirname(_t)
sys.path.insert(0, _t)

import mpmt
from common.assertions import Harness
from mpmt_protocols import _mpc_harness as h

SET_SIZE, FPR_M, FPR_E = 1024, 1.0, -3


def make_set(n: int, tag: str) -> list[str]:
    return [f"{tag}-{i}-{secrets.token_hex(8)}" for i in range(n)]


def main() -> int:
    harness = Harness()
    bf_size, ell_add2, hf_num, ell_root = mpmt.bf_param(SET_SIZE, FPR_M, FPR_E)
    seeds = h.random_seeds(hf_num)
    params = {"set_size": SET_SIZE, "fpr_mantissa": FPR_M, "fpr_exponent": FPR_E}
    A, B, A2 = make_set(20, "A"), make_set(20, "B"), make_set(20, "A2")
    plainA = h.plain_aggregate([A], bf_size, hf_num, ell_add2, ell_root, seeds)
    plainAB = h.plain_aggregate([A, B], bf_size, hf_num, ell_add2, ell_root, seeds)
    plainA2B = h.plain_aggregate([A2, B], bf_size, hf_num, ell_add2, ell_root, seeds)

    harness.section("JOIN A — single holder")
    out = h.run_protocol(params, seeds, [{"op": "JOIN", "data": A}])[0]
    harness.check("3-party reveal identical", out[0] == out[1] == out[2])
    harness.check("revealed root == gen_bf(A)", out[0] == plainA,
                  f"diff at {next((i for i,(x,y) in enumerate(zip(out[0], plainA)) if x!=y), '?')}")

    harness.section("JOIN A, JOIN B — two holders")
    out2 = h.run_protocol(params, seeds,
                          [{"op": "JOIN", "data": A}, {"op": "JOIN", "data": B}])
    harness.check("after JOIN B reveal == OR(A,B)", out2[-1][0] == plainAB)
    harness.check("root changed after second JOIN", out2[-1][0] != out2[0][0])

    harness.section("JOIN A, JOIN B, UPDATE A→A'")
    out3 = h.run_protocol(params, seeds,
                          [{"op": "JOIN", "data": A},
                           {"op": "JOIN", "data": B},
                           {"op": "UPDATE", "data": A2, "target": 0}])
    harness.check("after UPDATE reveal == OR(A',B)", out3[-1][0] == plainA2B)
    harness.check("root changed after UPDATE", out3[-1][0] != out3[-2][0])

    return harness.result()


if __name__ == "__main__":
    raise SystemExit(main())
