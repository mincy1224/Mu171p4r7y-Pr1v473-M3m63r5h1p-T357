"""Persistent TCP channels wrapping ``emp::NetIO``.

``Channel(sock)`` — wrap an already-connected Python socket.
``Channel.connect(host, port, timeout=None)`` — connect with retry.
``ChannelListener(host, port)`` — server-side bind/listen.
  ``.accept()`` → ``Channel``

All TCP connection establishment is handled by Python's ``socket`` module.
NetIO only receives already-connected sockets via ``NetIO_from_socket``.

@author  mincy
@ref     emp::NetIO from emp-toolkit (https://github.com/emp-toolkit/emp-tool)
"""

import socket as _socket
import os as _os
import time as _time

import mpmt._mpmt as _mpmt


#  Channel class
class Channel:
    """A connected byte-stream channel.

    Always constructed from an already-connected Python socket.
    """

    def __init__(self, sock: _socket.socket):
        fd = _os.dup(sock.fileno())
        sock.close()
        self._netio_ptr = _mpmt.NetIO_from_socket(fd)

    def __del__(self):
        if hasattr(self, '_netio_ptr') and self._netio_ptr != 0:
            _mpmt._netio_delete(self._netio_ptr)
            self._netio_ptr = 0

    @classmethod
    def _from_ptr(cls, ptr: int):
        ch = object.__new__(cls)
        ch._netio_ptr = ptr
        return ch

    @classmethod
    def connect(cls, host: str, port: int, timeout: float | None = None):
        """Connect to *host*:*port*.

        Retries until connection succeeds, or until *timeout* seconds
        elapse.  If *timeout* is ``None``, retries forever.
        """
        deadline = _time.monotonic() + timeout if timeout is not None else None
        while True:
            try:
                s = _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)
                s.settimeout(timeout)
                s.connect((host, port))
                s.settimeout(None)          # NetIO requires blocking
                return cls(s)
            except (ConnectionRefusedError, OSError):
                s.close()
                if deadline is not None and _time.monotonic() >= deadline:
                    raise TimeoutError(
                        f"Channel.connect: failed to connect to "
                        f"{host}:{port} within {timeout}s"
                    ) from None
                _time.sleep(0.1)

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


class ChannelListener:
    """Bind + listen.  ``accept()`` returns a connected ``Channel``."""

    def __init__(self, host: str, port: int):
        self._sock = _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)
        self._sock.setsockopt(_socket.SOL_SOCKET, _socket.SO_REUSEADDR, 1)
        self._sock.bind((host, port))
        self._sock.listen(1)

    def accept(self) -> Channel:
        """Accept one connection and return a ``Channel``."""
        conn, _ = self._sock.accept()
        self._sock.close()
        return Channel(conn)

    def close(self):
        """Close the listener without accepting."""
        try:
            self._sock.close()
        except OSError:
            pass
