#!/usr/bin/env python3
"""
ABY3 ring_conv_vec stress/regression test.

Purpose
-------
Catches bugs that n=8 unit tests cannot see:
  * large-message liveness;
  * repeated _reshare_ring calls on the SAME ShrRep3 instance;
  * replicated-share consistency after conversion;
  * production-sized n (optional).

This test creates a real localhost TCP ring using the production Channel API:
    Python TCP listener/connect -> mpmt.Channel -> NetIO_from_socket.

Examples
--------
  python3 tests/test_ringconv_stress.py --mode quick
  python3 tests/test_ringconv_stress.py --mode full
  python3 tests/test_ringconv_stress.py --mode production

"production" reads bf_size/ell_root from application/pretreat/pre.json
when possible and otherwise uses n=14_377_588, ell_to=4.
"""
from __future__ import annotations

import argparse
import multiprocessing as mp
import os
from pathlib import Path
import queue
import socket
import sys
import time
import traceback


def locate_project_root() -> Path:
    here = Path(__file__).resolve()
    candidates = [
        Path.cwd(),
        here.parent,
        here.parent.parent,
        here.parent.parent.parent,
    ]
    for c in candidates:
        if (c / "mpmt").is_dir() or (c / "application" / "config.json").is_file():
            return c.resolve()
    return Path.cwd().resolve()


ROOT = locate_project_root()
_sys_t = os.path.dirname(os.path.abspath(__file__))
while _sys_t and not os.path.isdir(os.path.join(_sys_t, 'common')):
    _sys_t = os.path.dirname(_sys_t)
sys.path.insert(0, _sys_t)
import mpmt


HOST = "127.0.0.1"


def free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind((HOST, 0))
    p = s.getsockname()[1]
    s.close()
    return p


