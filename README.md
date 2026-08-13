# MPMT — Multiparty Private Membership Test

[![status](https://img.shields.io/badge/status-WIP-orange)]()

A research prototype for **private set membership testing** under the semi-honest
model. Set holders contribute private sets, queriers test whether an element
belongs to the aggregated set — and **nobody learns the other party's data**:
holders never see the queried elements, queriers never see the sets.

The project is built in **two layers**:

1. **`mpmt`** — a self-contained **Python package** (C++20 core + nanobind
   bindings) that implements the underlying MPC protocols and can be used
   independently as a low-level multiparty-computation toolbox.
2. **The application layer** — a complete membership service (three MPC
   Agents + a Manager control panel + CLI clients) built **on top of** `mpmt`.

## Table of Contents

- [Introduction](#introduction)
- [Architecture](#architecture)
- [Using `mpmt` (Protocol Tutorials)](#using-mpmt-protocol-tutorials)
- [Using the Application](#using-the-application)
- [Running the Tests](#running-the-tests)
- [Disclaimer](#disclaimer)
- [References](#references)

---

## Introduction

MPMT answers the question: **“is this element in the union of the holders'
sets?”** without any party revealing its private input.

![C3 scenario overview](./c3_scenario.png)

```
set holders                         queriers
  │ private sets                      │ private element
  ▼                                   ▼
┌──────────────────────────────────────────────────┐
│       multi-party private membership test        │
│    Bloom filter · secret sharing · DPF · SIMD    │
└──────────────────────────────────────────────────┘
  ▲                                   ▲
nothing about the set leaks     nothing about the query leaks
```

Each holder encodes its set as a Bloom filter and **secret-shares** it among
three non-colluding MPC Agents. A querier runs a **distributed point function
(DPF)** so that only the three Agents jointly learn a single membership bit —
even the Agents individually learn nothing.

The protocols are implemented once, in C++, wrapped as the standalone
`mpmt` Python package, and reused by the application layer.

---

## Architecture

The project is built in **two layers**: the `mpmt` protocol package (a
standalone C++20 + nanobind Python library) and, on top of it, a complete
private membership service.

```
  ┌─ Application layer ─────────────────────────────────────────────┐
  │                                                                 │
  │  manage_server                                                  │
  │  agent_server ×3 — STEWARD / PEER0 / PEER1                      │
  │  set_holder · querier                                           │
  │                                                                 │
  ├──MPMT protocol package ─────────────────────────────────────────┤
  │                                                                 │
  │  Python API: AgentServer · SetHolder · Querier · _TreeCache     │
  │  Channel · Rvector · ShrRep3 · ShrAdd2 · Dpf · RingTransport    │
  │                                                                 │
  ├─ C++ bindings ──────────────────────────────────────────────────┤
  │                                                                 │
  │  nanobind · shared_ptr IOChannel registry · GIL-safe I/O        │
  │                                                                 │
  ├─ C++ core ──────────────────────────────────────────────────────┤
  │                                                                 │
  │  Rvector     SIMD-packed ring arithmetic  (1..8)                │
  │  ABY3        3-party replicated RSS       (1..8)                │
  │  EMP2        2-party additive sharing    (2..31)                │
  │  BGI16       distributed point function (13..31)                │
  │  RingTransport · Utils                                          │
  │                                                                 │
  └─────────────────────────────────────────────────────────────────┘
```

### The `mpmt` protocol package

`mpmt` is the cryptographic core — an **independently usable Python package**
(`import mpmt`) that wraps the C++20 implementations with
[nanobind](https://nanobind.readthedocs.io/), exposing share vectors, channels,
and protocol objects directly to Python.

- **SIMD-packed ring arithmetic** — `Rvector` packs many small ring elements
  into machine words; batch `add / mul / hadamard` run at near-native speed.
- **Replicated secret sharing (ABY3)** — shares are held as `(thisShare,
  nxtShare)` pairs; protocols run over three pairwise `Channel`s.
- **Distributed point function (BGI16)** — a querier sends two *function
  shares*; the Agents evaluate them over the shared Bloom filter and reveal a
  single membership bit.
- **IOChannel lifetime** — Python `Channel` objects hold opaque handles into a
  `shared_ptr` registry, guaranteeing every C++ protocol object outlives its
  channels.

> The **two-party** secure computation builds
> directly on [**EMP-toolkit**](https://github.com/emp-toolkit), wrapped here in a
> thin layer.

### Application layer

The application turns `mpmt` into a runnable **private membership test service**. It
adds orchestration, persistence, and human-facing interfaces on top of the raw
protocols — while keeping the MPC itself entirely inside `mpmt`.

| Component | Role |
|-----------|------|
| `pretreat` | One-time setup: allocates set-holder / querier user IDs, splits synthetic datasets, writes protocol parameters (`pre.json`). |
| `agent_server` (×3) | The MPC parties (`steward`, `peer0`, `peer1`). Each holds a secret-shared **TreeCache** — a binary OR-tree of Bloom-filter shares — persisted to disk. |
| `manage_server` | The orchestrator: interactive control panel, HTTP reserve/execute API, a **global FIFO queue** with a **single-active** scheduler, and an explicit `ms sync` that triggers the tree merge. |
| `set_holder` CLI | A holder joins the shared structure (`JOIN`), replaces its set (`UPDATE`), or leaves (`QUIT`). |
| `querier` CLI | Queries an element and prints `1` (member) or `0` (not member). |

**Key semantics**

- **JOIN / UPDATE / QUIT never merge automatically.** Each operation only
  marks the TreeCache *dirty*. The tree is merged — and the new root published —
  only when the operator issues `ms sync` in the Manager. This batches many
  writes into one MPC merge.
- **One operation at a time.** The Manager serializes all work through a FIFO
  queue (single active operation); `ms sync` is inserted immediately after the
  current operation and ahead of every queued business operation.
- **Restart-safe.** TreeCache state and the manager DB are persisted, so the
  service can be stopped and restarted without `pretreat` (see the tests).

> **Protocol usage** — see [Using `mpmt` (Protocol Tutorials)](#using-mpmt-protocol-tutorials)
> for hands-on examples, or the [tutorial/](./tutorial/) directory directly.

---

## Using `mpmt` (Protocol Tutorials)

The `mpmt` package is self-contained: build once, then `import mpmt` from any
Python environment. For the full protocol-level documentation and examples,
head to the [tutorial](./tutorial/) directory:

| Directory | Topics |
|-----------|--------|
| [base/](./tutorial/base/) | ring operations, rvector, share vectors |
| [building_blocks/](./tutorial/building_blocks/) | rep3 (ABY3), add2 (EMP2), dpf |
| [net/](./tutorial/net/) | channels, ring transport |

> Protocol tutorials for `handler / query / set holder / tree cache` and the
> Flask demo are planned; the protocol objects themselves are already exposed
> in the package (see `mpmt.AgentServer`, `mpmt.SetHolder`, `mpmt.Querier`).

---

## Using the Application

### 1. Build

```bash
pip install -e . -v
```

### 2. Fresh preprocessing

```bash
cd application
python3 run.py pretreat -f          # generate users, datasets, params
```

### 3. Start the three MPC Agents

```bash
python3 run.py steward &             # three terminals (or background)
python3 run.py peer0  &
python3 run.py peer1  &
```

Each Agent prints `management listening on ...` when ready.

### 4. Start the Manager

```bash
python3 run.py manage_server
```

You land in the control panel:

```
c3-manager> ms start
```

`ms start` connects to the three Agents and starts the HTTP API (idempotent —
a second `ms start` reports `already started`).

### 5. Set holders contribute / update / leave

```bash
# JOIN the shared structure (user IDs come from pretreat/set_holder_users.json)
python3 run.py set_holder -e JOIN <user_id>

# replace the set (UPDATE) or leave (QUIT)
python3 run.py set_holder -e UPDATE <user_id>
python3 run.py set_holder -e QUIT  <user_id>
```

### 6. Queriers test membership

```bash
# QUERY an element (user ID comes from pretreat/querier_users.json)
python3 run.py querier -e QUERY <user_id> <element>
```

Prints `"result": 1` if the element is in the aggregated set, `"result": 0`
otherwise.

### 7. Publish the merged tree

JOIN / UPDATE / QUIT only mark the tree dirty. To merge all pending changes
and publish the new root:

```
c3-manager> ms sync
```

### Manager control panel

| Command | Action |
|---------|--------|
| `ms start` | Connect Agents + start HTTP + scheduler (idempotent) |
| `ms status` | Show backend state and Agent connections (● connected / ● not) |
| `ms sync` | Merge the TreeCache (runs after the current op, ahead of the queue) |
| `ms log` | Show recent log lines |
| `ms exit` | Stop the manager |
| `help` | List commands |

Up / Down arrow recall command history.

### Example end-to-end session

```
# terminal 1–3: Agents          terminal 4: Manager      terminal 5: clients
python3 run.py steward          python3 run.py manage_server
python3 run.py peer0            c3-manager> ms start
python3 run.py peer1
                                                        python3 run.py set_holder -e JOIN <holder_id>
                                                        python3 run.py querier -e QUERY <querier_id> <element>
c3-manager> ms sync
```

---

## Running the Tests

> **Reserved** — the test-suite walkthrough (low-level protocol tests and the
> application-layer E2E scenarios) will be documented here.

---

## Disclaimer

This code is intended solely for **ACADEMIC RESEARCH PURPOSES** and has not undergone a formal **PRODUCTION SECURITY AUDIT**. It is provided **"AS IS,"** without any **EXPRESS OR IMPLIED WARRANTIES**. **USE IT AT YOUR OWN RISK.**

## References

- ABY3 (Mohassel & Rindal, CCS 2018): 3-party replicated secret sharing
- BGI16 DPF (Boyle, Gilboa & Ishai, CCS 2016): function secret sharing
- emp-toolkit: https://github.com/emp-toolkit
- SIMD packing inspired by [lemire/simdcomp](https://github.com/lemire/simdcomp)
