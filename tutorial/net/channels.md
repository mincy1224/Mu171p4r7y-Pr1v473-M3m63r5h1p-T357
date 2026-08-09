# Channel — Persistent TCP Channel

Wraps `emp::NetIO`. Requires two parties communicating over TCP.

## Construction

Two modes:

| Constructor | Role | Behavior |
|------|------|------|
| `Channel(port)` | Server | Listens on port, blocks until client connects |
| `Channel(host, port)` | Client | Connects to remote, retries indefinitely until success |

```python
from mpmt.channels import Channel

ch_srv = Channel(port=14000)                      # server
ch_cli = Channel(host="127.0.0.1", port=14000)    # client
```

Both ends block until the connection is established. The port must be bound on the server before the client.

## Methods

| Method | Description |
|------|------|
| `ch.acquire()` | Flush buffers, reset counters, return an IOChannel handle for use by C++ protocol instances |
| `ch.flush()` | Flush the underlying NetIO send buffer |
| `ch.send(data)` | Send `bytes` or `bytearray`, auto-flush |
| `ch.recv(buf)` | Receive **exactly** `len(buf)` bytes into a pre-allocated `bytearray`; blocks if insufficient |

## Lifetime

A Channel is shared by C++ protocol instances via `acquire()`.
The underlying NetIO is reference-counted via `shared_ptr` in a global registry —
the Channel survives even after the protocol instance is destroyed. Safe to switch protocol instances:

```python
# Safe: old instance destroyed, Channel survives, new instance reuses it
add2_inst = mpmt.ShrAdd2(ell=14, party=0)(ch)
# ... use ...
add2_inst = mpmt.ShrAdd2(ell=4, party=0)(ch)  # overwrite variable
```

## Low-level Socket Helpers

### wrap_socket

`wrap_socket(sock)` — Transfer a Python socket's fd to C++ NetIO.

Procedure: `dup(sock.fileno())` → `sock.close()` → `NetIO_from_socket(fd)`.

Returns a NetIO handle (`int`). NetIO takes exclusive ownership of the fd;
the Python side no longer holds the socket.

### connect_retry

`connect_retry(host, port)` — Connect to remote, retrying indefinitely until success.

```python
while True:
    try:
        s.connect((host, port))
        return s
    except (ConnectionRefusedError, OSError):
        pass
```

Returns a connected Python `socket`, intended for use with `wrap_socket`.

### Rationale

These two helpers avoid the glibc `FILE*` deadlock in the daemon thread `NetIO_listen` —
`fdopen` always executes in the calling thread. During multi-process startup,
`connect_retry` polls until the peer is ready, eliminating races.

## Multi-Process Startup Pattern

Using the Rep3 ring topology as an example, the strategy for breaking cyclic dependencies:

- **P0, P2**: first `connect_retry` (connect to prev as client), then `Channel(port)` (wait for next as server)
- **P1**: first `Channel(host, port)` (connect to prev as client), then `Channel(port)` (wait for next as server)

```python
from mpmt.channels import wrap_socket, connect_retry, Channel

def build_rep3_channels(prev_port, next_host, next_port, party_id):
    if party_id in (0, 2):
        # P0, P2: connect to prev first (client), then wait for next (server)
        ch_prev_sock = connect_retry("127.0.0.1", prev_port)
        ch_prev = Channel._from_ptr(wrap_socket(ch_prev_sock))
        ch_nxt = Channel(host=next_host, port=next_port)
    else:  # party_id == 1
        # P1: connect to prev first (client), then wait for next (server)
        ch_prev = Channel(host="127.0.0.1", port=prev_port)
        ch_nxt = Channel(port=next_port)
    return ch_prev, ch_nxt

# Called by each of the three processes:
# P0: build_rep3_channels(14000, "127.0.0.1", 14001, 0)
# P1: build_rep3_channels(14000, "127.0.0.1", 14001, 1)
# P2: build_rep3_channels(14000, "127.0.0.1", 14001, 2)
```

Similarly for the DPF star topology:

```python
# Dealer listens on two ports; each Evaluator connects to one
ch_eval0 = Channel._from_ptr(wrap_socket(connect_retry("127.0.0.1", 18000)))
ch_eval1 = Channel._from_ptr(wrap_socket(connect_retry("127.0.0.1", 18001)))
```

## Thread Safety

Channel methods are **not** thread-safe. Each Channel should be used within a single thread,
or externally locked by the caller. When multiple protocol instances share the same Channel,
access must be serialized.
