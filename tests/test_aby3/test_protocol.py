"""ABY3 core protocol: crng, share, add, mul, vector, hadamard — 1 boundary + 1 normal ELL."""
import sys, os, random
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
import mpmt
from test_aby3.harness import run_3party

PASS = FAIL = 0
def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1; print(f"  ✅ {name}")
    else:
        FAIL += 1; print(f"  ❌ {name}  {detail}")

# ═════════════════════════════════════════════
for ell_desc, ell in [("ELL=2", 2), ("ELL=6", 6)]:
    print(f"\n--- {ell_desc} ---")
    m = mpmt.ring_mask(ell)

    # ── crng (returns uint8_t, 3-party correlated) ──
    def crng_fn(pid, prev, nxt):
        inst = mpmt.ShrRep3(ell, pid)(prev, nxt)
        return inst.crng()
    r = run_3party(crng_fn, ell)
    s = mpmt.ring_add(ell, r[0], mpmt.ring_add(ell, r[1], r[2]))
    check(f"{ell_desc} crng sum-zero", s == 0, f"sum={s}")

    # ── share scalar + reconstruct ────────────
    secret = random.randint(0, m)
    def share_fn(pid, prev, nxt):
        inst = mpmt.ShrRep3(ell, pid)(prev, nxt)
        if pid == 0:
            ss = inst.share_scalar(secret)
        else:
            ss = inst.recv_scalar_share()
        return (ss.this_share, ss.nxt_share)
    r = run_3party(share_fn, ell)
    got = mpmt.ring_add(ell, r[0][0], mpmt.ring_add(ell, r[1][0], r[2][0]))
    check(f"{ell_desc} share/reconstruct", got == secret, f"got={got} exp={secret}")

    # ── add ───────────────────────────────────
    a, b = random.randint(0, m), random.randint(0, m)
    exp = mpmt.ring_add(ell, a, b)
    def add_fn(pid, prev, nxt):
        inst = mpmt.ShrRep3(ell, pid)(prev, nxt)
        sa = inst.share_scalar(a) if pid == 0 else inst.recv_scalar_share()
        sb = inst.share_scalar(b) if pid == 0 else inst.recv_scalar_share()
        so = inst.add(sa, sb)
        return (so.this_share, so.nxt_share)
    r = run_3party(add_fn, ell)
    got = mpmt.ring_add(ell, r[0][0], mpmt.ring_add(ell, r[1][0], r[2][0]))
    check(f"{ell_desc} add", got == exp, f"got={got} exp={exp}")

    # ── mul ───────────────────────────────────
    exp = mpmt.ring_mul(ell, a, b)
    def mul_fn(pid, prev, nxt):
        inst = mpmt.ShrRep3(ell, pid)(prev, nxt)
        sa = inst.share_scalar(a) if pid == 0 else inst.recv_scalar_share()
        sb = inst.share_scalar(b) if pid == 0 else inst.recv_scalar_share()
        so = inst.mul(sa, sb)
        return (so.this_share, so.nxt_share)
    r = run_3party(mul_fn, ell)
    got = mpmt.ring_add(ell, r[0][0], mpmt.ring_add(ell, r[1][0], r[2][0]))
    check(f"{ell_desc} mul", got == exp, f"got={got} exp={exp}")

    # ── vector share + reveal ─────────────────
    n_elems = 8
    vals = [random.randint(0, m) for _ in range(n_elems)]
    SV = mpmt.ShrRep3ShareVec(ell)
    def vec_fn(pid, prev, nxt):
        inst = mpmt.ShrRep3(ell, pid)(prev, nxt)
        sv = SV(n_elems); aux = mpmt.RvectorPack(ell)(n_elems)
        if pid == 0:
            Rv = mpmt.Rvector(ell); v = Rv(n_elems)
            for i, val in enumerate(vals): v[i] = val
            inst.share_vector(v, sv, aux)
        else:
            inst.recv_vector_share(sv, aux)
        Rv = mpmt.Rvector(ell); out = Rv(n_elems)
        inst.reveal_vector(sv, out, mpmt.RvectorPack(ell)(n_elems))
        return [out[i] for i in range(n_elems)]
    r = run_3party(vec_fn, ell)
    ok = r[0] == vals and r[0] == r[1] == r[2]
    check(f"{ell_desc} vec share/reveal", ok, f"expected equality")

    # ── hadamard (element-wise mul) ───────────
    va = [random.randint(0, m) for _ in range(n_elems)]
    vb = [random.randint(0, m) for _ in range(n_elems)]
    exp_had = [mpmt.ring_mul(ell, va[i], vb[i]) for i in range(n_elems)]
    def had_fn(pid, prev, nxt):
        inst = mpmt.ShrRep3(ell, pid)(prev, nxt)
        sv_a = SV(n_elems); sv_b = SV(n_elems); sv_out = SV(n_elems)
        if pid == 0:
            Rv = mpmt.Rvector(ell)
            v = Rv(n_elems)
            for i, val in enumerate(va): v[i] = val
            inst.share_vector(v, sv_a, mpmt.RvectorPack(ell)(n_elems))
        else:
            inst.recv_vector_share(sv_a, mpmt.RvectorPack(ell)(n_elems))
        if pid == 0:
            Rv = mpmt.Rvector(ell)
            v = Rv(n_elems)
            for i, val in enumerate(vb): v[i] = val
            inst.share_vector(v, sv_b, mpmt.RvectorPack(ell)(n_elems))
        else:
            inst.recv_vector_share(sv_b, mpmt.RvectorPack(ell)(n_elems))
        inst.hadamard(sv_a, sv_b, sv_out)
        Rv = mpmt.Rvector(ell); out = Rv(n_elems)
        inst.reveal_vector(sv_out, out, mpmt.RvectorPack(ell)(n_elems))
        return [out[i] for i in range(n_elems)]
    r = run_3party(had_fn, ell)
    check(f"{ell_desc} hadamard", r[0] == exp_had and r[0] == r[1] == r[2])

print(f"\n{'='*40}\n  PASS={PASS}  FAIL={FAIL}\n{'='*40}")
assert FAIL == 0
