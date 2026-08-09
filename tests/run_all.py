#!/usr/bin/env python3
"""Comprehensive test suite — all protocols, full parameter coverage, random data.

Usage:
  python3 tests/run_all.py            # full coverage (CI)
  python3 tests/run_all.py --small    # small-scale (local pre-push)
  TEST_SEED=42 python3 tests/run_all.py  # reproducible
"""
import sys, os, random, time, argparse

ap = argparse.ArgumentParser()
ap.add_argument("--small", action="store_true", help="Small-scale quick test")
args = ap.parse_args()

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
import mpmt
from building_blocks.test_aby3.harness import run_3party
from building_blocks.test_emp2.harness import run_2party
from building_blocks.test_dpf.harness import run_dpf

# Reproducible randomness
SEED = int(os.environ.get("TEST_SEED", random.randint(0, 2**31 - 1)))
random.seed(SEED)
PASS = FAIL = 0
TSTART = time.monotonic()

def check(name, cond, detail=""):
    global PASS, FAIL
    if cond: PASS += 1
    else: FAIL += 1; print(f"  FAIL {name}  {detail}")

def recon3(r, ell):
    return mpmt.ring_add(ell, r[0][0], mpmt.ring_add(ell, r[1][0], r[2][0]))

def recon2(r, ell):
    return mpmt.ring_add(ell, r[0], r[1])

ELLS_ABY3 = [1, 6] if args.small else list(range(1, 7))
ELLS_EMP2 = [2, 31] if args.small else list(range(2, 32))
ELLS_RVEC = [1, 8] if args.small else list(range(1, 9))
DPF_EI     = [13, 20] if args.small else list(range(13, 32))
DPF_EO     = [2, 6] if args.small else list(range(2, 7))
DPF_CORES  = [1] if args.small else [1, 4, 8, 16]
# Boundary sizes for packing corner cases
PACK_SIZES = [0, 1, 2, 3, 4, 5, 6, 7, 8, 15, 16, 17, 31, 32, 33, 63, 64, 65, 127, 128, 255, 256]

MODE = "SMALL" if args.small else "FULL"
print(f"=== MPMT Test Suite ({MODE})  seed={SEED} ===")

# ===========================================================
#  UTILS
# ===========================================================
print("--- Utils ---")

for ell in [1, 8, 31]:
    check(f"ring_mask({ell})", mpmt.ring_mask(ell) == (1 << ell) - 1)

for ell in [1, 8, 31]:
    v = mpmt.ring_rand(ell)
    check(f"ring_rand({ell})", v <= mpmt.ring_mask(ell))

for bad in [0, 32]:
    try: mpmt.ring_rand(bad); ok = False
    except Exception: ok = True
    check(f"ring_rand({bad}) reject", ok)

for ell, a, b in [(1, 0, 1), (8, 100, 200), (31, 1<<30, (1<<30)+1)]:
    m = mpmt.ring_mask(ell)
    check(f"ring_add({ell})", mpmt.ring_add(ell, a, b) == (a + b) & m)
    check(f"ring_sub({ell})", mpmt.ring_sub(ell, a, b) == (a - b) & m)
    check(f"ring_mul({ell})", mpmt.ring_mul(ell, a, b) == (a * b) & m)

for ell, val, mv in [(8, 100, 7), (16, 1000, 13)]:
    check(f"ring_mod({ell},{val},{mv})", mpmt.ring_mod(ell, val, mv) == val % mv)
try: mpmt.ring_mod(8, 5, 0); ok = False
except ValueError: ok = True
check("ring_mod reject mv=0", ok)

k = mpmt.get_key_128bits()
h1 = mpmt.hash_aes_dm(preimage=b"hello", key=k, ell=8)
h2 = mpmt.hash_aes_dm(preimage=b"hello", key=k, ell=8)
check("hash_aes_dm deterministic", h1 == h2)
check("hash_aes_dm in range", h1 <= mpmt.ring_mask(8))

# ===========================================================
#  RVECTOR — comprehensive boundary testing
# ===========================================================
print(f"--- Rvector ({len(ELLS_RVEC)} ELLs) ---")

