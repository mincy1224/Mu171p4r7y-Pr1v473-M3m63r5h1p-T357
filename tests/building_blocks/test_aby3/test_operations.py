"""ABY3: reshare, reveal_scalar, sub, sub_vec, dot, send_data/recv_data, counters."""
import sys, os, random
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', '..'))
import mpmt
from building_blocks.test_aby3.harness import run_3party

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
    print("=== ABY3 Operations ===")

    ells = [1, 6] if small else list(range(1, 7))

    for ell in ells:
        m = mpmt.ring_mask(ell)
        label = f"ABY3(ell={ell})"

        # -- reveal_scalar -----------------------
        secret = random.randint(0, m)
        def rev_s_fn(pid, prev, nxt):
            inst = mpmt.ShrRep3(ell, pid)(prev, nxt)
            if pid == 0:
                ss = inst.share_scalar(secret)
            else:
                ss = inst.recv_scalar_share()
            return inst.reveal_scalar(ss)
        r = run_3party(rev_s_fn, ell)
        check(f"{label} reveal_scalar", r[0] == secret and r[0] == r[1] == r[2])

        # -- sub (scalar) ------------------------
        a = random.randint(0, m); b = random.randint(0, m)
        exp_sub = mpmt.ring_sub(ell, a, b)
        def sub_fn(pid, prev, nxt):
            inst = mpmt.ShrRep3(ell, pid)(prev, nxt)
            sa = inst.share_scalar(a) if pid == 0 else inst.recv_scalar_share()
            sb = inst.share_scalar(b) if pid == 0 else inst.recv_scalar_share()
            so = inst.sub(sa, sb)
            return (so.this_share, so.nxt_share)
        r = run_3party(sub_fn, ell)
        check(f"{label} sub", recon3(r, ell) == exp_sub)

        # -- reshare_scalar ----------------------
        # Each party has its own additive share
        add_shares = [random.randint(0, m) for _ in range(3)]
        sum_plain = mpmt.ring_add(ell, add_shares[0],
                         mpmt.ring_add(ell, add_shares[1], add_shares[2]))
        def reshare_fn(pid, prev, nxt):
            inst = mpmt.ShrRep3(ell, pid)(prev, nxt)
            ss = inst.reshare_scalar(add_shares[pid])
            return (ss.this_share, ss.nxt_share)
        r = run_3party(reshare_fn, ell)
        check(f"{label} reshare_scalar", recon3(r, ell) == sum_plain)

        # -- reshare_vector ----------------------
        nv = 8 if not small else 4
        # Each party's additive vector shares
        vec_shares = []
        Rv = mpmt.Rvector(ell)
        for p in range(3):
            v = Rv(nv)
            for i in range(nv):
                v[i] = random.randint(0, m)
            vec_shares.append(v)
        SV = mpmt.ShrRep3ShareVec(ell)
        def reshare_vec_fn(pid, prev, nxt):
            inst = mpmt.ShrRep3(ell, pid)(prev, nxt)
            sv = SV(nv)
            inst.reshare_vector(vec_shares[pid], sv, mpmt.RvectorPack(ell)(nv))
            out = Rv(nv)
            inst.reveal_vector(sv, out, mpmt.RvectorPack(ell)(nv))
            return [out[i] for i in range(nv)]
        r = run_3party(reshare_vec_fn, ell)
        exp_sum = [mpmt.ring_add(ell, vec_shares[0][i],
                        mpmt.ring_add(ell, vec_shares[1][i], vec_shares[2][i]))
                   for i in range(nv)]
        check(f"{label} reshare_vector", r[0] == exp_sum and r[0] == r[1] == r[2])

        # -- dot ---------------------------------
        va = [random.randint(0, m) for _ in range(nv)]
        vb = [random.randint(0, m) for _ in range(nv)]
        exp_dot = sum(mpmt.ring_mul(ell, va[i], vb[i]) for i in range(nv)) & m
        def dot_fn(pid, prev, nxt):
            inst = mpmt.ShrRep3(ell, pid)(prev, nxt)
            sa = SV(nv); sb = SV(nv)
            if pid == 0:
                Rv = mpmt.Rvector(ell)
                v1 = Rv(nv); v2 = Rv(nv)
                for i in range(nv):
                    v1[i] = va[i]; v2[i] = vb[i]
                inst.share_vector(v1, sa, mpmt.RvectorPack(ell)(nv))
                inst.share_vector(v2, sb, mpmt.RvectorPack(ell)(nv))
            else:
                inst.recv_vector_share(sa, mpmt.RvectorPack(ell)(nv))
                inst.recv_vector_share(sb, mpmt.RvectorPack(ell)(nv))
            ss = inst.dot(sa, sb)
            return (ss.this_share, ss.nxt_share)
        r = run_3party(dot_fn, ell)
        check(f"{label} dot", recon3(r, ell) == exp_dot)

        # -- sub_vec -----------------------------
        def sub_vec_fn(pid, prev, nxt):
            inst = mpmt.ShrRep3(ell, pid)(prev, nxt)
            sa = SV(nv); sb = SV(nv); so = SV(nv)
            if pid == 0:
                RvC = mpmt.Rvector(ell)
                v1 = RvC(nv); v2 = RvC(nv)
                for i in range(nv):
                    v1[i] = va[i]; v2[i] = vb[i]
                inst.share_vector(v1, sa, mpmt.RvectorPack(ell)(nv))
                inst.share_vector(v2, sb, mpmt.RvectorPack(ell)(nv))
            else:
                inst.recv_vector_share(sa, mpmt.RvectorPack(ell)(nv))
                inst.recv_vector_share(sb, mpmt.RvectorPack(ell)(nv))
            inst.sub_vec(sa, sb, so)
            RvC2 = mpmt.Rvector(ell); out = RvC2(nv)
            inst.reveal_vector(so, out, mpmt.RvectorPack(ell)(nv))
            return [out[i] for i in range(nv)]
        r = run_3party(sub_vec_fn, ell)
        exp_vsub = [mpmt.ring_sub(ell, va[i], vb[i]) for i in range(nv)]
        check(f"{label} sub_vec", r[0] == exp_vsub and r[0] == r[1] == r[2])

        # -- send_data / recv_data scalar --------
        sv_p0_p1 = random.randint(0, m)
        sv_p1_p2 = random.randint(0, m)
        sv_p2_p0 = random.randint(0, m)
        def send_sc_fn(pid, prev, nxt):
            inst = mpmt.ShrRep3(ell, pid)(prev, nxt)
            results = {}
            if pid == 0:
                inst.send_data(to_pid=1, val=sv_p0_p1)
                results['from_2'] = inst.recv_data(from_pid=2)
            elif pid == 1:
                results['from_0'] = inst.recv_data(from_pid=0)
                inst.send_data(to_pid=2, val=sv_p1_p2)
            else:  # pid == 2
                results['from_1'] = inst.recv_data(from_pid=1)
                inst.send_data(to_pid=0, val=sv_p2_p0)
            return results
        r = run_3party(send_sc_fn, ell)
        check(f"{label} send/recv P0→P1", r[1].get('from_0', -1) == sv_p0_p1)
        check(f"{label} send/recv P1→P2", r[2].get('from_1', -1) == sv_p1_p2)
        check(f"{label} send/recv P2→P0", r[0].get('from_2', -1) == sv_p2_p0)

        # -- send_data / recv_data bytes ----------
        data_sizes = [(0, 4), (1, 8), (2, 16)] if not small else [(0, 8)]
        for from_pid, nbytes in data_sizes:
            payload = bytes([random.randint(0, 255) for _ in range(nbytes)])
            def send_bytes_fn(pid, prev, nxt):
                inst = mpmt.ShrRep3(ell, pid)(prev, nxt)
                if pid == from_pid:
                    inst.send_data(to_pid=(from_pid + 1) % 3, data=payload)
                elif pid == (from_pid + 1) % 3:
                    buf = bytearray(nbytes)
                    inst.recv_data(from_pid=from_pid, buf=buf)
                    return bytes(buf)
                return None
            r = run_3party(send_bytes_fn, ell)
            got = r[(from_pid + 1) % 3]
            check(f"{label} send/recv_bytes P{from_pid}→P{(from_pid+1)%3} {nbytes}B",
                  got == payload, f"got {got[:8]}... exp {payload[:8]}...")

        # -- counters / flush --------------------
        def ctr_fn(pid, prev, nxt):
            inst = mpmt.ShrRep3(ell, pid)(prev, nxt)
            before_s = inst.bytes_sent()
            before_r = inst.bytes_recv()
            # Each party sends one scalar to the next party
            if pid == 0:
                inst.send_data(to_pid=1, val=42)
                inst.flush()
                inst.recv_data(from_pid=2)
            elif pid == 1:
                inst.recv_data(from_pid=0)
                inst.send_data(to_pid=2, val=42)
                inst.flush()
            else:
                inst.send_data(to_pid=0, val=42)
                inst.flush()
                inst.recv_data(from_pid=1)
            after_s = inst.bytes_sent()
            after_r = inst.bytes_recv()
            return (before_s, before_r, after_s, after_r)
        r = run_3party(ctr_fn, ell)
        for pid in range(3):
            _, _, aft_s, aft_r = r[pid]
            check(f"{label} P{pid} bytes_sent>0", aft_s > 0)
            check(f"{label} P{pid} bytes_recv>0", aft_r > 0)

        # clear counters
        def clear_fn(pid, prev, nxt):
            inst = mpmt.ShrRep3(ell, pid)(prev, nxt)
            if pid == 0:
                inst.send_data(to_pid=1, val=1)
                inst.flush()
                _ = inst.recv_data(from_pid=2)
            elif pid == 1:
                _ = inst.recv_data(from_pid=0)
                inst.send_data(to_pid=2, val=1)
                inst.flush()
            else:
                inst.send_data(to_pid=0, val=1)
                inst.flush()
                _ = inst.recv_data(from_pid=1)
            before_clear_s = inst.bytes_sent()
            inst.clear_send_cnt()
            inst.clear_recv_cnt()
            return (inst.bytes_sent(), inst.bytes_recv(), before_clear_s)
        r = run_3party(clear_fn, ell)
        for pid in range(3):
            after_clear_s, after_clear_r, before_s = r[pid]
            check(f"{label} P{pid} clear_send_cnt", after_clear_s == 0,
                  f"got {after_clear_s}")
            check(f"{label} P{pid} clear_recv_cnt", after_clear_r == 0,
                  f"got {after_clear_r}")

    # -- sub_vec alias-safe (out == sv1) ---------
    ell = 6 if not small else 1
    m = mpmt.ring_mask(ell)
    nv = 8
    va = [random.randint(0, m) for _ in range(nv)]
    vb = [random.randint(0, m) for _ in range(nv)]
    exp_alias = [mpmt.ring_sub(ell, va[i], vb[i]) for i in range(nv)]
    def alias_fn(pid, prev, nxt):
        inst = mpmt.ShrRep3(ell, pid)(prev, nxt)
        SV = mpmt.ShrRep3ShareVec(ell)
        sa = SV(nv); sb = SV(nv)
        if pid == 0:
            Rv = mpmt.Rvector(ell)
            v1 = Rv(nv); v2 = Rv(nv)
            for i in range(nv):
                v1[i] = va[i]; v2[i] = vb[i]
            inst.share_vector(v1, sa, mpmt.RvectorPack(ell)(nv))
            inst.share_vector(v2, sb, mpmt.RvectorPack(ell)(nv))
        else:
            inst.recv_vector_share(sa, mpmt.RvectorPack(ell)(nv))
            inst.recv_vector_share(sb, mpmt.RvectorPack(ell)(nv))
        inst.sub_vec(sa, sb, sa)  # out == sv1 (alias-safe)
        RvC3 = mpmt.Rvector(ell); out = RvC3(nv)
        inst.reveal_vector(sa, out, mpmt.RvectorPack(ell)(nv))
        return [out[i] for i in range(nv)]
    r = run_3party(alias_fn, ell)
    check(f"ABY3(ell={ell}) sub_vec alias-safe (out=sv1)",
          r[0] == exp_alias and r[0] == r[1] == r[2])

    print(f"  PASS={PASS}  FAIL={FAIL}")
    return PASS, FAIL

if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--small", action="store_true")
    args = ap.parse_args()
    rc = run_tests(small=args.small)
    raise SystemExit(rc)
