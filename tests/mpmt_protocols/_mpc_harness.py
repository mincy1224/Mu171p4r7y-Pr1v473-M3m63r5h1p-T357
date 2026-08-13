"""4-party MPC harness for the high-level protocol tests.

Topology (mirrors the app's wiring, minus the Manager HTTP layer):

    client (SetHolder / Querier)  --3 channels-->  3 Agents (AgentServer)
    agents are connected in a ring (ch_prev/ch_nxt, real TCP)

A *round* is one protocol operation on its own set of 3 client ports:

    op "JOIN"/"UPDATE"  SetHolder.share_bf → response_share_bf → sync_cache → reveal
    op "QUERY"          Querier.query      → response_query (answer returns to client)

Messages are ``(round_idx, kind, pid, payload)``.  Agents may interleave their
``ready`` / ``done`` for a round, so a ``_Router`` stashes out-of-order messages
instead of discarding them — that was the bug in an earlier design.
"""
from __future__ import annotations

import multiprocessing as mp
import secrets
import socket
import tempfile
import time

import mpmt

ROLES = (mpmt.ServerRole.STEWARD, mpmt.ServerRole.PEER0, mpmt.ServerRole.PEER1)


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def random_seeds(hf_num: int) -> list[bytes]:
    return [secrets.token_bytes(16) for _ in range(hf_num)]


def plain_aggregate(sets, bf_size, hf_num, ell_add2, ell_root, seeds):
    """gen_bf(ell=ell_root) per set, then OR-aggregate (a + b - a*b)."""
    mask = (1 << ell_root) - 1
    bfs = []
    for s in sets:
        bf = mpmt.gen_bf(ell=ell_root, set=[e.encode() for e in s],
                         hash_seed_list=seeds, bf_size=bf_size,
                         hf_num=hf_num, ell_add2=ell_add2)
        bfs.append([int(bf[i]) for i in range(bf_size)])
    result = list(bfs[0])
    for bf in bfs[1:]:
        for i in range(bf_size):
            a, b = result[i], bf[i]
            result[i] = (a + b - a * b) & mask
    return result


def _reveal_root(agent, ch_prev, ch_nxt, pid):
    tc = agent._tc
    rep3 = mpmt.ShrRep3(tc._ell_root, pid)(ch_prev, ch_nxt)
    out = mpmt.Rvector(tc._ell_root)(tc._bf_size)
    rep3.reveal_vector(tc.root_node, out,
                       mpmt.RvectorPack(tc._ell_root)(tc._bf_size))
    return [int(out[i]) for i in range(tc._bf_size)]


def _agent_worker(pid, ring_ports, rounds, params, seeds, barrier, q):
    """One AgentServer party.  *rounds* = [ {op, port, data?, target?}, ... ]."""
    try:
        listener = mpmt.ChannelListener("127.0.0.1", ring_ports[pid])
        barrier.wait(timeout=30)
        nxt = (pid + 1) % 3
        ch_nxt = mpmt.Channel.connect("127.0.0.1", ring_ports[nxt], timeout=20)
        ch_prev = listener.accept()
        storage = tempfile.mkdtemp(prefix="tc_proto_")
        agent = mpmt.AgentServer(
            server_role=ROLES[pid],
            set_size=params["set_size"],
            fpr_mantissa=params["fpr_mantissa"],
            fpr_exponent=params["fpr_exponent"],
            storage_dir=storage,
            ch_prev=ch_prev,
            ch_nxt=ch_nxt,
            hash_seed_list=(seeds if pid == 0 else None),
            cores=1,
        )
        tokens: list[str] = []
        for i, r in enumerate(rounds):
            op, port = r["op"], r["port"]
            sh_listener = mpmt.ChannelListener("127.0.0.1", port)
            q.put((i, "ready", pid))
            ch_sh = sh_listener.accept()
            if op == "QUERY":
                agent.response_query(ch_querier=ch_sh)
                q.put((i, "done", pid))
            elif op == "JOIN":
                tok = agent.response_share_bf(
                    prot_type=mpmt.ProtType.JOIN, ch_set_holder=ch_sh)
                tokens.append(tok)
                agent.sync_cache()
                q.put((i, "done", pid, _reveal_root(agent, ch_prev, ch_nxt, pid)))
            else:
                target = int(r.get("target", 0))
                agent.response_share_bf(
                    prot_type=mpmt.ProtType.UPDATE, ch_set_holder=ch_sh,
                    token=tokens[target])
                agent.sync_cache()
                q.put((i, "done", pid, _reveal_root(agent, ch_prev, ch_nxt, pid)))
    except BaseException as e:
        q.put((-1, "err", pid, type(e).__name__, str(e)))


