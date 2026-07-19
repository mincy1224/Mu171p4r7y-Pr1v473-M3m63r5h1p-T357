"""pytest harness — persistent-worker PartyPool with reusable channels.

Design: workers forked once per module, channels reused via acquire()/release().
Crash detection: Process.sentinel + selectors (<1ms latency).
Serialization: cloudpickle for nested functions and closures.

@author  mincy
"""
# NOTE: start method is set in pytest_configure(), not at module level,
# so that spawned child processes (which import this module to unpickle
# worker functions) don't re-execute the setter.

import selectors
import multiprocessing as mp
import os
import random
import sys
import time
import fcntl
import warnings as _warnings
import logging as _logging
from dataclasses import dataclass
from typing import Any

import pytest
import cloudpickle
import mpmt
from mpmt.channels import Channel, _build_rep3_channels, _build_dpf_channels_dealer, _build_dpf_channels_evaluator



# ——— Protocol-specific setup wrappers for PartyPool ———————————————
# PartyPool passes the same *addrs* list to every worker; these wrappers
# extract the per-party-relevant addresses for each protocol.

def _setup_rep3(pid, addrs):
    """Build Rep3 ring channels. pid: listen addrs[pid], connect addrs[(pid+1)%3]."""
    prev_addr = addrs[pid]
    next_addr = addrs[(pid + 1) % 3]
    return _build_rep3_channels(
        prev_port=prev_addr[1],
        next_host=next_addr[0],
        next_port=next_addr[1],
        party_id=pid,
    )


def _setup_add2(pid, addrs):
    """Build ADD2 point-to-point channel. P0 listens, P1 connects."""
    if pid == 0:
        return {"peer": Channel(addrs[0][1])}
    else:
        return {"peer": Channel(addrs[0][0], addrs[0][1])}


_setup_ring_transport = _setup_add2  # same point-to-point topology

def _setup_dpf(pid, addrs):
    """Build DPF star-topology channels.
    Dealer listens on two ports; each evaluator connects to one."""
    if pid == 0:
        return _build_dpf_channels_dealer(
            eval0_port=addrs[0][1], eval1_port=addrs[1][1],
        )
    else:
        return _build_dpf_channels_evaluator(
            dealer_host=addrs[pid - 1][0],
            dealer_port=addrs[pid - 1][1],
        )

_logger = _logging.getLogger(__name__)

# ——— Linux-only guard ——————————————————————————————————————————————
if not sys.platform.startswith("linux"):
    _warnings.warn(
        "\033[33mWARN: MPC test harness uses Linux-only features "
        "(selectors+epoll, Process.sentinel as pipe fd). "
        "Tests may fail on non-Linux platforms.\033[0m"
    )

# ====================================================================
#  Sentinel
# ====================================================================

_UNSET = object()

# ====================================================================
#  Exceptions
# ====================================================================

class PartyCrashError(RuntimeError):
    """A worker process died unexpectedly or raised an exception."""


class PartyTimeoutError(RuntimeError):
    """Fallback timeout expired — possible protocol deadlock."""


class WorkerCrashedError(RuntimeError):
    """Failed to deserialize a worker result."""


@dataclass
class WorkerException:
    """Fallback serialization for non-picklable exceptions."""
    type_name: str
    message: str


# ====================================================================
#  Default timeout
# ====================================================================

_DEFAULT_TIMEOUT = 60
_mpc_timeout = _DEFAULT_TIMEOUT


# ====================================================================
#  Port pools — protocol-specific ranges to avoid cross-protocol conflicts
# ====================================================================

_PORT_BASES = {
    "rss3": 14000,
    "add2": 16000,
    "dpf":  18000,
}
_PORT_STRIDES = {
    "rss3": 3,
    "add2": 2,
    "dpf":  3,
}
_port_counters: dict[str, int] = {"rss3": 0, "add2": 0, "dpf": 0}


def _alloc_addrs(protocol: str) -> list:
    """Allocate fresh socket addresses for *protocol*."""
    base = _PORT_BASES[protocol]
    stride = _PORT_STRIDES[protocol]
    idx = _port_counters[protocol]
    _port_counters[protocol] += 1
    start = base + idx * stride
    return [("127.0.0.1", start + i) for i in range(stride)]


# ====================================================================
#  Party function validation
# ====================================================================

def _check_party_fn(fn):
    """Reject party functions that capture ``self`` or ``cls``."""
    freevars = getattr(fn.__code__, "co_freevars", ())
    if not freevars:
        return
    banned = {"self", "cls"}
    captured = set(freevars) & banned
    if captured:
        raise TypeError(
            f"Party function captures {captured!r} from enclosing scope. "
            f"Party functions must not reference self/cls. "
            f"Pass needed values via keyword arguments to pool.run() instead."
        )


