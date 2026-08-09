# ShrAdd2 — Two-Party Additive Secret Sharing (EMP2)

`ELL ∈ [2, 31]`. Two-party point-to-point protocol, communicating over a single `Channel`.
Used in MPMT for the Querier ↔ Server hashing phase and equality test phase.

## Factory

`ShrAdd2(ell, party)` returns a **type**. Call `type(ch_peer)` to construct an instance.

| Parameter | Type | Description |
|------|------|------|
| `ell` | `int` | Ring bit-width, `[2, 31]` |
| `party` | `int` | `0` or `1` |
| `ch_peer` | `Channel` | Communication channel to the peer |

## Attributes

| Attribute | Type | Description |
|------|------|------|
| `inst.ell` | `int` | Ring bit-width (read-only) |
| `inst.party` | `int` | Party ID (read-only) |

## Ring Additive Sharing

Standard `(a + b) mod 2^ELL` sharing. The scalar type is `int`,
automatically reduced modulo ELL bits.

### Share / Receive

| Method | Signature | Description |
|------|------|------|
| `share_scalar` | `(value)` | Local → share value; sends peer's share |
| `recv_scalar_share` | `()` | Receive the peer's additive share |

Both return `int`.

```python
# Each party calls:
my_share = inst.share_scalar(value=42)
# or
peer_share = inst.recv_scalar_share()
# my_share + peer_share ≡ original_value (mod 2^ELL)
```

### Raw Send / Receive (Scalar)

| Method | Signature | Description |
|------|------|------|
| `send_data` | `(val)` | Send scalar (`int`); validates range automatically |
| `recv_data` | `()` | Receive scalar (`int`) |

For `ELL < 32`, `send_data` validates that `val` is within `Z_{2^ELL}`.

```python
inst.send_data(val=my_share)
his_val = inst.recv_data()
```

### Raw Send / Receive (Bytes)

| Method | Signature | Description |
|------|------|------|
| `send_data` | `(data)` | Send raw bytes (no length prefix), `bytes` or `bytearray` |
| `recv_data` | `(buf)` | Receive raw bytes into pre-allocated `bytearray` |

Unlike the scalar overload, the byte variant has no length prefix and no range validation.

```python
inst.send_data(data=b"\x01\x02\x03")
buf = bytearray(3)
inst.recv_data(buf=buf)
```

## XOR Sharing (Elements / Keys)

Used for secret-sharing variable-length elements (e.g., query strings). XOR sharing is not tied to a specific ring —
each party holds an equal-length byte string; XORing them recovers the original.

### share_element / recv_element_share

| Method | Signature | Description |
|------|------|------|
| `share_element` | `(plain)` | XOR-share a variable-length element; `plain` is `bytes` or `str` |
| `recv_element_share` | `()` | Receive a variable-length XOR element share |

The sender first transmits `[len][peer_share]` (length-prefixed), and returns the local share.
The receiver reads `[len][data]`. The two calls are symmetric — both parties obtain one share each after calling `share_element`.

```python
my_share = inst.share_element(plain=b"alice")
peer_share = inst.recv_element_share()
# my_share ^ peer_share = element
```

### share_key / recv_key_share

| Method | Signature | Description |
|------|------|------|
| `share_key` | `(key)` | XOR-share a 16-byte AES key; `key` must be exactly 16 bytes of `bytes` |
| `recv_key_share` | `(buf)` | Receive a 16-byte key share into a pre-allocated `bytearray` |

No length prefix — key length is fixed at 16 bytes.

```python
# Both parties:
my_key_share = inst.share_key(key=seed)   # returns 16 bytes

# Or receive the peer's key share:
buf = bytearray(16)
inst.recv_key_share(buf=buf)
peer_key_share = bytes(buf)
```

## Circuit Operations

The following operations execute via garbled circuits.

### hash

`inst.hash(my_pt, my_key)` — Two-party AES-DM hash circuit; computes the same function as `mpmt.hash_aes_dm`.

| Parameter | Type | Description |
|------|------|------|
| `my_pt` | `bytes` / `str` | Local XOR share of the preimage, ≤16 bytes |
| `my_key` | `bytes` | Local XOR share of the 128-bit key, exactly 16 bytes |

Returns `int`: the **ring additive share** of the ELL-bit hash output.

```python
# Both parties in lockstep:
h_share = inst.hash(my_pt=my_element_share, my_key=my_key_share)
```

### mod

`inst.mod(my_a, mv)` — In-circuit modulo: `(a_0 + a_1) % mv → share`.

| Parameter | Type | Description |
|------|------|------|
| `my_a` | `int` | Local share of the dividend |
| `mv` | `int` | Modulus |

Returns `int`: ring additive share of the modulo result.

```python
# Both parties in lockstep:
idx_share = inst.mod(my_a=h_share, mv=bf_size)
```

### equality_test

`inst.equality_test(my_a, my_b)` — In-circuit equality test.

| Parameter | Type | Description |
|------|------|------|
| `my_a` | `int` | Local share of the first value |
| `my_b` | `int` | Local share of the second value |

Returns `int`: a share of 1 (if equal) or a share of 0 (if not equal).
The return value's ring matches the `ell` used when constructing `ShrAdd2`.

```python
# Both parties in lockstep:
et_share = inst.equality_test(my_a=dot_share, my_b=hf_num)
# et_share is an additive share of "dot == hf_num ? 1 : 0"
```

### Revealing Circuit Results

The ADD2 protocol has no built-in `reveal` method. To reveal an additive share:
exchange both parties' shares, then add locally.

```python
# Party 0:
inst.send_data(val=my_share)
his_share = inst.recv_data()
result = mpmt.ring_add(ell=ell, a=my_share, b=his_share)

# Party 1:
his_share = inst.recv_data()
inst.send_data(val=my_share)
result = mpmt.ring_add(ell=ell, a=my_share, b=his_share)
```

## Byte Counters

| Method | Description |
|------|------|
| `inst.bytes_sent()` | Total bytes sent |
| `inst.bytes_recv()` | Total bytes received |
| `inst.clear_send_cnt()` | Reset send counter |
| `inst.clear_recv_cnt()` | Reset receive counter |

## Instance Reuse

`ShrAdd2` instances can overwrite variables — the underlying `NetIO` is reference-counted
via `shared_ptr` in a global registry; the Channel is not released prematurely.

```python
# Safe: old instance destroyed, Channel kept alive by registry, new instance reuses same channel
add2_inst = mpmt.ShrAdd2(ell=14, party=0)(ch_peer)
# ... use ...
add2_inst = mpmt.ShrAdd2(ell=4, party=0)(ch_peer)
```
