# MPMT — Multiparty Private Membership Test[![status](https://img.shields.io/badge/status-WIP-orange)]()

A research prototype implementing MPMT(semi-honest) with C++20 and Python (nanobind 2.13).
Enables private membership testing while keeping both query elements and
multi-party set data confidential.

## Platform

- **OS**: Linux (tested on WSL2, Ubuntu 22.04)
- **Architecture**: x86-64 (AMD64), little-endian
- **Compiler**: Clang >= 15 (GCC not supported; `-march=native` required for SIMD)

## Architecture

```
  ┌─ Python ─────────────────────────────────────────────┐
  │  ProtocolHandler  QueryServer                        │
  │  MpmtServerLeader MpmtSetHolder  TreeCache           │
  ├─ C++ bindings ───────────────────────────────────────┤
  │  nanobind 2.13  ·  shared_ptr IOChannel registry     │
  │  GIL-safe I/O   ·  factory dispatch                  │
  ├─ C++ core ───────────────────────────────────────────┤
  │  Rvector        SIMD-packed ring arithmetic  (1..8)  │
  │  ABY3           3-party replicated RSS       (1..6)  │
  │  EMP2           2-party additive sharing    (2..31)  │
  │  BGI16          distributed point function (13..31)  │
  └──────────────────────────────────────────────────────┘
```

IOChannel lifetime is managed through a `shared_ptr` registry keyed by
opaque `uintptr_t` handles. Python `Channel` objects hold handles; C++
wrappers call `netio_acquire(handle)` to obtain a `shared_ptr` copy,
guaranteeing the channel outlives all protocol objects.

## Dependencies

| Dependency | Version |
|------------|---------|
| Clang | 15.0.7 |
| CMake | >= 3.25 |
| Ninja | >= 1.10 |
| emp-tool | 0.3.0 |
| emp-ot | 0.3.0 |
| emp-sh2pc | 0.3.0 |
| libsodium | 1.0.18 |
| nlohmann/json | 3.10.5 |
| nanobind | >= 2.13 |
| cloudpickle | >= 3.1 |
| Python | >= 3.10 |

## Demo Usage

```python
import mpmt

# ── Ring arithmetic ──────────────────────────────
v = mpmt.ring_add(ell=8, a=100, b=200)          # (100+200) mod 256
r = mpmt.ring_rand(ell=8)                       # random in Z_256
h = mpmt.hash_aes_dm(preimage=b"hello",
                     key=mpmt.get_key_128bits(),
                     ell=8)

# ── Rvector ──────────────────────────────────────
Rv = mpmt.Rvector(ell=4)
v = Rv(size=100)                                # 100-element Z_16 vector
v[0] = 5; v.fill(val=0); v.rand_fill()

# ── ABY3 (3-party RSS) ──────────────────────────
ShrRep3 = mpmt.ShrRep3(ell=4, party=0)          # ELL=4, party 0
inst = ShrRep3(prev_channel, next_channel)
share = inst.share_scalar(val=42)               # → ShrRep3ShareScalar

# ── EMP2 (2-party additive) ─────────────────────
ShrAdd2 = mpmt.ShrAdd2(ell=16, party=0)          # ELL=16, party 0
inst2 = ShrAdd2(peer_channel)
inst2.share_scalar(value=12345)

# ── DPF ─────────────────────────────────────────
Dealer = mpmt.DpfDealer(ell_in=20, ell_out=4)
k0, k1 = Dealer.gen(alpha=42, beta=7)
```

**Tutorials** ([tutorial/](./tutorial/)):  
`base/` — ring operations · rvector · share vectors  
`building_blocks/` — rep3 (ABY3) · add2 (EMP2) · dpf  
`net/` — channels · ring transport  
`protocol/` — handler · query · set holder · tree cache  
`application/` — Flask demo

## Build

```bash
pip install -e . -v
```

Or manually:

```bash
mkdir -p build && cd build
cmake .. -GNinja \
  -DCMAKE_BUILD_TYPE=Release \
  -DCMAKE_C_COMPILER=clang-15 \
  -DCMAKE_CXX_COMPILER=clang++-15 \
  -DCMAKE_PREFIX_PATH="$HOME/.local/lib/python3.10/site-packages/nanobind/cmake" \
  -DSKBUILD=ON
ninja
```

Sanitizer build (development):

```bash
cmake .. -DMPMT_SANITIZE=address,undefined
```

## Continuous Integration

Weekly on GitHub Actions (`.github/workflows/ci.yml`): every Monday + manually.
Builds emp-toolkit (cached), compiles mpmt, runs `python3 tests/run_all.py`.

```bash
python3 tests/run_all.py -sm   # local pre-push (30s, boundaries only)
python3 tests/run_all.py        # full coverage (all parameters)
```

## Disclaimer
This code is intended solely for **ACADEMIC RESEARCH PURPOSES** and has not undergone a formal **PRODUCTION SECURITY AUDIT**. It is provided **“AS IS,”** without any **EXPRESS OR IMPLIED WARRANTIES**. **USE IT AT YOUR OWN RISK.**

## References

- ABY3 (Mohassel & Rindal, CCS 2018): 3-party replicated secret sharing
- BGI16 DPF (Boyle, Gilboa & Ishai, CCS 2016): function secret sharing
- emp-toolkit: https://github.com/emp-toolkit
- SIMD packing inspired by [lemire/simdcomp](https://github.com/lemire/simdcomp)