for ell in ELLS_RVEC:
    Rv = mpmt.Rvector(ell); m = mpmt.ring_mask(ell)
    check(f"Rvector({ell}) factory", Rv is not None)

    # Test every boundary size for pack/unpack corner cases
    test_sizes = PACK_SIZES if not args.small else [0, 1, 2, 3, 4, 5, 6, 7, 8, 16, 32]
    for n in test_sizes:
        # construct + fill + get/set
        v = Rv(n)
        if n > 0:
            v.fill(0)
            v[0] = 1
            if n > 1: v[n-1] = 1
            # pack/unpack roundtrip (catches _pack6 OOB)
            aux = mpmt.RvectorPack(ell)(n)
            out = Rv(n)
            mpmt.rvector_pack(v, aux)
            mpmt.rvector_unpack(aux, out)
            ok = all(out[i] == v[i] for i in range(n))
            if not ok:
                check(f"Rvector{ell} pack/unpack n={n}", False)
                break
        if n == test_sizes[-1]:
            check(f"Rvector{ell} pack/unpack all sizes", True)

    # from_bytes exact size check
    v = Rv(32)
    v[0] = 1; v[31] = m
    raw = v.to_bytes()
    # exact match should work
    v2 = Rv(32)
    v2.from_bytes(raw)
    check(f"Rvector{ell} from_bytes exact", all(v2[i] == v[i] for i in range(32)))
    # wrong size should throw
    try: v2.from_bytes(raw + b'\x00'); ok = False
    except ValueError: ok = True
    check(f"Rvector{ell} from_bytes wrong size", ok)
    # canonicalize after from_bytes
    v3 = Rv(32)
    v3.from_bytes(raw)
    check(f"Rvector{ell} canonicalize", all(v3[i] == v[i] for i in range(32)))

    # OOB
    try: v[100]; ok = False
    except (IndexError, RuntimeError): ok = True
    check(f"Rvector{ell} OOB get", ok)

    # arithmetic with random data
    n = 32
    a = Rv(n); b = Rv(n); out = Rv(n)
    for i in range(n):
        a[i] = random.randint(0, m); b[i] = random.randint(0, m)
    cls = type(a)

    # in-place add (alias-safe): out == a
    out2 = Rv(n)
    for i in range(n): out2[i] = a[i]
    cls.add(out2, b, out2)
    check(f"Rvector{ell} add in-place(out=a)", all(out2[i] == mpmt.ring_add(ell, a[i], b[i]) for i in range(n)))

    # in-place add (alias-safe): out == b
    out3 = Rv(n)
    for i in range(n): out3[i] = b[i]
    cls.add(a, out3, out3)
    check(f"Rvector{ell} add in-place(out=b)", all(out3[i] == mpmt.ring_add(ell, a[i], b[i]) for i in range(n)))

    # regular add
    cls.add(a, b, out)
    check(f"Rvector{ell} add", all(out[i] == mpmt.ring_add(ell, a[i], b[i]) for i in range(n)))

    # hadamard (NO aliasing allowed)
    cls.hadamard(a, b, out)
    check(f"Rvector{ell} hadamard", all(out[i] == mpmt.ring_mul(ell, a[i], b[i]) for i in range(n)))

    # dot
    d = cls.dot(a, b)
    exp_dot = sum(mpmt.ring_mul(ell, a[i], b[i]) for i in range(n)) & m
    check(f"Rvector{ell} dot", d == exp_dot, f"{d} vs {exp_dot}")

    # reduce
    red = cls.reduce(a)
    exp_red = sum(a[i] for i in range(n)) & m
    check(f"Rvector{ell} reduce", red == exp_red, f"{red} vs {exp_red}")

    # batch_set/get (don't swallow exceptions)
    try:
        import array
        idxs = array.array('Q', [0, 1, 2, 3, 4])
        batch_val = random.randint(0, m)
        a.batch_set(idxs, batch_val)
        out_bt = Rv(5)
        a.batch_get(idxs, out_bt)
        ok = all(out_bt[i] == batch_val for i in range(5))
        check(f"Rvector{ell} batch_set/get", ok)
    except ImportError:
        pass  # array module unavailable is OK

    # rand_fill
    v2 = Rv(200); v2.rand_fill()
    nz = sum(1 for i in range(200) if v2[i] != 0)
    min_nz = 20 if ell > 1 else 50
    check(f"Rvector{ell} rand_fill", nz >= min_nz, f"only {nz}/200")

