"""ABY3 factory tests — ShrRep3(ell, party), ShrRep3ShareVec(ell) exhaustive validation."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', '..'))
import mpmt

PASS = FAIL = 0

def check(name, cond, detail=""):
    global PASS, FAIL
    if cond: PASS += 1
    else: FAIL += 1; print(f"  FAIL {name}  {detail}")

def run_tests(small=False):
    global PASS, FAIL
    PASS = FAIL = 0
    print("=== ABY3 Factory ===")

    # ——— ShrRep3(ell, party) ———————————————————————
    # All valid (ell, party) combinations
    ells = [1, 6] if small else list(range(1, 7))
    for ell in ells:
        for party in [0, 1, 2]:
            cls = mpmt.ShrRep3(ell, party)
            check(f"ShrRep3(ell={ell}, party={party}) returns class",
                  cls is not None)

    # Reject invalid ell
    for bad_ell in [0, 7, 8, 32]:
        try:
            mpmt.ShrRep3(bad_ell, 0)
            check(f"ShrRep3 reject ell={bad_ell}", False, "no error raised")
        except (ValueError, RuntimeError):
            check(f"ShrRep3 reject ell={bad_ell}", True)

    # Reject invalid party
    for bad_party in [-1, 3, 4]:
        try:
            mpmt.ShrRep3(1, bad_party)
            check(f"ShrRep3 reject party={bad_party}", False, "no error raised")
        except (ValueError, RuntimeError):
            check(f"ShrRep3 reject party={bad_party}", True)

    # ——— ShrRep3ShareVec(ell) ———————————————————————
    for ell in ells:
        SV = mpmt.ShrRep3ShareVec(ell)
        check(f"ShrRep3ShareVec(ell={ell}) returns class", SV is not None)

        # Zero-size construction
        sv0 = SV(0)
        check(f"ShrRep3ShareVec(ell={ell}) size=0", sv0.size == 0)

        # Non-zero construction
        sv = SV(16)
        check(f"ShrRep3ShareVec(ell={ell}) size=16", sv.size == 16)
        check(f"ShrRep3ShareVec(ell={ell}) this_share.size",
              sv.this_share.size == 16)
        check(f"ShrRep3ShareVec(ell={ell}) nxt_share.size",
              sv.nxt_share.size == 16)

        # Verify this_share and nxt_share are independent
        m = mpmt.ring_mask(ell)
        sv.this_share[0] = min(3, m)
        sv.nxt_share[0] = min(7, m)
        check(f"ShrRep3ShareVec(ell={ell}) independent shares",
              sv.this_share[0] == min(3, m) and sv.nxt_share[0] == min(7, m))

        # Size mismatch rejection: this_share
        Rv = mpmt.Rvector(ell)
        bad_v = Rv(99)
        try:
            sv.this_share = bad_v
            check(f"ShrRep3ShareVec(ell={ell}) reject mismatched this_share",
                  False, "no error raised")
        except (ValueError, RuntimeError):
            check(f"ShrRep3ShareVec(ell={ell}) reject mismatched this_share", True)

        # Size mismatch rejection: nxt_share
        sv2 = SV(16)
        try:
            sv2.nxt_share = bad_v
            check(f"ShrRep3ShareVec(ell={ell}) reject mismatched nxt_share",
                  False, "no error raised")
        except (ValueError, RuntimeError):
            check(f"ShrRep3ShareVec(ell={ell}) reject mismatched nxt_share", True)

        # Same-size assignment works
        good_v = Rv(16)
        sv3 = SV(16)
        try:
            sv3.this_share = good_v
            sv3.nxt_share = good_v
            check(f"ShrRep3ShareVec(ell={ell}) same-size assign", True)
        except (ValueError, RuntimeError):
            check(f"ShrRep3ShareVec(ell={ell}) same-size assign", False)

    # Reject invalid ell for ShareVec
    for bad_ell in [0, 7, 8]:
        try:
            mpmt.ShrRep3ShareVec(bad_ell)
            check(f"ShrRep3ShareVec reject ell={bad_ell}", False, "no error raised")
        except (ValueError, RuntimeError):
            check(f"ShrRep3ShareVec reject ell={bad_ell}", True)

    # ——— ShrRep3ShareScalar —————————————————————————
    ss = mpmt.ShrRep3ShareScalar()
    check("ShrRep3ShareScalar construct", ss is not None)
    ss.this_share = 42
    ss.nxt_share = 99
    check("ShrRep3ShareScalar this_share", ss.this_share == 42)
    check("ShrRep3ShareScalar nxt_share", ss.nxt_share == 99)

    print(f"  PASS={PASS}  FAIL={FAIL}")
    return PASS, FAIL

if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--small", action="store_true")
    args = ap.parse_args()
    rc = run_tests(small=args.small)
    raise SystemExit(rc)
