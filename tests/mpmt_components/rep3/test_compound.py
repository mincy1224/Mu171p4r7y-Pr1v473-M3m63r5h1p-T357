"""ABY3 compound operations — multi-step sequences combining share/compute/reveal."""
import sys, os, random
_sys_t = os.path.dirname(os.path.abspath(__file__))
while _sys_t and not os.path.isdir(os.path.join(_sys_t, 'common')):
    _sys_t = os.path.dirname(_sys_t)
sys.path.insert(0, _sys_t)
import mpmt
from mpmt_components.rep3.harness import run_3party

PASS = FAIL = 0

def check(name, cond, detail=""):
    global PASS, FAIL
    if cond: PASS += 1
    else: FAIL += 1; print(f"  FAIL {name}  {detail}")

def recon3(r, ell):
    return mpmt.ring_add(ell, r[0][0], mpmt.ring_add(ell, r[1][0], r[2][0]))

def run_tests(small=False):
    global PASS, FAIL
    PASS = FAIL = 0
    print("=== ABY3 Compound ===")

    ells = [1, 6] if small else list(range(1, 7))

    for ell in ells:
        m = mpmt.ring_mask(ell)
        SV = mpmt.ShrRep3ShareVec(ell)
        Rv = mpmt.Rvector(ell)
        label = f"ABY3(ell={ell})"

        a = random.randint(0, m); b = random.randint(0, m); c = random.randint(0, m)
        exp_abc = mpmt.ring_mul(ell, mpmt.ring_add(ell, a, b), c)
        def compound1_fn(pid, prev, nxt):
            inst = mpmt.ShrRep3(ell, pid)(prev, nxt)
            sa = inst.share_scalar(a) if pid == 0 else inst.recv_scalar_share()
            sb = inst.share_scalar(b) if pid == 0 else inst.recv_scalar_share()
            sc = inst.share_scalar(c) if pid == 0 else inst.recv_scalar_share()
            s_ab = inst.add(sa, sb)
            s_abc = inst.mul(s_ab, sc)
            return (s_abc.this_share, s_abc.nxt_share)
        r = run_3party(compound1_fn, ell)
        check(f"{label} (a+b)*c scalar", recon3(r, ell) == exp_abc)

        exp_abc2 = mpmt.ring_mul(ell, mpmt.ring_sub(ell, a, b), c)
        def compound2_fn(pid, prev, nxt):
            inst = mpmt.ShrRep3(ell, pid)(prev, nxt)
            sa = inst.share_scalar(a) if pid == 0 else inst.recv_scalar_share()
            sb = inst.share_scalar(b) if pid == 0 else inst.recv_scalar_share()
            sc = inst.share_scalar(c) if pid == 0 else inst.recv_scalar_share()
            s_ab = inst.sub(sa, sb)
            s_abc = inst.mul(s_ab, sc)
            return (s_abc.this_share, s_abc.nxt_share)
        r = run_3party(compound2_fn, ell)
        check(f"{label} (a-b)*c scalar", recon3(r, ell) == exp_abc2)

        nv = 16
        va = [random.randint(0, m) for _ in range(nv)]
        vb = [random.randint(0, m) for _ in range(nv)]
        vc = [random.randint(0, m) for _ in range(nv)]
        exp_add = [mpmt.ring_add(ell, va[i], vb[i]) for i in range(nv)]
        exp_final = [mpmt.ring_mul(ell, exp_add[i], vc[i]) for i in range(nv)]
        def vec_chain_fn(pid, prev, nxt):
            inst = mpmt.ShrRep3(ell, pid)(prev, nxt)
            sa = SV(nv); sb = SV(nv); sc = SV(nv)
            if pid == 0:
                v1 = Rv(nv); v2 = Rv(nv); v3 = Rv(nv)
                for i in range(nv):
                    v1[i] = va[i]; v2[i] = vb[i]; v3[i] = vc[i]
                inst.share_vector(v1, sa, mpmt.RvectorPack(ell)(nv))
                inst.share_vector(v2, sb, mpmt.RvectorPack(ell)(nv))
                inst.share_vector(v3, sc, mpmt.RvectorPack(ell)(nv))
            else:
                inst.recv_vector_share(sa, mpmt.RvectorPack(ell)(nv))
                inst.recv_vector_share(sb, mpmt.RvectorPack(ell)(nv))
                inst.recv_vector_share(sc, mpmt.RvectorPack(ell)(nv))
            s_ab = SV(nv)
            inst.add_vec(sa, sb, s_ab)
            s_final = SV(nv)
            inst.hadamard(s_ab, sc, s_final)
            out = Rv(nv)
            inst.reveal_vector(s_final, out, mpmt.RvectorPack(ell)(nv))
            return [out[i] for i in range(nv)]
        r = run_3party(vec_chain_fn, ell)
        check(f"{label} (a+b)*c vec chain",
              r[0] == exp_final and r[0] == r[1] == r[2])

        vs0 = random.randint(0, m)
        vs1 = random.randint(0, m)
        vs2 = random.randint(0, m)
        plain_sum = mpmt.ring_add(ell, vs0, mpmt.ring_add(ell, vs1, vs2))
        dv = random.randint(0, m)
        exp_with_d = mpmt.ring_add(ell, plain_sum, dv)
        def reshare_add_fn(pid, prev, nxt):
            inst = mpmt.ShrRep3(ell, pid)(prev, nxt)
            vs = [vs0, vs1, vs2]
            ss = inst.reshare_scalar(vs[pid])
            sd = inst.share_scalar(dv) if pid == 0 else inst.recv_scalar_share()
            s_sum = inst.add(ss, sd)
            return inst.reveal_scalar(s_sum)
        r = run_3party(reshare_add_fn, ell)
        check(f"{label} reshare+add+reveal", r[0] == exp_with_d and r[0] == r[1] == r[2])

        nv2 = 8
        vx = [random.randint(0, m) for _ in range(nv2)]
        vy = [random.randint(0, m) for _ in range(nv2)]
        vz = [random.randint(0, m) for _ in range(nv2)]
        exp_xy = [mpmt.ring_mul(ell, vx[i], vy[i]) for i in range(nv2)]
        exp_result = [mpmt.ring_add(ell, exp_xy[i], vz[i]) for i in range(nv2)]
        def multi_fn(pid, prev, nxt):
            inst = mpmt.ShrRep3(ell, pid)(prev, nxt)
            sx = SV(nv2); sy = SV(nv2); sz = SV(nv2)
            if pid == 0:
                v1 = Rv(nv2); v2 = Rv(nv2); v3 = Rv(nv2)
                for i in range(nv2):
                    v1[i] = vx[i]; v2[i] = vy[i]; v3[i] = vz[i]
                inst.share_vector(v1, sx, mpmt.RvectorPack(ell)(nv2))
                inst.share_vector(v2, sy, mpmt.RvectorPack(ell)(nv2))
                inst.share_vector(v3, sz, mpmt.RvectorPack(ell)(nv2))
            else:
                inst.recv_vector_share(sx, mpmt.RvectorPack(ell)(nv2))
                inst.recv_vector_share(sy, mpmt.RvectorPack(ell)(nv2))
                inst.recv_vector_share(sz, mpmt.RvectorPack(ell)(nv2))
            s_xy = SV(nv2)
            inst.hadamard(sx, sy, s_xy)
            s_out = SV(nv2)
            inst.add_vec(s_xy, sz, s_out)
            out = Rv(nv2)
            inst.reveal_vector(s_out, out, mpmt.RvectorPack(ell)(nv2))
            return [out[i] for i in range(nv2)]
        r = run_3party(multi_fn, ell)
        check(f"{label} hadamard+add_vec+reveal",
              r[0] == exp_result and r[0] == r[1] == r[2])

        nv3 = 16
        vu = [random.randint(0, m) for _ in range(nv3)]
        vw = [random.randint(0, m) for _ in range(nv3)]
        exp_dot_from_had = sum(mpmt.ring_mul(ell, vu[i], vw[i]) for i in range(nv3)) & m
        def dot_via_had_fn(pid, prev, nxt):
            inst = mpmt.ShrRep3(ell, pid)(prev, nxt)
            su = SV(nv3); sw = SV(nv3)
            if pid == 0:
                v1 = Rv(nv3); v2 = Rv(nv3)
                for i in range(nv3): v1[i] = vu[i]; v2[i] = vw[i]
                inst.share_vector(v1, su, mpmt.RvectorPack(ell)(nv3))
                inst.share_vector(v2, sw, mpmt.RvectorPack(ell)(nv3))
            else:
                inst.recv_vector_share(su, mpmt.RvectorPack(ell)(nv3))
                inst.recv_vector_share(sw, mpmt.RvectorPack(ell)(nv3))
            ss = inst.dot(su, sw)
            return (ss.this_share, ss.nxt_share)
        r = run_3party(dot_via_had_fn, ell)
        check(f"{label} dot via hadamard sum", recon3(r, ell) == exp_dot_from_had)

        def ctr_multi_fn(pid, prev, nxt):
            inst = mpmt.ShrRep3(ell, pid)(prev, nxt)
            before_s = inst.bytes_sent()
            if pid == 0:
                ss = inst.share_scalar(1)
            else:
                ss = inst.recv_scalar_share()
            after_share_s = inst.bytes_sent()
            if pid == 0:
                inst.share_scalar(2)
            else:
                inst.recv_scalar_share()
            after_share2_s = inst.bytes_sent()
            return (before_s, after_share_s, after_share2_s)
        r = run_3party(ctr_multi_fn, ell)
        for p in range(3):
            _, after_s1, after_s2 = r[p]
            check(f"{label} P{p} counters monotonic", after_s2 >= after_s1)
            check(f"{label} P{p} counters accumulating", after_s2 > 0)

    print("--- ABY3 ring_conv compound ---")
    bit = random.randint(0, 1)
    RC_ELLS = [2, 6] if small else list(range(2, 7))
    for ell_to in RC_ELLS:
        exp = bit & mpmt.ring_mask(ell_to)
        def rc_compound_fn(pid, prev, nxt):
            inst_bin = mpmt.ShrRep3(1, pid)(prev, nxt)
            inst_out = mpmt.ShrRep3(ell_to, pid)(prev, nxt)
            ss_bin = inst_bin.share_scalar(bit) if pid == 0 else inst_bin.recv_scalar_share()
            ss_out = inst_bin.ring_conv(ss_bin, ell_to)
            return inst_out.reveal_scalar(ss_out)
        r = run_3party(rc_compound_fn, 1)
        check(f"ring_conv compound 1->{ell_to} reveal",
              r[0] == exp and r[0] == r[1] == r[2])

    print(f"  PASS={PASS}  FAIL={FAIL}")
    return PASS, FAIL

if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--small", action="store_true")
    args = ap.parse_args()
    rc = run_tests(small=args.small)
    raise SystemExit(0 if rc[1] == 0 else 1)
