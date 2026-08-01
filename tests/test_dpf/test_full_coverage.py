"""DPF full coverage: ELL_OUT [2,6] × ELL_IN {13,16,20} = 15 combos.
Verification done inside workers for large vectors to avoid huge IPC."""
import sys, os, random
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
import mpmt
from test_dpf.harness import run_dpf

PASS = FAIL = 0
def check(ei, eo, label, op, cond, detail=""):
    global PASS, FAIL
    tag = f"EI={ei:2d} EO={eo} {label:5s} {op}"
    if cond: PASS += 1; print(f"  ✅ {tag}")
    else: FAIL += 1; print(f"  ❌ {tag}  {detail}")

for eo in [2, 3, 4, 5, 6]:
    for ei, label, cores in [(13, "S:8K", 1), (16, "M:64K", 4), (20, "L:1M", 4)]:
        vec_len = 1 << ei
        m = mpmt.ring_mask(eo)
        alpha = random.randint(0, vec_len - 1)
        beta = random.randint(1, m)

        # ── Full eval + reveal (verify in-worker) ─
        def d_full(ch_e0, ch_e1):
            DC = mpmt.DpfDealer(ei, eo); d = DC(ch_e0, ch_e1)
            k0, k1 = DC.gen(alpha, beta); d.send_key(k0, 0); d.send_key(k1, 1)
            Rv = mpmt.Rvector(eo); out = Rv(vec_len); d.reveal(out)
            # Verify locally (dealer has reconstructed output)
            ok = (out[alpha] == beta)
            if not ok: return ("fail", alpha, out[alpha], beta)
            # Spot-check a few other positions
            for i in random.sample(range(vec_len), min(10, vec_len)):
                if i != alpha and out[i] != 0:
                    return ("fail_extra", i, out[i], 0)
            return ("ok",)
        def ev_full(pid):
            def fn(ch):
                EC = mpmt.DpfEvaluator(ei, eo, pid)
                ev = EC(ch); key = ev.recv_key()
                Rv = mpmt.Rvector(eo); buf = Rv(vec_len)
                EC.eval(key, buf, cores=cores); ev.reveal(buf)
                return ("ok",)
            return fn
        r = run_dpf(d_full, ev_full(0), ev_full(1), timeout=180)
        check(ei, eo, label, "full_eval", r[0][0] == "ok",
              f"dealer_err={r[0]}")

        # ── Range eval + reveal ────────────────
        bg = random.randint(0, max(0, vec_len - 20))
        ed = min(bg + random.randint(5, 20), vec_len - 1)
        rlen = ed - bg + 1
        def d_range(ch_e0, ch_e1):
            DC = mpmt.DpfDealer(ei, eo); d = DC(ch_e0, ch_e1)
            k0, k1 = DC.gen(alpha, beta); d.send_key(k0, 0); d.send_key(k1, 1)
            Rv = mpmt.Rvector(eo); out = Rv(rlen); d.reveal(out)
            ok = True; bad = []
            for i in range(rlen):
                exp = beta if (bg+i) == alpha else 0
                if out[i] != exp: ok = False; bad.append((bg+i, out[i], exp))
            return ("ok",) if ok else ("fail", bad[:3])
        def ev_range(pid):
            def fn(ch):
                EC = mpmt.DpfEvaluator(ei, eo, pid)
                ev = EC(ch); key = ev.recv_key()
                Rv = mpmt.Rvector(eo); buf = Rv(rlen)
                EC.eval_range(key, buf, bg, ed, cores=cores); ev.reveal(buf)
                return ("ok",)
            return fn
        r = run_dpf(d_range, ev_range(0), ev_range(1), timeout=180)
        check(ei, eo, label, "range_eval", r[0][0] == "ok",
              f"dealer_err={r[0]}")

        # ── Factory ────────────────────────────
        DC = mpmt.DpfDealer(ei, eo)
        check(ei, eo, label, "factory", DC is not None)

print(f"\n{'='*50}\n  PASS={PASS}  FAIL={FAIL}\n{'='*50}")
assert FAIL == 0
