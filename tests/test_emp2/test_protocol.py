"""EMP2: share, send/recv, mod, equality_test, hash. 2-party. Verified 2026-08-02."""
import sys, os, random
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
import mpmt
from test_emp2.harness import run_2party

PASS = FAIL = 0
def check(name, cond, detail=""):
    global PASS, FAIL
    if cond: PASS += 1; print(f"  ✅ {name}")
    else: FAIL += 1; print(f"  ❌ {name}  {detail}")

def recon2(r, ell):
    return mpmt.ring_add(ell, r[0], r[1])

for ell_desc, ell in [("ELL=2", 2), ("ELL=31", 31)]:
    print(f"\n--- {ell_desc} ---")
    m = mpmt.ring_mask(ell)

    # ── share scalar + reconstruct ────────────
    secret = random.randint(0, m)
    def s_fn(pid, ch):
        inst = mpmt.ShrAdd2(ell, pid)(ch)
        return inst.share_scalar(secret) if pid == 0 else inst.recv_scalar_share()
    r = run_2party(s_fn, ell)
    check(f"{ell_desc} share/reconstruct", recon2(r, ell) == secret)

    # ── send/recv scalar ──────────────────────
    val = random.randint(0, m)
    def sr_fn(pid, ch):
        inst = mpmt.ShrAdd2(ell, pid)(ch)
        if pid == 0:
            inst.send_data(val); return inst.recv_data()
        else:
            r = inst.recv_data(); inst.send_data(val); return r
    r = run_2party(sr_fn, ell)
    check(f"{ell_desc} send/recv scalar", r == [val, val])

    # ── send/recv bytes ───────────────────────
    data = b"emp2_" + bytes([random.randint(0, 255) for _ in range(8)])
    def b_fn(pid, ch):
        inst = mpmt.ShrAdd2(ell, pid)(ch)
        if pid == 0:
            inst.send_data(data); buf = bytearray(len(data)); inst.recv_data(buf); return bytes(buf)
        else:
            buf = bytearray(len(data)); inst.recv_data(buf); inst.send_data(data); return bytes(buf)
    r = run_2party(b_fn, ell)
    check(f"{ell_desc} send/recv bytes", r[0] == data and r[1] == data)

    # ── share_key (XOR, 16-byte) ──────────────
    key = bytes([random.randint(0, 255) for _ in range(16)])
    def k_fn(pid, ch):
        inst = mpmt.ShrAdd2(ell, pid)(ch)
        if pid == 0:
            return bytes(inst.share_key(key))
        else:
            buf = bytearray(16); inst.recv_key_share(buf); return bytes(buf)
    r = run_2party(k_fn, ell)
    check(f"{ell_desc} share_key XOR", bytes(a ^ b for a, b in zip(r[0], r[1])) == key)

    # ── share_element (XOR, variable-length) ──
    elem = b"elem_" + bytes([random.randint(0, 255) for _ in range(16)])
    def e_fn(pid, ch):
        inst = mpmt.ShrAdd2(ell, pid)(ch)
        if pid == 0:
            return bytes(inst.share_element(elem))
        else:
            return bytes(inst.recv_element_share())
    r = run_2party(e_fn, ell)
    check(f"{ell_desc} share_element XOR", bytes(a ^ b for a, b in zip(r[0], r[1])) == elem)

    # ── mod(a, mv) ────────────────────────────
    a_val = random.randint(0, m)
    mv = random.randint(2, min(m, 100))
    exp_mod = mpmt.ring_mod(ell, a_val, mv)
    def mod_fn(pid, ch):
        inst = mpmt.ShrAdd2(ell, pid)(ch)
        sa = inst.share_scalar(a_val) if pid == 0 else inst.recv_scalar_share()
        r = inst.mod(sa, mv)
        if pid == 0:
            inst.send_data(r); o = inst.recv_data()
        else:
            o = inst.recv_data(); inst.send_data(r)
        return mpmt.ring_add(ell, r, o)
    r = run_2party(mod_fn, ell)
    check(f"{ell_desc} mod(a,{mv})", r[0] == exp_mod and r[0] == r[1],
          f"got={r[0]} exp={exp_mod}")

    # ── equality_test(a, b) ───────────────────
    b_val = random.choice([a_val, random.randint(0, m)])
    exp_eq = 1 if a_val == b_val else 0
    def eq_fn(pid, ch):
        inst = mpmt.ShrAdd2(ell, pid)(ch)
        sa = inst.share_scalar(a_val) if pid == 0 else inst.recv_scalar_share()
        sb = inst.share_scalar(b_val) if pid == 0 else inst.recv_scalar_share()
        r = inst.equality_test(sa, sb)
        if pid == 0:
            inst.send_data(r); o = inst.recv_data()
        else:
            o = inst.recv_data(); inst.send_data(r)
        return mpmt.ring_add(ell, r, o)
    r = run_2party(eq_fn, ell)
    check(f"{ell_desc} equality_test (a{'=' if exp_eq else '≠'}b)",
          r[0] == exp_eq and r[0] == r[1],
          f"got={r[0]} exp={exp_eq}")

    # ── hash(pt, key16) ───────────────────────
    # EMP2 hash expects XOR-shared inputs: each party has a share of pt and key.
    # The circuit XOR-reconstructs internally, then computes AES-DM.
    pt = b"test_preimage_12"  # 16 bytes
    hkey = bytes([random.randint(0, 255) for _ in range(16)])
    exp_hash = mpmt.hash_aes_dm(preimage=pt, key=hkey, ell=ell)
    def h_fn(pid, ch):
        inst = mpmt.ShrAdd2(ell, pid)(ch)
        # Step 1: XOR-share preimage and key between parties
        if pid == 0:
            pt_local = inst.share_element(pt)
            key_local = inst.share_key(hkey)
        else:
            pt_local = inst.recv_element_share()
            key_buf = bytearray(16); inst.recv_key_share(key_buf); key_local = bytes(key_buf)
        # Step 2: each party hashes with their XOR-share
        r = inst.hash(pt_local, key_local)
        # Step 3: exchange additive shares and reconstruct
        if pid == 0:
            inst.send_data(r); o = inst.recv_data()
        else:
            o = inst.recv_data(); inst.send_data(r)
        return mpmt.ring_add(ell, r, o)
    r = run_2party(h_fn, ell)
    check(f"{ell_desc} hash(pt, key)", r[0] == exp_hash and r[0] == r[1],
          f"got={r[0]} exp={exp_hash}")

print(f"\n{'='*40}\n  PASS={PASS}  FAIL={FAIL}\n{'='*40}")
assert FAIL == 0
