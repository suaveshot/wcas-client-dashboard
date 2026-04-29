"""Generic per-tenant pipelines. Each subdirectory is one pipeline.

Each pipeline ships:
  - run.py        CLI entry point. Accepts --tenant <id>, returns 0 on success.
  - state.py      Per-tenant state file read/write helpers. Optional.
  - tests live in tests/ at the repo root, named test_<pipeline>_*.py
"""
