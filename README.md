# MPMT — Multiparty Private Membership Test [![status](https://img.shields.io/badge/status-WIP-orange)]()

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

## Tutorials

| Directory | Topics |
|-----------|--------|
| [base/](./tutorial/base/) | ring operations, rvector, share vectors |
| [building_blocks/](./tutorial/building_blocks/) | rep3 (ABY3), add2 (EMP2), dpf |
| [net/](./tutorial/net/) | channels, ring transport |
| [protocol/](./tutorial/protocol/) | handler, query, set holder, tree cache |
| [application/](./tutorial/application/) | Flask demo |

## Quick Start
**Build & Install**
```bash
pip install -e . -v
```

**Ring ops**

```python
mpmt.ring_add(ell=8, a=100, b=200)     # (100+200) mod 256
mpmt.ring_rand(ell=8)                  # random in Z_256
mpmt.hash_aes_dm(preimage=b"hi", key=mpmt.get_key_128bits(), ell=8)
```

**Rvector** — Vectors upon $\mathbb{Z}$, ELL ∈ [1,8]

```python
v = mpmt.Rvector(ell=4)(size=100)       # 100 elements in Z_16
v[0] = 5; v.fill(val=0); v.rand_fill()
mpmt.Rvector(ell=4).add(v, v, v)        # in-place add
```

**ABY3** — 3PC RSS, ELL ∈ [1,6]

```python
inst = mpmt.ShrRep3(ell=4, party=0)(prev_ch, next_ch)
share = inst.share_scalar(val=42)
inst.add(share, share)                  # local
inst.mul(share, share)                  # network round
inst.ring_conv(share, ell_to=6)         # binary→arithmetic (ELL=1 only)
```

**EMP2** — 2PC Additive, ELL ∈ [2,31]

```python
inst = mpmt.ShrAdd2(ell=16, party=0)(peer_ch)
inst.share_scalar(value=12345)
inst.equality_test(inst.share_scalar(5), inst.recv_scalar_share())
```

**DPF** — Distributed Point Function, ELL_IN ∈ [13,31], ELL_OUT ∈ [2,6]

```python
DC = mpmt.DpfDealer(ell_in=20, ell_out=4)
k0, k1 = DC.gen(alpha=42, beta=7)
EC = mpmt.DpfEvaluator(ell_in=20, ell_out=4, party=0)
EC.eval(k0, mpmt.Rvector(ell=4)(size=1 << 20), cores=4)
```

**Run TESTs**
```bash
python3 tests/run_all.py -sm        # run tests (2s)
```

## Disclaimer
This code is intended solely for **ACADEMIC RESEARCH PURPOSES** and has not undergone a formal **PRODUCTION SECURITY AUDIT**. It is provided **"AS IS,"** without any **EXPRESS OR IMPLIED WARRANTIES**. **USE IT AT YOUR OWN RISK.**

## References

- ABY3 (Mohassel & Rindal, CCS 2018): 3-party replicated secret sharing
- BGI16 DPF (Boyle, Gilboa & Ishai, CCS 2016): function secret sharing
- emp-toolkit: https://github.com/emp-toolkit
- SIMD packing inspired by [lemire/simdcomp](https://github.com/lemire/simdcomp)