# ====================================================================
#  Serialization helpers
# ====================================================================

def _safe_send(conn, value):
    """Send a value, falling back to WorkerException on serialization failure."""
    try:
        raw = cloudpickle.dumps(value)
    except Exception:
        safe = WorkerException(
            type_name=type(value).__qualname__,
            message=str(value),
        )
        raw = cloudpickle.dumps(safe)
    conn.send_bytes(raw)


def _safe_unpickle(raw: bytes, pid: int):
    """Deserialize a worker result, returning WorkerCrashedError on failure."""
    try:
        return cloudpickle.loads(raw)
    except Exception as e:
        return WorkerCrashedError(
            f"Party {pid}: failed to deserialize result ({e})"
        )


# ====================================================================
#  Worker main
# ====================================================================

def _worker_main(pid: int, setup_fn, setup_args, parent_pipe, ready_event):
    """Entry point for persistent worker processes.

    1. Wait for *ready_event* (parent confirms all workers are alive).
    2. Build channels via *setup_fn*.
    3. Enter message loop: receive cloudpickled ``(fn, kwargs)``, execute,
       send result back.
    """
    import mpmt  # noqa: F401

    ready_event.wait()
    try:
        channels = setup_fn(pid, *setup_args)
    except BaseException as exc:
        _safe_send(parent_pipe, exc)
        parent_pipe.close()
        return

    # Signal parent that channels are ready
    parent_pipe.send_bytes(cloudpickle.dumps("ready"))

    # Message loop
    while True:
        try:
            msg = parent_pipe.recv_bytes()
        except EOFError:
            break

        fn, kwargs = cloudpickle.loads(msg)
        try:
            result = fn(pid, channels, **kwargs)
            _safe_send(parent_pipe, result)
        except BaseException as exc:
            _safe_send(parent_pipe, exc)

    parent_pipe.close()


# ====================================================================
#  PartyPool
# ====================================================================

