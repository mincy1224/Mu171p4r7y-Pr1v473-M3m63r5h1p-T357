"""Compare local hashAESDM vs two-party circuit hash."""
import sys, os, time, socket, secrets, multiprocessing as mp
_sys_t = os.path.dirname(os.path.abspath(__file__))
while _sys_t and not os.path.isdir(os.path.join(_sys_t, 'common')):
    _sys_t = os.path.dirname(_sys_t)
sys.path.insert(0, _sys_t)
import mpmt

ELL = 24
KEY = b'0123456789abcdef'

def _pick_port():
    s = socket.socket()
    s.bind(('127.0.0.1', 0))
    p = s.getsockname()[1]
    s.close()
    return p


def p0_worker(port, preimage, q):
    listener = mpmt.ChannelListener('127.0.0.1', port)
    ch = listener.accept()
    add2 = mpmt.ShrAdd2(ELL, party=0)(ch)
    pt_share = add2.share_element(preimage)
    key_share = add2.share_key(KEY)
    q.put(add2.hash(pt_share, key_share))


def p1_worker(port, preimage, q):
    ch = mpmt.Channel.connect('127.0.0.1', port, timeout=30)
    add2 = mpmt.ShrAdd2(ELL, party=1)(ch)
    pt_share = add2.recv_element_share()
    buf = bytearray(16); add2.recv_key_share(buf); key_share = bytes(buf)
    q.put(add2.hash(pt_share, key_share))


def test(preimage: bytes, label: str):
    port = _pick_port()
    q0, q1 = mp.Queue(), mp.Queue()
    p0 = mp.Process(target=p0_worker, args=(port, preimage, q0))
    p1 = mp.Process(target=p1_worker, args=(port, preimage, q1))
    t0 = time.monotonic()
    p0.start(); p1.start()
    p0.join(60); p1.join(60)
    dt = time.monotonic() - t0
    if p0.is_alive() or p1.is_alive():
        p0.terminate(); p1.terminate()
        print(f'{label}: TIMEOUT after {dt:.1f}s')
        return False
    circuit = mpmt.ring_add(ELL, q0.get(), q1.get())
    local   = mpmt.hash_aes_dm(preimage, KEY, ELL)
    ok = local == circuit
    print(f'{label}: local=0x{local:08x}  circuit=0x{circuit:08x}  '
          f'match={ok}  ({dt:.1f}s)')
    return ok


if __name__ == '__main__':
    print(f'KEY=0x{KEY.hex()}')
    test(b'hello_8b',          '  8B')
    test(b'exactly_16_bytes!', ' 16B')
    test(b'A' * 32,            ' 32B')
    test(secrets.token_bytes(64), ' 64B')
    print('Done.')