# ===========================================================
#  ABY3
# ===========================================================
print(f"--- ABY3 ({len(ELLS_ABY3)} ELLs) ---")

for ell in ELLS_ABY3:
    m = mpmt.ring_mask(ell)
    SV = mpmt.ShrRep3ShareVec(ell)

    for p in [0, 1, 2]:
        check(f"ABY3({ell},{p})", mpmt.ShrRep3(ell, p) is not None)
    for bad in [0, 7]:
        try: mpmt.ShrRep3(bad, 0); ok = False
        except ValueError: ok = True
        check(f"ABY3 reject ell={bad}", ok)

    # ShareVec size consistency
    sv = SV(10)
    check(f"ABY3{ell} ShareVec size match", sv.this_share.size == sv.nxt_share.size)
    # Assign mismatched size should throw
    try:
        Rv2 = mpmt.Rvector(ell)
        big = Rv2(99)
        sv.this_share = big; ok = False
    except (ValueError, RuntimeError): ok = True
    check(f"ABY3{ell} ShareVec reject mismatched this_share", ok)
    sv2 = SV(10)
    try:
        Rv2 = mpmt.Rvector(ell)
        big = Rv2(99)
        sv2.nxt_share = big; ok = False
    except (ValueError, RuntimeError): ok = True
    check(f"ABY3{ell} ShareVec reject mismatched nxt_share", ok)

    # crng
    def crng_fn(pid, p, n):
        return mpmt.ShrRep3(ell, pid)(p, n).crng()
    r = run_3party(crng_fn, ell)
    s = mpmt.ring_add(ell, r[0], mpmt.ring_add(ell, r[1], r[2]))
    check(f"ABY3{ell} crng", s == 0, f"sum={s}")

    # scalar ops
    sv = random.randint(0, m); a = random.randint(0, m); b = random.randint(0, m)
    def sops(pid, p, n):
        inst = mpmt.ShrRep3(ell, pid)(p, n)
        sh = inst.share_scalar(sv) if pid == 0 else inst.recv_scalar_share()
        sa = inst.share_scalar(a) if pid == 0 else inst.recv_scalar_share()
        sb = inst.share_scalar(b) if pid == 0 else inst.recv_scalar_share()
        return ((sh.this_share, sh.nxt_share),
                (inst.add(sa, sb).this_share, inst.add(sa, sb).nxt_share),
                (inst.mul(sa, sb).this_share, inst.mul(sa, sb).nxt_share))
    r = run_3party(sops, ell)
    check(f"ABY3{ell} share", recon3([x[0] for x in r], ell) == sv)
    check(f"ABY3{ell} add", recon3([x[1] for x in r], ell) == mpmt.ring_add(ell, a, b))
    check(f"ABY3{ell} mul", recon3([x[2] for x in r], ell) == mpmt.ring_mul(ell, a, b))

    # vec share/reveal + in-place add_vec
    nv = 16
    vals = [random.randint(0, m) for _ in range(nv)]
    def vfn(pid, p, n):
        inst = mpmt.ShrRep3(ell, pid)(p, n)
        sv = SV(nv)
        if pid == 0:
            Rv = mpmt.Rvector(ell); v = Rv(nv)
            for i, x in enumerate(vals): v[i] = x
            inst.share_vector(v, sv, mpmt.RvectorPack(ell)(nv))
        else:
            inst.recv_vector_share(sv, mpmt.RvectorPack(ell)(nv))
        Rv = mpmt.Rvector(ell); out = Rv(nv)
        inst.reveal_vector(sv, out, mpmt.RvectorPack(ell)(nv))
        return [out[i] for i in range(nv)]
    r = run_3party(vfn, ell)
    check(f"ABY3{ell} vec share/reveal", r[0] == vals and r[0] == r[1] == r[2])

    # hadamard
    va = [random.randint(0, m) for _ in range(nv)]
    vb = [random.randint(0, m) for _ in range(nv)]
    exp = [mpmt.ring_mul(ell, va[i], vb[i]) for i in range(nv)]
    def hfn(pid, p, n):
        inst = mpmt.ShrRep3(ell, pid)(p, n)
        sa = SV(nv); sb = SV(nv); so = SV(nv)
        if pid == 0:
            Rv = mpmt.Rvector(ell)
            v = Rv(nv)
            for i, x in enumerate(va): v[i] = x
            inst.share_vector(v, sa, mpmt.RvectorPack(ell)(nv))
            v2 = Rv(nv)
            for i, x in enumerate(vb): v2[i] = x
            inst.share_vector(v2, sb, mpmt.RvectorPack(ell)(nv))
        else:
            inst.recv_vector_share(sa, mpmt.RvectorPack(ell)(nv))
            inst.recv_vector_share(sb, mpmt.RvectorPack(ell)(nv))
        inst.hadamard(sa, sb, so)
        Rv = mpmt.Rvector(ell); out = Rv(nv)
        inst.reveal_vector(so, out, mpmt.RvectorPack(ell)(nv))
        return [out[i] for i in range(nv)]
    r = run_3party(hfn, ell)
    check(f"ABY3{ell} hadamard", r[0] == exp and r[0] == r[1] == r[2])

