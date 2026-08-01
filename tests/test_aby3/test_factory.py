"""ABY3 factory: ell range, party range, instance creation. Pure local."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))
import mpmt

PASS = FAIL = 0
def check(name, cond):
    global PASS, FAIL
    if cond: PASS += 1; print(f"  ✅ {name}")
    else: FAIL += 1; print(f"  ❌ {name}")

print("=== ABY3 Factory ===")

# -- ell range [1, 6] --
for ell in [1, 2, 3, 4, 5, 6]:
    for p in [0, 1, 2]:
        cls = mpmt.ShrRep3(ell, p)
        check(f"ShrRep3(ell={ell}, party={p})", cls is not None)

# -- reject ell out of range --
for bad in [0, 7, 8, 9]:
    try: mpmt.ShrRep3(bad, 0); ok = False
    except ValueError: ok = True
    check(f"ShrRep3(ell={bad}, ...) raises ValueError", ok)

# -- reject party out of range --
for bad in [-1, 3, 99]:
    try: mpmt.ShrRep3(2, bad); ok = False
    except ValueError: ok = True
    check(f"ShrRep3(ell=2, party={bad}) raises ValueError", ok)

print(f"\nPASS={PASS} FAIL={FAIL}")
assert FAIL == 0
