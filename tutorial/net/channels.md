# Channel — Persistent TCP Channel

Wraps `emp::NetIO`. All TCP connection establishment is done by Python's
`socket` module; NetIO only receives already-connected sockets.

## Construction

| Constructor | Description |
|-------------|-------------|
| `Channel(sock)` | Wraps an already-connected Python socket (takes ownership) |
| `Channel.connect(host, port, timeout=None)` | Connect to remote; retries until success or timeout |
| `ChannelListener(host, port)` | Server-side bind/listen |
| `ChannelListener.accept()` → `Channel` | Accept one connection, return a `Channel` |

```python
from mpmt.channels import Channel, ChannelListener

# server — listen then accept
listener = ChannelListener("127.0.0.1", 14000)
ch_srv = listener.accept()

# client — connect with retry (5-second timeout per connect window)
ch_cli = Channel.connect("127.0.0.1", 14000, timeout=5.0)

# client — retry forever
ch_cli = Channel.connect("127.0.0.1", 14000)

# wrap an existing connected socket
import socket
s = socket.socket()
s.connect(("127.0.0.1", 14000))
ch = Channel(s)
```

`ChannelListener.__init__` (bind + listen) is non-blocking.
`ChannelListener.accept()` blocks until a connection arrives.
`Channel.connect()` blocks until connection succeeds or timeout elapses.

## Methods

| Method | Description |
|--------|-------------|
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

## ChannelListener

A thin wrapper around Python `socket.bind` + `socket.listen`.

```python
listener = ChannelListener("127.0.0.1", 14000)  # bind + listen (non-blocking)
ch = listener.accept()                           # accept → Channel (blocking)
listener.close()                                 # close without accepting
```

## Multi-Process Startup Pattern

Using the Rep3 ring topology as an example, the strategy for breaking cyclic dependencies:

- Every party: **bind/listen on its own PREV port** (non-blocking), then
  **connect to NEXT** (blocking with retry), then **accept PREV**.

```python
from mpmt.channels import Channel, ChannelListener

def build_rep3_channels(prev_port, next_host, next_port):
    # 1. Bind/listen (does NOT block)
    listener = ChannelListener("127.0.0.1", prev_port)

    # 2. Connect to NEXT (blocks until peer is up)
    ch_nxt = Channel.connect(next_host, next_port, timeout=5.0)

    # 3. Accept from PREV
    ch_prev = listener.accept()

    return ch_prev, ch_nxt

# Called by each of the three processes:
# STEWARD: build_rep3_channels(14000, "127.0.0.1", 14001)
# PEER0:   build_rep3_channels(14001, "127.0.0.1", 14002)
# PEER1:   build_rep3_channels(14002, "127.0.0.1", 14000)
```

Similarly for the DPF star topology:

```python
# Dealer listens on two ports; each Evaluator connects to one
listener_e0 = ChannelListener("127.0.0.1", 18000)
listener_e1 = ChannelListener("127.0.0.1", 18001)
ch_eval0 = listener_e0.accept()
ch_eval1 = listener_e1.accept()

# Evaluator 0
ch_d = Channel.connect("127.0.0.1", 18000)

# Evaluator 1
ch_d = Channel.connect("127.0.0.1", 18001)
```

## Thread Safety

Channel methods are **not** thread-safe. Each Channel should be used within a single thread,
or externally locked by the caller. When multiple protocol instances share the same Channel,
access must be serialized.