# ring_conv
print(f"--- ABY3 ring_conv ---")
bit = random.randint(0, 1)
RC_ELLS = [2, 6] if args.small else list(range(2, 7))
for ell_to in RC_ELLS:
    exp = bit & mpmt.ring_mask(ell_to)
    def rcfn(pid, p, n):
        inst = mpmt.ShrRep3(1, pid)(p, n)
        ss = inst.share_scalar(bit) if pid == 0 else inst.recv_scalar_share()
        r = inst.ring_conv(ss, ell_to)
        return (r.this_share, r.nxt_share)
    r = run_3party(rcfn, 1)
    got = mpmt.ring_add(ell_to, r[0][0], mpmt.ring_add(ell_to, r[1][0], r[2][0]))
    check(f"ring_conv 1→{ell_to}", got == exp, f"{got} vs {exp}")

# ring_conv_vec
bits = [random.randint(0, 1) for _ in range(8)]
for ell_to in RC_ELLS:
    exp_v = [b & mpmt.ring_mask(ell_to) for b in bits]
    def rcvfn(pid, p, n):
        inst1 = mpmt.ShrRep3(1, pid)(p, n)
        inst_to = mpmt.ShrRep3(ell_to, pid)(p, n)
        sv = mpmt.ShrRep3ShareVec(1)(8)
        if pid == 0:
            Rv = mpmt.Rvector(1); v = Rv(8)
            for i, b in enumerate(bits): v[i] = b
            inst1.share_vector(v, sv, mpmt.RvectorPack(1)(8))
        else:
            inst1.recv_vector_share(sv, mpmt.RvectorPack(1)(8))
        so = mpmt.ShrRep3ShareVec(ell_to)(8)
        inst1.ring_conv_vec(sv, so, ell_to)
        Rv = mpmt.Rvector(ell_to); out = Rv(8)
        inst_to.reveal_vector(so, out, mpmt.RvectorPack(ell_to)(8))
        return [out[i] for i in range(8)]
    r = run_3party(rcvfn, 1)
    check(f"ring_conv_vec 1→{ell_to}", r[0] == exp_v and r[0] == r[1] == r[2])

# ===========================================================
#  EMP2
# ===========================================================
print(f"--- EMP2 ({len(ELLS_EMP2)} ELLs) ---")

