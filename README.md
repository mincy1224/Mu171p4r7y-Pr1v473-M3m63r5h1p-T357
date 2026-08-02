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

**Ring ops + Rvector** — local, no channels needed

```python
mpmt.ring_add(ell=8, a=100, b=200)          # (100+200) mod 256
mpmt.ring_rand(ell=8)                        # random in Z_256

v = mpmt.Rvector(ell=4)(size=100)            # 100 elements in Z_16
v[0] = 5; v.fill(val=0); v.rand_fill()
mpmt.Rvector(ell=4).add(v, v, v)             # in-place add
```

**EMP2** — 2-party additive, ELL ∈ [2,31]

Party 0 (server):
```python
from mpmt.channels import Channel
ch = Channel(12345)
inst = mpmt.ShrAdd2(ell=16, party=0)(ch)
s0 = inst.share_scalar(value=12345)
```

Party 1 (client):
```python
from mpmt.channels import Channel
ch = Channel("localhost", 12345)
inst = mpmt.ShrAdd2(ell=16, party=1)(ch)
s1 = inst.recv_scalar_share()
# s0 + s1 ≡ 12345 (mod 2^16)
```

**ABY3** — 3-party RSS, ELL ∈ [1,6]

Ring topology P0→P1→P2→P0. To break the circular connect dependency, at
least one party must connect before listening (shown here with P1). Other
deadlock-free orderings work equally well.

Party 0 (listen, then connect):
```python
from mpmt.channels import Channel
ch_to_p1 = Channel(12000)
ch_from_p2 = Channel(12002)
inst = mpmt.ShrRep3(ell=4, party=0)(ch_to_p1, ch_from_p2)
s0 = inst.share_scalar(val=42)
```

Party 1 (client to P0, server for P2):
```python
from mpmt.channels import Channel
ch_from_p0 = Channel("localhost", 12000)
ch_to_p2 = Channel(12001)
inst = mpmt.ShrRep3(ell=4, party=1)(ch_to_p2, ch_from_p0)
s1 = inst.recv_scalar_share()
```

Party 2 (client on both ports):
```python
from mpmt.channels import Channel
ch_from_p1 = Channel("localhost", 12001)
ch_to_p0 = Channel("localhost", 12002)
inst = mpmt.ShrRep3(ell=4, party=2)(ch_to_p0, ch_from_p1)
s2 = inst.recv_scalar_share()
# s0.this_share + s1.this_share + s2.this_share ≡ 42 (mod 16)
```

ring_conv — binary to arithmetic, ELL=1 only, target ∈ [2,6]:
```python
inst = mpmt.ShrRep3(ell=1, party=0)(ch_to_p1, ch_from_p2)
conv = inst.ring_conv(inst.share_scalar(val=1), ell_to=6)
```

**DPF** — function secret sharing, ELL_IN ∈ [13,31], ELL_OUT ∈ [2,6]

Dealer:
```python
from mpmt.channels import Channel
ch_ev0 = Channel(13000)
ch_ev1 = Channel(13001)
DC = mpmt.DpfDealer(ell_in=20, ell_out=4)
d = DC(ch_ev0, ch_ev1)
k0, k1 = DC.gen(alpha=42, beta=7)
d.send_key(k0, 0); d.send_key(k1, 1)
out = mpmt.Rvector(ell=4)(size=1 << 20)
d.reveal(out)  # out[42] == 7
```

Evaluator 0:
```python
ch = mpmt.channels.Channel("localhost", 13000)
EC = mpmt.DpfEvaluator(ell_in=20, ell_out=4, party=0)(ch)
key = EC.recv_key()
EC.eval(key, mpmt.Rvector(ell=4)(size=1 << 20), cores=4)
```

Evaluator 1:
```python
ch = mpmt.channels.Channel("localhost", 13001)
EC = mpmt.DpfEvaluator(ell_in=20, ell_out=4, party=1)(ch)
key = EC.recv_key()
EC.eval(key, mpmt.Rvector(ell=4)(size=1 << 20), cores=4)
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
