"""ShrRep3ShareScalar / ShrRep3ShareVec — standalone container tests."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))
import mpmt

PASS = FAIL = 0

def check(name, cond, detail=""):
    global PASS, FAIL
    if cond: PASS += 1
    else: FAIL += 1; print(f"  FAIL {name}  {detail}")

def run_tests(small=False):
    global PASS, FAIL
    PASS = FAIL = 0
    print("=== ShareVec ===")

    # -- ShrRep3ShareScalar ----------------------
    ss = mpmt.ShrRep3ShareScalar()
    check("ShareScalar construct", ss is not None)

    ss.this_share = 7
    ss.nxt_share = 2
    check("ShareScalar this_share", ss.this_share == 7)
    check("ShareScalar nxt_share", ss.nxt_share == 2)

    # Overwrite
    ss.this_share = 15
    ss.nxt_share = 3
    check("ShareScalar overwrite this", ss.this_share == 15)
    check("ShareScalar overwrite nxt", ss.nxt_share == 3)

    # Default values
    ss2 = mpmt.ShrRep3ShareScalar()
    check("ShareScalar default int", isinstance(ss2.this_share, int))

    # -- ShrRep3ShareVec -------------------------
    ells = [1, 6] if small else list(range(1, 7))
    for ell in ells:
        SV = mpmt.ShrRep3ShareVec(ell)
        check(f"ShareVec factory ell={ell}", SV is not None)

        sv = SV(10)
        check(f"ShareVec ell={ell} construct", sv is not None)
        check(f"ShareVec ell={ell} size", sv.size == 10)

        # this_share / nxt_share are Rvector instances
        ts = sv.this_share
        ns = sv.nxt_share
        check(f"ShareVec ell={ell} this_share type", ts is not None)
        check(f"ShareVec ell={ell} nxt_share type", ns is not None)

        # Read/write elements
        m_val = mpmt.ring_mask(ell)
        v1 = min(ell, m_val)
        v2 = min(ell + 1, m_val)
        sv.this_share[0] = v1
        sv.nxt_share[0] = v2
        check(f"ShareVec ell={ell} this_share[0]", sv.this_share[0] == v1)
        check(f"ShareVec ell={ell} nxt_share[0]", sv.nxt_share[0] == v2)

        # Size mismatch rejection — this_share
        Rv = mpmt.Rvector(ell)
        bad = Rv(99)
        try: sv.this_share = bad
        except (ValueError, RuntimeError): check(f"ShareVec ell={ell} reject bad this_share size", True)
        else: check(f"ShareVec ell={ell} reject bad this_share size", False, "no error raised")

        # Size mismatch rejection — nxt_share
        sv2 = SV(10)
        try: sv2.nxt_share = bad
        except (ValueError, RuntimeError): check(f"ShareVec ell={ell} reject bad nxt_share size", True)
        else: check(f"ShareVec ell={ell} reject bad nxt_share size", False, "no error raised")

        # Same size assignment should work
        good = Rv(10)
        sv3 = SV(10)
        try:
            sv3.this_share = good
            sv3.nxt_share = good
            check(f"ShareVec ell={ell} same-size assign", True)
        except (ValueError, RuntimeError):
            check(f"ShareVec ell={ell} same-size assign", False, "unexpected error")

        # Zero-size
        sv0 = SV(0)
        check(f"ShareVec ell={ell} size=0", sv0.size == 0)

        # Large size (only in full mode)
        if not small:
            sv_large = SV(100000)
            check(f"ShareVec ell={ell} size=100000", sv_large.size == 100000)

    # Reject invalid ell
    for bad in [0, 7]:
        try: mpmt.ShrRep3ShareVec(bad)
        except (ValueError, RuntimeError): check(f"ShareVec reject ell={bad}", True)
        else: check(f"ShareVec reject ell={bad}", False, "no error raised")

    print(f"  PASS={PASS}  FAIL={FAIL}")
    return PASS, FAIL

if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--small", action="store_true")
    args = ap.parse_args()
    rc = run_tests(small=args.small)
    raise SystemExit(rc)