class PartyPool:
    """Persistent pool of MPC worker processes with reusable channels.

    Usage::

        with PartyPool(3, make_rep3_channels, ()) as pool:
            results = pool.run(party_fn, ...)
    """

    def __init__(self, n: int, setup_fn, setup_args=(),
                 protocol: str = "rss3", timeout: int | None = None):
        self._n = n
        self._setup_fn = setup_fn
        self._setup_args = setup_args
        self._protocol = protocol
        self._timeout = timeout if timeout is not None else _mpc_timeout
        self._dirty = False
        self._procs: list[mp.Process] = []
        self._parent_conns: list = []
        self._build()

    # —— build / teardown —————————————————————

    def _build(self):
        """Allocate addresses, fork workers, establish channels."""
        addrs = _alloc_addrs(self._protocol)

        # For each worker: pipe (parent-end, child-end)
        self._parent_conns = []
        all_child_conns = []
        for _ in range(self._n):
            pc, cc = mp.Pipe(duplex=True)
            try:
                fcntl.fcntl(pc.fileno(), fcntl.F_SETPIPE_SZ, 1024 * 1024)
            except OSError:
                pass
            self._parent_conns.append(pc)
            all_child_conns.append(cc)

        ready_event = mp.Event()
        self._procs = []
        pid_of: dict[mp.Process, int] = {}

        for pid in range(self._n):
            p = mp.Process(
                target=_worker_main,
                args=(pid, self._setup_fn, (addrs,),
                      all_child_conns[pid], ready_event),
            )
            self._procs.append(p)
            pid_of[p] = pid

        for p in self._procs:
            p.start()

        # Close parent copies of child write ends
        for cc in all_child_conns:
            cc.close()

        # Wait for all workers to be alive, then release them
        for p in self._procs:
            if not p.is_alive():
                self._kill_all()
                raise PartyCrashError(
                    f"Worker process died during startup"
                )
        ready_event.set()

        # Wait for "ready" signal from every worker
        deadline = time.monotonic() + 60  # generous for NetIO connection retries
        ready_count = 0
        sel = selectors.DefaultSelector()
        for p in self._procs:
            sel.register(p.sentinel, selectors.EVENT_READ)
        fd_to_pid = {p.sentinel: pid_of[p] for p in self._procs}

        try:
            while ready_count < self._n:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    self._kill_all()
                    raise PartyTimeoutError("Workers did not become ready in time")

                # Check pipes FIRST (before sentinel) — workers may have
                # sent an error message before exiting.
                for pid, conn in enumerate(self._parent_conns):
                    if conn.poll(0):
                        raw = conn.recv_bytes()
                        msg = cloudpickle.loads(raw)
                        if isinstance(msg, BaseException):
                            self._kill_all()
                            raise PartyCrashError(
                                f"Worker {pid} failed during channel setup"
                            ) from msg
                        if msg == "ready":
                            ready_count += 1

                events = sel.select(timeout=min(remaining, 1.0))
                for key, _ in events:
                    pid = fd_to_pid[key.fileobj]
                    # Check pipe one more time before declaring crash
                    if self._parent_conns[pid].poll(0):
                        raw = self._parent_conns[pid].recv_bytes()
                        msg = cloudpickle.loads(raw)
                        if isinstance(msg, BaseException):
                            self._kill_all()
                            raise PartyCrashError(
                                f"Worker {pid} failed during channel setup"
                            ) from msg
                    raise PartyCrashError(
                        f"Worker {pid} died during channel setup "
                        f"(exit code: {_safe_exitcode(self._procs[pid])})"
                    )
        finally:
            sel.close()

        self._dirty = False

    def _kill_all(self, timeout=3):
        """Kill all workers with escalating force (SIGTERM → SIGKILL)."""
        for p in self._procs:
            if p is not None and p.is_alive():
                p.terminate()

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if not any(p is not None and p.is_alive() for p in self._procs):
                break
            time.sleep(0.1)

        for p in self._procs:
            if p is not None and p.is_alive():
                p.kill()
        for p in self._procs:
            if p is not None:
                p.join(timeout=1)

    # —— run ————————————————————————————

    def run(self, fn, **kwargs):
        """Execute *fn(pid, channels, \\*\\*kwargs)* on every worker.

        Returns a list of *n* results (one per party).  If any worker crashes
        or raises an exception the pool is marked dirty and will be rebuilt
        before the next ``run()``.
        """
        if self._dirty:
            self._kill_all()
            self._build()

        _check_party_fn(fn)

        task = cloudpickle.dumps((fn, kwargs))
        for conn in self._parent_conns:
            conn.send_bytes(task)

        # Monitor workers via sentinel + pipe
        sel = selectors.DefaultSelector()
        pid_of = {}
        for i, p in enumerate(self._procs):
            sel.register(p.sentinel, selectors.EVENT_READ)
            pid_of[p.sentinel] = i

        results: list[Any] = [_UNSET] * self._n
        deadline = time.monotonic() + self._timeout

        try:
            while unfinished := [i for i, r in enumerate(results) if r is _UNSET]:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    self._kill_all()
                    self._dirty = True
                    raise PartyTimeoutError(
                        f"Test timed out after {self._timeout}s — "
                        f"parties {unfinished} did not finish. "
                        f"Possible deadlock."
                    )
                remaining = max(0.001, remaining)

                # Cap select timeout to drain pipes frequently (sentinel
                # fires only on process exit, but exceptions arrive via pipe
                # while the process is still alive).
                events = sel.select(timeout=min(remaining, 0.5))

                # Check sentinels
                for key, _ in events:
                    pid = pid_of[key.fileobj]
                    if results[pid] is not _UNSET:
                        continue
                    sel.unregister(key.fileobj)
                    # Worker has exited.  Try to read result from pipe.
                    if self._parent_conns[pid].poll(0):
                        try:
                            raw = self._parent_conns[pid].recv_bytes()
                            results[pid] = _safe_unpickle(raw, pid)
                        except EOFError:
                            pass
                    if results[pid] is _UNSET:
                        self._kill_all()
                        self._dirty = True
                        raise PartyCrashError(
                            f"Party {pid} (PID {self._procs[pid].pid}) "
                            f"died unexpectedly "
                            f"(exit code: {_safe_exitcode(self._procs[pid])})"
                        )
                    if isinstance(results[pid], BaseException):
                        self._kill_all()
                        self._dirty = True
                        raise PartyCrashError(
                            f"Party {pid} raised an exception"
                        ) from results[pid]

                # Drain pipes for already-completed-but-sentinel-not-yet-fired
                for pid, conn in enumerate(self._parent_conns):
                    if results[pid] is _UNSET and conn.poll(0):
                        raw = conn.recv_bytes()
                        results[pid] = _safe_unpickle(raw, pid)
                        # One party raised → kill all immediately
                        if isinstance(results[pid], BaseException):
                            self._kill_all()
                            self._dirty = True
                            raise PartyCrashError(
                                f"Party {pid} raised an exception"
                            ) from results[pid]

        except KeyboardInterrupt:
            self._kill_all()
            self._dirty = True
            raise
        finally:
            sel.close()

        # Check for any remaining results / exceptions
        for pid in range(self._n):
            if results[pid] is _UNSET:
                self._kill_all()
                self._dirty = True
                raise PartyCrashError(f"Party {pid} did not return a result")
            if isinstance(results[pid], WorkerCrashedError):
                self._kill_all()
                self._dirty = True
                raise results[pid]
            if isinstance(results[pid], WorkerException):
                self._kill_all()
                self._dirty = True
                raise PartyCrashError(
                    f"Party {pid}: {results[pid].type_name}: {results[pid].message}"
                )

        return results

    # —— context manager —————————————————

    def shutdown(self):
        """Send EOF to all workers, wait for exit, clean up."""
        for conn in self._parent_conns:
            try:
                conn.close()
            except OSError:
                pass
        for p in self._procs:
            if p is not None and p.is_alive():
                p.kill()
            if p is not None:
                p.join(timeout=1)
                p.close()
        self._parent_conns.clear()
        self._procs.clear()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.shutdown()
        return False


