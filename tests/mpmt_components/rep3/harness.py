"""Lightweight 3-party harness — no pytest, no PartyPool."""
import multiprocessing as mp
import socket, time, traceback, sys, os

_sys_t = os.path.dirname(os.path.abspath(__file__))
while _sys_t and not os.path.isdir(os.path.join(_sys_t, 'common')):
    _sys_t = os.path.dirname(_sys_t)
sys.path.insert(0, _sys_t)
import mpmt


class Chan:
    """Minimal channel wrapper — mimics mpmt.channels.Channel."""
    def __init__(self, sock):
        self._handle = mpmt.NetIO_from_socket(sock.fileno())
    def acquire(self):
        return self._handle
    def __del__(self):
        try: mpmt._netio_delete(self._handle)
        except: pass


def run_3party(party_fn, ell, timeout=60):
    """
    Spawn 3 processes with ring-topology channels via socketpair.
    party_fn(pid, prev_chan, next_chan) -> result (must be picklable).
    """
    pairs = [socket.socketpair() for _ in range(3)]
    queues = [mp.Queue() for _ in range(3)]

    def worker(pid, prev_sock, next_sock, q):
        try:
            prev = Chan(prev_sock)
            nxt = Chan(next_sock)
            result = party_fn(pid, prev, nxt)
            q.put(('ok', result))
        except Exception:
            q.put(('err', traceback.format_exc()))
        finally:
            try: prev_sock.close()
            except: pass
            try: next_sock.close()
            except: pass

    sock_map = [
        (pairs[2][1], pairs[0][0]),
        (pairs[0][1], pairs[1][0]),
        (pairs[1][1], pairs[2][0]),
    ]

    procs = []
    for pid in range(3):
        prev_s, nxt_s = sock_map[pid]
        p = mp.Process(target=worker, args=(pid, prev_s, nxt_s, queues[pid]))
        procs.append(p)
        p.start()

    for a, b in pairs:
        a.close(); b.close()

    results = []
    deadline = time.monotonic() + timeout
    for pid in range(3):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            for p in procs: p.terminate()
            raise TimeoutError(f"Party {pid} timed out")
        try:
            status, data = queues[pid].get(timeout=max(remaining, 1))
        except Exception:
            for p in procs: p.terminate()
            raise TimeoutError(f"Party {pid} queue timeout")
        if status == 'err':
            for p in procs: p.terminate()
            raise RuntimeError(f"Party {pid} error:\n{data}")
        results.append(data)

    for p in procs:
        p.join(timeout=3)
        if p.is_alive():
            p.terminate()
            p.join()
    return results


def check(name, cond):
    if cond:
        print(f"  OK {name}")
        return True
    else:
        print(f"  FAIL {name}")
        return False
