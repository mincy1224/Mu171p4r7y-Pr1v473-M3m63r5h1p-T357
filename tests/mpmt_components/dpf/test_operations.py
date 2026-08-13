"""DPF: eval_range wraparound, gen parameter validation."""
import sys, os, random
_sys_t = os.path.dirname(os.path.abspath(__file__))
while _sys_t and not os.path.isdir(os.path.join(_sys_t, 'common')):
    _sys_t = os.path.dirname(_sys_t)
sys.path.insert(0, _sys_t)
import mpmt
from mpmt_components.dpf.harness import run_dpf

PASS = FAIL = 0

def check(name, cond, detail=""):
    global PASS, FAIL
    if cond: PASS += 1
    else: FAIL += 1; print(f"  FAIL {name}  {detail}")

def run_tests(small=False):
    global PASS, FAIL
    PASS = FAIL = 0
    print("=== DPF Operations ===")

    test_ei_eo = [(13, 2)] if small else [(13, 2), (16, 4), (20, 6)]

    for ei, eo in test_ei_eo:
        label = f"DPF(EI={ei},EO={eo})"
        vl = 1 << ei
        m = mpmt.ring_mask(eo)

        def d_gen_alpha(ch_e0, ch_e1):
            DC = mpmt.DpfDealer(ei, eo); d = DC(ch_e0, ch_e1)
            results = []
            for bad_alpha in [vl, vl + 1]:
                try:
                    d.gen(bad_alpha, 1)
                    results.append(('no_error', bad_alpha))
                except (ValueError, RuntimeError, TypeError):
                    results.append(('ok', bad_alpha))
            return results

        def ev_dummy(pid):
            def fn(ch):
                EC = mpmt.DpfEvaluator(ei, eo, pid)
                ev = EC(ch)
                return ('ok',)
            return fn

        r = run_dpf(d_gen_alpha, ev_dummy(0), ev_dummy(1), timeout=30)
        for status, bad_val in r[0]:
            check(f"{label} gen reject alpha={bad_val}", status == 'ok',
                  f"got {status}")

        def d_gen_ok(ch_e0, ch_e1):
            DC = mpmt.DpfDealer(ei, eo); d = DC(ch_e0, ch_e1)
            results = []
            for a_val in [0, vl - 1]:
                try:
                    k0, k1 = d.gen(a_val, 1)
                    results.append(isinstance(k0, str) and isinstance(k1, str))
                except Exception:
                    results.append(False)
            return tuple(results)

        r3 = run_dpf(d_gen_ok, ev_dummy(0), ev_dummy(1), timeout=30)
        if len(r3[0]) >= 2:
            check(f"{label} gen alpha=0", r3[0][0] is True)
            check(f"{label} gen alpha=vl-1", r3[0][1] is True)

    for ei, eo in [(13, 2), (15, 6)] if not small else [(13, 2)]:
        label = f"DPF(EI={ei},EO={eo})"
        vl = 1 << ei
        m = mpmt.ring_mask(eo)
        alpha = random.randint(0, vl - 1)
        beta = random.randint(1, m)

        bg = vl - 10
        ed = 9
        rlen = (vl - bg) + (ed + 1)

        def d_wrap(ch_e0, ch_e1):
            DC = mpmt.DpfDealer(ei, eo); d = DC(ch_e0, ch_e1)
            k0, k1 = d.gen(alpha, beta)
            d.send_key(k0, 0); d.send_key(k1, 1)
            Rv = mpmt.Rvector(eo); out = Rv(rlen)
            d.reveal(out)
            ok = True; bad = []
            for i in range(rlen):
                idx = (bg + i) % vl
                exp_val = beta if idx == alpha else 0
                if out[i] != exp_val:
                    ok = False
                    bad.append((i, idx, out[i], exp_val))
                    if len(bad) >= 3: break
            return ("ok",) if ok else ("fail", bad[:3])

        def ev_wrap(pid):
            def fn(ch):
                EC = mpmt.DpfEvaluator(ei, eo, pid)
                ev = EC(ch); key = ev.recv_key()
                Rv = mpmt.Rvector(eo); buf = Rv(rlen)
                ev.eval_range(key, buf, bg, ed, cores=1)
                ev.reveal(buf)
                return ("ok",)
            return fn

        r = run_dpf(d_wrap, ev_wrap(0), ev_wrap(1), timeout=120)
        check(f"{label} eval_range wrap [{bg},{ed}]",
              r[0][0] == "ok", f"dealer={r[0]}")

        bg2 = random.randint(0, max(0, vl - 20))
        ed2 = bg2 + 15
        rlen2 = ed2 - bg2 + 1

        def d_normal(ch_e0, ch_e1):
            DC = mpmt.DpfDealer(ei, eo); d = DC(ch_e0, ch_e1)
            k0, k1 = d.gen(alpha, beta)
            d.send_key(k0, 0); d.send_key(k1, 1)
            Rv = mpmt.Rvector(eo); out = Rv(rlen2)
            d.reveal(out)
            ok = True; bad = []
            for i in range(rlen2):
                exp_val = beta if (bg2 + i) == alpha else 0
                if out[i] != exp_val:
                    ok = False
                    bad.append((bg2 + i, out[i], exp_val))
                    if len(bad) >= 3: break
            return ("ok",) if ok else ("fail", bad[:3])

        def ev_normal(pid):
            def fn(ch):
                EC = mpmt.DpfEvaluator(ei, eo, pid)
                ev = EC(ch); key = ev.recv_key()
                Rv = mpmt.Rvector(eo); buf = Rv(rlen2)
                ev.eval_range(key, buf, bg2, ed2, cores=1)
                ev.reveal(buf)
                return ("ok",)
            return fn

        r2 = run_dpf(d_normal, ev_normal(0), ev_normal(1), timeout=120)
        check(f"{label} eval_range normal [{bg2},{ed2}]",
              r2[0][0] == "ok", f"dealer={r2[0]}")

    ei, eo = 13, 4
    vl = 1 << ei
    alpha = random.randint(0, vl - 1)
    beta = random.randint(1, mpmt.ring_mask(eo))
    cores_list = [1, 4] if not small else [1]

    for cores in cores_list:
        def d_mt(ch_e0, ch_e1):
            DC = mpmt.DpfDealer(ei, eo); d = DC(ch_e0, ch_e1)
            k0, k1 = d.gen(alpha, beta)
            d.send_key(k0, 0); d.send_key(k1, 1)
            Rv = mpmt.Rvector(eo); out = Rv(vl); d.reveal(out)
            return ("ok",)

        def ev_mt(pid):
            def fn(ch):
                EC = mpmt.DpfEvaluator(ei, eo, pid)
                ev = EC(ch); key = ev.recv_key()
                Rv = mpmt.Rvector(eo); buf = Rv(vl)
                ev.eval(key, buf, cores=cores)
                ev.reveal(buf)
                return ("ok",)
            return fn

        r = run_dpf(d_mt, ev_mt(0), ev_mt(1), timeout=120)
        check(f"DPF(EI={ei},EO={eo}) eval cores={cores}",
              r[0][0] == "ok", f"dealer={r[0]}")

    print(f"  PASS={PASS}  FAIL={FAIL}")
    return PASS, FAIL

if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--small", action="store_true")
    args = ap.parse_args()
    rc = run_tests(small=args.small)
    raise SystemExit(0 if rc[1] == 0 else 1)
