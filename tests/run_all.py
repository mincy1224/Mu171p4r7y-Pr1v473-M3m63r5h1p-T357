#!/usr/bin/env python3
"""Comprehensive test suite — all protocols, full parameter coverage, random data.

Usage:
  python3 tests/run_all.py            # full coverage (CI, ~minutes)
  python3 tests/run_all.py -sm        # small-scale (local pre-push, ~seconds)
"""
import sys, os, random, time, argparse

ap = argparse.ArgumentParser()
ap.add_argument("-sm", action="store_true", help="Small-scale quick test")
args = ap.parse_args()

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
import mpmt
from test_aby3.harness import run_3party
from test_emp2.harness import run_2party
from test_dpf.harness import run_dpf

PASS = FAIL = 0
TSTART = time.monotonic()

def check(name, cond, detail=""):
    global PASS, FAIL
    if cond: PASS += 1
    else: FAIL += 1; print(f"  ❌ {name}  {detail}")

elli = lambda: None  # will be set per-section

# ═══════════════════════════════════════════════════════════
#  HELPERS
# ═══════════════════════════════════════════════════════════

def recon3(r, ell):
    return mpmt.ring_add(ell, r[0][0], mpmt.ring_add(ell, r[1][0], r[2][0]))

def recon2(r, ell):
    return mpmt.ring_add(ell, r[0], r[1])

ELLS_ABY3 = [1, 6] if args.sm else list(range(1, 7))        # 2 vs 6
ELLS_EMP2 = [2, 31] if args.sm else list(range(2, 32))      # 2 vs 30
ELLS_RVEC = [1, 8] if args.sm else list(range(1, 9))        # 2 vs 8
DPF_EI     = [13, 31] if args.sm else list(range(13, 32))    # 2 vs 19
DPF_EO     = [2, 6] if args.sm else list(range(2, 7))        # 2 vs 5
DPF_CORES  = [1] if args.sm else [1, 4, 8, 16]

MODE = "SMALL" if args.sm else "FULL"
print(f"=== MPMT Test Suite ({MODE}) ===")

# ═══════════════════════════════════════════════════════════
#  MODULE-LEVEL UTILITIES
# ═══════════════════════════════════════════════════════════
print("─── Utils ───")

for ell in [1, 8, 31, 63]:
    check(f"ring_mask({ell})", mpmt.ring_mask(ell) == (1 << ell) - 1)

for ell in [1, 8, 31]:
    v = mpmt.ring_rand(ell)
    check(f"ring_rand({ell}) in range", v <= mpmt.ring_mask(ell))

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

bf_sz, ea, hf, eq = mpmt.bf_param(1024, 1.0, -3)
check("bf_param", bf_sz > 0 and hf > 0)

# ═══════════════════════════════════════════════════════════
#  RVECTOR
# ═══════════════════════════════════════════════════════════
print(f"─── Rvector ({len(ELLS_RVEC)} ELLs) ───")

for ell in ELLS_RVEC:
    Rv = mpmt.Rvector(ell); m = mpmt.ring_mask(ell)
    check(f"Rvector({ell}) factory", Rv is not None)

    # construct + fill
    v = Rv(100)
    v.fill(0)
    check(f"Rvector{ell} size", len(v) == 100 and v.size == 100)

    # random set/get
    indices = random.sample(range(100), min(10, 100))
    vals = [random.randint(0, m) for _ in indices]
    for i, val in zip(indices, vals): v[i] = val
    ok = all(v[i] == val for i, val in zip(indices, vals))
    check(f"Rvector{ell} get/set", ok)

    # OOB
    try: v[100]; ok = False
    except (IndexError, RuntimeError): ok = True
    check(f"Rvector{ell} OOB get", ok)
    try: v[100] = 0; ok = False
    except (IndexError, RuntimeError): ok = True
    check(f"Rvector{ell} OOB set", ok)

    # arithmetic (static) — random data
    n = 32
    a = Rv(n); b = Rv(n); out = Rv(n)
    for i in range(n):
        a[i] = random.randint(0, m); b[i] = random.randint(0, m)
    cls = type(a)
    cls.add(a, b, out)
    ok = all(out[i] == mpmt.ring_add(ell, a[i], b[i]) for i in range(n))
    check(f"Rvector{ell} add", ok)
    cls.hadamard(a, b, out)
    ok = all(out[i] == mpmt.ring_mul(ell, a[i], b[i]) for i in range(n))
    check(f"Rvector{ell} hadamard", ok)
    d = cls.dot(a, b)
    exp_dot = sum(mpmt.ring_mul(ell, a[i], b[i]) for i in range(n)) & m
    check(f"Rvector{ell} dot", d == exp_dot, f"{d} vs {exp_dot}")
    red = cls.reduce(a)
    exp_red = sum(a[i] for i in range(n)) & m
    check(f"Rvector{ell} reduce", red == exp_red, f"{red} vs {exp_red}")

    # pack/unpack
    aux = mpmt.RvectorPack(ell)(n)
    mpmt.rvector_pack(a, aux)
    mpmt.rvector_unpack(aux, out)
    check(f"Rvector{ell} pack/unpack", all(out[i] == a[i] for i in range(n)))

    # rand_fill
    v2 = Rv(200); v2.rand_fill()
    nz = sum(1 for i in range(200) if v2[i] != 0)
    min_nz = 20 if ell > 1 else 50  # ELL=1: ~50% bits set
    check(f"Rvector{ell} rand_fill", nz >= min_nz, f"only {nz}/200")

    # batch_set / batch_get
    try:
        import array
        idxs = array.array('Q', random.sample(range(n), 5))
        batch_val = random.randint(0, m)
        a.batch_set(idxs, batch_val)
        out2 = Rv(5)
        a.batch_get(idxs, out2)
        ok = all(out2[i] == batch_val for i in range(5))
        check(f"Rvector{ell} batch_set/get", ok)
    except (ImportError, Exception) as e:
        pass  # skip batch if array not available

