# `mpmt` — Python Interface Overview

`mpmt` is a standalone Python package that wraps the C++20 MPMT protocol core
(nanobind bindings). It exposes ring arithmetic, communication channels, the MPC
building blocks (replicated sharing, additive sharing, DPF) and the high-level
membership protocols — usable independently of the application layer.

```python
import mpmt

# parameters for a Bloom-filter of expected size `set_size` (>= 1024) and
# false-positive rate `fpr = fpr_mantissa × 10^fpr_exponent`
bf_size, ell_add2, hf_num, ell_root = mpmt.bf_param(set_size=1024, fpr_mantissa=1.0, fpr_exponent=-3)

# ring arithmetic over Z_{2^ell}
m = mpmt.ring_mask(ell=8)
x, y = 5, 3
assert mpmt.ring_add(8, x, y) == (x + y) & m
```

## Interface at a glance

| Area | Objects |
|------|---------|
| Ring data & arithmetic | `Rvector` / `RvectorPack`, `rvector_pack` / `rvector_unpack`, `ring_add` / `ring_sub` / `ring_mul` / `ring_mod` / `ring_mask` / `ring_rand` |
| Communication | `Channel`, `ChannelListener`, `RingTransport`, `NetIO_connect` / `NetIO_listen` / `NetIO_from_socket` |
| Building blocks | `ShrRep3` (3-party replicated sharing, ABY3), `ShrAdd2` (2-party additive, EMP2), `DpfDealer` / `DpfEvaluator` (BGI16 DPF), `ShrRep3ShareScalar` / `ShrRep3ShareVec` |
| High-level protocols | `SetHolder` (share a set), `Querier` (membership query), `AgentServer` (3-party protocol party, `ProtType` / `ServerRole`) |
| Utilities | `bf_param`, `gen_bf`, `get_key_128bits`, `hash_aes_dm` |

The templated C++ classes are exposed as per-`ell` / per-`party` concrete types
(e.g. `ShrRep3_1_0`, `ShrAdd2_24_1`), while the factories (`ShrRep3(ell, party)`,
`ShrAdd2(ell, party)`) select the right template and return a **type** to be
called on the party's channels:

```python
# all three parties in lockstep over a ring topology
inst = mpmt.ShrRep3(ell=1, party=pid)(ch_prev, ch_nxt)   # party 0 / 1 / 2
```

## Tutorials

| Directory | Topics |
|-----------|--------|
| [base/](./base/) | basic data structures and ring operations (notebooks) |
| [building_blocks/](./building_blocks/) | [rep3 (ABY3)](./building_blocks/rep3.md), [add2 (EMP2)](./building_blocks/add2.md), [dpf (BGI16)](./building_blocks/dpf.md) |
| [net/](./net/) | [channels](./net/channels.md), [ring transport](./net/ring_transport.md) |

## Example — a minimal 2-party additive share / reconstruct

```python
import mpmt

ELL = 8

def party(pid, ch):
    inst = mpmt.ShrAdd2(ELL, pid)(ch)
    if pid == 0:
        return inst.share_scalar(42)      # leader shares the plaintext
    return inst.recv_scalar_share()       # helper receives a share

# parties 0 and 1 run `party(pid, ch)` over a Channel; reconstruct:
# mpmt.ring_add(ELL, share0, share1) == 42
```

See the per-topic tutorials above for the full protocol surface, and
[`tests/`](../tests/) for executable examples of every layer.
