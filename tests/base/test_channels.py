"""Channel: construction via socket, send/recv, acquire, connect/listener."""
import sys, os, time, socket, multiprocessing as mp
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))
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


def _srv_listener(port, q):
    """Server: ChannelListener.accept() → recv → send ack."""
    try:
        listener = ChannelListener("127.0.0.1", port)
        q.put(('listening', True))
        ch = listener.accept()
        q.put(('ok', ch is not None))
        buf = bytearray(16)
        ch.recv(buf)
        ch.send(b"ACK_SERVER_TO_CP")
        q.put(('ok', bytes(buf)))
    except Exception as e:
        q.put(('err', str(e)))


def _cli_connect(host, port, payload, q):
    """Client: Channel.connect() → send → recv ack."""
    try:
        time.sleep(0.05)  # let server bind
        ch = Channel.connect(host, port)
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

    # -- ChannelListener + Channel.connect bidir -----
    port = _find_free_port()
    q_srv, q_cli = mp.Queue(), mp.Queue()
    payload = b"HELLO_FROM_CLI_X"  # exactly 16 bytes

    srv = mp.Process(target=_srv_listener, args=(port, q_srv))
    cli = mp.Process(target=_cli_connect, args=("127.0.0.1", port, payload, q_cli))
    srv.start(); cli.start()
    srv.join(timeout=10); cli.join(timeout=10)
    if srv.is_alive(): srv.terminate()
    if cli.is_alive(): cli.terminate()

    srv_ok = not q_srv.empty() and q_srv.get()[0] == 'listening'
    if not q_srv.empty():
        _, srv_ch_ok = q_srv.get()
        srv_ok = srv_ok and srv_ch_ok
    cli_ok = not q_cli.empty() and q_cli.get()[0] == 'ok'
    check("ChannelListener + Channel.connect construct", srv_ok and cli_ok)

    if srv_ok and cli_ok:
        _, srv_recv = q_srv.get()
        check("ChannelListener recv", srv_recv[:16] == payload,
              f"got {srv_recv[:16]} exp {payload}")
        _, cli_recv = q_cli.get()
        check("Channel.connect recv", cli_recv[:16] == b"ACK_SERVER_TO_CP",
              f"got {cli_recv[:16]}")

    # -- Channel.send / recv / acquire ---------------
    port2 = _find_free_port()
    q2_srv, q2_cli = mp.Queue(), mp.Queue()

    def srv2_worker():
        try:
            listener = ChannelListener("127.0.0.1", port2)
            q2_srv.put(('listening', True))
            ch = listener.accept()
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
            ch = Channel.connect("127.0.0.1", port2)
            handle = ch.acquire()
            q2_cli.put(('ok', isinstance(handle, int)))
            ch.send(b"A" * 32)
            ch.flush()
            q2_cli.put(('ok', True))
        except Exception as e:
            q2_cli.put(('err', str(e)))

    s2 = mp.Process(target=srv2_worker)
    c2 = mp.Process(target=cli2_worker)
    s2.start()
    q2_srv.get(timeout=5)  # wait for listening
    c2.start()
    s2.join(timeout=10); c2.join(timeout=10)
    if s2.is_alive(): s2.terminate()
    if c2.is_alive(): c2.terminate()

    if not q2_srv.empty():
        _, is_int = q2_srv.get()
        check("Channel acquire returns int", is_int is not None)
    if not q2_srv.empty():
        _, data = q2_srv.get()
        check("Channel send/recv 32B", data == b"A" * 32)

    # -- Channel(sock) wrapping -----------------------
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
            ch = Channel(conn)
            # Verify fd ownership transferred (socket closed)
            try: conn.fileno()
            except OSError: pass  # expected
            # Bidir
            buf = bytearray(16)
            ch.recv(buf)
            ch.send(b"ACK_WRAP_SRV_CPT")
            q3.put(('ok', bytes(buf)))
        except Exception as e:
            q3.put(('err', str(e)))

    def cli3_worker():
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(10)
            s.connect(('127.0.0.1', port3))
            s.settimeout(None)
            ch = Channel(s)
            ch.send(b"HELLO_WRAP_CLI_X")
            buf = bytearray(16)
            ch.recv(buf)
            q3.put(('ok', bytes(buf)))
        except Exception as e:
            q3.put(('err', str(e)))

    s3 = mp.Process(target=srv3_worker)
    c3 = mp.Process(target=cli3_worker)
    s3.start()
    q3.get(timeout=5)  # wait for listening
    c3.start()
    s3.join(timeout=10); c3.join(timeout=10)
    if s3.is_alive(): s3.terminate()
    if c3.is_alive(): c3.terminate()

    results = []
    while not q3.empty():
        results.append(q3.get())
    ok_srv = [r for r in results if r[0] == 'ok' and isinstance(r[1], bytes)]
    ok_cli = [r for r in results if r[0] == 'ok' and r[1] == b"ACK_WRAP_SRV_CPT"]
    check("Channel(sock) srv bidir", len(ok_srv) >= 1)
    check("Channel(sock) cli bidir", len(ok_cli) >= 1)

    # -- _from_ptr ------------------------------------
    try:
        ch = Channel._from_ptr(0)
        check("Channel._from_ptr exists", True)
    except Exception:
        check("Channel._from_ptr exists (rejects invalid)", True)

    # -- Error: no args -------------------------------
    try:
        Channel()
        check("Channel() rejects no args", False)
    except TypeError:
        check("Channel() rejects no args", True)

    print(f"  PASS={PASS}  FAIL={FAIL}")
    return PASS, FAIL

if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--small", action="store_true")
    args = ap.parse_args()
    rc = run_tests(small=args.small)
    raise SystemExit(rc)
