# DPF — Distributed Point Function (BGI16)

`ELL_IN ∈ [13, 31]`, `ELL_OUT ∈ [2, 6]`. Star topology: one Dealer,
two Evaluators. Based on Boyle, Gilboa, Ishai (CCS 2016).

## Factory

`DpfDealer(ell_in, ell_out)` returns a type; `type(ch_eval0, ch_eval1)` constructs an instance.

`DpfEvaluator(ell_in, ell_out, party)` returns a type; `type(ch_dealer)` constructs an instance.
`party` is `0` or `1`.

```
        Dealer
       /      \
  Eval0      Eval1
```

The Dealer holds two channels (to Eval0 and Eval1 respectively); each Evaluator holds one channel (to the Dealer).

## Attributes

| Attribute | Type | Description |
|------|------|------|
| `inst.ell_in` | `int` | Input ring bit-width (read-only) |
| `inst.ell_out` | `int` | Output ring bit-width (read-only) |
| `eval.inst.party` | `int` | Evaluator ID (0 or 1, read-only) |

## Dealer: gen

`dealer.gen(alpha, beta)` — Generate a pair of DPF keys encoding the point function
`f(x) = beta if x == alpha, else 0`.

| Parameter | Type | Description |
|------|------|------|
| `alpha` | `int` | Secret point, `alpha < 2^ELL_IN` |
| `beta` | `int` | Output value, `beta < 2^ELL_OUT` |

Returns `(key0_json, key1_json)` — two JSON strings.

`gen` is a **purely local computation**, no network communication.

```python
key_e0, key_e1 = dealer.gen(alpha=42, beta=1)
```

## Dealer: send_key

`dealer.send_key(key_json, party)` — Send a key to an Evaluator over the injected channel.
`party` is `0` or `1`.

```python
dealer.send_key(key_e0, party=0)
dealer.send_key(key_e1, party=1)
```

## Dealer: reveal

`dealer.reveal(out)` — Receive evaluation results from both Evaluators and XOR-reconstruct into plaintext.

| Parameter | Type | Description |
|------|------|------|
| `out` | `Rvector(ELL_OUT)` | Pre-allocated output vector |

```python
out = mpmt.Rvector(ell=dealer.ell_out)(bf_size)
dealer.reveal(out)  # out[i] = eval0[i] ^ eval1[i]
```

## Evaluator: recv_key

`evaluator.recv_key()` — Receive a DPF key from the Dealer; returns a JSON string.

```python
key_json = evaluator.recv_key()
```

## Evaluator: eval

`evaluator.eval(key_json, buf, cores=1)` — Full-domain evaluation of the DPF.

| Parameter | Type | Description |
|------|------|------|
| `key_json` | `str` | DPF key (JSON string) |
| `buf` | `Rvector(ELL_OUT)` | Pre-allocated output buffer, size = `2^ELL_IN` |
| `cores` | `int` | Thread count, `{1, 2, 4, 8, 16, 32}`, default 1 |

`eval` is a **purely local computation**, no network communication.
The two Evaluators' results form a 2-of-2 additive sharing of the point function: `eval0[i] + eval1[i] ≡ f(i)`.

```python
buf = mpmt.Rvector(ell=evaluator.ell_out)(bf_size)
evaluator.eval(key_json=key_json, buf=buf, cores=4)
```

## Evaluator: eval_range

`evaluator.eval_range(key_json, buf, bg, ed, cores=1)` — Range evaluation.

| Parameter | Type | Description |
|------|------|------|
| `key_json` | `str` | DPF key |
| `buf` | `Rvector(ELL_OUT)` | Pre-allocated output buffer, size = `(ed - bg + 1) mod 2^ELL_IN` |
| `bg` | `int` | Start index (inclusive) |
| `ed` | `int` | End index (**inclusive**) |
| `cores` | `int` | Thread count |

Closed interval `[bg, ed]`, both ends inclusive. Supports **wraparound intervals**: when `ed < bg`,
the interval wraps as `[bg, 2^ELL_IN) ∪ [0, ed]`, with output laid out linearly (`bg` at offset 0).
Subtrees outside the interval are pruned, dramatically reducing computation.

`bg` and `ed` must be `< 2^ELL_IN`. Purely local computation.

```python
# Ordinary interval: bg=0, ed=999   → [0, 999]
evaluator.eval_range(key_json=key_json, buf=buf, bg=0, ed=999)

# Wraparound interval: bg=1000, ed=99 → [1000, 2^N) ∪ [0, 99]
evaluator.eval_range(key_json=key_json, buf=buf, bg=1000, ed=99)
```

## Evaluator: reveal

`evaluator.reveal(buf)` — Send evaluation results to the Dealer for reconstruction.

```python
evaluator.reveal(buf)
```

## Usage in the GenBF Protocol

The query protocol uses DPF to generate the querier's Bloom filter without leaking hash indices:

1. **Steward** (as Dealer) generates DPF keys for the **blinded** index `idx_L + idx_A`,
   sending them to Peer0 and Peer1 respectively
2. **Peer0** and **Peer1** evaluate their keys over `[0, bf_size)`,
   apply a cyclic shift, and obtain a 2-of-2 additive sharing of the query BF
3. **crng + reshare**: convert the 2-of-2 shares into Rep3 (2-of-3) via correlated randomness and reshare,
   enabling the three-party servers to compute dot products

## Channel Topology

DPF uses a star topology (not a ring). The Dealer listens on two ports; each Evaluator connects to one port.
Channels are built via `ChannelListener` / `Channel.connect` / `Channel(sock)`
and injected at construction time.

```python
from mpmt.channels import Channel, ChannelListener

# Dealer
listener_e0 = ChannelListener("127.0.0.1", 18000)
listener_e1 = ChannelListener("127.0.0.1", 18001)
ch_eval0 = listener_e0.accept()
ch_eval1 = listener_e1.accept()
dealer = mpmt.DpfDealer(ell_in=20, ell_out=4)(ch_eval0, ch_eval1)

# Evaluator 0
ch_d = Channel.connect("127.0.0.1", 18000)
eval0 = mpmt.DpfEvaluator(ell_in=20, ell_out=4, party=0)(ch_d)

# Evaluator 1
ch_d = Channel.connect("127.0.0.1", 18001)
eval1 = mpmt.DpfEvaluator(ell_in=20, ell_out=4, party=1)(ch_d)
```
