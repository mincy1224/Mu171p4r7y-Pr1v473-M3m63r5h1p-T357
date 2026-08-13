"""Isolate ring_conv: share one ell=1 vector, ring_conv to ell_to, reveal."""
import sys, os, time, socket, multiprocessing as mp
_sys_t = os.path.dirname(os.path.abspath(__file__))
while _sys_t and not os.path.isdir(os.path.join(_sys_t, 'common')):
    _sys_t = os.path.dirname(_sys_t)
sys.path.insert(0, _sys_t)
import mpmt

PASS = FAIL = 0
def check(n, c, d=""):
    global PASS, FAIL
    if c: PASS += 1; print(f"  PASS {n}")
    else: FAIL += 1; print(f"  FAIL {n}: {d}")

def free_port():
    s = socket.socket(); s.bind(('127.0.0.1', 0)); p = s.getsockname()[1]; s.close(); return p

def worker(pid, ports, n, ell_to, barrier, q):
    try:
        listener = mpmt.ChannelListener('127.0.0.1', ports[pid])
        barrier.wait(timeout=20)
        nxt = (pid + 1) % 3
        ch_nxt = mpmt.Channel.connect('127.0.0.1', ports[nxt], timeout=15)
        ch_prev = listener.accept()
        rep3_1 = mpmt.ShrRep3(1, pid)(ch_prev, ch_nxt)
        rep3_to = mpmt.ShrRep3(ell_to, pid)(ch_prev, ch_nxt)

        sv1 = mpmt.ShrRep3ShareVec(1)(n)
        if pid == 0:
            v = mpmt.Rvector(1)(n); v.fill(1)
            rep3_1.share_vector(v, sv1, mpmt.RvectorPack(1)(n))
        else:
            rep3_1.recv_vector_share(sv1, mpmt.RvectorPack(1)(n))

        sv_to = mpmt.ShrRep3ShareVec(ell_to)(n)
        rep3_1.ring_conv_vec(sv1, sv_to, ell_to)

        out = mpmt.Rvector(ell_to)(n)
        rep3_to.reveal_vector(sv_to, out, mpmt.RvectorPack(ell_to)(n))
        vals = [int(out[i]) for i in range(n)]
        q.put(("ok", vals))
    except BaseException as e:
        q.put(("err", type(e).__name__, str(e)))

def run_case(n, ell_to, timeout=40):
    print(f"\n=== ring_conv-only: n={n} ell_to={ell_to} ===")
    ports = [free_port() for _ in range(3)]
    ctx = mp.get_context("fork")
    barrier = ctx.Barrier(3)
    q = ctx.Queue()
    procs = [ctx.Process(target=worker, args=(pid, ports, n, ell_to, barrier, q)) for pid in range(3)]
    for p in procs: p.start()
    deadline = time.monotonic() + timeout
    for p in procs: p.join(max(0.0, deadline - time.monotonic()))
    alive = [p for p in procs if p.is_alive()]
    if alive:
        print(f"  FAIL timeout alive={[p.pid for p in alive]}")
        for p in alive: p.terminate()
        return
    msgs = []
    while not q.empty(): msgs.append(q.get())
    errs = [m for m in msgs if m[0] == "err"]
    if errs:
        print(f"  FAIL err={errs}"); return
    vals = [v for _, v in msgs]
    exp = [1] * n
    check("reveal identical", vals[0] == vals[1] == vals[2])
    check("ring_conv(1->ell_to) == all-ones", vals[0] == exp,
          f"diff={_d(vals[0], exp)}")

def _d(a, b):
    for i in range(min(len(a), len(b))):
        if a[i] != b[i]: return f"idx={i} got={a[i]} exp={b[i]}"
    return "no diff"

if __name__ == '__main__':
    run_case(500, 3)
    run_case(500, 4)
    run_case(2000, 4)
    print(f"\n  PASS={PASS}  FAIL={FAIL}")
    raise SystemExit(0 if FAIL == 0 else 1)
