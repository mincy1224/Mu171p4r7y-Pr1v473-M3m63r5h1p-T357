"""Lightweight 3-party harness — no pytest, no PartyPool."""
import multiprocessing as mp
import socket, time, traceback, sys, os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', '..'))
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
    # socketpairs for ring topology
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

    # P0: prev=S2[1], next=S0[0];  P1: prev=S0[1], next=S1[0];  P2: prev=S1[1], next=S2[0]
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

    # Close parent ends
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
