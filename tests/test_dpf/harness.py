"""3-party star-topology harness for DPF (1 Dealer + 2 Evaluators)."""
import multiprocessing as mp
import socket, time, traceback, sys, os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))
import mpmt


class Chan:
    def __init__(self, sock):
        self._handle = mpmt.NetIO_from_socket(sock.fileno())
    def acquire(self):
        return self._handle
    def __del__(self):
        try: mpmt._netio_delete(self._handle)
        except: pass


def run_dpf(dealer_fn, ev0_fn, ev1_fn, timeout=60):
    """
    Spawn 3 processes with star topology (Dealer ↔ E0, Dealer ↔ E1).
    dealer_fn(ch_e0, ch_e1) -> result_d
    ev0_fn(ch_dealer) -> result_0
    ev1_fn(ch_dealer) -> result_1
    """
    d_e0_a, d_e0_b = socket.socketpair()  # Dealer↔E0
    d_e1_a, d_e1_b = socket.socketpair()  # Dealer↔E1

    queues = [mp.Queue(), mp.Queue(), mp.Queue()]

    # Dealer: gets d_e0_a (to E0) and d_e1_a (to E1)
    # E0: gets d_e0_b (to Dealer)
    # E1: gets d_e1_b (to Dealer)

    def dealer_worker():
        try:
            ch0 = Chan(d_e0_a); ch1 = Chan(d_e1_a)
            r = dealer_fn(ch0, ch1)
            queues[0].put(('ok', r))
        except Exception:
            queues[0].put(('err', traceback.format_exc()))
        finally:
            try: d_e0_a.close(); d_e1_a.close()
            except: pass

    def ev0_worker():
        try:
            ch = Chan(d_e0_b)
            r = ev0_fn(ch)
            queues[1].put(('ok', r))
        except Exception:
            queues[1].put(('err', traceback.format_exc()))
        finally:
            try: d_e0_b.close()
            except: pass

    def ev1_worker():
        try:
            ch = Chan(d_e1_b)
            r = ev1_fn(ch)
            queues[2].put(('ok', r))
        except Exception:
            queues[2].put(('err', traceback.format_exc()))
        finally:
            try: d_e1_b.close()
            except: pass

    procs = [
        mp.Process(target=dealer_worker),
        mp.Process(target=ev0_worker),
        mp.Process(target=ev1_worker),
    ]
    for p in procs: p.start()

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
        if p.is_alive(): p.terminate(); p.join()
    return results