def sample_indices(n: int, count: int = 96) -> list[int]:
    if n <= 0:
        return []
    base = {0, n - 1, n // 2}
    if n <= count:
        base.update(range(n))
    else:
        step = max(1, n // count)
        base.update(range(0, n, step))
    return sorted(i for i in base if 0 <= i < n)[: max(count, 3)]


def _worker(pid: int, ports: list[int], barrier, q,
            n: int, ell_to: int, rounds: int, samples: list[int]) -> None:
    try:
        listener = mpmt.ChannelListener(HOST, ports[pid])
        barrier.wait(timeout=20)

        nxt = (pid + 1) % 3
        ch_nxt = mpmt.Channel.connect(HOST, ports[nxt], timeout=15.0)
        ch_prev = listener.accept()

        t_ctor = time.monotonic()
        inst = mpmt.ShrRep3(1, pid)(ch_prev, ch_nxt)
        print(f"[P{pid}] ShrRep3 ctor DONE ({time.monotonic()-t_ctor:.3f}s)",
              flush=True)

        SV1 = mpmt.ShrRep3ShareVec(1)
        SVT = mpmt.ShrRep3ShareVec(ell_to)

        results = []
        for rnd in range(rounds):
            secret_bit = rnd & 1
            sv = SV1(n)

            sv.this_share.fill(secret_bit if pid == 0 else 0)
            sv.nxt_share.fill(secret_bit if pid == 2 else 0)

            out = SVT(n)
            t0 = time.monotonic()
            print(f"[P{pid}] round={rnd} BEGIN ring_conv n={n} ell_to={ell_to}",
                  flush=True)
            inst.ring_conv_vec(sv, out, ell_to)
            dt = time.monotonic() - t0
            print(f"[P{pid}] round={rnd} DONE  ring_conv ({dt:.3f}s)",
                  flush=True)

            this_vals = [int(out.this_share[i]) for i in samples]
            nxt_vals = [int(out.nxt_share[i]) for i in samples]
            results.append({
                "round": rnd,
                "secret": secret_bit,
                "elapsed": dt,
                "this": this_vals,
                "nxt": nxt_vals,
            })

        q.put(("ok", pid, results))
    except BaseException as e:
        q.put(("err", pid, type(e).__name__, str(e), traceback.format_exc()))


def run_case(n: int, ell_to: int, rounds: int, timeout: float) -> bool:
    print(f"\n=== ringConv stress: n={n:,} ell_to={ell_to} rounds={rounds} ===")
    ports = [free_port() for _ in range(3)]
    samples = sample_indices(n)

    ctx = mp.get_context("fork")
    barrier = ctx.Barrier(3)
    q = ctx.Queue()
    procs = [
        ctx.Process(target=_worker,
                    args=(pid, ports, barrier, q, n, ell_to, rounds, samples))
        for pid in range(3)
    ]

    for p in procs:
        p.start()

    deadline = time.monotonic() + timeout
    for p in procs:
        rem = max(0.0, deadline - time.monotonic())
        p.join(rem)

    alive = [p for p in procs if p.is_alive()]
    if alive:
        print("FAIL: timeout/deadlock; alive PIDs:",
              [p.pid for p in alive], flush=True)
        for p in alive:
            p.terminate()
        for p in alive:
            p.join(3)
        for p in alive:
            if p.is_alive():
                p.kill()
        return False

    msgs = []
    while True:
        try:
            msgs.append(q.get_nowait())
        except queue.Empty:
            break

    errors = [m for m in msgs if m[0] == "err"]
    if errors:
        for m in errors:
            _, pid, typ, msg, tb = m
            print(f"FAIL P{pid}: {typ}: {msg}\n{tb}")
        return False

    ok_msgs = sorted((m for m in msgs if m[0] == "ok"), key=lambda x: x[1])
    if len(ok_msgs) != 3:
        print(f"FAIL: expected 3 worker results, got {len(ok_msgs)}")
        return False

    per_pid = {pid: results for _, pid, results in ok_msgs}
    mask = (1 << ell_to) - 1

    for rnd in range(rounds):
        secret = rnd & 1
        for j, idx in enumerate(samples):
            p0 = per_pid[0][rnd]
            p1 = per_pid[1][rnd]
            p2 = per_pid[2][rnd]

            got = (p0["this"][j] + p1["this"][j] + p2["this"][j]) & mask
            if got != secret:
                print(f"FAIL correctness round={rnd} idx={idx}: "
                      f"got={got} expected={secret}")
                return False

            if p0["nxt"][j] != p1["this"][j]:
                print(f"FAIL RSS edge P0.nxt!=P1.this round={rnd} idx={idx}")
                return False
            if p1["nxt"][j] != p2["this"][j]:
                print(f"FAIL RSS edge P1.nxt!=P2.this round={rnd} idx={idx}")
                return False
            if p2["nxt"][j] != p0["this"][j]:
                print(f"FAIL RSS edge P2.nxt!=P0.this round={rnd} idx={idx}")
                return False

    elapsed = [
        per_pid[pid][rnd]["elapsed"]
        for pid in range(3)
        for rnd in range(rounds)
    ]
    print(f"PASS: correctness + liveness; "
          f"min={min(elapsed):.3f}s max={max(elapsed):.3f}s")
    return True


def production_params() -> tuple[int, int]:
    import json
    candidates = [
        ROOT / "application" / "pretreat" / "pre.json",
        Path.cwd() / "application" / "pretreat" / "pre.json",
    ]
    for p in candidates:
        if p.is_file():
            try:
                pre = json.loads(p.read_text())
                if "bf_size" in pre and "ell_root" in pre:
                    return int(pre["bf_size"]), int(pre["ell_root"])
                if {"set_size", "fpr_mantissa", "fpr_exponent"} <= pre.keys():
                    bf_size, _, _, ell_root = mpmt.bf_param(
                        int(pre["set_size"]),
                        float(pre["fpr_mantissa"]),
                        int(pre["fpr_exponent"]),
                    )
                    return int(bf_size), int(ell_root)
            except Exception:
                pass
    return 14_377_588, 4


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=("quick", "full", "production"),
                    default="quick")
    ap.add_argument("--timeout", type=float, default=240.0)
    args = ap.parse_args()

    cases: list[tuple[int, int, int]]
    if args.mode == "quick":
        cases = [
            (257, 2, 4),
            (257, 6, 4),
            (1_000_000, 4, 3),
        ]
    elif args.mode == "full":
        cases = [(4097, ell, 5) for ell in range(2, 7)]
        cases += [(2_000_000, ell, 3) for ell in range(2, 7)]
    else:
        n, ell = production_params()
        cases = [
            (4097, ell, 5),
            (n, ell, 2),
        ]

    failures = 0
    for n, ell, rounds in cases:
        if not run_case(n, ell, rounds, args.timeout):
            failures += 1
            break

    print(f"\nRESULT: {'PASS' if failures == 0 else 'FAIL'}")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