def _safe_exitcode(p):
    """Return exitcode after ensuring poll() has been called."""
    if p is None:
        return None
    p.join(timeout=0)
    return p.exitcode


# ====================================================================
#  pytest integration
# ====================================================================

def pytest_addoption(parser):
    parser.addoption(
        "--mpc-timeout",
        type=int,
        default=_DEFAULT_TIMEOUT,
        help="Fallback timeout (seconds) for MPC deadlock detection. "
             "Default 60s."
    )


def pytest_configure(config):
    global _mpc_timeout
    _mpc_timeout = config.getoption("--mpc-timeout")
    # Set spawn early, before any Process objects are created.
    # spawn avoids fork+threading interactions with C extensions
    # (NetIO_listen in a background thread after fork would deadlock
    # on the GIL without gil_scoped_release).
    mp.set_start_method("spawn", force=True)
    random.seed(0)


# ====================================================================
#  pytest fixtures  (module-scoped persistent pools)
# ====================================================================

@pytest.fixture(scope="module")
def rss3_pool():
    """Module-scoped 3-party RSS3 pool."""
    pool = PartyPool(3, _setup_rep3, protocol="rss3")
    yield pool
    pool.shutdown()


@pytest.fixture(scope="module")
def add2_pool():
    """Module-scoped 2-party ADD2 pool."""
    pool = PartyPool(2, _setup_add2, protocol="add2")
    yield pool
    pool.shutdown()


@pytest.fixture(scope="module")
def dpf_pool():
    """Module-scoped 3-party DPF pool."""
    pool = PartyPool(3, _setup_dpf, protocol="dpf")
    yield pool
    pool.shutdown()


@pytest.fixture(scope="module")
def ring_transport_pool():
    """Module-scoped 2-party RingTransport pool."""
    pool = PartyPool(2, _setup_ring_transport, protocol="ring_transport")
    yield pool
    pool.shutdown()


# ====================================================================
#  Rvector serialisation helpers  (unchanged)
# ====================================================================

def _rv_to_list(v) -> list:
    """Convert an Rvector to a plain Python list."""
    return [v[i] for i in range(len(v))]


def _make_rv(ell: int, elems: list):
    """Create an Rvector of the given *ell* filled with *elems*."""
    Rv = mpmt.Rvector(ell)
    v = Rv(len(elems))
    v.fill()
    for i, e in enumerate(elems):
        v[i] = e
    return v


def _sv_to_lists(sv):
    """Convert a ShareVec to ``(this_list, nxt_list)`` for pickling."""
    return (_rv_to_list(sv.this_share), _rv_to_list(sv.nxt_share))


# ====================================================================
#  RSS3 protocol primitives  (unchanged)
# ====================================================================

def _share_scalar(inst, pid: int, sharer: int, val: int):
    if pid == sharer:
        return inst.share_scalar(val)
    return inst.recv_scalar_share()


def _share_vec(inst, pid: int, sharer: int, ell: int, vec: list, sv):
    if pid == sharer:
        rv = _make_rv(ell, vec)
        auxBuf = mpmt.RvectorPack(ell=ell)(n=len(vec))
        inst.share_vector(rv, sv, auxBuf)
    else:
        auxBuf = mpmt.RvectorPack(ell=ell)(n=len(vec))
        inst.recv_vector_share(sv, auxBuf)


def _unpack_scalar(ss):
    return (ss.this_share, ss.nxt_share)


def _reconstruct_scalar(results: list, ell: int) -> int:
    r = 0
    for (this_, _) in results:
        r = mpmt.ring_add(ell, r, this_)
    return r


def _reconstruct_vec(results: list, ell: int, n: int) -> list:
    return [mpmt.ring_add(ell, mpmt.ring_add(ell, results[0][0][i], results[1][0][i]), results[2][0][i]) for i in range(n)]