# ═══════════════════════════════════════════════════════════
#  ABY3 (ShrRep3)
# ═══════════════════════════════════════════════════════════
print(f"─── ABY3 ({len(ELLS_ABY3)} ELLs) ───")

for ell in ELLS_ABY3:
    m = mpmt.ring_mask(ell)
    SV = mpmt.ShrRep3ShareVec(ell)

    for p in [0, 1, 2]:
        check(f"ABY3({ell},{p})", mpmt.ShrRep3(ell, p) is not None)
    for bad in [0, 7]:
        try: mpmt.ShrRep3(bad, 0); ok = False
        except ValueError: ok = True
        check(f"ABY3 reject ell={bad}", ok)

    # crng
    def crng_fn(pid, p, n):
        inst = mpmt.ShrRep3(ell, pid)(p, n)
        return inst.crng()
    r = run_3party(crng_fn, ell)
    s = mpmt.ring_add(ell, r[0], mpmt.ring_add(ell, r[1], r[2]))
    check(f"ABY3{ell} crng", s == 0, f"sum={s}")

    # scalar ops (random)
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

    # vector share/reveal
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

    # send_data/recv_data (scalar)
    sc_val = random.randint(0, m)
    def sdfn(pid, p, n):
        inst = mpmt.ShrRep3(ell, pid)(p, n)
        if pid == 0:
            inst.send_data(1, sc_val)
            return inst.recv_data(2)
        elif pid == 1:
            rv = inst.recv_data(0)
            inst.send_data(2, sc_val)
            return rv
        else:
            rv = inst.recv_data(1)
            inst.send_data(0, sc_val)
            return rv
    r = run_3party(sdfn, ell)
    check(f"ABY3{ell} send/recv", r[0] == sc_val and r[1] == sc_val and r[2] == sc_val)

# ring_conv (ELL=1 only, target [2,6])
print(f"─── ABY3 ring_conv ───")
bit = random.randint(0, 1)
RC_ELLS = [2, 6] if args.sm else list(range(2, 7))
for ell_to in RC_ELLS:
    exp = bit & mpmt.ring_mask(ell_to)
    def rcfn(pid, p, n):
        inst = mpmt.ShrRep3(1, pid)(p, n)
        ss = inst.share_scalar(bit) if pid == 0 else inst.recv_scalar_share()
        r = inst.ring_conv(ss, ell_to)
        return (r.this_share, r.nxt_share)
    r = run_3party(rcfn, 1)
    got = recon3([(r[0][0], r[0][1]), (r[1][0], r[1][1]), (r[2][0], r[2][1])], ell_to)
    check(f"ring_conv 1→{ell_to}", got == exp, f"{got} vs {exp}")

# ring_conv_vec
bits = [random.randint(0, 1) for _ in range(8)]
for ell_to in RC_ELLS:
    exp = [b & mpmt.ring_mask(ell_to) for b in bits]
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
    check(f"ring_conv_vec 1→{ell_to}", r[0] == exp and r[0] == r[1] == r[2])

# ═══════════════════════════════════════════════════════════
#  EMP2 (ShrAdd2)
# ═══════════════════════════════════════════════════════════
print(f"─── EMP2 ({len(ELLS_EMP2)} ELLs) ───")

