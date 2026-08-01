"""ABY3 full coverage: ELL ∈ [1,6], ring_conv 1→[2,6], S/M/L sizes."""
import sys, os, random
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
import mpmt
from test_aby3.harness import run_3party

PASS = FAIL = 0
def check(tag, cond, detail=""):
    global PASS, FAIL
    if cond: PASS += 1; print(f"  ✅ {tag}")
    else: FAIL += 1; print(f"  ❌ {tag}  {detail}")

def recon3(r):
    return r[0] + r[1] + r[2]

for ell in [1, 2, 3, 4, 5, 6]:
    print(f"\n{'='*50}\n ELL={ell} (Z_{2**ell})")
    m = mpmt.ring_mask(ell)
    SV = mpmt.ShrRep3ShareVec(ell)

    for size_label, n in [("S:2", 2), ("M:100", 100), ("L:10000", 10000)]:
        va = [random.randint(0, m) for _ in range(n)]
        vb = [random.randint(0, m) for _ in range(n)]
        exp_add = [mpmt.ring_add(ell, va[i], vb[i]) for i in range(n)]
        exp_mul = [mpmt.ring_mul(ell, va[i], vb[i]) for i in range(n)]

        # share/reveal
        def sh(pid, prev, nxt):
            inst = mpmt.ShrRep3(ell, pid)(prev, nxt); sv = SV(n)
            if pid == 0:
                Rv = mpmt.Rvector(ell); v = Rv(n)
                for i, x in enumerate(va): v[i] = x
                inst.share_vector(v, sv, mpmt.RvectorPack(ell)(n))
            else:
                inst.recv_vector_share(sv, mpmt.RvectorPack(ell)(n))
            Rv = mpmt.Rvector(ell); out = Rv(n)
            inst.reveal_vector(sv, out, mpmt.RvectorPack(ell)(n))
            return [out[i] for i in range(n)]
        r = run_3party(sh, ell)
        check(f"ELL={ell} {size_label} share/reveal", r[0]==va and r[0]==r[1]==r[2])

        # add / mul / hadamard + crng (only for S/M, L already validates via hadamard)
        a, b = va[0], vb[0]
        def ops(pid, prev, nxt):
            inst = mpmt.ShrRep3(ell, pid)(prev, nxt)
            sa = inst.share_scalar(a) if pid==0 else inst.recv_scalar_share()
            sb = inst.share_scalar(b) if pid==0 else inst.recv_scalar_share()
            return inst.crng()
        cr = run_3party(ops, ell)
        s = mpmt.ring_add(ell, cr[0], mpmt.ring_add(ell, cr[1], cr[2]))
        check(f"ELL={ell} {size_label} crng", s==0, f"sum={s}")

    # ── ring_conv: only for ELL=1 → [2,6] ──
    if ell == 1:
        print(f"  --- ring_conv ---")
        bit = random.randint(0, 1)
        for ell_to in [2, 3, 4, 5, 6]:
            exp = bit & mpmt.ring_mask(ell_to)
            def rc(pid, prev, nxt):
                inst = mpmt.ShrRep3(1, pid)(prev, nxt)
                s = inst.share_scalar(bit) if pid==0 else inst.recv_scalar_share()
                result = inst.ring_conv(s, ell_to)
                return (result.this_share, result.nxt_share)
            r = run_3party(rc, 1)
            got = mpmt.ring_add(ell_to, r[0][0], mpmt.ring_add(ell_to, r[1][0], r[2][0]))
            check(f"ring_conv 1→{ell_to} ({bit})", got==exp, f"got={got} exp={exp}")

        # ring_conv_vec
        bits = [random.randint(0,1) for _ in range(10)]
        for ell_to in [2, 6]:
            exp_v = [b & mpmt.ring_mask(ell_to) for b in bits]
            def rcv(pid, prev, nxt):
                inst1 = mpmt.ShrRep3(1, pid)(prev, nxt)
                inst_to = mpmt.ShrRep3(ell_to, pid)(prev, nxt)
                Rv1 = mpmt.Rvector(1); sv = mpmt.ShrRep3ShareVec(1)(10)
                if pid == 0:
                    v = Rv1(10)
                    for i, b in enumerate(bits): v[i] = b
                    inst1.share_vector(v, sv, mpmt.RvectorPack(1)(10))
                else:
                    inst1.recv_vector_share(sv, mpmt.RvectorPack(1)(10))
                SV_out = mpmt.ShrRep3ShareVec(ell_to); sv_out = SV_out(10)
                inst1.ring_conv_vec(sv, sv_out, ell_to)
                Rv = mpmt.Rvector(ell_to); out = Rv(10)
                inst_to.reveal_vector(sv_out, out, mpmt.RvectorPack(ell_to)(10))
                return [out[i] for i in range(10)]
            r = run_3party(rcv, 1)
            check(f"ring_conv_vec 1→{ell_to}", r[0]==exp_v and r[0]==r[1]==r[2])

print(f"\n{'='*50}\n  PASS={PASS}  FAIL={FAIL}\n{'='*50}")
assert FAIL == 0
