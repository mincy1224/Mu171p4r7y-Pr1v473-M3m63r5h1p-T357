"""Two-party hash consistency test (self-spawning: runs both parties)."""
import sys, os, time, socket, secrets
_sys_t = os.path.dirname(os.path.abspath(__file__))
while _sys_t and not os.path.isdir(os.path.join(_sys_t, 'common')):
    _sys_t = os.path.dirname(_sys_t)
sys.path.insert(0, _sys_t)
import mpmt

ELL  = 24
KEY  = b'0123456789abcdef'
PORT = 21234

PREIMAGES = {
    '8B':   b'hello_8b',
    '16B':  b'exactly16bytes!!',
    '32B':  b'A' * 32,
    '256B': b'B' * 256,
    '512B': b'C' * 512,
}

def _party_main(party: int) -> None:
    print(f'=== Party {party} ({PORT=}  {ELL=}) ===')
    for label, preimage in PREIMAGES.items():
        if party == 0:
            listener = mpmt.ChannelListener('127.0.0.1', PORT)
            ch = listener.accept()
            add2 = mpmt.ShrAdd2(ELL, party=0)(ch)
            pt_share = add2.share_element(preimage)
            key_share = add2.share_key(KEY)
            t0 = time.monotonic()
            h_share = add2.hash(pt_share, key_share)
            dt = time.monotonic() - t0
            p1_share = add2.recv_data()
            circuit = mpmt.ring_add(ELL, h_share, p1_share)
            local   = mpmt.hash_aes_dm(preimage, KEY, ELL)
            match = 'MATCH' if local == circuit else 'MISMATCH'
            print(f'  {label}: local=0x{local:08x}  circuit=0x{circuit:08x}  {match}  ({dt:.1f}s)')
        else:
            ch = mpmt.Channel.connect('127.0.0.1', PORT, timeout=30)
            add2 = mpmt.ShrAdd2(ELL, party=1)(ch)
            pt_share = add2.recv_element_share()
            buf = bytearray(16); add2.recv_key_share(buf); key_share = bytes(buf)
            t0 = time.monotonic()
            h_share = add2.hash(pt_share, key_share)
            dt = time.monotonic() - t0
            add2.send_data(h_share)
            local = mpmt.hash_aes_dm(preimage, KEY, ELL)
            print(f'  {label}: h_share=0x{h_share:08x}  local=0x{local:08x}  ({dt:.1f}s)')
    print(f'=== Party {party} done ===')


if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1] in ("0", "1"):
        _party_main(int(sys.argv[1]))
    else:
        import subprocess
        cmd0 = [sys.executable, "-u", os.path.abspath(__file__), "0"]
        cmd1 = [sys.executable, "-u", os.path.abspath(__file__), "1"]
        p0 = subprocess.Popen(cmd0, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, text=True)
        p1 = subprocess.Popen(cmd1, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, text=True)
        out0, err0 = p0.communicate(timeout=120)
        out1, err1 = p1.communicate(timeout=120)
        print(out0, end="")
        print(out1, end="")
        ok = (p0.returncode == 0 and p1.returncode == 0
              and "MATCH" in out0 and "MISMATCH" not in out0
              and "MISMATCH" not in out1)
        print(f"\n  PASS={1 if ok else 0}  FAIL={0 if ok else 1}")
        raise SystemExit(0 if ok else 1)
