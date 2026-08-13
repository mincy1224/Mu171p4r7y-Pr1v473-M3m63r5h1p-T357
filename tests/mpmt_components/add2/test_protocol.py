"""EMP2: complete protocol tests — scalar ops, circuit ops, byte I/O, error paths."""
import sys, os, random
_sys_t = os.path.dirname(os.path.abspath(__file__))
while _sys_t and not os.path.isdir(os.path.join(_sys_t, 'common')):
    _sys_t = os.path.dirname(_sys_t)
sys.path.insert(0, _sys_t)
import mpmt
from mpmt_components.add2.harness import run_2party

PASS = FAIL = 0

def check(name, cond, detail=""):
    global PASS, FAIL
    if cond: PASS += 1
    else: FAIL += 1; print(f"  FAIL {name}  {detail}")

def recon2(r, ell):
    return mpmt.ring_add(ell, r[0], r[1])

def run_tests(small=False):
    global PASS, FAIL
    PASS = FAIL = 0
    print("=== EMP2 Protocol ===")

    ells = [2, 31] if small else list(range(2, 32))

    for ell in ells:
        m = mpmt.ring_mask(ell)
        label = f"EMP2(ell={ell})"

        val = random.randint(0, m)
        def share_fn(pid, ch):
            inst = mpmt.ShrAdd2(ell, pid)(ch)
            if pid == 0:
                return inst.share_scalar(val)
            else:
                return inst.recv_scalar_share()
        r = run_2party(share_fn, ell)
        check(f"{label} share+recv scalar", recon2(r, ell) == val)

        sv = random.randint(0, m)
        def srs_fn(pid, ch):
            inst = mpmt.ShrAdd2(ell, pid)(ch)
            if pid == 0:
                inst.send_data(sv)
                return inst.recv_data()
            else:
                rv = inst.recv_data()
                inst.send_data(sv)
                return rv
        r = run_2party(srs_fn, ell)
        check(f"{label} send/recv scalar bidirectional",
              r[0] == sv and r[1] == sv)

        for nbytes in [4, 8, 64, 256] if not small else [4, 64]:
            payload = bytes([random.randint(0, 255) for _ in range(nbytes)])
            def srb_fn(pid, ch):
                inst = mpmt.ShrAdd2(ell, pid)(ch)
                if pid == 0:
                    inst.send_data(payload)
                else:
                    buf = bytearray(nbytes)
                    inst.recv_data(buf)
                    return bytes(buf)
                return None
            r = run_2party(srb_fn, ell)
            check(f"{label} send/recv_bytes {nbytes}B",
                  r[1] == payload)

        if ell < 31:
            bad_val = m + 1
            def oob_fn(pid, ch):
                inst = mpmt.ShrAdd2(ell, pid)(ch)
                try:
                    inst.send_data(bad_val)
                    return "no_error"
                except (ValueError, RuntimeError):
                    return "ok"
            r = run_2party(oob_fn, ell)
            check(f"{label} reject send OOB scalar",
                  r[0] == "ok" and r[1] == "ok")

        def bad_buf_fn(pid, ch):
            inst = mpmt.ShrAdd2(ell, pid)(ch)
            if pid == 0:
                inst.send_data(b'\x00' * 4)
            else:
                try:
                    inst.recv_data(b'\x00' * 4)
                    return "no_error"
                except (ValueError, RuntimeError):
                    return "ok"
            return None
        r = run_2party(bad_buf_fn, ell)
        check(f"{label} reject recv_data non-bytearray",
              r[1] == "ok")

        key = bytes([random.randint(0, 255) for _ in range(16)])
        def sk_fn(pid, ch):
            inst = mpmt.ShrAdd2(ell, pid)(ch)
            if pid == 0:
                return bytes(inst.share_key(key))
            else:
                buf = bytearray(16)
                inst.recv_key_share(buf)
                return bytes(buf)
        r = run_2party(sk_fn, ell)
        recon_key = bytes(a ^ b for a, b in zip(r[0], r[1]))
        check(f"{label} share_key XOR", recon_key == key)

        def bad_key_fn(pid, ch):
            inst = mpmt.ShrAdd2(ell, pid)(ch)
            if pid == 0:
                try:
                    inst.share_key(b'short')
                    return "no_error"
                except (ValueError, RuntimeError):
                    return "ok"
            else:
                return "skip"
        r = run_2party(bad_key_fn, ell)
        check(f"{label} reject share_key len!=16", r[0] == "ok")

        def bad_rk_fn(pid, ch):
            inst = mpmt.ShrAdd2(ell, pid)(ch)
            if pid == 0:
                inst.share_key(bytes(16))
            else:
                try:
                    inst.recv_key_share(bytearray(8))
                    return "no_error"
                except (ValueError, RuntimeError):
                    return "ok"
            return None
        r = run_2party(bad_rk_fn, ell)
        check(f"{label} reject recv_key_share len!=16", r[1] == "ok")

        def bad_rk2_fn(pid, ch):
            inst = mpmt.ShrAdd2(ell, pid)(ch)
            if pid == 0:
                inst.share_key(bytes(16))
            else:
                try:
                    inst.recv_key_share(b'0123456789abcdef')
                    return "no_error"
                except (ValueError, RuntimeError):
                    return "ok"
            return None
        r = run_2party(bad_rk2_fn, ell)
        check(f"{label} reject recv_key_share non-bytearray", r[1] == "ok")

        elem = bytes([random.randint(0, 255) for _ in range(random.randint(8, 128))])
        def se_fn(pid, ch):
            inst = mpmt.ShrAdd2(ell, pid)(ch)
            if pid == 0:
                return bytes(inst.share_element(elem))
            else:
                return bytes(inst.recv_element_share())
        r = run_2party(se_fn, ell)
        recon = bytes(a ^ b for a, b in zip(r[0], r[1]))
        check(f"{label} share_element bytes len={len(elem)}", recon == elem)

        s = "hello_é_test"
        def sestr_fn(pid, ch):
            inst = mpmt.ShrAdd2(ell, pid)(ch)
            if pid == 0:
                return bytes(inst.share_element(s))
            else:
                return bytes(inst.recv_element_share())
        r = run_2party(sestr_fn, ell)
        recon_str = bytes(a ^ b for a, b in zip(r[0], r[1]))
        check(f"{label} share_element str", recon_str.decode('utf-8') == s)

        eq_a = random.randint(0, m)
        eq_b = random.choice([eq_a, random.randint(0, m)])
        exp_eq = 1 if eq_a == eq_b else 0
        def eq_fn(pid, ch):
            inst = mpmt.ShrAdd2(ell, pid)(ch)
            sa = inst.share_scalar(eq_a) if pid == 0 else inst.recv_scalar_share()
            sb = inst.share_scalar(eq_b) if pid == 0 else inst.recv_scalar_share()
            r = inst.equality_test(sa, sb)
            if pid == 0:
                inst.send_data(r)
                o = inst.recv_data()
            else:
                o = inst.recv_data()
                inst.send_data(r)
            return mpmt.ring_add(ell, r, o)
        r = run_2party(eq_fn, ell)
        match_str = "equal" if eq_a == eq_b else "not_equal"
        check(f"{label} eq({match_str})", r[0] == exp_eq and r[0] == r[1])

        mv = random.randint(2, min(m, 100))
        mod_a = random.randint(0, m)
        exp_mod = mpmt.ring_mod(ell, mod_a, mv)
        def mod_fn(pid, ch):
            inst = mpmt.ShrAdd2(ell, pid)(ch)
            sa = inst.share_scalar(mod_a) if pid == 0 else inst.recv_scalar_share()
            r = inst.mod(sa, mv)
            if pid == 0:
                inst.send_data(r)
                o = inst.recv_data()
            else:
                o = inst.recv_data()
                inst.send_data(r)
            return mpmt.ring_add(ell, r, o)
        r = run_2party(mod_fn, ell)
        check(f"{label} mod {mod_a}%{mv}", r[0] == exp_mod and r[0] == r[1])

        pt = bytes([random.randint(0, 255) for _ in range(16)])
        hkey = bytes([random.randint(0, 255) for _ in range(16)])
        exp_h = mpmt.hash_aes_dm(preimage=pt, key=hkey, ell=ell)
        def hash_fn(pid, ch):
            inst = mpmt.ShrAdd2(ell, pid)(ch)
            if pid == 0:
                pt_l = inst.share_element(pt)
                k_l = inst.share_key(hkey)
            else:
                pt_l = inst.recv_element_share()
                kb = bytearray(16)
                inst.recv_key_share(kb)
                k_l = bytes(kb)
            r = inst.hash(pt_l, k_l)
            if pid == 0:
                inst.send_data(r)
                o = inst.recv_data()
            else:
                o = inst.recv_data()
                inst.send_data(r)
            return mpmt.ring_add(ell, r, o)
        r = run_2party(hash_fn, ell)
        check(f"{label} hash in-circuit", r[0] == exp_h and r[0] == r[1])

        def ctr_fn(pid, ch):
            inst = mpmt.ShrAdd2(ell, pid)(ch)
            init_s = inst.bytes_sent()
            init_r = inst.bytes_recv()
            if pid == 0:
                inst.send_data(1)
                _ = inst.recv_data()
            else:
                _ = inst.recv_data()
                inst.send_data(1)
            after_s = inst.bytes_sent()
            after_r = inst.bytes_recv()
            inst.clear_send_cnt()
            inst.clear_recv_cnt()
            return (init_s, init_r, after_s, after_r,
                    inst.bytes_sent(), inst.bytes_recv())
        r = run_2party(ctr_fn, ell)
        for p in range(2):
            init_s, init_r, after_s, after_r, clr_s, clr_r = r[p]
            check(f"{label} P{p} init bytes_sent is int", isinstance(init_s, int))
            check(f"{label} P{p} init bytes_recv is int", isinstance(init_r, int))
            check(f"{label} P{p} after I/O bytes_sent>0", after_s > 0)
            check(f"{label} P{p} after clear cnt==0", clr_s == 0 and clr_r == 0)

    if not small:
        print("--- EMP2 instance reuse ---")
        for ell_a, ell_b in [(2, 31), (31, 4)]:
            m_a = mpmt.ring_mask(ell_a)
            m_b = mpmt.ring_mask(ell_b)
            v_a = random.randint(0, m_a)
            v_b = random.randint(0, m_b)
            def reuse_fn(pid, ch):
                inst1 = mpmt.ShrAdd2(ell_a, pid)(ch)
                if pid == 0:
                    inst1.send_data(v_a)
                    r1 = inst1.recv_data()
                else:
                    r1 = inst1.recv_data()
                    inst1.send_data(v_a)
                inst2 = mpmt.ShrAdd2(ell_b, pid)(ch)
                if pid == 0:
                    inst2.send_data(v_b)
                    r2 = inst2.recv_data()
                else:
                    r2 = inst2.recv_data()
                    inst2.send_data(v_b)
                return (r1, r2, ell_a, ell_b)
            r = run_2party(reuse_fn, max(ell_a, ell_b))
            check(f"reuse {ell_a}->{ell_b} val_a", r[0][0] == v_a and r[1][0] == v_a)
            check(f"reuse {ell_a}->{ell_b} val_b", r[0][1] == v_b and r[1][1] == v_b)

    print(f"  PASS={PASS}  FAIL={FAIL}")
    return PASS, FAIL

if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--small", action="store_true")
    args = ap.parse_args()
    rc = run_tests(small=args.small)
    raise SystemExit(0 if rc[1] == 0 else 1)
