"""Lightweight 2-party harness for EMP2."""
import multiprocessing as mp
import socket, time, traceback, sys, os

_sys_t = os.path.dirname(os.path.abspath(__file__))
while _sys_t and not os.path.isdir(os.path.join(_sys_t, 'common')):
    _sys_t = os.path.dirname(_sys_t)
sys.path.insert(0, _sys_t)
import mpmt


class Chan:
    def __init__(self, sock):
        self._handle = mpmt.NetIO_from_socket(sock.fileno())
    def acquire(self):
        return self._handle
    def __del__(self):
        try: mpmt._netio_delete(self._handle)
        except: pass


def run_2party(party_fn, ell, timeout=60):
    """Spawn 2 processes with a socketpair channel. party_fn(pid, chan) -> result."""
    a, b = socket.socketpair()
    queues = [mp.Queue(), mp.Queue()]

    def worker(pid, sock, q):
        try:
            ch = Chan(sock)
            result = party_fn(pid, ch)
            q.put(('ok', result))
        except Exception:
            q.put(('err', traceback.format_exc()))
        finally:
            try: sock.close()
            except: pass

    p0 = mp.Process(target=worker, args=(0, a, queues[0]))
    p1 = mp.Process(target=worker, args=(1, b, queues[1]))
    p0.start(); p1.start()
    a.close(); b.close()

    results = []
    deadline = time.monotonic() + timeout
    for pid in range(2):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            p0.terminate(); p1.terminate()
            raise TimeoutError(f"Party {pid} timed out")
        try:
            status, data = queues[pid].get(timeout=max(remaining, 1))
        except Exception:
            p0.terminate(); p1.terminate()
            raise TimeoutError(f"Party {pid} queue timeout")
        if status == 'err':
            p0.terminate(); p1.terminate()
            raise RuntimeError(f"Party {pid} error:\n{data}")
        results.append(data)

    for p in [p0, p1]:
        p.join(timeout=3)
        if p.is_alive(): p.terminate(); p.join()
    return results
