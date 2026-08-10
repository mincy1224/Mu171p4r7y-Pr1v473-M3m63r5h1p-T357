"""Persistent TCP channels wrapping ``emp::NetIO``.

``Channel(port)`` — server (listen).
``Channel(host, port)`` — client (connect, emp-internal retry).
``Channel(host, port, retry_timeout=N)`` — client (connect, Python-side retry
with timeout in seconds).
``Channel(sock=...)`` — wrap an already-connected Python socket.

``wrap_socket`` / ``connect_retry`` — low-level helpers (deprecated in
favour of the ``sock=`` / ``retry_timeout=`` constructor arguments).

@author  mincy
@ref     emp::NetIO from emp-toolkit (https://github.com/emp-toolkit/emp-tool)
"""

import socket as _socket
import os as _os
import time as _time

import mpmt._mpmt as _mpmt


#  Channel class
class Channel:
    def __init__(self, *args, port=None, host=None, sock=None,
                 retry_timeout: float | None = None):
        n_pos = len(args)
        if port is not None or host is not None or sock is not None:
            if n_pos > 0:
                raise TypeError(
                    "Channel() accepts positional OR keyword args, not both"
                )
        elif n_pos == 1:
            port = args[0]
        elif n_pos == 2:
            host, port = args
        elif n_pos == 0:
            raise TypeError(
                "Channel(port) for server, Channel(host,port) for client, "
                "or Channel(sock=...) for wrapping a socket"
            )
        else:
            raise TypeError(
                f"Channel() takes 1-2 positional args, got {n_pos}"
            )

        if sock is not None:
            # Wrap existing Python socket
            fd = _os.dup(sock.fileno())
            sock.close()
            self._netio_ptr = _mpmt.NetIO_from_socket(fd)
        elif host is not None:
            if port is None:
                raise TypeError("port is required with host")
            if retry_timeout is not None:
                # Python-side connect with retry + timeout
                self._netio_ptr = _mpmt.NetIO_from_socket(
                    _connect_retry(host, port, retry_timeout)
                )
            else:
                # emp-internal connect with retry
                self._netio_ptr = _mpmt.NetIO_connect(host, port)
        else:
            if port is None:
                raise TypeError("port is required for server mode")
            self._netio_ptr = _mpmt.NetIO_listen(port)

    def __del__(self):
        if hasattr(self, '_netio_ptr') and self._netio_ptr != 0:
            _mpmt._netio_delete(self._netio_ptr)
            self._netio_ptr = 0

    @classmethod
    def _from_ptr(cls, ptr: int):
        ch = object.__new__(cls)
        ch._netio_ptr = ptr
        return ch

    def acquire(self) -> int:
        _mpmt._netio_flush(self._netio_ptr)
        _mpmt._netio_clear_counters(self._netio_ptr)
        return _mpmt._netio_as_iochannel(self._netio_ptr)

    def flush(self):
        _mpmt._netio_flush(self._netio_ptr)

    def send(self, data: bytes | bytearray):
        _mpmt._netio_send(self._netio_ptr, data)

    def recv(self, buf: bytearray):
        _mpmt._netio_recv(self._netio_ptr, buf)


#  Low-level helpers
def wrap_socket(sock) -> int:
    fd = _os.dup(sock.fileno())
    sock.close()
    return _mpmt.NetIO_from_socket(fd)


def connect_retry(host: str, port: int,
                  timeout: float | None = None) -> int:
    deadline = _time.monotonic() + timeout if timeout is not None else None
    while True:
        try:
            s = _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)
            s.connect((host, port))
            fd = _os.dup(s.fileno())
            s.close()
            return fd
        except (ConnectionRefusedError, OSError):
            s.close()
            if deadline is not None and _time.monotonic() >= deadline:
                raise TimeoutError(
                    f"connect_retry: failed to connect to {host}:{port} "
                    f"within {timeout}s"
                ) from None

def _connect_retry(host: str, port: int, timeout: float) -> int:
    """Same as ``connect_retry`` but returns a socket fd (used by Channel)."""
    return connect_retry(host, port, timeout=timeout)
