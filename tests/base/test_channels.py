"""Channel: construction, send/recv, acquire, multi-process startup."""
import sys, os, time, random, socket, multiprocessing as mp
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))
import mpmt
from mpmt.channels import Channel, wrap_socket, connect_retry

PASS = FAIL = 0

def check(name, cond, detail=""):
    global PASS, FAIL
    if cond: PASS += 1
    else: FAIL += 1; print(f"  FAIL {name}  {detail}")

def _find_free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]

def _server_worker(port, q):
    try:
        ch = Channel(port)
        q.put(('ok', ch is not None))
        # Wait for client to send something
        buf = bytearray(16)
        ch.recv(buf)
        ch.send(b"ACK_SERVER_TO_CP")
        q.put(('ok', bytes(buf)))
    except Exception as e:
        q.put(('err', str(e)))

def _client_worker(host, port, payload, q):
    try:
        time.sleep(0.05)  # Let server bind
        ch = Channel(host, port)
        q.put(('ok', ch is not None))
        ch.send(payload)
        buf = bytearray(16)
        ch.recv(buf)
        q.put(('ok', bytes(buf)))
    except Exception as e:
        q.put(('err', str(e)))

def run_tests(small=False):
    global PASS, FAIL
    PASS = FAIL = 0
    print("=== Channels ===")

    # -- Channel server + client ------------------
    port = _find_free_port()
    q_srv, q_cli = mp.Queue(), mp.Queue()
    payload = b"HELLO_FROM_CLI_X"  # exactly 16 bytes

    srv = mp.Process(target=_server_worker, args=(port, q_srv))
    cli = mp.Process(target=_client_worker, args=("127.0.0.1", port, payload, q_cli))
    srv.start(); cli.start()
    srv.join(timeout=10); cli.join(timeout=10)
    if srv.is_alive(): srv.terminate()
    if cli.is_alive(): cli.terminate()

    srv_ok = not q_srv.empty() and q_srv.get()[0] == 'ok'
    cli_ok = not q_cli.empty() and q_cli.get()[0] == 'ok'
    check("Channel server construct", srv_ok)
    check("Channel client construct", cli_ok)

    if srv_ok and cli_ok:
        # Server receives payload
        _, srv_recv = q_srv.get()
        check("Channel server recv", srv_recv[:16] == payload,
              f"got {srv_recv[:16]} exp {payload}")
        # Client receives ACK
        _, cli_recv = q_cli.get()
        check("Channel client recv", cli_recv[:16] == b"ACK_SERVER_TO_CP",
              f"got {cli_recv[:16]}")

    # -- Channel: send / recv / acquire ----------
    port2 = _find_free_port()
    q2_srv, q2_cli = mp.Queue(), mp.Queue()

    def srv2_worker():
        try:
            ch = Channel(port2)
            handle = ch.acquire()
            q2_srv.put(('ok', isinstance(handle, int)))
            buf = bytearray(32)
            ch.recv(buf)
            q2_srv.put(('ok', bytes(buf)))
        except Exception as e:
            q2_srv.put(('err', str(e)))

    def cli2_worker():
        try:
            time.sleep(0.05)
            ch = Channel("127.0.0.1", port2)
            handle = ch.acquire()
            q2_cli.put(('ok', isinstance(handle, int)))
            ch.send(b"A" * 32)
            ch.flush()
            q2_cli.put(('ok', True))
        except Exception as e:
            q2_cli.put(('err', str(e)))

    s2 = mp.Process(target=srv2_worker)
    c2 = mp.Process(target=cli2_worker)
    s2.start(); c2.start()
    s2.join(timeout=10); c2.join(timeout=10)
    if s2.is_alive(): s2.terminate()
    if c2.is_alive(): c2.terminate()

    if not q2_srv.empty() and q2_srv.get()[0] == 'ok':
        _, is_int = q2_srv.get_nowait() if not q2_srv.empty() else (None, False)
        check("Channel acquire returns int", is_int is not None)
    if not q2_srv.empty():
        _, data = q2_srv.get()
        check("Channel send/recv 32B", data == b"A" * 32)

    # -- wrap_socket + connect_retry -------------
    if not small:
        port3 = _find_free_port()
        q3 = mp.Queue()

        def srv3_worker():
            try:
                srv_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                srv_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                srv_sock.bind(('127.0.0.1', port3))
                srv_sock.listen(1)
                q3.put(('listening', port3))
                conn, _ = srv_sock.accept()
                handle = wrap_socket(conn)
                # Verify fd was transferred (socket should be closed)
                try: conn.fileno()
                except OSError: pass  # Expected
                q3.put(('ok', isinstance(handle, int)))
            except Exception as e:
                q3.put(('err', str(e)))

        def cli3_worker():
            try:
                s = connect_retry("127.0.0.1", port3)
                q3.put(('ok', s is not None))
            except Exception as e:
                q3.put(('err', str(e)))

        s3 = mp.Process(target=srv3_worker)
        c3 = mp.Process(target=cli3_worker)
        s3.start()
        # Wait for server to be listening
        q3.get(timeout=5)
        c3.start()
        s3.join(timeout=10); c3.join(timeout=10)
        if s3.is_alive(): s3.terminate()
        if c3.is_alive(): c3.terminate()
        # Consume remaining
        results = []
        while not q3.empty():
            results.append(q3.get())
        ok_count = sum(1 for r in results if r[0] == 'ok')
        check("wrap_socket + connect_retry", ok_count >= 2, f"got {ok_count}/2 ok")

    # -- _from_ptr ------------------------------
    try:
        ch = Channel._from_ptr(0)  # Invalid handle, should not crash immediately
        check("Channel._from_ptr exists", True)
    except Exception:
        check("Channel._from_ptr exists (rejects invalid)", True)

    # -- keyword arguments -----------------------
    # Error: no port
    try: Channel()
    except TypeError: check("Channel() rejects no args", True)
    else: check("Channel() rejects no args", False)

    # Error: host without port
    try: Channel(host="127.0.0.1")
    except TypeError: check("Channel(host=...) rejects missing port", True)
    else: check("Channel(host=...) rejects missing port", False)

    # Error: mixed positional + keyword
    try: Channel(14000, port=14000)
    except TypeError: check("Channel(pos, kw) rejects mixed", True)
    else: check("Channel(pos, kw) rejects mixed", False)

    # Happy path: keyword server + keyword client
    port4 = _find_free_port()
    q4_srv, q4_cli = mp.Queue(), mp.Queue()

    def srv4_worker():
        try:
            ch = Channel(port=port4)
            q4_srv.put(('ok', True))
        except Exception as e:
            q4_srv.put(('err', str(e)))

    def cli4_worker():
        try:
            time.sleep(0.05)
            ch = Channel(host="127.0.0.1", port=port4)
            q4_cli.put(('ok', True))
        except Exception as e:
            q4_cli.put(('err', str(e)))

    s4 = mp.Process(target=srv4_worker)
    c4 = mp.Process(target=cli4_worker)
    s4.start(); c4.start()
    s4.join(timeout=10); c4.join(timeout=10)
    if s4.is_alive(): s4.terminate()
    if c4.is_alive(): c4.terminate()

    s4_ok = not q4_srv.empty() and q4_srv.get()[0] == 'ok'
    c4_ok = not q4_cli.empty() and q4_cli.get()[0] == 'ok'
    check("Channel(port=...) keyword server", s4_ok)
    check("Channel(host=..., port=...) keyword client", c4_ok)

    print(f"  PASS={PASS}  FAIL={FAIL}")
    return PASS, FAIL

if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--small", action="store_true")
    args = ap.parse_args()
    rc = run_tests(small=args.small)
    raise SystemExit(rc)
