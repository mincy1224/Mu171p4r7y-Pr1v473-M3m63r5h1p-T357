# ShrRep3 — Three-Party Replicated Secret Sharing (ABY3)

`ELL ∈ [1, 6]`. Three-party ring topology (P0 → P1 → P2 → P0).
Each party holds **two** replicated shares. Total communication complexity is O(1) rounds.

## Factory

`ShrRep3(ell, party)` returns a **type** (not an instance).
Call `type(ch_prev, ch_nxt)` to construct a protocol instance.

| Parameter | Type | Description |
|------|------|------|
| `ell` | `int` | Ring bit-width, `[1, 6]` |
| `party` | `int` | Party ID, `0` / `1` / `2` |
| `ch_prev` | `Channel` | Channel receiving from the previous party |
| `ch_nxt` | `Channel` | Channel sending to the next party |

P0's `ch_prev` connects to P2, `ch_nxt` connects to P1.
P1's `ch_prev` connects to P0, `ch_nxt` connects to P2.
P2's `ch_prev` connects to P1, `ch_nxt` connects to P0.

## Attributes

| Attribute | Type | Description |
|------|------|------|
| `inst.ell` | `int` | Ring bit-width (read-only) |
| `inst.party` | `int` | Party ID (read-only) |

## Scalar Sharing

**Leader (P0)**: `inst.share_scalar(val)` — Convert plaintext value `val` into RSS3 shares.

**Helper (P1, P2)**: `inst.recv_scalar_share()` — Receive shares from the ring.

Returns: `ShrRep3ShareScalar`, with fields `this_share` and `nxt_share` (`int`).

```python
# All three parties call in lockstep:
if party == 0:
    ss = inst.share_scalar(val=7)
else:
    ss = inst.recv_scalar_share()
# ss.this_share, ss.nxt_share
```

## Vector Sharing

**Leader**: `inst.share_vector(vec, sv, aux_buf)` — Share an `Rvector` into RSS3 form.

**Helper**: `inst.recv_vector_share(sv, aux_buf)` — Receive vector shares.

| Parameter | Description |
|------|------|
| `vec` | Plaintext `Rvector` (P0 only; for P1/P2 the sharing implicitly includes a zero-vector) |
| `sv` | Pre-allocated `ShrRep3ShareVec`, receives the result; receiver must pre-allocate with non-zero size |
| `aux_buf` | `RvectorPack` scratch buffer, present only for API compatibility |

Both methods release the GIL. `share_vector` automatically resizes `sv` to match `vec`.

```python
SV   = mpmt.ShrRep3ShareVec(ell=4)
aux  = mpmt.RvectorPack(ell=4)(bf_size)

if party == 0:
    vec = mpmt.Rvector(ell=4)(bf_size)
    vec.fill(val=1)
    sv  = SV(bf_size)
    inst.share_vector(vec, sv, aux_buf=aux)
else:
    sv  = SV(bf_size)
    inst.recv_vector_share(sv, aux_buf=aux)
```

## Reshare

**share** converts a *plaintext* value held by P0 into RSS3.
**reshare** redistributes *additive shares already held by each party* into RSS3 —
each party already possesses its own additive component; no party holds the plaintext.

All three roles are symmetric — no Leader/Helper distinction; all three parties call the same method.

### reshare_scalar

`inst.reshare_scalar(val)` — Convert an existing additive scalar share into RSS3.
Returns `ShrRep3ShareScalar`.

### reshare_vector

`inst.reshare_vector(vec, sv, aux_buf)` — Convert an existing additive vector share into RSS3.

> **Aliasing constraint**: `vec` may alias `sv.nxt_share`, but **must not** alias `sv.this_share` —
> `this_share` is the send buffer and must not be overwritten before ring communication completes.

```python
# All three parties, identical call:
additive = my_additive_byte(...)
ss = inst.reshare_scalar(val=additive)

sv = SV(bf_size)
inst.reshare_vector(vec=additive_vec, sv=sv, aux_buf=aux)
```

