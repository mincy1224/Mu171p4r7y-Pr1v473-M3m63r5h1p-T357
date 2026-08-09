"""bf_param, get_key_128bits, hash_aes_dm — comprehensive coverage."""
import sys, os, random
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
    print("=== Util ===")

    # -- bf_param --------------------------------
    # Known fixed value
    bf_size, log2up, hf_num, hf_log2up = mpmt.bf_param(
        set_size=2**10, fpr_mantissa=1.0, fpr_exponent=-3)
    check("bf_param(1024, 1e-3) bf_size", bf_size == 14723, f"got {bf_size}")
    check("bf_param(1024, 1e-3) log2up", log2up == 14, f"got {log2up}")
    check("bf_param(1024, 1e-3) hf_num", hf_num == 10, f"got {hf_num}")
    check("bf_param(1024, 1e-3) hf_log2up", hf_log2up == 4, f"got {hf_log2up}")

    # hf_num reference table: fpr → (hf_num, hf_log2up)
    hf_table = {
        "1e-1":  ( 3, 2), "1e-2":  ( 7, 3), "1e-3":  (10, 4),
        "1e-4":  (13, 4), "1e-5":  (17, 5), "1e-6":  (20, 5),
        "1e-7":  (23, 5), "1e-8":  (27, 5), "1e-9":  (30, 5),
        "1e-10": (33, 6), "1e-11": (37, 6), "1e-12": (40, 6),
    }
    exponents = list(range(-1, -13, -1))
    set_sizes_test = [2**10, 2**15, 2**20, 2**25] if not small else [2**10, 2**20]

    for exp in exponents:
        mantissa = 1.0
        _, _, hf_num_got, hf_log2up_got = mpmt.bf_param(
            set_size=2**10, fpr_mantissa=mantissa, fpr_exponent=exp)
        exp_str = f"1e{exp}"
        expected_hf, expected_log = hf_table[exp_str]
        check(f"hf_num fpr={exp_str}", hf_num_got == expected_hf,
              f"got {hf_num_got} exp {expected_hf}")
        check(f"hf_log2up fpr={exp_str}", hf_log2up_got == expected_log,
              f"got {hf_log2up_got} exp {expected_log}")

    # bf_size reference table: spot-check
    bf_ref = {
        (10, -1): 4908, (10, -3): 14723, (10, -6): 29445,
        (15, -1): 157042, (15, -3): 471125, (15, -6): 942250,
        (20, -1): 5025331, (20, -3): 15075993, (20, -6): 30151987,
        (25, -1): 160810595, (25, -3): 482431784, (25, -6): 964863569,
    }
    for (log_n, fpr_exp), expected in bf_ref.items():
        # Only test larger sizes in full mode
        if small and log_n >= 25:
            continue
        size, _, _, _ = mpmt.bf_param(
            set_size=2**log_n, fpr_mantissa=1.0, fpr_exponent=fpr_exp)
        check(f"bf_size(2^{log_n}, 1e{fpr_exp})", size == expected,
              f"got {size} exp {expected}")

    # bf_size scales with set_size (monotonicity)
    if not small:
        prev = 0
        for log_n in [10, 15, 20, 25]:
            size, _, _, _ = mpmt.bf_param(
                set_size=2**log_n, fpr_mantissa=1.0, fpr_exponent=-3)
            check(f"bf_size monotonic 2^{log_n}", size > prev,
                  f"{size} <= {prev}")
            prev = size

    # Parameter validation
    for bad_size in [2**9, 2**26]:
        try: mpmt.bf_param(set_size=bad_size, fpr_mantissa=1.0, fpr_exponent=-3)
        except (ValueError, OverflowError): check(f"bf_param reject set_size={bad_size}", True)
        else: check(f"bf_param reject set_size={bad_size}", False)

    for bad_mantissa in [0.5, 10.0]:
        try: mpmt.bf_param(set_size=2**10, fpr_mantissa=bad_mantissa, fpr_exponent=-3)
        except (ValueError, OverflowError): check(f"bf_param reject mantissa={bad_mantissa}", True)
        else: check(f"bf_param reject mantissa={bad_mantissa}", False)

    for bad_exp in [-13, 0]:
        try: mpmt.bf_param(set_size=2**10, fpr_mantissa=1.0, fpr_exponent=bad_exp)
        except (ValueError, OverflowError): check(f"bf_param reject exponent={bad_exp}", True)
        else: check(f"bf_param reject exponent={bad_exp}", False)

    # -- get_key_128bits -------------------------
    key = mpmt.get_key_128bits()
    check("get_key_128bits type", isinstance(key, bytes))
    check("get_key_128bits len", len(key) == 16, f"got {len(key)}")

    k1 = mpmt.get_key_128bits()
    k2 = mpmt.get_key_128bits()
    check("get_key_128bits unique", k1 != k2)

    # -- hash_aes_dm ------------------------------
    ells_hash = [4, 20] if small else [1, 2, 4, 8, 16, 20, 31]
    seed = mpmt.get_key_128bits()

    for ell in ells_hash:
        m = mpmt.ring_mask(ell)

        # bytes preimage
        h = mpmt.hash_aes_dm(preimage=b"alice", key=seed, ell=ell)
        check(f"hash_aes_dm bytes ell={ell} range", 0 <= h <= m,
              f"got {h} not in [0, {m}]")

        # str preimage (UTF-8)
        h_str = mpmt.hash_aes_dm(preimage="alice", key=seed, ell=ell)
        check(f"hash_aes_dm str ell={ell} matches bytes",
              h_str == h, f"{h_str} != {h}")

        # Determinism
        h2 = mpmt.hash_aes_dm(preimage=b"alice", key=seed, ell=ell)
        check(f"hash_aes_dm deterministic ell={ell}", h == h2)

        # Different preimage → different hash (probabilistic: skip for small ell)
        if ell >= 16:
            h_diff = mpmt.hash_aes_dm(preimage=b"bob", key=seed, ell=ell)
            check(f"hash_aes_dm diff_pt ell={ell}", h != h_diff,
                  f"same hash {h} for alice and bob")

        # Different key → different hash (probabilistic: skip small ell)
        if ell >= 16:
            seed2 = mpmt.get_key_128bits()
            h_key = mpmt.hash_aes_dm(preimage=b"alice", key=seed2, ell=ell)
            check(f"hash_aes_dm diff_key ell={ell}", h != h_key)

    print(f"  PASS={PASS}  FAIL={FAIL}")
    return PASS, FAIL

if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--small", action="store_true")
    args = ap.parse_args()
    rc = run_tests(small=args.small)
    raise SystemExit(rc)