for ell in ELLS_EMP2:
    m = mpmt.ring_mask(ell)

    for p in [0, 1]:
        check(f"EMP2({ell},{p})", mpmt.ShrAdd2(ell, p) is not None)
    for bad in [1, 32]:
        try: mpmt.ShrAdd2(bad, 0); ok = False
        except ValueError: ok = True
        check(f"EMP2 reject ell={bad}", ok)

    sv = random.randint(0, m)
    def sf(pid, ch):
        inst = mpmt.ShrAdd2(ell, pid)(ch)
        return inst.share_scalar(sv) if pid == 0 else inst.recv_scalar_share()
    r = run_2party(sf, ell)
    check(f"EMP2{ell} share", recon2(r, ell) == sv)

    val = random.randint(0, m)
    def srf(pid, ch):
        inst = mpmt.ShrAdd2(ell, pid)(ch)
        if pid == 0: inst.send_data(val); return inst.recv_data()
        else: rv = inst.recv_data(); inst.send_data(val); return rv
    r = run_2party(srf, ell)
    check(f"EMP2{ell} send/recv", r == [val, val])

    key = bytes([random.randint(0, 255) for _ in range(16)])
    def kf(pid, ch):
        inst = mpmt.ShrAdd2(ell, pid)(ch)
        if pid == 0: return bytes(inst.share_key(key))
        else: buf = bytearray(16); inst.recv_key_share(buf); return bytes(buf)
    r = run_2party(kf, ell)
    check(f"EMP2{ell} share_key", bytes(a^b for a,b in zip(r[0],r[1])) == key)

    elem = bytes([random.randint(0, 255) for _ in range(random.randint(8, 32))])
    def ef(pid, ch):
        inst = mpmt.ShrAdd2(ell, pid)(ch)
        if pid == 0: return bytes(inst.share_element(elem))
        else: return bytes(inst.recv_element_share())
    r = run_2party(ef, ell)
    check(f"EMP2{ell} share_element", bytes(a^b for a,b in zip(r[0],r[1])) == elem)

    av = random.randint(0, m); mv = random.randint(2, min(m, 100))
    exp = mpmt.ring_mod(ell, av, mv)
    def mf(pid, ch):
        inst = mpmt.ShrAdd2(ell, pid)(ch)
        sa = inst.share_scalar(av) if pid == 0 else inst.recv_scalar_share()
        r = inst.mod(sa, mv)
        if pid == 0: inst.send_data(r); o = inst.recv_data()
        else: o = inst.recv_data(); inst.send_data(r)
        return mpmt.ring_add(ell, r, o)
    r = run_2party(mf, ell)
    check(f"EMP2{ell} mod", r[0] == exp and r[0] == r[1])

    bv = random.choice([av, random.randint(0, m)])
    exp_eq = 1 if av == bv else 0
    def eqf(pid, ch):
        inst = mpmt.ShrAdd2(ell, pid)(ch)
        sa = inst.share_scalar(av) if pid == 0 else inst.recv_scalar_share()
        sb = inst.share_scalar(bv) if pid == 0 else inst.recv_scalar_share()
        r = inst.equality_test(sa, sb)
        if pid == 0: inst.send_data(r); o = inst.recv_data()
        else: o = inst.recv_data(); inst.send_data(r)
        return mpmt.ring_add(ell, r, o)
    r = run_2party(eqf, ell)
    check(f"EMP2{ell} eq", r[0] == exp_eq and r[0] == r[1])

    pt = bytes([random.randint(0, 255) for _ in range(16)])
    hkey = bytes([random.randint(0, 255) for _ in range(16)])
    exp_h = mpmt.hash_aes_dm(preimage=pt, key=hkey, ell=ell)
    def hf(pid, ch):
        inst = mpmt.ShrAdd2(ell, pid)(ch)
        if pid == 0:
            pt_l = inst.share_element(pt); k_l = inst.share_key(hkey)
        else:
            pt_l = inst.recv_element_share()
            kb = bytearray(16); inst.recv_key_share(kb); k_l = bytes(kb)
        r = inst.hash(pt_l, k_l)
        if pid == 0: inst.send_data(r); o = inst.recv_data()
        else: o = inst.recv_data(); inst.send_data(r)
        return mpmt.ring_add(ell, r, o)
    r = run_2party(hf, ell)
    check(f"EMP2{ell} hash", r[0] == exp_h and r[0] == r[1])

# ===========================================================
#  DPF
# ===========================================================
print(f"--- DPF ({len(DPF_EI)} EI × {len(DPF_EO)} EO) ---")

