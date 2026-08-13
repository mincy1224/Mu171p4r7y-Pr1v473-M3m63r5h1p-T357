# High-level membership protocols — SetHolder, Querier, AgentServer

> **Note.** The protocol interfaces documented here are oriented primarily
> toward engineering implementation needs, so they differ slightly from the
> paper's description. The paper's standard protocol interfaces can
> nevertheless be constructed from the interfaces presented below. The
> [C3 application](../README.md) provides a usage example of these
> interfaces.

The `mpmt` package exposes three objects that together implement private
membership testing: a **SetHolder** contributes a private set, three
**AgentServers** (STEWARD / PEER0 / PEER1) hold secret-shared Bloom-filter
shares — one TreeCache each — and a **Querier** asks whether an element belongs
to the union of the holders' sets. The lower-level building blocks
(`ShrRep3`, `ShrAdd2`, `Dpf`, `Channel`) are documented in
[`building_blocks/`](./building_blocks/) and [`net/`](./net/).

## SetHolder — contribute a set (client side)

```python
holder = mpmt.SetHolder(
    set_size=1_000_000,        # expected set size (for BF sizing)
    fpr_mantissa=1.0,          # false-positive rate = mantissa × 10^exponent
    fpr_exponent=-3,
)
```

| Method | Description |
|--------|-------------|
| `share_bf(*, set, hash_seed_list, ch_steward, ch_peer0, ch_peer1)` | Share `set` (a `list[bytes]`) among the three AgentServers |

The holder opens three `Channel`s to the agents' set-holder ports, then shares:

```python
holder.share_bf(
    set=[b"alice", b"bob", b"carol"],
    hash_seed_list=[bytes.fromhex(h) for h in hash_seeds_hex],  # 16-byte seeds, one per hash
    ch_steward=ch_steward, ch_peer0=ch_peer0, ch_peer1=ch_peer1,
)
```

## Querier — membership query (client side)

```python
q = mpmt.Querier(set_size=1_000_000, fpr_mantissa=1.0, fpr_exponent=-3)
```

| Method | Description |
|--------|-------------|
| `query(*, element, ch_steward, ch_peer0, ch_peer1) -> int` | Returns `1` if `element` is in the aggregated set, `0` otherwise |

```python
result = q.query(
    element=b"alice",
    ch_steward=ch_steward, ch_peer0=ch_peer0, ch_peer1=ch_peer1,
)   # -> 0 or 1
```

## AgentServer — one of the three protocol parties

```python
agent = mpmt.AgentServer(
    server_role=mpmt.ServerRole.STEWARD,   # STEWARD / PEER0 / PEER1
    set_size=1_000_000,
    fpr_mantissa=1.0,
    fpr_exponent=-3,
    storage_dir="storage/steward",         # TreeCache persists here
    ch_prev=ch_prev,                       # ring channel (prev party)
    ch_nxt=ch_nxt,                         # ring channel (next party)
    hash_seed_list=hash_seeds_bytes,       # required for STEWARD only
    cores=1,
)
```

| Method | Description |
|--------|-------------|
| `response_share_bf(*, prot_type, ch_set_holder, token=None)` | Handle a SetHolder's JOIN / UPDATE / QUIT; returns the holder's leaf token on JOIN |
| `response_query(*, ch_querier)` | Handle a Querier's membership query |
| `sync_cache()` | Explicitly merge the TreeCache (publishes the aggregated root) |

The three agents run in a ring (STEWARD → PEER0 → PEER1 → STEWARD). All three
must call `response_share_bf` / `response_query` in lockstep; each agent accepts
the client's channel and serves its side of the protocol.

```python
if server_role == mpmt.ServerRole.STEWARD:
    token = agent.response_share_bf(prot_type=mpmt.ProtType.JOIN,
                                    ch_set_holder=ch_holder)
# UPDATE / QUIT reuse the holder's token:
#   agent.response_share_bf(prot_type=mpmt.ProtType.UPDATE,
#                           ch_set_holder=ch_holder, token=token)
#   agent.response_share_bf(prot_type=mpmt.ProtType.QUIT, token=token)
```

`JOIN` / `UPDATE` / `QUIT` only mark the tree dirty — the merged root is
published by an explicit `sync_cache()` (all three agents in lockstep).

## _TreeCache — internal storage-management helper

`_TreeCache` is an **internal** class used by `AgentServer` to manage the
secret-shared Bloom-filter shares. Because those shares are aggregated in a
**tree**, a dedicated class is needed to own that aggregation state and its
persistence.

* `insert(node)` / `update(token, new_node)` / `remove(token)` update the
  per-holder leaf shares and mark the tree dirty;
* `execute_merge()` (reached through `AgentServer.sync_cache()`) folds the
  dirty leaves up the tree and refreshes the aggregated root;
* the tree state is persisted under the agent's `storage_dir`, so it survives
  a restart.

It is not meant to be used directly by application code — access it through
`AgentServer`.