## Reveal

Reveal a shared value. All three parties learn the result.

| Method | Signature | Description |
|------|------|------|
| `reveal_scalar` | `(ss)` | Returns reconstructed `int` |
| `reveal_vector` | `(sv, out, aux_buf)` | `out` must be a pre-allocated `Rvector` of matching size |

P1 sends the missing share to P0; P0 reconstructs and broadcasts. All three parties receive the plaintext.

```python
out = mpmt.Rvector(ell=4)(bf_size)
inst.reveal_vector(sv, out, aux_buf=aux)
# out[i] is the plaintext value
```

## Arithmetic

All arithmetic operations execute one round of communication over the network (except `add` / `sub`, which are local).

### Scalar

| Method | Signature | Network |
|------|------|------|
| `add` | `(a, b)` | local |
| `sub` | `(a, b)` | local |
| `mul` | `(a, b)` | 1 round |

Returns `ShrRep3ShareScalar`.

### Vector

| Method | Signature | Network | Aliasing |
|------|------|------|------|
| `add_vec` | `(sv1, sv2, out)` | local | aliasing-safe |
| `sub_vec` | `(sv1, sv2, out)` | local | aliasing-safe |
| `hadamard` | `(sv1, sv2, out)` | 1 round | **out must not alias inputs** |
| `dot` | `(sv1, sv2)` | 1 round | returns `ShrRep3ShareScalar` |

```python
# Union formula (paper Eq. 3.3): B(X∪Y) = X+Y − X·Y
inst.add_vec(sv1, sv2, out)
inst.hadamard(sv1, sv2, tmp)
inst.sub_vec(out, tmp, out)
```

## Correlated Randomness (crng)

| Method | Description |
|------|------|
| `inst.crng()` | Single random byte; the three parties' values sum to 0 |
| `inst.crng_vec(vec)` | Vector variant |

$$r_{P_0} + r_{P_1} + r_{P_2} \equiv 0 \pmod{2^{\,ELL}}$$

Used in the GenBF protocol: converts DPF outputs (2-of-2 shares) into 3-of-3 additive shares —
each party adds its crng vector to its own DPF share component.

```python
r = inst.crng()               # single byte
rv = mpmt.Rvector(ell=4)(bf_size)
inst.crng_vec(rv)             # vector
```

## Ring Conversion

Available only on `ELL=1` instances. Converts binary shares into arithmetic ring shares.

| Method | Signature | Description |
|------|------|------|
| `ring_conv` | `(ss, ell_to)` | Scalar, `ell_to ∈ [2, 6]` |
| `ring_conv_vec` | `(sv, sv_out, ell_to)` | Vector; `sv_out` is a `ShrRep3ShareVec` of the target ELL |

Used before dot products: the root BF (binary, ELL=1) must first be converted to an arithmetic ring.

```python
# ELL=1 instance only:
sv_q = mpmt.ShrRep3ShareVec(ell=4)(bf_size)
inst_ell1.ring_conv_vec(root_sv, sv_q, ell_to=4)
```

## Raw Data Transfer

| Method | Signature | Description |
|------|------|------|
| `send_data` | `(to_pid, val)` | Send scalar `int` |
| `send_data` | `(to_pid, data)` | Send raw bytes (no length prefix) |
| `recv_data` | `(from_pid)` | Receive scalar `int` |
| `recv_data` | `(from_pid, buf)` | Receive raw bytes into pre-allocated `bytearray` |

`to_pid` / `from_pid` ∈ {0, 1, 2}, must not refer to self.

```python
inst.send_data(to=1, val=42)
x = inst.recv_data(from=2)
```

## Byte Counters / Flush

| Method | Description |
|------|------|
| `inst.bytes_sent()` | Total bytes sent across both channels |
| `inst.bytes_recv()` | Total bytes received across both channels |
| `inst.clear_send_cnt()` | Reset send counter |
| `inst.clear_recv_cnt()` | Reset receive counter |
| `inst.flush()` | Flush both NetIO send buffers |