for ei in DPF_EI:
    for eo in DPF_EO:
        vl = 1 << ei; m = mpmt.ring_mask(eo)
        cores = DPF_CORES[random.randint(0, len(DPF_CORES)-1)]

        check(f"DPF Dealer({ei},{eo})", mpmt.DpfDealer(ei, eo) is not None)
        check(f"DPF Eval0({ei},{eo})", mpmt.DpfEvaluator(ei, eo, 0) is not None)
        check(f"DPF Eval1({ei},{eo})", mpmt.DpfEvaluator(ei, eo, 1) is not None)

        alpha = random.randint(0, vl - 1)
        beta = random.randint(1, m)

        def d_full(c0, c1):
            DC = mpmt.DpfDealer(ei, eo); d = DC(c0, c1)
            k0, k1 = d.gen(alpha, beta); d.send_key(k0, 0); d.send_key(k1, 1)
            Rv = mpmt.Rvector(eo); out = Rv(vl); d.reveal(out)
            ok = out[alpha] == beta
            for i in random.sample(range(vl), min(20, vl)):
                if i != alpha and out[i] != 0: ok = False; break
            return "OK" if ok else "FAIL"
        def ev_full(pid):
            return lambda c: (EC:=mpmt.DpfEvaluator(ei,eo,pid), ev:=EC(c),
                  k:=ev.recv_key(), b:=mpmt.Rvector(eo)(vl),
                  ev.eval(k, b, cores=cores), ev.reveal(b), "OK")[-1]
        r = run_dpf(d_full, ev_full(0), ev_full(1), timeout=180)
        check(f"DPF({ei},{eo},c{cores}) full", r[0] == "OK")

        if vl > 20:
            bg = random.randint(0, vl - 20)
            ed = min(bg + random.randint(5, 20), vl - 1)
        else:
            bg = 0; ed = min(5, vl - 1)
        rl = ed - bg + 1
        def d_range(c0, c1):
            DC = mpmt.DpfDealer(ei, eo); d = DC(c0, c1)
            k0, k1 = d.gen(alpha, beta); d.send_key(k0, 0); d.send_key(k1, 1)
            Rv = mpmt.Rvector(eo); out = Rv(rl); d.reveal(out)
            ok = True
            for i in range(rl):
                exp = beta if (bg + i) == alpha else 0
                if out[i] != exp: ok = False; break
            return "OK" if ok else "FAIL"
        def ev_range(pid):
            return lambda c: (EC:=mpmt.DpfEvaluator(ei,eo,pid), ev:=EC(c),
                  k:=ev.recv_key(), b:=mpmt.Rvector(eo)(rl),
                  ev.eval_range(k, b, bg, ed, cores=cores), ev.reveal(b), "OK")[-1]
        r = run_dpf(d_range, ev_range(0), ev_range(1), timeout=180)
        check(f"DPF({ei},{eo},c{cores}) range", r[0] == "OK")

# ===========================================================
#  COVERAGE MODULES
# ===========================================================

from base.test_util import run_tests as run_util
from base.test_sharevec import run_tests as run_sharevec
from base.test_channels import run_tests as run_channels
from base.test_ring_transport import run_tests as run_ring_transport
from building_blocks.test_aby3.test_operations import run_tests as run_aby3_ops
from building_blocks.test_aby3.test_protocol import run_tests as run_aby3_proto
from building_blocks.test_aby3.test_factory import run_tests as run_aby3_fact
from building_blocks.test_aby3.test_compound import run_tests as run_aby3_comp
from building_blocks.test_emp2.test_operations import run_tests as run_emp2_ops
from building_blocks.test_emp2.test_protocol import run_tests as run_emp2_proto
from building_blocks.test_emp2.test_factory import run_tests as run_emp2_fact
from building_blocks.test_dpf.test_operations import run_tests as run_dpf_ops
from building_blocks.test_dpf.test_basic import run_tests as run_dpf_basic

for _mod in [run_util, run_sharevec, run_channels, run_ring_transport,
             run_aby3_ops, run_aby3_proto, run_aby3_fact, run_aby3_comp,
             run_emp2_ops, run_emp2_proto, run_emp2_fact,
             run_dpf_ops, run_dpf_basic]:
    p, f = _mod(small=args.small)
    PASS += p; FAIL += f

# ===========================================================
elapsed = time.monotonic() - TSTART
print(f"\n{'='*60}")
print(f"  MODE={MODE}  PASS={PASS}  FAIL={FAIL}  seed={SEED}  ({elapsed:.1f}s)")
print(f"{'='*60}")
sys.exit(0 if FAIL == 0 else 1)
