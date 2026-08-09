"""EMP2: bytes counters, instance reuse, share_element with str."""
import sys, os, random
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', '..'))
import mpmt
from building_blocks.test_emp2.harness import run_2party

PASS = FAIL = 0

def check(name, cond, detail=""):
    global PASS, FAIL
    if cond: PASS += 1
    else: FAIL += 1; print(f"  FAIL {name}  {detail}")

def run_tests(small=False):
    global PASS, FAIL
    PASS = FAIL = 0
    print("=== EMP2 Operations ===")

    ells = [2, 31] if small else list(range(2, 32))

    for ell in ells:
        m = mpmt.ring_mask(ell)
        label = f"EMP2(ell={ell})"

        # -- bytes_sent / bytes_recv -------------
        def cnt_fn(pid, ch):
            inst = mpmt.ShrAdd2(ell, pid)(ch)
            before_s = inst.bytes_sent()
            before_r = inst.bytes_recv()
            val = random.randint(0, m)
            if pid == 0:
                inst.send_data(val)
                inst.recv_data()
            else:
                inst.recv_data()
                inst.send_data(val)
            after_s = inst.bytes_sent()
            after_r = inst.bytes_recv()
            return (before_s, before_r, after_s, after_r)
        r = run_2party(cnt_fn, ell)
        for pid in range(2):
            _, _, aft_s, aft_r = r[pid]
            check(f"{label} P{pid} bytes_sent>0", aft_s > 0)
            check(f"{label} P{pid} bytes_recv>0", aft_r > 0)

        # -- clear_send_cnt / clear_recv_cnt -----
        def clr_fn(pid, ch):
            inst = mpmt.ShrAdd2(ell, pid)(ch)
            if pid == 0:
                inst.send_data(1)
                inst.recv_data()
            else:
                inst.recv_data()
                inst.send_data(1)
            before_s = inst.bytes_sent()
            before_r = inst.bytes_recv()
            inst.clear_send_cnt()
            inst.clear_recv_cnt()
            return (inst.bytes_sent(), inst.bytes_recv(), before_s, before_r)
        r = run_2party(clr_fn, ell)
        for pid in range(2):
            after_s, after_r, before_s, before_r = r[pid]
            check(f"{label} P{pid} clear_send_cnt", after_s == 0, f"got {after_s}")
            check(f"{label} P{pid} clear_recv_cnt", after_r == 0, f"got {after_r}")

        # -- share_element with str --------------
        elem_str = "alice_hello_世界"  # unicode test
        def estr_fn(pid, ch):
            inst = mpmt.ShrAdd2(ell, pid)(ch)
            if pid == 0:
                local = inst.share_element(elem_str)
                return bytes(local)
            else:
                return bytes(inst.recv_element_share())
        r = run_2party(estr_fn, ell)
        recon = bytes(a ^ b for a, b in zip(r[0], r[1]))
        check(f"{label} share_element str", recon.decode('utf-8') == elem_str,
              f"got {recon[:20]}")

        # -- share_element with bytes (varying len) --
        for nbytes in [3, 16, 128] if not small else [16]:
            elem = bytes([random.randint(0, 255) for _ in range(nbytes)])
            def eb_fn(pid, ch):
                inst = mpmt.ShrAdd2(ell, pid)(ch)
                if pid == 0:
                    local = inst.share_element(elem)
                    return bytes(local)
                else:
                    return bytes(inst.recv_element_share())
            r = run_2party(eb_fn, ell)
            recon = bytes(a ^ b for a, b in zip(r[0], r[1]))
            check(f"{label} share_element bytes len={nbytes}", recon == elem)

        # -- share_key roundtrip -----------------
        key = bytes([random.randint(0, 255) for _ in range(16)])
        def k_fn(pid, ch):
            inst = mpmt.ShrAdd2(ell, pid)(ch)
            if pid == 0:
                return bytes(inst.share_key(key))
            else:
                buf = bytearray(16)
                inst.recv_key_share(buf)
                return bytes(buf)
        r = run_2party(k_fn, ell)
        recon = bytes(a ^ b for a, b in zip(r[0], r[1]))
        check(f"{label} share_key XOR", recon == key)

    # -- Instance reuse (only on boundary ELLs) --
    for ell in [2, 31] if not small else [31]:
        m = mpmt.ring_mask(ell)
        label = f"EMP2(ell={ell})"

        # Reuse: overwrite variable with new instance on same channel
        # Use send_data/recv_data for symmetric bidirectional test
        val1 = random.randint(0, m)
        val2 = random.randint(0, m)
        def reuse_fn(pid, ch):
            inst1 = mpmt.ShrAdd2(ell, pid)(ch)
            if pid == 0:
                inst1.send_data(val1)
                r1 = inst1.recv_data()
            else:
                r1 = inst1.recv_data()
                inst1.send_data(val1)
            # Overwrite with second instance, same channel
            inst2 = mpmt.ShrAdd2(ell, pid)(ch)
            if pid == 0:
                inst2.send_data(val2)
                r2 = inst2.recv_data()
            else:
                r2 = inst2.recv_data()
                inst2.send_data(val2)
            return (r1, r2)
        r = run_2party(reuse_fn, ell)
        check(f"{label} instance reuse val1", r[0][0] == val1 and r[1][0] == val1)
        check(f"{label} instance reuse val2", r[0][1] == val2 and r[1][1] == val2)

        # Reuse with different ELL
        ell2 = 4 if ell == 31 else 31
        if ell2 <= 31:
            val3 = random.randint(0, mpmt.ring_mask(ell2))
            def reuse_ell_fn(pid, ch):
                inst1 = mpmt.ShrAdd2(ell, pid)(ch)
                if pid == 0:
                    inst1.send_data(val1)
                    r1 = inst1.recv_data()
                else:
                    r1 = inst1.recv_data()
                    inst1.send_data(val1)
                # New instance with different ell on same channel
                inst2 = mpmt.ShrAdd2(ell2, pid)(ch)
                if pid == 0:
                    inst2.send_data(val3)
                    r2 = inst2.recv_data()
                else:
                    r2 = inst2.recv_data()
                    inst2.send_data(val3)
                return (r1, r2, ell, ell2)
            r = run_2party(reuse_ell_fn, max(ell, ell2))
            check(f"{label}→{ell2} reuse diff ell val1",
                  r[0][0] == val1 and r[1][0] == val1)
            check(f"{label}→{ell2} reuse diff ell val2",
                  r[0][1] == val3 and r[1][1] == val3)

    print(f"  PASS={PASS}  FAIL={FAIL}")
    return PASS, FAIL

if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--small", action="store_true")
    args = ap.parse_args()
    rc = run_tests(small=args.small)
    raise SystemExit(rc)
