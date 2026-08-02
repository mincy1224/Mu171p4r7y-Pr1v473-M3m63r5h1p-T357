"""Rvector + RvectorPack standalone — factory, properties, save/load, scalar ops."""
import sys, os, random, tempfile
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
import mpmt

PASS = FAIL = 0
def check(name, cond, detail=""):
    global PASS, FAIL
    if cond: PASS += 1
    else: FAIL += 1; print(f"  ❌ {name}  {detail}")

def packed_bytes(ell, n):
    """RvectorPack._bytesFor result — packed buffer size."""
    if ell == 1: return ((n + 63) // 64) * 8
    if ell == 8: return n
    return (n * ell + 7) // 8

def rv_bytes(ell, n):
    """Rvector raw storage size — to_bytes() output length."""
    if ell == 1: return ((n + 63) // 64) * 8
    return n  # ELL >= 2: one byte per element

print("=== RvectorPack ===")

# Factory + reject invalid ell
for ell in [1, 4, 8]:
    rp = mpmt.RvectorPack(ell)
    check(f"RvectorPack({ell}) factory", rp is not None)
for bad in [0, 9]:
    try: mpmt.RvectorPack(bad)(1); ok = False
    except ValueError: ok = True
    check(f"RvectorPack({bad}) reject", ok)

# Properties
for ell in [1, 4, 8]:
    for n in [0, 1, 10, 100]:
        rp = mpmt.RvectorPack(ell)(n)
        check(f"RP({ell},{n}).ell", rp.ell == ell)
        check(f"RP({ell},{n}).size", rp.size == packed_bytes(ell, n),
              f"{rp.size} vs {packed_bytes(ell, n)}")
        check(f"RP({ell},{n}).n_elements", rp.n_elements == n)

# pack/unpack roundtrip (all ELLs, boundary sizes)
for ell in [1, 2, 3, 4, 5, 6, 7, 8]:
    m = mpmt.ring_mask(ell); Rv = mpmt.Rvector(ell)
    for n in [0, 1, 2, 3, 4, 5, 6, 7, 8, 15, 16, 17, 31, 32, 33, 64]:
        v = Rv(n)
        if n > 0:
            for i in range(n): v[i] = random.randint(0, m)
        aux = mpmt.RvectorPack(ell)(n)
        out = Rv(n)
        mpmt.rvector_pack(v, aux)
        mpmt.rvector_unpack(aux, out)
        ok = all(out[i] == v[i] for i in range(n))
        if not ok:
            check(f"pack/unpack ELL={ell} n={n}", False)
            break
    else:
        check(f"pack/unpack ELL={ell} all sizes", True)

# Mismatched ELL/size should throw
v4 = mpmt.Rvector(4)(10); v4.fill(5)
v1 = mpmt.Rvector(1)(10)
try: mpmt.rvector_pack(v4, mpmt.RvectorPack(1)(10)); ok = False
except (ValueError, RuntimeError): ok = True
check("pack mismatch ELL", ok)
try: mpmt.rvector_unpack(mpmt.RvectorPack(4)(10), v1); ok = False
except (ValueError, RuntimeError): ok = True
check("unpack mismatch ELL", ok)
try: mpmt.rvector_pack(v4, mpmt.RvectorPack(4)(9)); ok = False
except (ValueError, RuntimeError): ok = True
check("pack mismatch size", ok)
try: mpmt.rvector_unpack(mpmt.RvectorPack(4)(9), v4); ok = False
except (ValueError, RuntimeError): ok = True
check("unpack mismatch size", ok)

print("=== Rvector scalar ops ===")
for ell in [1, 4, 8]:
    m = mpmt.ring_mask(ell); Rv = mpmt.Rvector(ell)
    n = 32
    a = Rv(n); out = Rv(n)
    for i in range(n): a[i] = random.randint(0, m)
    scalar = random.randint(0, m)
    cls = type(a)
    cls.add_scalar(a, scalar, out)
    ok = all(out[i] == mpmt.ring_add(ell, a[i], scalar) for i in range(n))
    check(f"Rvector{ell} add_scalar", ok)
    cls.sub_scalar(a, scalar, out)
    ok = all(out[i] == mpmt.ring_sub(ell, a[i], scalar) for i in range(n))
    check(f"Rvector{ell} sub_scalar", ok)
    cls.mul_scalar(a, scalar, out)
    ok = all(out[i] == mpmt.ring_mul(ell, a[i], scalar) for i in range(n))
    check(f"Rvector{ell} mul_scalar", ok)

print("=== Rvector save/load ===")
for ell in [1, 4, 8]:
    m = mpmt.ring_mask(ell); Rv = mpmt.Rvector(ell)
    n = 64
    a = Rv(n)
    for i in range(n): a[i] = random.randint(0, m)
    with tempfile.NamedTemporaryFile(suffix='.mpmtrvp', delete=False) as f:
        tmp = f.name
    try:
        aux = mpmt.RvectorPack(ell)(n)
        a.save(tmp, aux)
        b = Rv(n)
        b.load(tmp, aux)
        ok = all(a[i] == b[i] for i in range(n))
        check(f"Rvector{ell} save/load", ok)
    finally:
        os.unlink(tmp)
try: a.save("/nonexistent/dir/x.mpmtrvp", aux); ok = False
except (RuntimeError, OSError): ok = True
check("save invalid path", ok)

print("=== Rvector to_bytes/from_bytes ===")
for ell in [1, 4, 8]:
    m = mpmt.ring_mask(ell); Rv = mpmt.Rvector(ell)
    a = Rv(32)
    for i in range(32): a[i] = random.randint(0, m)
    raw = a.to_bytes()
    check(f"Rvector{ell} to_bytes len", len(raw) == rv_bytes(ell, 32),
          f"{len(raw)} vs {rv_bytes(ell, 32)}")
    b = Rv(32)
    b.from_bytes(raw)
    ok = all(a[i] == b[i] for i in range(32))
    check(f"Rvector{ell} to/from_bytes", ok)

print(f"\nPASS={PASS} FAIL={FAIL}")
assert FAIL == 0
