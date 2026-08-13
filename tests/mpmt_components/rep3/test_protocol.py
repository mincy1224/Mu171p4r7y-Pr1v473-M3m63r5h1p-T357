"""ABY3: complete protocol-level tests — crng_vec, all scalar/vector ops, error paths."""
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
    print("=== ABY3 Protocol ===")

    ells = [1, 6] if small else list(range(1, 7))

    for ell in ells:
        m = mpmt.ring_mask(ell)
        SV = mpmt.ShrRep3ShareVec(ell)
        Rv = mpmt.Rvector(ell)
        label = f"ABY3(ell={ell})"

        nv = 16
        def crng_vec_fn(pid, prev, nxt):
            inst = mpmt.ShrRep3(ell, pid)(prev, nxt)
            v = Rv(nv)
            inst.crng_vec(v)
            return [v[i] for i in range(nv)]
        r = run_3party(crng_vec_fn, ell)
        ok = True
        for i in range(nv):
            s = mpmt.ring_add(ell, r[0][i], mpmt.ring_add(ell, r[1][i], r[2][i]))
            if s != 0: ok = False; break
        check(f"{label} crng_vec sum=0", ok)

        secret = random.randint(0, m)
        def share_rev_fn(pid, prev, nxt):
            inst = mpmt.ShrRep3(ell, pid)(prev, nxt)
            if pid == 0:
                ss = inst.share_scalar(secret)
            else:
                ss = inst.recv_scalar_share()
            return inst.reveal_scalar(ss)
        r = run_3party(share_rev_fn, ell)
        check(f"{label} share+reveal scalar", r[0] == secret and r[0] == r[1] == r[2])

        add_shares = [random.randint(0, m) for _ in range(3)]
        plain_sum = mpmt.ring_add(ell, add_shares[0],
                         mpmt.ring_add(ell, add_shares[1], add_shares[2]))
        def resh_fn(pid, prev, nxt):
            inst = mpmt.ShrRep3(ell, pid)(prev, nxt)
            ss = inst.reshare_scalar(add_shares[pid])
            return (ss.this_share, ss.nxt_share)
        r = run_3party(resh_fn, ell)
        check(f"{label} reshare_scalar", recon3(r, ell) == plain_sum)

        a = random.randint(0, m); b = random.randint(0, m)
        def scalar_ops_fn(pid, prev, nxt):
            inst = mpmt.ShrRep3(ell, pid)(prev, nxt)
            sa = inst.share_scalar(a) if pid == 0 else inst.recv_scalar_share()
            sb = inst.share_scalar(b) if pid == 0 else inst.recv_scalar_share()
            s_add = inst.add(sa, sb)
            s_sub = inst.sub(sa, sb)
            s_mul = inst.mul(sa, sb)
            return ((s_add.this_share, s_add.nxt_share),
                    (s_sub.this_share, s_sub.nxt_share),
                    (s_mul.this_share, s_mul.nxt_share))
        r = run_3party(scalar_ops_fn, ell)
        check(f"{label} add scalar", recon3([x[0] for x in r], ell) == mpmt.ring_add(ell, a, b))
        check(f"{label} sub scalar", recon3([x[1] for x in r], ell) == mpmt.ring_sub(ell, a, b))
        check(f"{label} mul scalar", recon3([x[2] for x in r], ell) == mpmt.ring_mul(ell, a, b))

        nv = 32 if not small else 8
        vals = [random.randint(0, m) for _ in range(nv)]
        def vec_share_rev_fn(pid, prev, nxt):
            inst = mpmt.ShrRep3(ell, pid)(prev, nxt)
            sv = SV(nv)
            if pid == 0:
                v = Rv(nv)
                for i, x in enumerate(vals): v[i] = x
                inst.share_vector(v, sv, mpmt.RvectorPack(ell)(nv))
            else:
                inst.recv_vector_share(sv, mpmt.RvectorPack(ell)(nv))
            out = Rv(nv)
            inst.reveal_vector(sv, out, mpmt.RvectorPack(ell)(nv))
            return [out[i] for i in range(nv)]
        r = run_3party(vec_share_rev_fn, ell)
        check(f"{label} share+reveal vector", r[0] == vals and r[0] == r[1] == r[2])

        vec_shares = []
        for p in range(3):
            v = Rv(nv)
            for i in range(nv): v[i] = random.randint(0, m)
            vec_shares.append(v)
        exp_sum = [mpmt.ring_add(ell, vec_shares[0][i],
                        mpmt.ring_add(ell, vec_shares[1][i], vec_shares[2][i]))
                   for i in range(nv)]
        def reshare_vec_fn(pid, prev, nxt):
            inst = mpmt.ShrRep3(ell, pid)(prev, nxt)
            sv = SV(nv)
            inst.reshare_vector(vec_shares[pid], sv, mpmt.RvectorPack(ell)(nv))
            out = Rv(nv)
            inst.reveal_vector(sv, out, mpmt.RvectorPack(ell)(nv))
            return [out[i] for i in range(nv)]
        r = run_3party(reshare_vec_fn, ell)
        check(f"{label} reshare_vector", r[0] == exp_sum and r[0] == r[1] == r[2])

        va = [random.randint(0, m) for _ in range(nv)]
        vb = [random.randint(0, m) for _ in range(nv)]
        exp_add = [mpmt.ring_add(ell, va[i], vb[i]) for i in range(nv)]
        exp_sub = [mpmt.ring_sub(ell, va[i], vb[i]) for i in range(nv)]
        def vec_arith_fn(pid, prev, nxt):
            inst = mpmt.ShrRep3(ell, pid)(prev, nxt)
            sa = SV(nv); sb = SV(nv)
            if pid == 0:
                v1 = Rv(nv); v2 = Rv(nv)
                for i in range(nv): v1[i] = va[i]; v2[i] = vb[i]
                inst.share_vector(v1, sa, mpmt.RvectorPack(ell)(nv))
                inst.share_vector(v2, sb, mpmt.RvectorPack(ell)(nv))
            else:
                inst.recv_vector_share(sa, mpmt.RvectorPack(ell)(nv))
                inst.recv_vector_share(sb, mpmt.RvectorPack(ell)(nv))
            so_add = SV(nv); so_sub = SV(nv)
            inst.add_vec(sa, sb, so_add)
            inst.sub_vec(sa, sb, so_sub)
            out_add = Rv(nv); out_sub = Rv(nv)
            inst.reveal_vector(so_add, out_add, mpmt.RvectorPack(ell)(nv))
            inst.reveal_vector(so_sub, out_sub, mpmt.RvectorPack(ell)(nv))
            return ([out_add[i] for i in range(nv)],
                    [out_sub[i] for i in range(nv)])
        r = run_3party(vec_arith_fn, ell)
        check(f"{label} add_vec", r[0][0] == exp_add and r[0][0] == r[1][0] == r[2][0])
        check(f"{label} sub_vec", r[0][1] == exp_sub and r[0][1] == r[1][1] == r[2][1])

        exp_had = [mpmt.ring_mul(ell, va[i], vb[i]) for i in range(nv)]
        exp_dot = sum(exp_had) & m
        def had_dot_fn(pid, prev, nxt):
            inst = mpmt.ShrRep3(ell, pid)(prev, nxt)
            sa = SV(nv); sb = SV(nv)
            if pid == 0:
                v1 = Rv(nv); v2 = Rv(nv)
                for i in range(nv): v1[i] = va[i]; v2[i] = vb[i]
                inst.share_vector(v1, sa, mpmt.RvectorPack(ell)(nv))
                inst.share_vector(v2, sb, mpmt.RvectorPack(ell)(nv))
            else:
                inst.recv_vector_share(sa, mpmt.RvectorPack(ell)(nv))
                inst.recv_vector_share(sb, mpmt.RvectorPack(ell)(nv))
            so_had = SV(nv)
            inst.hadamard(sa, sb, so_had)
            ss_dot = inst.dot(sa, sb)
            out_had = Rv(nv)
            inst.reveal_vector(so_had, out_had, mpmt.RvectorPack(ell)(nv))
            return ([out_had[i] for i in range(nv)],
                    (ss_dot.this_share, ss_dot.nxt_share))
        r = run_3party(had_dot_fn, ell)
        check(f"{label} hadamard", r[0][0] == exp_had and r[0][0] == r[1][0] == r[2][0])
        check(f"{label} dot", recon3([x[1] for x in r], ell) == exp_dot)

        payloads = [
            (0, 1, bytes([random.randint(0, 255) for _ in range(64)])),
            (1, 2, bytes([random.randint(0, 255) for _ in range(64)])),
            (2, 0, bytes([random.randint(0, 255) for _ in range(64)])),
        ]
        for from_p, to_p, payload in payloads:
            def send_bytes_fn(pid, prev, nxt):
                inst = mpmt.ShrRep3(ell, pid)(prev, nxt)
                if pid == from_p:
                    inst.send_data(to_pid=to_p, data=payload)
                elif pid == to_p:
                    buf = bytearray(len(payload))
                    inst.recv_data(from_pid=from_p, buf=buf)
                    return bytes(buf)
                return None
            r = run_3party(send_bytes_fn, ell)
            check(f"{label} send/recv_bytes P{from_p}->P{to_p}",
                  r[to_p] == payload)

        def init_ctr_fn(pid, prev, nxt):
            inst = mpmt.ShrRep3(ell, pid)(prev, nxt)
            return (inst.bytes_sent(), inst.bytes_recv())
        r = run_3party(init_ctr_fn, ell)
        for p in range(3):
            check(f"{label} P{p} bytes_sent is int", isinstance(r[p][0], int))
            check(f"{label} P{p} bytes_recv is int", isinstance(r[p][1], int))

        def flush_fn(pid, prev, nxt):
            inst = mpmt.ShrRep3(ell, pid)(prev, nxt)
            inst.flush()
            return True
        r = run_3party(flush_fn, ell)
        check(f"{label} flush no-op", all(r))

        def self_send_fn(pid, prev, nxt):
            inst = mpmt.ShrRep3(ell, pid)(prev, nxt)
            try:
                inst.send_data(to_pid=pid, val=0)
                return "no_error"
            except (ValueError, RuntimeError):
                return "ok"
        r = run_3party(self_send_fn, ell)
        for p in range(3):
            check(f"{label} P{p} reject send to self", r[p] == "ok")

        def self_recv_fn(pid, prev, nxt):
            inst = mpmt.ShrRep3(ell, pid)(prev, nxt)
            try:
                inst.recv_data(from_pid=pid)
                return "no_error"
            except (ValueError, RuntimeError):
                return "ok"
        r = run_3party(self_recv_fn, ell)
        for p in range(3):
            check(f"{label} P{p} reject recv from self", r[p] == "ok")

        def zero_sv_fn(pid, prev, nxt):
            inst = mpmt.ShrRep3(ell, pid)(prev, nxt)
            try:
                sv = SV(0)
                inst.recv_vector_share(sv, mpmt.RvectorPack(ell)(0))
                return "no_error"
            except (ValueError, RuntimeError):
                return "ok"
        r = run_3party(zero_sv_fn, ell)
        for p in range(3):
            check(f"{label} P{p} reject recv_vector_share size=0", r[p] == "ok")

        if not small:
            def alias_reshare_fn(pid, prev, nxt):
                inst = mpmt.ShrRep3(ell, pid)(prev, nxt)
                sv = SV(8)
                try:
                    inst.reshare_vector(sv.this_share, sv, mpmt.RvectorPack(ell)(8))
                    return "no_error"
                except (ValueError, RuntimeError):
                    return "ok"
            r = run_3party(alias_reshare_fn, ell)
            for p in range(3):
                check(f"{label} P{p} reject reshare_vector alias", r[p] == "ok")

    print("--- ABY3 ring_conv ---")
    bit = random.randint(0, 1)
    RC_ELLS = [2, 6] if small else list(range(2, 7))
    for ell_to in RC_ELLS:
        exp = bit & mpmt.ring_mask(ell_to)
        def rcfn(pid, prev, nxt):
            inst = mpmt.ShrRep3(1, pid)(prev, nxt)
            ss = inst.share_scalar(bit) if pid == 0 else inst.recv_scalar_share()
            r = inst.ring_conv(ss, ell_to)
            return (r.this_share, r.nxt_share)
        r = run_3party(rcfn, 1)
        got = recon3(r, ell_to)
        check(f"ring_conv 1->{ell_to} scalar", got == exp, f"{got} vs {exp}")

    try:
        def bad_rc_fn(pid, prev, nxt):
            inst = mpmt.ShrRep3(1, pid)(prev, nxt)
            ss = inst.share_scalar(0) if pid == 0 else inst.recv_scalar_share()
            return inst.ring_conv(ss, 7)
        r = run_3party(bad_rc_fn, 1)
        check("ring_conv reject ell_to=7", False, "no error raised")
    except (ValueError, RuntimeError):
        check("ring_conv reject ell_to=7", True)

    bits_v = [random.randint(0, 1) for _ in range(8)]
    for ell_to in RC_ELLS:
        exp_v = [b & mpmt.ring_mask(ell_to) for b in bits_v]
        def rcvfn(pid, prev, nxt):
            inst1 = mpmt.ShrRep3(1, pid)(prev, nxt)
            inst_to = mpmt.ShrRep3(ell_to, pid)(prev, nxt)
            sv = mpmt.ShrRep3ShareVec(1)(8)
            if pid == 0:
                Rv1 = mpmt.Rvector(1); v = Rv1(8)
                for i, b in enumerate(bits_v): v[i] = b
                inst1.share_vector(v, sv, mpmt.RvectorPack(1)(8))
            else:
                inst1.recv_vector_share(sv, mpmt.RvectorPack(1)(8))
            so = mpmt.ShrRep3ShareVec(ell_to)(8)
            inst1.ring_conv_vec(sv, so, ell_to)
            Rv_to = mpmt.Rvector(ell_to); out = Rv_to(8)
            inst_to.reveal_vector(so, out, mpmt.RvectorPack(ell_to)(8))
            return [out[i] for i in range(8)]
        r = run_3party(rcvfn, 1)
        check(f"ring_conv_vec 1->{ell_to}", r[0] == exp_v and r[0] == r[1] == r[2])

    print(f"  PASS={PASS}  FAIL={FAIL}")
    return PASS, FAIL

if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--small", action="store_true")
    args = ap.parse_args()
    rc = run_tests(small=args.small)
    raise SystemExit(0 if rc[1] == 0 else 1)
