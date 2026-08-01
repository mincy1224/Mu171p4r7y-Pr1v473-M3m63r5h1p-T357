"""ABY3 compound arithmetic: (a+b)*c, a*b+c*d, chained operations."""
import sys, os, random
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
import mpmt
from test_aby3.harness import run_3party

PASS = FAIL = 0
def check(name, cond, detail=""):
    global PASS, FAIL
    if cond: PASS += 1; print(f"  ✅ {name}")
    else: FAIL += 1; print(f"  ❌ {name}  {detail}")

def recon3(results, ell):
    return mpmt.ring_add(ell, results[0][0],
           mpmt.ring_add(ell, results[1][0], results[2][0]))

for ell_desc, ell in [("ELL=2", 2), ("ELL=6", 6)]:
    print(f"\n--- {ell_desc} ---")
    m = mpmt.ring_mask(ell)
    a, b, c, d = [random.randint(0, m) for _ in range(4)]

    # ── (a + b) * c ──────────────────────────
    exp = mpmt.ring_mul(ell, mpmt.ring_add(ell, a, b), c)
    def f1(pid, prev, nxt):
        inst = mpmt.ShrRep3(ell, pid)(prev, nxt)
        sa = inst.share_scalar(a) if pid == 0 else inst.recv_scalar_share()
        sb = inst.share_scalar(b) if pid == 0 else inst.recv_scalar_share()
        sc = inst.share_scalar(c) if pid == 0 else inst.recv_scalar_share()
        s_ab = inst.add(sa, sb)
        s_out = inst.mul(s_ab, sc)
        return (s_out.this_share, s_out.nxt_share)
    r = run_3party(f1, ell)
    check(f"{ell_desc} (a+b)*c", recon3(r, ell) == exp)

    # ── a*b + c*d ────────────────────────────
    exp = mpmt.ring_add(ell,
          mpmt.ring_mul(ell, a, b),
          mpmt.ring_mul(ell, c, d))
    def f2(pid, prev, nxt):
        inst = mpmt.ShrRep3(ell, pid)(prev, nxt)
        sa = inst.share_scalar(a) if pid == 0 else inst.recv_scalar_share()
        sb = inst.share_scalar(b) if pid == 0 else inst.recv_scalar_share()
        sc = inst.share_scalar(c) if pid == 0 else inst.recv_scalar_share()
        sd = inst.share_scalar(d) if pid == 0 else inst.recv_scalar_share()
        s_ab = inst.mul(sa, sb)
        s_cd = inst.mul(sc, sd)
        s_out = inst.add(s_ab, s_cd)
        return (s_out.this_share, s_out.nxt_share)
    r = run_3party(f2, ell)
    check(f"{ell_desc} a*b + c*d", recon3(r, ell) == exp)

    # ── (a + b) * (c + d) ────────────────────
    exp = mpmt.ring_mul(ell,
          mpmt.ring_add(ell, a, b),
          mpmt.ring_add(ell, c, d))
    def f3(pid, prev, nxt):
        inst = mpmt.ShrRep3(ell, pid)(prev, nxt)
        sa = inst.share_scalar(a) if pid == 0 else inst.recv_scalar_share()
        sb = inst.share_scalar(b) if pid == 0 else inst.recv_scalar_share()
        sc = inst.share_scalar(c) if pid == 0 else inst.recv_scalar_share()
        sd = inst.share_scalar(d) if pid == 0 else inst.recv_scalar_share()
        s_ab = inst.add(sa, sb)
        s_cd = inst.add(sc, sd)
        s_out = inst.mul(s_ab, s_cd)
        return (s_out.this_share, s_out.nxt_share)
    r = run_3party(f3, ell)
    check(f"{ell_desc} (a+b)*(c+d)", recon3(r, ell) == exp)

    # ── a*b + c  (mul then add) ──────────────
    exp = mpmt.ring_add(ell, mpmt.ring_mul(ell, a, b), c)
    def f4(pid, prev, nxt):
        inst = mpmt.ShrRep3(ell, pid)(prev, nxt)
        sa = inst.share_scalar(a) if pid == 0 else inst.recv_scalar_share()
        sb = inst.share_scalar(b) if pid == 0 else inst.recv_scalar_share()
        sc = inst.share_scalar(c) if pid == 0 else inst.recv_scalar_share()
        s_ab = inst.mul(sa, sb)
        s_out = inst.add(s_ab, sc)
        return (s_out.this_share, s_out.nxt_share)
    r = run_3party(f4, ell)
    check(f"{ell_desc} a*b + c", recon3(r, ell) == exp)

    # ── 3-term chain: a*b + c*d + a*c ────────
    exp = mpmt.ring_add(ell,
          mpmt.ring_add(ell, mpmt.ring_mul(ell, a, b),
                            mpmt.ring_mul(ell, c, d)),
          mpmt.ring_mul(ell, a, c))
    def f5(pid, prev, nxt):
        inst = mpmt.ShrRep3(ell, pid)(prev, nxt)
        sa = inst.share_scalar(a) if pid == 0 else inst.recv_scalar_share()
        sb = inst.share_scalar(b) if pid == 0 else inst.recv_scalar_share()
        sc = inst.share_scalar(c) if pid == 0 else inst.recv_scalar_share()
        sd = inst.share_scalar(d) if pid == 0 else inst.recv_scalar_share()
        s_ab = inst.mul(sa, sb)
        s_cd = inst.mul(sc, sd)
        s_ac = inst.mul(sa, sc)
        s1 = inst.add(s_ab, s_cd)
        s_out = inst.add(s1, s_ac)
        return (s_out.this_share, s_out.nxt_share)
    r = run_3party(f5, ell)
    check(f"{ell_desc} a*b + c*d + a*c", recon3(r, ell) == exp)

print(f"\n{'='*40}\n  PASS={PASS}  FAIL={FAIL}\n{'='*40}")
assert FAIL == 0
