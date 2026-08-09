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
  │  AgentServer SetHolder Querier                       │
  │  _TreeCache                                          │
  ├─ C++ bindings ───────────────────────────────────────┤
  │  nanobind  ·  shared_ptr IOChannel registry          │
  │  GIL-safe I/O   ·  factory dispatch                  │
  ├─ C++ core ───────────────────────────────────────────┤
  │  Rvector        SIMD-packed ring arithmetic  (1..8)  │
  │  ABY3           3-party replicated RSS       (1..6)  │
  │  EMP2           2-party additive sharing    (2..31)  │
  │  BGI16          distributed point function (13..31)  │
  │  RingTransport                                       │
  │  Utils                                               │
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

## Build
**Build & Install**
```bash
pip install -e . -v
```

## Disclaimer
This code is intended solely for **ACADEMIC RESEARCH PURPOSES** and has not undergone a formal **PRODUCTION SECURITY AUDIT**. It is provided **"AS IS,"** without any **EXPRESS OR IMPLIED WARRANTIES**. **USE IT AT YOUR OWN RISK.**

## References

- ABY3 (Mohassel & Rindal, CCS 2018): 3-party replicated secret sharing
- BGI16 DPF (Boyle, Gilboa & Ishai, CCS 2016): function secret sharing
- emp-toolkit: https://github.com/emp-toolkit
- SIMD packing inspired by [lemire/simdcomp](https://github.com/lemire/simdcomp)