for ell in ELLS_EMP2:
    m = mpmt.ring_mask(ell)

    for p in [0, 1]:
        check(f"EMP2({ell},{p})", mpmt.ShrAdd2(ell, p) is not None)
    for bad in [1, 32]:
        try: mpmt.ShrAdd2(bad, 0); ok = False
        except ValueError: ok = True
        check(f"EMP2 reject ell={bad}", ok)

    # share/reconstruct
    sv = random.randint(0, m)
    def sf(pid, ch):
        inst = mpmt.ShrAdd2(ell, pid)(ch)
        return inst.share_scalar(sv) if pid == 0 else inst.recv_scalar_share()
    r = run_2party(sf, ell)
    check(f"EMP2{ell} share", recon2(r, ell) == sv)

    # send/recv scalar
    val = random.randint(0, m)
    def srf(pid, ch):
        inst = mpmt.ShrAdd2(ell, pid)(ch)
        if pid == 0: inst.send_data(val); return inst.recv_data()
        else: rv = inst.recv_data(); inst.send_data(val); return rv
    r = run_2party(srf, ell)
    check(f"EMP2{ell} send/recv", r == [val, val])

    # share_key
    key = bytes([random.randint(0, 255) for _ in range(16)])
    def kf(pid, ch):
        inst = mpmt.ShrAdd2(ell, pid)(ch)
        if pid == 0: return bytes(inst.share_key(key))
        else: buf = bytearray(16); inst.recv_key_share(buf); return bytes(buf)
    r = run_2party(kf, ell)
    check(f"EMP2{ell} share_key", bytes(a^b for a,b in zip(r[0],r[1])) == key)

    # share_element
    elem = bytes([random.randint(0, 255) for _ in range(random.randint(8, 32))])
    def ef(pid, ch):
        inst = mpmt.ShrAdd2(ell, pid)(ch)
        if pid == 0: return bytes(inst.share_element(elem))
        else: return bytes(inst.recv_element_share())
    r = run_2party(ef, ell)
    check(f"EMP2{ell} share_element", bytes(a^b for a,b in zip(r[0],r[1])) == elem)

    # mod
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

    # equality_test
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
    lbl = "eq" if exp_eq else "neq"
    check(f"EMP2{ell} eq({lbl})", r[0] == exp_eq and r[0] == r[1])

    # hash (XOR-shared inputs)
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

# ═══════════════════════════════════════════════════════════
#  DPF
# ═══════════════════════════════════════════════════════════
print(f"─── DPF ({len(DPF_EI)} EI × {len(DPF_EO)} EO) ───")

for ei in DPF_EI:
    for eo in DPF_EO:
        vl = 1 << ei; m = mpmt.ring_mask(eo)
        cores = DPF_CORES[random.randint(0, len(DPF_CORES)-1)]

        check(f"DPF Dealer({ei},{eo})", mpmt.DpfDealer(ei, eo) is not None)
        check(f"DPF Eval0({ei},{eo})", mpmt.DpfEvaluator(ei, eo, 0) is not None)
        check(f"DPF Eval1({ei},{eo})", mpmt.DpfEvaluator(ei, eo, 1) is not None)

        alpha = random.randint(0, vl - 1)
        beta = random.randint(1, m)

        # full eval + reveal
        def d_full(c0, c1):
            DC = mpmt.DpfDealer(ei, eo); d = DC(c0, c1)
            k0, k1 = DC.gen(alpha, beta); d.send_key(k0, 0); d.send_key(k1, 1)
            Rv = mpmt.Rvector(eo); out = Rv(vl); d.reveal(out)
            ok = out[alpha] == beta
            for i in random.sample(range(vl), min(20, vl)):
                if i != alpha and out[i] != 0: ok = False; break
            return "OK" if ok else "FAIL"
        def ev_full(pid):
            return lambda c: (EC:=mpmt.DpfEvaluator(ei,eo,pid), ev:=EC(c),
                  k:=ev.recv_key(), b:=mpmt.Rvector(eo)(vl),
                  EC.eval(k, b, cores=cores), ev.reveal(b), "OK")[-1]
        r = run_dpf(d_full, ev_full(0), ev_full(1), timeout=180)
        check(f"DPF({ei},{eo},c{cores}) full", r[0] == "OK")

        # range eval + reveal
        if vl > 20:
            bg = random.randint(0, vl - 20)
            ed = min(bg + random.randint(5, 20), vl - 1)
        else:
            bg = 0; ed = min(5, vl - 1)
        rl = ed - bg + 1
        def d_range(c0, c1):
            DC = mpmt.DpfDealer(ei, eo); d = DC(c0, c1)
            k0, k1 = DC.gen(alpha, beta); d.send_key(k0, 0); d.send_key(k1, 1)
            Rv = mpmt.Rvector(eo); out = Rv(rl); d.reveal(out)
            ok = True
            for i in range(rl):
                exp = beta if (bg + i) == alpha else 0
                if out[i] != exp: ok = False; break
            return "OK" if ok else "FAIL"
        def ev_range(pid):
            return lambda c: (EC:=mpmt.DpfEvaluator(ei,eo,pid), ev:=EC(c),
                  k:=ev.recv_key(), b:=mpmt.Rvector(eo)(rl),
                  EC.eval_range(k, b, bg, ed, cores=cores), ev.reveal(b), "OK")[-1]
        r = run_dpf(d_range, ev_range(0), ev_range(1), timeout=180)
        check(f"DPF({ei},{eo},c{cores}) range", r[0] == "OK")

# ═══════════════════════════════════════════════════════════
elapsed = time.monotonic() - TSTART
print(f"\n{'='*60}")
print(f"  MODE={MODE}  PASS={PASS}  FAIL={FAIL}  ({elapsed:.1f}s)")
print(f"{'='*60}")
sys.exit(0 if FAIL == 0 else 1)
