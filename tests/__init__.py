"""C3 test suite.

Three layers (by system under test):
  mpmt_components/  — building blocks (incl. multi-process primitives)
  mpmt_protocols/   — high-level protocol flows (SetHolder/Querier/AgentServer/TreeCache)
  app/              — the application layer (self-spawned process stack)

Runners: run_components.py / run_protocols.py / run_app.py / run_all.py
"""
