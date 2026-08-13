"""Verify mpmt BF aggregation (merge = OR, a+b-a*b) + ring_conv == plaintext.

For several (set_size, fpr) presets:
  - build 3 small sets
  - plaintext: gen_bf(ell=ell_root) per set, then OR-aggregate
  - MPC: 3-party ShrRep3(ell=1) share each BF, merge (add+hadamard+sub),
         ring_conv 1→ell_root, reveal
  - compare revealed root vs plaintext aggregate
"""
import sys, os, time, socket, secrets, multiprocessing as mp
import os as _os, sys as _sys
_t = _os.path.dirname(_os.path.abspath(__file__))
while _t and not _os.path.isdir(_os.path.join(_t, 'common')):
    _t = _os.path.dirname(_t)
_sys.path.insert(0, _t)
import mpmt

PASS = FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS {name}")
    else:
        FAIL += 1
        print(f"  FAIL {name}: {detail}")


def free_port():
    s = socket.socket(); s.bind(('127.0.0.1', 0)); p = s.getsockname()[1]; s.close()
    return p


def make_sets(n_sets, per_set):
    """n_sets sets, each with per_set distinct 16-byte elements."""
    all_elems = [secrets.token_hex(16) for _ in range(n_sets * per_set * 4)]
    return [all_elems[i * per_set:(i + 1) * per_set] for i in range(n_sets)]


def plain_aggregate(sets, bf_size, hf_num, ell_add2, ell_root, seeds):
    """gen_bf(ell=ell_root) per set, then OR-aggregate (a + b - a*b) in Z_{2^ell_root}."""
    mask = (1 << ell_root) - 1
    bfs = []
    for s in sets:
        bf = mpmt.gen_bf(ell=ell_root, set=[e.encode() for e in s],
                         hash_seed_list=seeds, bf_size=bf_size,
                         hf_num=hf_num, ell_add2=ell_add2)
        bfs.append([int(bf[i]) for i in range(bf_size)])
    result = list(bfs[0])
    for bf in bfs[1:]:
        for i in range(bf_size):
            a = result[i]; b = bf[i]
            result[i] = (a + b - a * b) & mask
    return result


def worker(pid, ports, n_sets, bf_size, ell_root, ell_add2, seeds, sets, barrier, q):
    import faulthandler
    faulthandler.dump_traceback_later(15, exit=False)
    try:
        listener = mpmt.ChannelListener('127.0.0.1', ports[pid])
        barrier.wait(timeout=20)
        nxt = (pid + 1) % 3
        ch_nxt = mpmt.Channel.connect('127.0.0.1', ports[nxt], timeout=15)
        ch_prev = listener.accept()

        rep3_1 = mpmt.ShrRep3(1, pid)(ch_prev, ch_nxt)
        rep3_root = mpmt.ShrRep3(ell_root, pid)(ch_prev, ch_nxt)

        SV1 = mpmt.ShrRep3ShareVec(1)
        SVR = mpmt.ShrRep3ShareVec(ell_root)
        pack1 = mpmt.RvectorPack(1)(bf_size)
        packR = mpmt.RvectorPack(ell_root)(bf_size)

        shares = []
        for si in range(n_sets):
            sv = SV1(bf_size)
            if pid == 0:
                bf = mpmt.gen_bf(ell=1, set=[e.encode() for e in sets[si]],
                                 hash_seed_list=seeds, bf_size=bf_size,
                                 hf_num=len(seeds), ell_add2=ell_add2)
                rep3_1.share_vector(bf, sv, pack1)
            else:
                rep3_1.recv_vector_share(sv, pack1)
            shares.append(sv)
        print(f"[P{pid}] share done", flush=True)

        acc = shares[0]
        for sv in shares[1:]:
            t1 = SV1(bf_size)
            t2 = SV1(bf_size)
            t3 = SV1(bf_size)
            rep3_1.add_vec(acc, sv, t1)
            rep3_1.hadamard(acc, sv, t2)
            rep3_1.sub_vec(t1, t2, t3)
            acc = t3
        merged = acc
        print(f"[P{pid}] merge done", flush=True)

        root = SVR(bf_size)
        rep3_1.ring_conv_vec(merged, root, ell_root)
        print(f"[P{pid}] ring_conv done", flush=True)

        out = mpmt.Rvector(ell_root)(bf_size)
        rep3_root.reveal_vector(root, out, packR)
        print(f"[P{pid}] reveal done", flush=True)
        vals = [int(out[i]) for i in range(bf_size)]
        print(f"[P{pid}] vals done len={len(vals)}", flush=True)
        q.put(("ok", vals))
        print(f"[P{pid}] q.put done", flush=True)
    except BaseException as e:
        q.put(("err", type(e).__name__, str(e)))


def run_case(set_size, fpr_mantissa, fpr_exponent, n_sets=3, per_set=50, timeout=60):
    bf_size, ell_add2, hf_num, ell_root = mpmt.bf_param(set_size, fpr_mantissa, fpr_exponent)
    seeds = [secrets.token_bytes(16) for _ in range(hf_num)]
    sets = make_sets(n_sets, per_set)
    plain = plain_aggregate(sets, bf_size, hf_num, ell_add2, ell_root, seeds)

    print(f"\n=== set_size={set_size} fpr={fpr_mantissa}e{fpr_exponent} "
          f"bf_size={bf_size} ell_root={ell_root} hf_num={hf_num} ===")

    ports = [free_port() for _ in range(3)]
    ctx = mp.get_context("fork")
    barrier = ctx.Barrier(3)
    q = ctx.Queue()
    procs = [ctx.Process(target=worker, args=(pid, ports, n_sets, bf_size, ell_root,
                                              ell_add2, seeds, sets, barrier, q))
             for pid in range(3)]
    for p in procs:
        p.start()

    deadline = time.monotonic() + timeout
    for p in procs:
        p.join(max(0.0, deadline - time.monotonic()))
    alive = [p for p in procs if p.is_alive()]

    msgs = []
    while not q.empty():
        msgs.append(q.get())
    errs = [m for m in msgs if m[0] == "err"]
    if errs:
        print(f"  FAIL worker error: {errs}")
        for p in alive:
            p.terminate()
        return False
    oks = [m for m in msgs if m[0] == "ok"]
    if alive:
        print(f"  NOTE destructor hang alive={[p.pid for p in alive]} "
              f"(but {len(oks)} results collected)")
        for p in alive:
            p.terminate()
    if len(oks) != 3:
        print(f"  FAIL expected 3 ok, got {len(oks)}")
        return False

    vals = [v for _, v in oks]
    same = (vals[0] == vals[1] == vals[2])
    match = (vals[0] == plain)
    check(f"3-party reveal identical", same)
    check(f"revealed == plaintext aggregate", match,
          f"mismatch at {_first_diff(vals[0], plain)}" if not match else "")
    return same and match


def _first_diff(a, b):
    for i in range(len(a)):
        if a[i] != b[i]:
            return f"idx={i} mpc={a[i]} plain={b[i]}"
    return "no diff (length?)"


if __name__ == '__main__':
    print("=== BF aggregation (merge=OR + ring_conv) vs plaintext ===")
    cases = [
        (1024, 1.0, -2),
        (1024, 1.0, -3),
        (2048, 5.0, -3),
    ]
    for c in cases:
        run_case(*c)
    print(f"\n  PASS={PASS}  FAIL={FAIL}")
    raise SystemExit(0 if FAIL == 0 else 1)
