"""DPF: basic end-to-end tests — gen, send_key, recv_key, eval, eval_range, reveal."""
import sys, os, random
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', '..'))
import mpmt
from building_blocks.test_dpf.harness import run_dpf

PASS = FAIL = 0

def check(name, cond, detail=""):
    global PASS, FAIL
    if cond: PASS += 1
    else: FAIL += 1; print(f"  FAIL {name}  {detail}")

def run_tests(small=False):
    global PASS, FAIL
    PASS = FAIL = 0
    print("=== DPF Basic ===")

    # DpfDealer / DpfEvaluator factories
    test_pairs = [(13, 2)] if small else [(13, 2), (16, 4), (20, 6), (25, 6), (31, 2)]

    for ei, eo in test_pairs:
        label = f"DPF(EI={ei},EO={eo})"

        # Factory returns class
        DC = mpmt.DpfDealer(ei, eo)
        EC0 = mpmt.DpfEvaluator(ei, eo, 0)
        EC1 = mpmt.DpfEvaluator(ei, eo, 1)
        check(f"{label} Dealer factory", DC is not None)
        check(f"{label} Eval0 factory", EC0 is not None)
        check(f"{label} Eval1 factory", EC1 is not None)

    # Reject invalid params for factory
    for bad_ei, bad_eo in [(0, 2), (12, 2), (13, 1), (13, 7), (32, 2), (13, 0)]:
        try:
            mpmt.DpfDealer(bad_ei, bad_eo)
            check(f"DpfDealer reject ({bad_ei},{bad_eo})", False, "no error")
        except (ValueError, RuntimeError):
            check(f"DpfDealer reject ({bad_ei},{bad_eo})", True)

    for bad_party in [-1, 2]:
        try:
            mpmt.DpfEvaluator(13, 2, bad_party)
            check(f"DpfEvaluator reject party={bad_party}", False, "no error")
        except (ValueError, RuntimeError):
            check(f"DpfEvaluator reject party={bad_party}", True)

    # Full DPF flow: gen→send_key→recv_key→eval→reveal
    test_flows = [(13, 2)] if small else [(13, 2), (16, 4), (20, 6)]

    for ei, eo in test_flows:
        vl = 1 << ei
        m = mpmt.ring_mask(eo)
        alpha = random.randint(0, vl - 1)
        beta = random.randint(1, m)
        label = f"DPF(EI={ei},EO={eo})"

        # Dealer: gen + send_key + reveal
        def d_flow(ch_e0, ch_e1):
            DC = mpmt.DpfDealer(ei, eo)
            d = DC(ch_e0, ch_e1)
            k0, k1 = d.gen(alpha, beta)
            d.send_key(k0, 0)
            d.send_key(k1, 1)
            Rv = mpmt.Rvector(eo)
            out = Rv(vl)
            d.reveal(out)
            # Verify output
            if out[alpha] != beta:
                return ("fail", f"out[{alpha}]={out[alpha]} != {beta}")
            # Spot-check a few non-alpha positions
            for i in random.sample(range(vl), min(50, vl)):
                if i != alpha and out[i] != 0:
                    return ("fail", f"out[{i}]={out[i]} != 0")
            return ("ok",)

        # Evaluator: recv_key + eval + reveal
        def ev_flow(pid):
            def fn(ch):
                EC = mpmt.DpfEvaluator(ei, eo, pid)
                ev = EC(ch)
                key = ev.recv_key()
                Rv = mpmt.Rvector(eo)
                buf = Rv(vl)
                ev.eval(key, buf, cores=1)
                ev.reveal(buf)
                return ("ok",)
            return fn

        r = run_dpf(d_flow, ev_flow(0), ev_flow(1), timeout=120)
        check(f"{label} full flow", r[0][0] == "ok", f"dealer={r[0]}")

        # eval_range basic
        bg = random.randint(0, max(0, vl - 20))
        ed = min(bg + random.randint(5, 20), vl - 1)
        rlen = ed - bg + 1

        def d_range(ch_e0, ch_e1):
            DC = mpmt.DpfDealer(ei, eo)
            d = DC(ch_e0, ch_e1)
            k0, k1 = d.gen(alpha, beta)
            d.send_key(k0, 0)
            d.send_key(k1, 1)
            Rv = mpmt.Rvector(eo)
            out = Rv(rlen)
            d.reveal(out)
            for i in range(rlen):
                exp = beta if (bg + i) == alpha else 0
                if out[i] != exp:
                    return ("fail", f"[{i}] got {out[i]} exp {exp}")
            return ("ok",)

        def ev_range(pid):
            def fn(ch):
                EC = mpmt.DpfEvaluator(ei, eo, pid)
                ev = EC(ch)
                key = ev.recv_key()
                Rv = mpmt.Rvector(eo)
                buf = Rv(rlen)
                ev.eval_range(key, buf, bg, ed, cores=1)
                ev.reveal(buf)
                return ("ok",)
            return fn

        r2 = run_dpf(d_range, ev_range(0), ev_range(1), timeout=120)
        check(f"{label} eval_range [{bg},{ed}]", r2[0][0] == "ok", f"dealer={r2[0]}")

        # eval multithreaded 
        for cores in [1, 4] if not small else [1]:
            def d_mt(ch_e0, ch_e1):
                DC = mpmt.DpfDealer(ei, eo)
                d = DC(ch_e0, ch_e1)
                k0, k1 = d.gen(alpha, beta)
                d.send_key(k0, 0)
                d.send_key(k1, 1)
                Rv = mpmt.Rvector(eo)
                out = Rv(vl)
                d.reveal(out)
                if out[alpha] != beta:
                    return ("fail",)
                return ("ok",)

            def ev_mt(pid):
                def fn(ch):
                    EC = mpmt.DpfEvaluator(ei, eo, pid)
                    ev = EC(ch)
                    key = ev.recv_key()
                    Rv = mpmt.Rvector(eo)
                    buf = Rv(vl)
                    ev.eval(key, buf, cores=cores)
                    ev.reveal(buf)
                    return ("ok",)
                return fn

            r3 = run_dpf(d_mt, ev_mt(0), ev_mt(1), timeout=120)
            check(f"{label} eval cores={cores}", r3[0][0] == "ok")

    # gen parameter validation 
    ei, eo = 13, 2
    vl = 1 << ei

    def d_gen_val(ch_e0, ch_e1):
        DC = mpmt.DpfDealer(ei, eo)
        d = DC(ch_e0, ch_e1)
        results = {}
        # alpha out of range
        for bad_alpha in [vl, vl + 100]:
            try:
                d.gen(bad_alpha, 1)
                results[f"alpha={bad_alpha}"] = "no_error"
            except (ValueError, RuntimeError):
                results[f"alpha={bad_alpha}"] = "ok"
        # beta=0 is valid (uint8_t, no range check beyond that)
        try:
            k0, k1 = d.gen(0, 0)
            results["beta=0"] = "ok" if (isinstance(k0, str) and isinstance(k1, str)) else "bad_type"
        except Exception:
            results["beta=0"] = "unexpected_err"
        # valid alpha=0, alpha=mask
        for a_ok in [0, vl - 1]:
            try:
                k0, k1 = d.gen(a_ok, 1)
                results[f"alpha={a_ok}"] = "ok" if (isinstance(k0, str) and isinstance(k1, str)) else "bad_type"
            except Exception:
                results[f"alpha={a_ok}"] = "unexpected_err"
        return results

    def ev_dummy(pid):
        def fn(ch):
            return ("ok",)
        return fn

    r4 = run_dpf(d_gen_val, ev_dummy(0), ev_dummy(1), timeout=30)
    for key, val in r4[0].items():
        check(f"DPF gen validation: {key}", val == "ok", f"got {val}")

    # eval_range wraparound 
    (ei, eo) = (15, 6) if not small else (13, 2)
    vl = 1 << ei
    m = mpmt.ring_mask(eo)
    alpha = random.randint(0, vl - 1)
    beta = random.randint(1, m)
    bg = vl - 10
    ed = 9
    rlen = (vl - bg) + (ed + 1)

    def d_wrap(ch_e0, ch_e1):
        DC = mpmt.DpfDealer(ei, eo)
        d = DC(ch_e0, ch_e1)
        k0, k1 = d.gen(alpha, beta)
        d.send_key(k0, 0)
        d.send_key(k1, 1)
        Rv = mpmt.Rvector(eo)
        out = Rv(rlen)
        d.reveal(out)
        for i in range(rlen):
            idx = (bg + i) % vl
            exp = beta if idx == alpha else 0
            if out[i] != exp:
                return ("fail", f"i={i} idx={idx} got={out[i]} exp={exp}")
        return ("ok",)

    def ev_wrap(pid):
        def fn(ch):
            EC = mpmt.DpfEvaluator(ei, eo, pid)
            ev = EC(ch)
            key = ev.recv_key()
            Rv = mpmt.Rvector(eo)
            buf = Rv(rlen)
            ev.eval_range(key, buf, bg, ed, cores=1)
            ev.reveal(buf)
            return ("ok",)
        return fn

    r5 = run_dpf(d_wrap, ev_wrap(0), ev_wrap(1), timeout=120)
    check(f"DPF(EI={ei},EO={eo}) wraparound [{bg},{ed}]",
          r5[0][0] == "ok", f"dealer={r5[0]}")

    # eval rejects invalid cores (in-process test) 
    import socket as _socket
    from mpmt.channels import Channel
    a0, b0 = _socket.socketpair()
    a1, b1 = _socket.socketpair()
    ch_e0 = Channel(sock=a0)
    ch_e1 = Channel(sock=a1)
    d = mpmt.DpfDealer(13, 2)(ch_e0, ch_e1)
    k0, k1 = d.gen(0, 1)
    EC = mpmt.DpfEvaluator(13, 2, 0)
    ev = EC(Channel(sock=b0))
    Rv = mpmt.Rvector(2)
    buf = Rv(1 << 13)
    try:
        ev.eval(k0, buf, cores=3)
        check("DPF eval reject cores=3", False, "no error raised")
    except (ValueError, RuntimeError):
        check("DPF eval reject cores=3", True)
    b0.close(); b1.close()

    print(f"  PASS={PASS}  FAIL={FAIL}")
    return PASS, FAIL

if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--small", action="store_true")
    args = ap.parse_args()
    rc = run_tests(small=args.small)
    raise SystemExit(rc)
