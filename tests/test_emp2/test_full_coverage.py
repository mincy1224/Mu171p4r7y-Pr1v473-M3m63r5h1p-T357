"""EMP2 full ELL coverage: ELL ∈ [2,31], small/medium/large data per ELL."""
import sys, os, random
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
import mpmt
from test_emp2.harness import run_2party

PASS = FAIL = 0
def check(ell, label, op, cond, detail=""):
    global PASS, FAIL
    tag = f"ELL={ell:2d} {label:5s} {op}"
    if cond: PASS += 1; print(f"  ✅ {tag}")
    else: FAIL += 1; print(f"  ❌ {tag}  {detail}")

def recon2(r, ell):
    return mpmt.ring_add(ell, r[0], r[1])

for ell in range(2, 32):  # [2, 31]
    m = mpmt.ring_mask(ell)

    for size_label, nbytes in [("S:8B", 8), ("M:1K", 1024), ("L:64K", 65536)]:
        # ── share/reconstruct scalar ──────────
        secret = random.randint(0, m)
        def s_fn(pid, ch):
            inst = mpmt.ShrAdd2(ell, pid)(ch)
            return inst.share_scalar(secret) if pid == 0 else inst.recv_scalar_share()
        r = run_2party(s_fn, ell)
        check(ell, size_label, f"share({secret&0xFF}..)", recon2(r, ell) == secret)

        # ── send/recv scalar ──────────────────
        val = random.randint(0, m)
        def sr_fn(pid, ch):
            inst = mpmt.ShrAdd2(ell, pid)(ch)
            if pid == 0: inst.send_data(val); return inst.recv_data()
            else: r = inst.recv_data(); inst.send_data(val); return r
        r = run_2party(sr_fn, ell)
        ok = (r[0] == val and r[1] == val)
        check(ell, size_label, "send/recv", ok, f"got={r} exp={val}")

        # ── send/recv bytes ───────────────────
        data = bytes([random.randint(0, 255) for _ in range(nbytes)])
        def b_fn(pid, ch):
            inst = mpmt.ShrAdd2(ell, pid)(ch)
            if pid == 0:
                inst.send_data(data); buf = bytearray(nbytes); inst.recv_data(buf)
                return bytes(buf)
            else:
                buf = bytearray(nbytes); inst.recv_data(buf); inst.send_data(data)
                return bytes(buf)
        r = run_2party(b_fn, ell)
        check(ell, size_label, f"bytes({nbytes})", r[0] == data and r[1] == data)

        # ── share_key XOR ─────────────────────
        key = bytes([random.randint(0, 255) for _ in range(16)])
        def k_fn(pid, ch):
            inst = mpmt.ShrAdd2(ell, pid)(ch)
            if pid == 0: return bytes(inst.share_key(key))
            else: buf = bytearray(16); inst.recv_key_share(buf); return bytes(buf)
        r = run_2party(k_fn, ell)
        check(ell, size_label, "share_key", bytes(a^b for a,b in zip(r[0],r[1])) == key)

    # ── Circuit ops (once per ELL) ────────────
    a_val = random.randint(0, m)
    mv = random.randint(2, min(m, 127))
    exp_mod = mpmt.ring_mod(ell, a_val, mv)
    def mod_fn(pid, ch):
        inst = mpmt.ShrAdd2(ell, pid)(ch)
        sa = inst.share_scalar(a_val) if pid == 0 else inst.recv_scalar_share()
        r = inst.mod(sa, mv)
        if pid == 0: inst.send_data(r); o = inst.recv_data()
        else: o = inst.recv_data(); inst.send_data(r)
        return mpmt.ring_add(ell, r, o)
    r = run_2party(mod_fn, ell)
    check(ell, "circ", f"mod({a_val&0xFF},{mv})", r[0]==exp_mod and r[0]==r[1],
          f"got={r[0]} exp={exp_mod}")

    b_val = random.choice([a_val, random.randint(0, m)])
    exp_eq = 1 if a_val == b_val else 0
    def eq_fn(pid, ch):
        inst = mpmt.ShrAdd2(ell, pid)(ch)
        sa = inst.share_scalar(a_val) if pid == 0 else inst.recv_scalar_share()
        sb = inst.share_scalar(b_val) if pid == 0 else inst.recv_scalar_share()
        r = inst.equality_test(sa, sb)
        if pid == 0: inst.send_data(r); o = inst.recv_data()
        else: o = inst.recv_data(); inst.send_data(r)
        return mpmt.ring_add(ell, r, o)
    r = run_2party(eq_fn, ell)
    check(ell, "circ", "eq_test", r[0]==exp_eq and r[0]==r[1],
          f"got={r[0]} exp={exp_eq}")

    pt = bytes([random.randint(0, 255) for _ in range(16)])
    hkey = bytes([random.randint(0, 255) for _ in range(16)])
    exp_hash = mpmt.hash_aes_dm(preimage=pt, key=hkey, ell=ell)
    def h_fn(pid, ch):
        inst = mpmt.ShrAdd2(ell, pid)(ch)
        if pid == 0:
            pt_loc = inst.share_element(pt); key_loc = inst.share_key(hkey)
        else:
            pt_loc = inst.recv_element_share()
            kb = bytearray(16); inst.recv_key_share(kb); key_loc = bytes(kb)
        r = inst.hash(pt_loc, key_loc)
        if pid == 0: inst.send_data(r); o = inst.recv_data()
        else: o = inst.recv_data(); inst.send_data(r)
        return mpmt.ring_add(ell, r, o)
    r = run_2party(h_fn, ell)
    check(ell, "circ", "hash", r[0]==exp_hash and r[0]==r[1],
          f"got={r[0]} exp={exp_hash}")

print(f"\n{'='*50}\n  PASS={PASS}  FAIL={FAIL}\n{'='*50}")
assert FAIL == 0
