"""RingTransport: send_scalar, recv_scalar, send_vector, recv_vector."""
import sys, os, time, random, socket, multiprocessing as mp
_sys_t = os.path.dirname(os.path.abspath(__file__))
while _sys_t and not os.path.isdir(os.path.join(_sys_t, 'common')):
    _sys_t = os.path.dirname(_sys_t)
sys.path.insert(0, _sys_t)
import mpmt
from mpmt.channels import Channel, ChannelListener

PASS = FAIL = 0

def check(name, cond, detail=""):
    global PASS, FAIL
    if cond: PASS += 1
    else: FAIL += 1; print(f"  FAIL {name}  {detail}")

def _find_free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]

def run_tests(small=False):
    global PASS, FAIL
    PASS = FAIL = 0
    print("=== RingTransport ===")

    ells = [1, 8, 31] if small else list(range(1, 32))

    for ell in [1, 8, 31]:
        RT = mpmt.RingTransport(ell)
        check(f"RingTransport factory ell={ell}", RT is not None)
    for bad in [0, 32]:
        try: mpmt.RingTransport(bad)
        except (ValueError, RuntimeError): check(f"RingTransport reject ell={bad}", True)
        else: check(f"RingTransport reject ell={bad}", False, "no error raised")

    for ell in [1, 8, 14, 31] if not small else [1, 31]:
        port = _find_free_port()
        m = mpmt.ring_mask(ell)
        vals = [random.randint(0, m) for _ in range(5)]
        q_srv, q_cli = mp.Queue(), mp.Queue()

        def srv_scalar():
            try:
                listener = ChannelListener("127.0.0.1", port)
                ch = listener.accept()
                rt = mpmt.RingTransport(ell)(ch)
                q_srv.put(('ok', rt.ell))
                for _ in vals:
                    v = rt.recv_scalar()
                    q_srv.put(('ok', v))
            except Exception as e:
                q_srv.put(('err', str(e)))

        def cli_scalar():
            try:
                time.sleep(0.05)
                ch = Channel.connect("127.0.0.1", port)
                rt = mpmt.RingTransport(ell)(ch)
                q_cli.put(('ok', rt.ell))
                for v in vals:
                    rt.send_scalar(v)
                q_cli.put(('ok', True))
            except Exception as e:
                q_cli.put(('err', str(e)))

        srv = mp.Process(target=srv_scalar)
        cli = mp.Process(target=cli_scalar)
        srv.start(); cli.start()
        srv.join(timeout=10); cli.join(timeout=10)
        if srv.is_alive(): srv.terminate()
        if cli.is_alive(): cli.terminate()

        srv_results = []
        while not q_srv.empty():
            srv_results.append(q_srv.get())
        cli_results = []
        while not q_cli.empty():
            cli_results.append(q_cli.get())

        ok_srv = [r for r in srv_results if r[0] == 'ok']
        ok_cli = [r for r in cli_results if r[0] == 'ok']

        if len(ok_srv) >= 1 + len(vals):
            check(f"RT ell={ell} rt.ell srv", ok_srv[0][1] == ell)
        received = [r[1] for r in ok_srv[1:1+len(vals)] if isinstance(r[1], int)]
        if len(received) == len(vals):
            check(f"RT ell={ell} send/recv_scalar x{len(vals)}",
                  received == vals, f"got {received[:3]} exp {vals[:3]}")

        if ell < 31:
            bad_val = m + 1
            try:
                a, b = socket.socketpair()
                ch = Channel(a)
                rt = mpmt.RingTransport(ell)(ch)
                rt.send_scalar(bad_val)
                b.close()
                check(f"RT ell={ell} reject OOB scalar", False, "no error raised")
            except ValueError:
                b.close()
                check(f"RT ell={ell} reject OOB scalar", True)
            except Exception as e:
                b.close()
                check(f"RT ell={ell} reject OOB scalar", False, str(e))

    vec_ells = [1, 8] if small else [1, 2, 3, 4, 5, 6, 7, 8]
    vec_sizes = [0, 1, 10, 100] if small else [0, 1, 2, 4, 8, 16, 32, 64, 100, 1000]

    for ell in vec_ells:
        Rv = mpmt.Rvector(ell)
        for n in vec_sizes:
            port = _find_free_port()
            m = mpmt.ring_mask(ell)
            v_send = Rv(n)
            if n > 0:
                v_send.rand_fill()
            q_v = mp.Queue()

            def srv_vec():
                try:
                    listener = ChannelListener("127.0.0.1", port)
                    ch = listener.accept()
                    rt = mpmt.RingTransport(ell)(ch)
                    aux = mpmt.RvectorPack(ell)(n)
                    v_recv = Rv(n)
                    rt.recv_vector(vec=v_recv, aux_buf=aux)
                    q_v.put(('ok', v_recv.to_bytes()))
                except Exception as e:
                    q_v.put(('err', str(e)))

            def cli_vec():
                try:
                    time.sleep(0.05)
                    ch = Channel.connect("127.0.0.1", port)
                    rt = mpmt.RingTransport(ell)(ch)
                    aux = mpmt.RvectorPack(ell)(n)
                    rt.send_vector(vec=v_send, aux_buf=aux)
                    q_v.put(('ok', v_send.to_bytes()))
                except Exception as e:
                    q_v.put(('err', str(e)))

            srv_p = mp.Process(target=srv_vec)
            cli_p = mp.Process(target=cli_vec)
            srv_p.start(); cli_p.start()
            srv_p.join(timeout=15); cli_p.join(timeout=15)
            if srv_p.is_alive(): srv_p.terminate()
            if cli_p.is_alive(): cli_p.terminate()

            v_results = []
            while not q_v.empty():
                v_results.append(q_v.get())
            ok_v = [r[1] for r in v_results if r[0] == 'ok']
            if len(ok_v) == 2:
                check(f"RT ell={ell} send/recv_vector n={n}",
                      ok_v[0] == ok_v[1],
                      f"len srv={len(ok_v[0])} cli={len(ok_v[1])}")
            else:
                check(f"RT ell={ell} send/recv_vector n={n}", False,
                      f"errors: {[r for r in v_results if r[0]=='err']}")

    print(f"  PASS={PASS}  FAIL={FAIL}")
    return PASS, FAIL

if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--small", action="store_true")
    args = ap.parse_args()
    rc = run_tests(small=args.small)
    raise SystemExit(0 if rc[1] == 0 else 1)
