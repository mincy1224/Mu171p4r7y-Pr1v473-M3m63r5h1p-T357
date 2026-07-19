"""Persistent TCP channels wrapping ``emp::NetIO``.

``Channel(port)`` — server (listen).
``Channel(host, port)`` — client (connect, retries until ready).

``_build_rep3_channels`` / ``_build_dpf_channels_*`` establish
multi-party topologies without daemon threads via Python socket
coordination — ``fdopen`` stays in the calling thread.

@author  mincy
@ref     emp::NetIO from emp-toolkit (https://github.com/emp-toolkit/emp-tool)
"""

import socket as _socket
import os as _os

import mpmt._mpmt as _mpmt


# ——————————————————————————————————————————————
#  Channel class
# ——————————————————————————————————————————————

class Channel:
    """Persistent IOChannel wrapping an ``emp::NetIO`` instance.

    ``acquire()`` is called by C++ protocol constructors to prepare the
    channel for a new protocol instance.  It is idempotent — channels
    are flushed and counters reset between uses.

    Parameters
    ----------
    port : int
        Server mode — listen on *port*, block until a client connects.
    host : str
        Client mode — remote host to connect to.
    port : int
        Client mode — remote port to connect to (passed together with *host*).

    Examples
    --------
    Server::

        ch = Channel(14000)           # listen on port 14000

    Client::

        ch = Channel("127.0.0.1", 14000)   # connect to 127.0.0.1:14000
    """

    def __init__(self, *args):
        n = len(args)
        if n == 1:
            port = args[0]
            self._netio_ptr = _mpmt.NetIO_listen(port)
        elif n == 2:
            host, port = args
            self._netio_ptr = _mpmt.NetIO_connect(host, port)
        else:
            raise TypeError(
                "Channel(port) for server or Channel(host, port) for client"
            )

    def __del__(self):
        if hasattr(self, '_netio_ptr') and self._netio_ptr != 0:
            _mpmt._netio_delete(self._netio_ptr)
            self._netio_ptr = 0

    @classmethod
    def _from_ptr(cls, ptr: int):
        """Internal: wrap an already-created NetIO pointer."""
        ch = object.__new__(cls)
        ch._netio_ptr = ptr
        return ch

    def acquire(self) -> int:
        """Prepare the channel for a protocol instance."""
        _mpmt._netio_flush(self._netio_ptr)
        _mpmt._netio_clear_counters(self._netio_ptr)
        return _mpmt._netio_as_iochannel(self._netio_ptr)

    def flush(self):
        """Flush the underlying NetIO send buffer."""
        _mpmt._netio_flush(self._netio_ptr)

    def send(self, data: bytes | bytearray):
        """Send raw bytes over the channel (with flush)."""
        _mpmt._netio_send(self._netio_ptr, data)

    def recv(self, buf: bytearray):
        """Receive bytes into a **pre-allocated** ``bytearray``."""
        _mpmt._netio_recv(self._netio_ptr, buf)


# ——————————————————————————————————————————————
#  Internal builders — multi-party channel coordination
# ——————————————————————————————————————————————
#
#  These are NOT part of the public API.  Protocol classes
#  (MpmtServerLeader, MpmtServerHelper, MpmtSetHolder) call them
#  during __init__ to establish their party-specific connections.
#
#  All builders use Python sockets for listen/accept/connect and wrap
#  the resulting fds via ``NetIO_from_socket``.  This keeps ``fdopen``
#  in the calling thread, avoiding the glibc FILE*-lock deadlock that
#  occurs with daemon-thread ``NetIO_listen``.


def _wrap_socket(sock) -> int:
    """Duplicate *sock*'s fd, close *sock*, return a NetIO pointer."""
    fd = _os.dup(sock.fileno())
    sock.close()
    return _mpmt.NetIO_from_socket(fd)


def _connect_retry(host: str, port: int):
    """Connect to *host:port*, retrying until the server is ready."""
    s = _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)
    while True:
        try:
            s.connect((host, port))
            return s
        except (ConnectionRefusedError, OSError):
            pass


# ——————————————————————————————————————————————
#  Rep3 — ring topology  (P0 → P1 → P2 → P0)
# ——————————————————————————————————————————————

def _build_rep3_channels(*, prev_port, next_host, next_port, party_id):
    """Build Rep3 ring channel pair — thread-free.

    *party_id* (0, 1, or 2) picks the listen/connect order:
    P0 & P2 accept first then connect; P1 connects first then accepts.
    This breaks the 3-party ring dependency without daemon threads.

    Returns ``{"prev": Channel, "next": Channel}``.
    """
    listen_sock = _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)
    listen_sock.setsockopt(_socket.SOL_SOCKET, _socket.SO_REUSEADDR, 1)
    listen_sock.bind(("0.0.0.0", prev_port))
    listen_sock.listen(1)

    if party_id == 1:
        # P1: connect to next first, then accept from prev
        next_sock = _connect_retry(next_host, next_port)
        prev_sock, _ = listen_sock.accept()
        listen_sock.close()
    else:
        # P0, P2: accept from prev first, then connect to next
        prev_sock, _ = listen_sock.accept()
        listen_sock.close()
        next_sock = _connect_retry(next_host, next_port)

    return {
        "prev": Channel._from_ptr(_wrap_socket(prev_sock)),
        "next": Channel._from_ptr(_wrap_socket(next_sock)),
    }


# ——————————————————————————————————————————————
#  DPF — star topology  (Dealer ↔ Eval0, Dealer ↔ Eval1)
# ——————————————————————————————————————————————

def _build_dpf_channels_dealer(*, eval0_port, eval1_port):
    """Dealer: listen on two ports, accept both connections.

    Returns ``{"eval0": Channel, "eval1": Channel}``.
    """
    # Bind both listen sockets first
    s0 = _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)
    s0.setsockopt(_socket.SOL_SOCKET, _socket.SO_REUSEADDR, 1)
    s0.bind(("0.0.0.0", eval0_port))
    s0.listen(1)

    s1 = _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)
    s1.setsockopt(_socket.SOL_SOCKET, _socket.SO_REUSEADDR, 1)
    s1.bind(("0.0.0.0", eval1_port))
    s1.listen(1)

    # Accept in the order evaluators are expected to connect.
    # Both evaluators connect to their respective ports; order
    # doesn't matter for the star topology.
    e0_sock, _ = s0.accept()
    s0.close()
    e1_sock, _ = s1.accept()
    s1.close()

    return {
        "eval0": Channel._from_ptr(_wrap_socket(e0_sock)),
        "eval1": Channel._from_ptr(_wrap_socket(e1_sock)),
    }


def _build_dpf_channels_evaluator(*, dealer_host, dealer_port):
    """Evaluator: connect to the dealer.

    Returns ``{"dealer": Channel}``.
    """
    sock = _connect_retry(dealer_host, dealer_port)
    return {"dealer": Channel._from_ptr(_wrap_socket(sock))}
