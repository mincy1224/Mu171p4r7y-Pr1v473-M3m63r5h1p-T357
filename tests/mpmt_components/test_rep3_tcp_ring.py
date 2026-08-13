"""Three-party ShrRep3 TCP ring — integration test.

Verifies that ShrRep3(ell=1) and ShrRep3(ell=4) complete correctly over
real TCP connections built with Python sockets → Channel(sock).
"""
import sys, os, time, socket, multiprocessing as mp
_sys_t = os.path.dirname(os.path.abspath(__file__))
while _sys_t and not os.path.isdir(os.path.join(_sys_t, 'common')):
    _sys_t = os.path.dirname(_sys_t)
sys.path.insert(0, _sys_t)
import mpmt
from mpmt.channels import Channel, ChannelListener

PASS = FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
        print(f"  FAIL {name}  {detail}")


def _find_free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


def _party_worker(party_id: int, party_name: str,
                  prev_port: int, next_host: str, next_port: int,
                  result_q: mp.Queue):
    """One party in the Rep3 ring."""
    try:
        listener = ChannelListener("127.0.0.1", prev_port)

        ch_nxt = Channel.connect(next_host, next_port, timeout=30)

        ch_prev = listener.accept()

        t0 = time.monotonic()
        rep3_1 = mpmt.ShrRep3(ell=1, party=party_id)(ch_prev, ch_nxt)
        t1 = time.monotonic()
        rep3_4 = mpmt.ShrRep3(ell=4, party=party_id)(ch_prev, ch_nxt)
        t2 = time.monotonic()

        result_q.put(('ok', {
            'party': party_name,
            'ell1_s': round(t1 - t0, 3),
            'ell4_s': round(t2 - t1, 3),
        }))
    except Exception as e:
        result_q.put(('err', f"{party_name}: {type(e).__name__}: {e}"))


def _launch_ring(ports: tuple[int, int, int],
                 start_delays: tuple[float, float, float] | None = None,
                 timeout: float = 60):
    """Launch three parties in a ring.

    *ports*: (steward_prev, peer0_prev, peer1_prev)
    *start_delays*: seconds to sleep before starting each party, or None
                    for staggered (steward first, peer0 +2s, peer1 +2s).
    """
    p0_port, p1_port, p2_port = ports
    if start_delays is None:
        start_delays = (0.0, 2.0, 4.0)

    results: list[dict] = []
    errors: list[str] = []
    q = mp.Queue()
    procs: list[mp.Process] = []

    configs = [
        (0, "STEWARD", p0_port, "127.0.0.1", p1_port, start_delays[0]),
        (1, "PEER0",   p1_port, "127.0.0.1", p2_port, start_delays[1]),
        (2, "PEER1",   p2_port, "127.0.0.1", p0_port, start_delays[2]),
    ]

    for party_id, name, prev_p, nxt_h, nxt_p, delay in configs:
        def _target(pid=party_id, nm=name, pp=prev_p, nh=nxt_h, np=nxt_p, d=delay):
            if d > 0:
                time.sleep(d)
            _party_worker(pid, nm, pp, nh, np, q)

        p = mp.Process(target=_target)
        procs.append(p)
        p.start()

    deadline = time.monotonic() + timeout
    for p in procs:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            p.terminate()
            continue
        p.join(timeout=max(1.0, remaining))
        if p.is_alive():
            p.terminate()

    while not q.empty():
        item = q.get()
        if item[0] == 'ok':
            results.append(item[1])
        else:
            errors.append(item[1])

    return results, errors


def run_tests(small=False):
    global PASS, FAIL
    PASS = FAIL = 0
    print("=== ShrRep3 TCP Ring ===")

    ports = (_find_free_port(), _find_free_port(), _find_free_port())
    results, errors = _launch_ring(ports, start_delays=(0.0, 2.0, 4.0))

    check("staggered: 3 parties completed", len(results) == 3,
          f"got {len(results)}/3, errors={errors}")
    for r in results:
        check(f"staggered: {r['party']} ell=1",
              r['ell1_s'] >= 0,
              f"{r['ell1_s']}s")
        check(f"staggered: {r['party']} ell=4",
              r['ell4_s'] >= 0,
              f"{r['ell4_s']}s")

    if not small:
        ports2 = (_find_free_port(), _find_free_port(), _find_free_port())
        results2, errors2 = _launch_ring(ports2, start_delays=(4.0, 2.0, 0.0))
        check("reverse order: 3 parties completed", len(results2) == 3,
              f"got {len(results2)}/3, errors={errors2}")

        ports3 = (_find_free_port(), _find_free_port(), _find_free_port())
        results3, errors3 = _launch_ring(ports3, start_delays=(2.0, 0.0, 4.0))
        check("peer0-first: 3 parties completed", len(results3) == 3,
              f"got {len(results3)}/3, errors={errors3}")

        ports4 = (_find_free_port(), _find_free_port(), _find_free_port())
        results4, errors4 = _launch_ring(ports4, start_delays=(0.0, 2.0, 4.0))
        check("multi-round: 3 parties completed", len(results4) == 3,
              f"got {len(results4)}/3, errors={errors4}")

    print(f"  PASS={PASS}  FAIL={FAIL}")
    return PASS, FAIL


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--small", action="store_true")
    args = ap.parse_args()
    rc = run_tests(small=args.small)
    raise SystemExit(0 if rc[1] == 0 else 1)
