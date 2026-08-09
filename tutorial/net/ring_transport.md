# RingTransport — Ring Element Transport

Send/receive ring elements over a single `Channel`. Requires two parties communicating over TCP.

## Factory

`RingTransport(ell)` returns a **type**; `type(channel)` constructs an instance.

| Parameter | Type | Description |
|------|------|------|
| `ell` | `int` | Ring bit-width, `[1, 31]` |
| `channel` | `Channel` | Channel to the peer |

Instances have a read-only attribute `ell`.

## send_scalar / recv_scalar

**ELL 1–31.** Transmit a single ring element, `ceil(ELL/8)` bytes little-endian.

`send_scalar(val)` — Send an `int`; validates `val ∈ Z_{2^ELL}` internally.

`recv_scalar()` → `int` — Receive a scalar.

```python
# Sender
rt = mpmt.RingTransport(ell=14)(ch)
rt.send_scalar(val=42)

# Receiver
rt = mpmt.RingTransport(ell=14)(ch)
val = rt.recv_scalar()  # 42
```

## send_vector / recv_vector

**ELL 1–8.** Pack an Rvector and transmit in bulk; uses `rvector_pack`/`rvector_unpack` internally.

| Parameter | Type | Description |
|------|------|------|
| `vec` | `Rvector(ELL)` | Vector to send; receiver must pre-allocate |
| `aux_buf` | `RvectorPack` | Scratch buffer, must match ELL and n |

Releases the GIL. Sender auto-`flush`es.

```python
# Sender
rt = mpmt.RingTransport(ell=4)(ch)
v = mpmt.Rvector(ell=4)(100)
v.rand_fill()
aux = mpmt.RvectorPack(ell=4)(n=100)
rt.send_vector(vec=v, aux_buf=aux)

# Receiver
rt = mpmt.RingTransport(ell=4)(ch)
out = mpmt.Rvector(ell=4)(100)
aux = mpmt.RvectorPack(ell=4)(n=100)
rt.recv_vector(vec=out, aux_buf=aux)
```

## Wire Format

Scalar transport: little-endian `ceil(ELL/8)` bytes, no framing, no length prefix.

Vector transport: raw `RvectorPack` bytes, length `ceil(ELL × n / 8)`.

The caller must know the data volume in advance.
