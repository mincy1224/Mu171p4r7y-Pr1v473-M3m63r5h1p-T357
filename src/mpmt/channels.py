"""Persistent TCP channels wrapping ``emp::NetIO``.

``Channel(port)`` — server (listen).
``Channel(host, port)`` — client (connect, retries until ready).

``wrap_socket`` / ``connect_retry`` — low-level helpers for building
multi-party topologies from Python sockets.  ``wrap_socket`` transfers a
socket fd into a C++ NetIO; ``connect_retry`` retries until the peer is
ready, avoiding race conditions in multi-party startup.

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

    def __init__(self, *args, port=None, host=None):
        # Resolve keyword vs positional — keyword wins if given
        n_pos = len(args)
        if port is not None or host is not None:
            if n_pos > 0:
                raise TypeError(
                    "Channel() accepts positional or keyword args, not both"
                )
        elif n_pos == 1:
            port = args[0]
        elif n_pos == 2:
            host, port = args
        else:
            raise TypeError(
                "Channel(port) for server or Channel(host, port) for client"
            )

        if host is not None:
            if port is None:
                raise TypeError("port is required with host")
            self._netio_ptr = _mpmt.NetIO_connect(host, port)
        else:
            if port is None:
                raise TypeError("port is required")
            self._netio_ptr = _mpmt.NetIO_listen(port)

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
#  Low-level socket helpers
# ——————————————————————————————————————————————
#
#  ``wrap_socket`` transfers a Python socket fd into a C++ NetIO, closing
#  the Python socket after the dup — NetIO gets exclusive ownership of the
#  fd.  ``connect_retry`` polls until the peer is ready, avoiding race
#  conditions in multi-party startup.
#
#  These avoid the glibc FILE*-lock deadlock that occurs with daemon-thread
#  ``NetIO_listen`` by keeping fdopen in the calling thread.


def wrap_socket(sock) -> int:
    """Duplicate *sock*'s fd, close *sock*, return a NetIO pointer."""
    fd = _os.dup(sock.fileno())
    sock.close()
    return _mpmt.NetIO_from_socket(fd)


def connect_retry(host: str, port: int):
    """Connect to *host:port*, retrying until the server is ready."""
    s = _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)
    while True:
        try:
            s.connect((host, port))
            return s
        except (ConnectionRefusedError, OSError):
            pass
