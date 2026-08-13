# Test Suite

Three layers, organised by **system under test** (not by single/multi process):

| Layer | Directory | System under test |
|-------|-----------|-------------------|
| building blocks | `mpmt_components/` | Rvector, Channel, RingTransport, util, ABY3/EMP2/DPF primitives, ring_conv, reveal/flush, hash, 3-party TCP |
| high-level protocols | `mpmt_protocols/` | SetHolder.share_bf, AgentServer.response_share_bf/response_query, Querier.query, TreeCache (insert/update/remove/execute_merge incl. last-leaf no-tree), BF aggregation |
| application | `app/` | Manager (HTTP reserve/execute, FIFO, single-active, explicit ms sync), Agent management, user/token state, restart persistence |

Every test file runs in its **own subprocess**; runners judge purely by the
subprocess return code (`0 if FAIL == 0 else 1`).

## Run

`mpmt` is an installable Python package (`pip install -e .`); use the `python3`
of any environment that has it installed.

```bash
python3 tests/run_components.py [--small]
python3 tests/run_protocols.py [--include-bf]
python3 tests/run_app.py [--only test_sync_e2e.py]
python3 tests/run_all.py          # all three, in order
```

App tests spawn their own minimal stack (fresh pretreat + agents + Manager
`ms start`) via `common.stack.Stack` — **no manual consoles needed**. The
interactive sync prompts of the old scripts are now automated.

## old → new mapping

| tmp/ | tests/ |
|------|--------|
| `base/test_rvector.py` | `mpmt_components/test_rvector.py` |
| `base/test_sharevec.py` | `mpmt_components/test_sharevec.py` |
| `base/test_channels.py` | `mpmt_components/test_channels.py` |
| `base/test_ring_transport.py` | `mpmt_components/test_ring_transport.py` |
| `base/test_util.py` | `mpmt_components/test_util.py` |
| `test_factory.py` | `mpmt_components/rep3/test_factory.py` |
| `building_blocks/test_aby3/*` | `mpmt_components/rep3/{harness,test_operations,test_protocol,test_compound}.py` |
| `building_blocks/test_emp2/*` | `mpmt_components/add2/{harness,test_factory,test_operations,test_protocol}.py` |
| `building_blocks/test_dpf/*` | `mpmt_components/dpf/{harness,test_basic,test_operations}.py` |
| `integration/test_rep3_tcp_ring.py` | `mpmt_components/test_rep3_tcp_ring.py` |
| `test_ringconv_only.py` / `test_ringconv_stress.py` | `mpmt_components/` (same names) |
| `test_reveal_flush.py` / `test_hash_consistency.py` / `test_hash_party.py` | `mpmt_components/` (same names) |
| `test_bf_aggregation.py` | `mpmt_protocols/test_bf_aggregation.py` |
| *(new)* | `mpmt_protocols/test_setholder.py` |
| *(new)* | `mpmt_protocols/test_query.py` |
| *(new)* | `mpmt_protocols/test_treecache.py` |
| `test_biz_state.py` | `app/test_state_machine.py` |
| `test_ultra.py` | `app/test_business_e2e.py` |
| `test_ms_sync_e2e.py` | `app/test_sync_e2e.py` |
| `test_biz_e2e.py` | `app/test_lifecycle_e2e.py` |
| `run_sync_e2e.py` (coordinator) | obsolete — automation folded into `common.stack` + the app tests |
| `{_tree_cache,agent_server,querier,set_holder}.py` | **not migrated** — stale copies of `src/mpmt`, never imported (editable install resolves `src/mpmt`) |

## Merges / removals / reasons

* **`test_factory.py` (rep3) and `building_blocks/test_emp2/test_factory.py`** —
  kept **separate** (`rep3/test_factory.py`, `add2/test_factory.py`): different
  SUTs (ShrRep3 vs ShrAdd2 factory); coverage/parameter spaces differ.
* **`test_hash_party.py`** — made self-spawning (previously two manual consoles).
* **`test_business_e2e.py`** — under the explicit-sync semantics, JOIN/UPDATE/QUIT
  never auto-merge; `query_present` now issues `ms_sync()` before querying.
* **`test_bf_aggregation.py`** — kept (moved) but **excluded from the default
  runner**: it has a pre-existing hang in its raw-ShrRep3 reveal path
  (independent of this refactor). The same BF-aggregation coverage is provided
  by `test_setholder` using the real SetHolder/AgentServer objects. Run with
  `run_protocols.py --include-bf` if desired.
* **`run_sync_e2e.py`** — superseded: `common.stack.Stack.ms_sync()` automates
  the Manager CLI stdin directly.
* **stale `tmp/` module copies** — not migrated; dead code.

## Stack lifecycle (`common.stack`)

`Stack(mode=...)` starts the minimal services a test needs and cleans up after
itself:

* `mode="full"` — fresh pretreat → spawn 3 Agents (all first, then wait each for
  `management listening` + alive) → Manager (`ms start`) → **safe probe**
  (unreserved QUERY /execute for a valid querier must return `NOT_RESERVED`
  with the operations DB bit-identical).
* `mode="manager_only"` — fresh pretreat → 3 **fake mgmt listeners**
  (`common.fake_agent`, accept + idle — the control-plane test never issues an
  Agent management command) → Manager (`ms start`).

`ms_sync()` sends `ms sync` to the Manager stdin and waits only for *acceptance*
(`SYNC queued`); **completion is judged by the test's own oracle**
(TreeCache meta/root files, DB, real QUERY) — never by log strings.

`restart_without_pretreat()` exits the Manager via `ms exit` (graceful), stops
the Agents, re-spawns them and the Manager **without pretreat**, and re-probes.

Readiness is never a fixed sleep; cleanup is graceful (SIGTERM → wait → SIGKILL)
against the child's own process group, touching only processes the test spawned
(no global `pkill`). Every spawned Python subprocess uses `sys.executable -u`.

## Coverage before / after

| Area | before | after |
|------|--------|-------|
| components (rvector/sharevec/channels/transport/util/factory) | base/ + test_factory (standalone) | `mpmt_components` (20 tests, subprocess-isolated) |
| building blocks (rep3/add2/dpf/ring_conv/reveal/hash/tcp) | building_blocks/ + integration/ | `mpmt_components` (subprocess-isolated) |
| high-level protocol (SetHolder/AgentServer/Querier/TreeCache) | only via app E2E (indirect) | `mpmt_protocols` **direct** SUT tests |
| app state machine | test_biz_state (manual stack) | `app/test_state_machine` (manager_only auto) |
| app business E2E | test_ultra (manual stack) | `app/test_business_e2e` (full auto, 464 checks) |
| explicit sync semantics | test_ms_sync (manual ms sync) | `app/test_sync_e2e` (auto, 82 checks) |
| full lifecycle + restart | test_biz_e2e (manual, 146 checks) | `app/test_lifecycle_e2e` (auto sync + auto restart, 146 checks) |

`git diff -- src/mpmt application` is **empty** — no product code was touched.
