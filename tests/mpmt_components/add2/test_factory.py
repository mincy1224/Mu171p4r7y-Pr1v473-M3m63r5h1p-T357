"""EMP2 factory tests — ShrAdd2(ell, party) exhaustive validation."""
import sys, os
_sys_t = os.path.dirname(os.path.abspath(__file__))
while _sys_t and not os.path.isdir(os.path.join(_sys_t, 'common')):
    _sys_t = os.path.dirname(_sys_t)
sys.path.insert(0, _sys_t)
import mpmt

PASS = FAIL = 0

def check(name, cond, detail=""):
    global PASS, FAIL
    if cond: PASS += 1
    else: FAIL += 1; print(f"  FAIL {name}  {detail}")

def run_tests(small=False):
    global PASS, FAIL
    PASS = FAIL = 0
    print("=== EMP2 Factory ===")

    ells = [2, 31] if small else list(range(2, 32))
    for ell in ells:
        for party in [0, 1]:
            cls = mpmt.ShrAdd2(ell, party)
            check(f"ShrAdd2(ell={ell}, party={party}) returns class",
                  cls is not None)

    for bad_ell in [0, 1, 32, 64]:
        try:
            mpmt.ShrAdd2(bad_ell, 0)
            check(f"ShrAdd2 reject ell={bad_ell}", False, "no error raised")
        except (ValueError, RuntimeError):
            check(f"ShrAdd2 reject ell={bad_ell}", True)

    for bad_party in [-1, 2, 3]:
        try:
            mpmt.ShrAdd2(2, bad_party)
            check(f"ShrAdd2 reject party={bad_party}", False, "no error raised")
        except (ValueError, RuntimeError):
            check(f"ShrAdd2 reject party={bad_party}", True)

    if not small:
        for ell in range(2, 32):
            for party in [0, 1]:
                cls = mpmt.ShrAdd2(ell, party)
                check(f"ShrAdd2 ell={ell} p={party} exists", cls is not None)

    print(f"  PASS={PASS}  FAIL={FAIL}")
    return PASS, FAIL

if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--small", action="store_true")
    args = ap.parse_args()
    rc = run_tests(small=args.small)
    raise SystemExit(0 if rc[1] == 0 else 1)
