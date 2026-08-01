"""EMP2 factory: ell range [2,31], party 0/1. Pure local."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))
import mpmt

PASS = FAIL = 0
def chk(name, cond):
    global PASS, FAIL
    if cond: PASS += 1; print(f"  ✅ {name}")
    else: FAIL += 1; print(f"  ❌ {name}")

print("=== EMP2 Factory ===")

# Valid range
for ell in [2, 20, 31]:
    for p in [0, 1]:
        cls = mpmt.ShrAdd2(ell, p)
        chk(f"ShrAdd2(ell={ell}, party={p})", cls is not None)

# Reject ell
for bad in [1, 32]:
    try: mpmt.ShrAdd2(bad, 0); ok = False
    except ValueError: ok = True
    chk(f"ShrAdd2(ell={bad}, ...) raises ValueError", ok)

# Reject invalid party
try: mpmt.ShrAdd2(2, 2); ok = False
except ValueError: ok = True
chk("ShrAdd2(ell=2, party=2) raises ValueError", ok)

print(f"\nPASS={PASS} FAIL={FAIL}")
assert FAIL == 0