class _Router:
    """Read messages from the shared queue, stashing any that belong to a
    different round/kind than the current consumer (never discarding)."""

    def __init__(self, q):
        self.q = q
        self.stash: list[tuple] = []

    def _read(self, timeout):
        if self.stash:
            return self.stash.pop(0)
        return self.q.get(timeout=timeout)

    def wait(self, rnd, kind, count=3, timeout=30.0):
        got = []
        deadline = time.monotonic() + timeout
        while len(got) < count and time.monotonic() < deadline:
            m = self._read(5)
            if m[1] == "err":
                raise RuntimeError(f"agent error: {m}")
            if m[0] != rnd:
                self.stash.append(m)
                continue
            if m[1] == kind:
                got.append(m)
            else:
                self.stash.append(m)
        if len(got) < count:
            raise RuntimeError(f"only {len(got)}/{count} '{kind}' for round {rnd}")
        return got


def run_protocol(params, seeds, rounds):
    """*rounds*: list of dicts:
        {"op": "JOIN"|"UPDATE", "data": [elements], "target"?: int}
        {"op": "QUERY", "element": bytes}
    Returns per-round outcomes:
        JOIN/UPDATE → list of 3 revealed value-lists
        QUERY       → the querier's returned membership int
    """
    n_agents = 3
    ring_ports = [free_port() for _ in range(n_agents)]
    round_ports = [[free_port() for _ in range(n_agents)] for _ in rounds]
    per_agent = [
        [{**r, "port": round_ports[i][pid]} for i, r in enumerate(rounds)]
        for pid in range(n_agents)
    ]
    ctx = mp.get_context("fork")
    barrier = ctx.Barrier(n_agents)
    q = ctx.Queue()
    procs = [ctx.Process(target=_agent_worker,
                         args=(pid, ring_ports, per_agent[pid], params, seeds,
                               barrier, q))
             for pid in range(n_agents)]
    for p in procs:
        p.start()
    router = _Router(q)

    outcomes = []
    try:
        for i, r in enumerate(rounds):
            op = r["op"]
            ports = round_ports[i]
            router.wait(i, "ready")
            if op == "QUERY":
                querier = mpmt.Querier(set_size=params["set_size"],
                                       fpr_mantissa=params["fpr_mantissa"],
                                       fpr_exponent=params["fpr_exponent"])
                chs = {}
                for role, port in zip(("STEWARD", "PEER0", "PEER1"), ports):
                    chs[role] = mpmt.Channel.connect("127.0.0.1", port, timeout=20)
                got = querier.query(element=r["element"], ch_steward=chs["STEWARD"],
                                    ch_peer0=chs["PEER0"], ch_peer1=chs["PEER1"])
                router.wait(i, "done")
                outcomes.append(got)
            else:
                holder = mpmt.SetHolder(set_size=params["set_size"],
                                        fpr_mantissa=params["fpr_mantissa"],
                                        fpr_exponent=params["fpr_exponent"])
                chs = {}
                for role, port in zip(("STEWARD", "PEER0", "PEER1"), ports):
                    chs[role] = mpmt.Channel.connect("127.0.0.1", port, timeout=20)
                holder.share_bf(set=[e.encode() for e in r["data"]],
                                hash_seed_list=seeds,
                                ch_steward=chs["STEWARD"], ch_peer0=chs["PEER0"],
                                ch_peer1=chs["PEER1"])
                msgs = router.wait(i, "done")
                outcomes.append([next(m[3] for m in msgs if m[2] == pid)
                                 for pid in range(n_agents)])
    finally:
        for p in procs:
            p.join(10)
        for p in procs:
            if p.is_alive():
                p.terminate()
    return outcomes
