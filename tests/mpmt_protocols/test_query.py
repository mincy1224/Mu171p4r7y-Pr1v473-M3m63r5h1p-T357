"""High-level Querier → AgentServer query protocol flow (real mpmt objects).

JOIN a set via SetHolder.share_bf + AgentServer.response_share_bf, merge with
sync_cache(), then run real membership queries via mpmt.Querier.query and
AgentServer.response_query — the same code paths the app's querier uses.

Each scenario is a fresh 3-agent stack with a single query round (the harness
runs JOIN + one QUERY reliably; multi-query-on-one-stack is covered by the app
lifecycle E2E).
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


def run_join_query(harness, params, seeds, A, element, want, label):
    out = h.run_protocol(params, seeds, [
        {"op": "JOIN", "data": A},
        {"op": "QUERY", "element": element},
    ])
    harness.check(label, out[1] == want, f"got {out[1]}")


def main() -> int:
    harness = Harness()
    _, _, hf_num, _ = mpmt.bf_param(SET_SIZE, FPR_M, FPR_E)
    params = {"set_size": SET_SIZE, "fpr_mantissa": FPR_M, "fpr_exponent": FPR_E}

    harness.section("member queries")
    A = make_set(20, "A")
    seeds = h.random_seeds(hf_num)
    run_join_query(harness, params, seeds, A, A[0].encode(), 1,
                   "query a member == 1")
    seeds = h.random_seeds(hf_num)
    run_join_query(harness, params, seeds, A, A[5].encode(), 1,
                   "query another member == 1")

    harness.section("non-member query")
    seeds = h.random_seeds(hf_num)
    nonmember = b"not-in-set-" + secrets.token_hex(8).encode()
    run_join_query(harness, params, seeds, A, nonmember, 0,
                   "query a non-member == 0")

    harness.section("different set, member + non-member")
    B = make_set(20, "B")
    seeds = h.random_seeds(hf_num)
    run_join_query(harness, params, seeds, B, B[3].encode(), 1,
                   "query B member == 1")
    seeds = h.random_seeds(hf_num)
    run_join_query(harness, params, seeds, B, nonmember, 0,
                   "query B non-member == 0")

    return harness.result()


if __name__ == "__main__":
    raise SystemExit(main())
