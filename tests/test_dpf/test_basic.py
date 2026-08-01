"""DPF: gen, send_key, recv_key, eval, eval_range, reveal — all tested."""
import sys, os, random
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
import mpmt
from test_dpf.harness import run_dpf

PASS = FAIL = 0
def check(name, cond, detail=""):
    global PASS, FAIL
    if cond: PASS += 1; print(f"  ✅ {name}")
    else: FAIL += 1; print(f"  ❌ {name}  {detail}")

for desc, ei, eo, cores in [("EI=13 EO=2", 13, 2, 1),
                              ("EI=15 EO=6", 15, 6, 4)]:
    print(f"\n--- {desc} ---")
    vec_len = 1 << ei
    m = mpmt.ring_mask(eo)
    alpha = random.randint(0, vec_len - 1)
    beta = random.randint(0, m)

    # ── 1. Full eval + reveal ─────────────────
    def d_full(ch_e0, ch_e1):
        DealerCls = mpmt.DpfDealer(ei, eo); d = DealerCls(ch_e0, ch_e1)
        k0, k1 = DealerCls.gen(alpha, beta); d.send_key(k0, 0); d.send_key(k1, 1)
        Rv = mpmt.Rvector(eo); out = Rv(vec_len); d.reveal(out)
        return [out[i] for i in range(vec_len)]

    def ev_full(pid):
        def fn(ch):
            EvalCls = mpmt.DpfEvaluator(ei, eo, pid)
            ev = EvalCls(ch); key = ev.recv_key()
            Rv = mpmt.Rvector(eo); buf = Rv(vec_len)
            EvalCls.eval(key, buf, cores=cores); ev.reveal(buf)
            return [buf[i] for i in range(vec_len)]
        return fn

    r = run_dpf(d_full, ev_full(0), ev_full(1), timeout=60)
    d_out, e0, e1 = r
    recon = [mpmt.ring_add(eo, e0[i], e1[i]) for i in range(vec_len)]
    ok = True; bad = []
    for i in range(vec_len):
        exp = beta if i == alpha else 0
        if recon[i] != exp: ok = False; bad.append((i, recon[i], exp))
        if len(bad) >= 3: break
    check(f"{desc} full eval+reveal", ok and d_out == recon,
          f"bad={bad[:3]} dealer_ok={d_out==recon}")

    # ── 2. Range eval + reveal ────────────────
    bg = random.randint(0, vec_len - 10)
    ed = min(bg + random.randint(1, 10), vec_len - 1)
    rlen = ed - bg + 1

    def d_range(ch_e0, ch_e1):
        DealerCls = mpmt.DpfDealer(ei, eo); d = DealerCls(ch_e0, ch_e1)
        k0, k1 = DealerCls.gen(alpha, beta); d.send_key(k0, 0); d.send_key(k1, 1)
        Rv = mpmt.Rvector(eo); out = Rv(rlen); d.reveal(out)
        return [out[i] for i in range(rlen)]

    def ev_range(pid):
        def fn(ch):
            EvalCls = mpmt.DpfEvaluator(ei, eo, pid)
            ev = EvalCls(ch); key = ev.recv_key()
            Rv = mpmt.Rvector(eo); buf = Rv(rlen)
            EvalCls.eval_range(key, buf, bg, ed, cores=cores); ev.reveal(buf)
            return [buf[i] for i in range(rlen)]
        return fn

    r = run_dpf(d_range, ev_range(0), ev_range(1), timeout=60)
    d_out, e0r, e1r = r
    recon_r = [mpmt.ring_add(eo, e0r[i], e1r[i]) for i in range(rlen)]
    ok_r = True; bad_r = []
    for i in range(rlen):
        exp = beta if (bg + i) == alpha else 0
        if recon_r[i] != exp: ok_r = False; bad_r.append((bg+i, recon_r[i], exp))
        if len(bad_r) >= 3: break
    check(f"{desc} range_eval+reveal [{bg},{ed}]", ok_r and d_out == recon_r,
          f"bad={bad_r[:3]} dealer_ok={d_out==recon_r}")

    # ── 3. Factory properties (verify on instances) ──
    def props_fn(ch_e0, ch_e1):
        d = mpmt.DpfDealer(ei, eo)(ch_e0, ch_e1)
        return (d.ell_in, d.ell_out)
    def ev_props_fn(pid):
        def fn(ch):
            ev = mpmt.DpfEvaluator(ei, eo, pid)(ch)
            return (ev.ell_in, ev.ell_out, ev.party)
        return fn
    r = run_dpf(props_fn, ev_props_fn(0), ev_props_fn(1), timeout=30)
    check(f"{desc} Dealer ell_in/out", r[0] == (ei, eo))
    check(f"{desc} Eval(p=0) ell_in/out/party", r[1] == (ei, eo, 0))
    check(f"{desc} Eval(p=1) ell_in/out/party", r[2] == (ei, eo, 1))

print(f"\n{'='*40}\n  PASS={PASS}  FAIL={FAIL}\n{'='*40}")
assert FAIL == 0
