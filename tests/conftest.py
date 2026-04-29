"""Shared pytest fixtures.

Autouse fixtures here apply to every test in tests/ and protect against
shared-state leaks into developer machines or CI runners. Add only fixtures
that need to apply across the suite. Per-feature fixtures belong in their
test file.
"""

from __future__ import annotations

import os

import pytest


@pytest.fixture(autouse=True)
def _isolate_cost_log_path(tmp_path_factory, monkeypatch):
    """Force every test to write its cost log to a private temp directory.

    Without this, tests that exercise cost_tracker (directly or via
    brightlocal_master.should_allow / opus.chat) hit the production-default
    path /opt/wc-solns/_platform/cost_log.jsonl, which on Windows resolves
    to C:\\opt\\wc-solns\\_platform\\cost_log.jsonl. Cumulative spend across
    runs eventually crosses the $2 daily tenant cap and starts failing
    unrelated tests with BrightLocalBudgetExceeded. A per-test temp path
    keeps each test's spend isolated.

    Tests that explicitly want to control the path (e.g. the cost-log
    rotation tests) are free to monkeypatch the env var to their own
    tmp_path; the last setenv wins, so this fixture composes correctly.
    """
    tmp = tmp_path_factory.mktemp("cost_log")
    monkeypatch.setenv("COST_LOG_PATH", str(tmp / "cost_log.jsonl"))
    yield
