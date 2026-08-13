"""Regression: SH2PCSession::reveal_() missing flush.

Tests that mod/hash/equality_test work when they are the LAST operation on a
channel (no subsequent op would drain the send buffer).
"""
import sys, os, time, socket, multiprocessing as mp
_sys_t = os.path.dirname(os.path.abspath(__file__))
while _sys_t and not os.path.isdir(os.path.join(_sys_t, 'common')):
    _sys_t = os.path.dirname(_sys_t)
sys.path.insert(0, _sys_t)
import mpmt

ELL = 24
BF_SIZE = 14_377_588
ELEMENT = b'8fcf647714e1f1c9d6149ad5fbd47df4'
KEYS = [bytes([i] * 16) for i in range(10)]

PASS = FAIL = 0

def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS {name}")
    else:
        FAIL += 1
        print(f"  FAIL {name}: {detail}")


def _run_two_party(worker0, worker1, timeout=20):
    """Run worker0 (party 0) and worker1 (party 1) over TCP, both must return."""
    s = socket.socket(); s.bind(('127.0.0.1', 0)); port = s.getsockname()[1]; s.close()
    q0, q1 = mp.Queue(), mp.Queue()
    p0 = mp.Process(target=worker0, args=(port, q0))
    p1 = mp.Process(target=worker1, args=(port, q1))
    p0.start(); p1.start()
    p0.join(timeout); p1.join(timeout)
    alive = []
    if p0.is_alive(): alive.append('P0'); p0.terminate()
    if p1.is_alive(): alive.append('P1'); p1.terminate()
    if alive:
        return False, f"TIMEOUT: {alive} stuck"
    r0 = q0.get(timeout=5) if not q0.empty() else "no-result"
    r1 = q1.get(timeout=5) if not q1.empty() else "no-result"
    return True, (r0, r1)


def _mod_worker0(port, q):
    listener = mpmt.ChannelListener('127.0.0.1', port)
    ch = listener.accept()
    add2 = mpmt.ShrAdd2(ELL, party=0)(ch)
    v = add2.share_scalar(12345)
    r = add2.mod(v, BF_SIZE)
    q.put(("ok", r))


def _mod_worker1(port, q):
    time.sleep(0.2)
    ch = mpmt.Channel.connect('127.0.0.1', port, timeout=5)
    add2 = mpmt.ShrAdd2(ELL, party=1)(ch)
    v = add2.recv_scalar_share()
    r = add2.mod(v, BF_SIZE)
    q.put(("ok", r))


def _hash_worker0(port, q):
    listener = mpmt.ChannelListener('127.0.0.1', port)
    ch = listener.accept()
    add2 = mpmt.ShrAdd2(ELL, party=0)(ch)
    es = add2.share_element(ELEMENT)
    ks = add2.recv_element_share()
    r = add2.hash(es, ks)
    q.put(("ok", r))


def _hash_worker1(port, q):
    time.sleep(0.2)
    ch = mpmt.Channel.connect('127.0.0.1', port, timeout=5)
    add2 = mpmt.ShrAdd2(ELL, party=1)(ch)
    es = add2.recv_element_share()
    ks = add2.share_element(KEYS[0])
    r = add2.hash(es, ks)
    q.put(("ok", r))


def _eq_worker0(port, q):
    listener = mpmt.ChannelListener('127.0.0.1', port)
    ch = listener.accept()
    add2 = mpmt.ShrAdd2(ELL, party=0)(ch)
    a = add2.share_scalar(5)
    b = add2.share_scalar(5)
    r = add2.equality_test(a, b)
    q.put(("ok", r))


def _eq_worker1(port, q):
    time.sleep(0.2)
    ch = mpmt.Channel.connect('127.0.0.1', port, timeout=5)
    add2 = mpmt.ShrAdd2(ELL, party=1)(ch)
    a = add2.recv_scalar_share()
    b = add2.recv_scalar_share()
    r = add2.equality_test(a, b)
    q.put(("ok", r))


def _query_worker0(port, q):
    listener = mpmt.ChannelListener('127.0.0.1', port)
    ch = listener.accept()
    add2 = mpmt.ShrAdd2(ELL, party=0)(ch)
    es = add2.share_element(ELEMENT)
    for i in range(10):
        ks = add2.recv_element_share()
        h = add2.hash(es, ks)
        m = add2.mod(h, BF_SIZE)
    q.put(("ok", m))


def _query_worker1(port, q):
    time.sleep(0.2)
    ch = mpmt.Channel.connect('127.0.0.1', port, timeout=5)
    add2 = mpmt.ShrAdd2(ELL, party=1)(ch)
    es = add2.recv_element_share()
    for i in range(10):
        ks = add2.share_element(KEYS[i])
        h = add2.hash(es, ks)
        m = add2.mod(h, BF_SIZE)
    q.put(("ok", m))


if __name__ == '__main__':
    print("=== reveal_() flush regression ===\n")

    ok, detail = _run_two_party(_mod_worker0, _mod_worker1)
    check("terminal mod (last op, no touch)", ok, detail)

    ok, detail = _run_two_party(_hash_worker0, _hash_worker1)
    check("terminal hash (last op, no touch)", ok, detail)

    ok, detail = _run_two_party(_eq_worker0, _eq_worker1)
    check("terminal equality_test (last op, no touch)", ok, detail)

    ok, detail = _run_two_party(_query_worker0, _query_worker1)
    check("10x hash+mod (last op = mod, no touch)", ok, detail)

    print(f"\n  PASS={PASS}  FAIL={FAIL}")
    raise SystemExit(0 if FAIL == 0 else 1)
