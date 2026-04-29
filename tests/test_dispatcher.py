"""Tests for wc_solns_pipelines.platform.dispatcher and the
list_due / list_due_all additions to dashboard_app.services.tenant_schedule.

The dispatcher accepts injectable `subprocess_runner`, `list_due_fn`, and
`dispatch_fn` callables so we can exercise the full tick flow without
spawning real Python processes.
"""

from __future__ import annotations

import json
import os
import subprocess
from datetime import datetime, timezone
from typing import Any

os.environ.setdefault("SESSION_SECRET", "test-session-secret-32-bytes-plus-aaaaa")

import pytest

from dashboard_app.services import tenant_schedule as ts
from wc_solns_pipelines.platform import dispatcher


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def tenant_root(tmp_path, monkeypatch):
    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    return tmp_path


def _write_schedule(tenant_root, tenant_id: str, entries: list[dict[str, Any]]) -> None:
    """Drop a schedule.json directly so we can build odd states without
    going through ts.set_entry (which validates against automation_catalog)."""
    cfg = tenant_root / tenant_id / "config"
    cfg.mkdir(parents=True, exist_ok=True)
    doc = {
        "version": 1,
        "tenant_id": tenant_id,
        "updated_at": "2026-04-29T00:00:00+00:00",
        "entries": entries,
    }
    (cfg / "schedule.json").write_text(json.dumps(doc), encoding="utf-8")


# ---------------------------------------------------------------------------
# cron_matches
# ---------------------------------------------------------------------------


def test_cron_matches_daily_9am():
    expr = "0 9 * * *"
    # 2026-04-29 is a Wednesday.
    assert ts.cron_matches(expr, datetime(2026, 4, 29, 9, 0, tzinfo=timezone.utc)) is True
    assert ts.cron_matches(expr, datetime(2026, 4, 29, 9, 1, tzinfo=timezone.utc)) is False


def test_cron_matches_step_intervals():
    expr = "*/15 * * * *"
    assert ts.cron_matches(expr, datetime(2026, 4, 29, 12, 0, tzinfo=timezone.utc)) is True
    assert ts.cron_matches(expr, datetime(2026, 4, 29, 12, 15, tzinfo=timezone.utc)) is True
    assert ts.cron_matches(expr, datetime(2026, 4, 29, 12, 30, tzinfo=timezone.utc)) is True
    assert ts.cron_matches(expr, datetime(2026, 4, 29, 12, 45, tzinfo=timezone.utc)) is True
    assert ts.cron_matches(expr, datetime(2026, 4, 29, 12, 7, tzinfo=timezone.utc)) is False


def test_cron_matches_business_hours_weekdays_only():
    expr = "0 9-17 * * 1-5"  # Mon-Fri 9am-5pm on the hour
    # 2026-04-29 = Wednesday.
    assert ts.cron_matches(expr, datetime(2026, 4, 29, 9, 0, tzinfo=timezone.utc)) is True
    assert ts.cron_matches(expr, datetime(2026, 4, 29, 17, 0, tzinfo=timezone.utc)) is True
    assert ts.cron_matches(expr, datetime(2026, 4, 29, 18, 0, tzinfo=timezone.utc)) is False
    # 2026-05-02 = Saturday, must not fire.
    assert ts.cron_matches(expr, datetime(2026, 5, 2, 10, 0, tzinfo=timezone.utc)) is False
    # 2026-05-03 = Sunday, must not fire either.
    assert ts.cron_matches(expr, datetime(2026, 5, 3, 10, 0, tzinfo=timezone.utc)) is False


def test_cron_matches_dow_sunday_zero():
    expr = "0 8 * * 0"  # Sundays 8am
    # 2026-05-03 = Sunday.
    assert ts.cron_matches(expr, datetime(2026, 5, 3, 8, 0, tzinfo=timezone.utc)) is True
    assert ts.cron_matches(expr, datetime(2026, 5, 4, 8, 0, tzinfo=timezone.utc)) is False


def test_cron_matches_comma_list():
    expr = "0 10 * * 2,4,6"  # Tue/Thu/Sat 10am
    # 2026-04-28 = Tuesday.
    assert ts.cron_matches(expr, datetime(2026, 4, 28, 10, 0, tzinfo=timezone.utc)) is True
    # 2026-04-30 = Thursday.
    assert ts.cron_matches(expr, datetime(2026, 4, 30, 10, 0, tzinfo=timezone.utc)) is True
    # 2026-04-29 = Wednesday.
    assert ts.cron_matches(expr, datetime(2026, 4, 29, 10, 0, tzinfo=timezone.utc)) is False


@pytest.mark.parametrize("bad", [
    "",
    "not a cron",
    "* * * *",        # 4 fields
    "* * * * * *",    # 6 fields
    "60 * * * *",     # minute > 59
    "*/0 * * * *",    # zero step
    "abc * * * *",
    None,
    42,
])
def test_cron_matches_malformed_returns_false(bad):
    now = datetime(2026, 4, 29, 9, 0, tzinfo=timezone.utc)
    assert ts.cron_matches(bad, now) is False  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# list_due / list_due_all
# ---------------------------------------------------------------------------


def test_list_due_strict_match(tenant_root):
    _write_schedule(tenant_root, "acme", [
        {"pipeline_id": "reviews", "cron": "0 9 * * *", "enabled": True,
         "last_modified_at": "...", "source": "tier_default"},
        {"pipeline_id": "seo", "cron": "0 7 * * 1", "enabled": True,
         "last_modified_at": "...", "source": "tier_default"},
    ])
    now = datetime(2026, 4, 29, 9, 0, tzinfo=timezone.utc)  # Wed 9am
    due = ts.list_due("acme", now)
    assert len(due) == 1
    assert due[0]["pipeline_id"] == "reviews"


def test_list_due_tolerance_window(tenant_root):
    _write_schedule(tenant_root, "acme", [
        {"pipeline_id": "reviews", "cron": "0 9 * * *", "enabled": True,
         "last_modified_at": "...", "source": "tier_default"},
    ])
    now_late = datetime(2026, 4, 29, 9, 1, tzinfo=timezone.utc)
    # Strict: nothing matches at 9:01.
    assert ts.list_due("acme", now_late, tolerance_minutes=0) == []
    # Tolerance 2: 9:00 falls inside [9:01-2min, 9:01], so it matches.
    matched = ts.list_due("acme", now_late, tolerance_minutes=2)
    assert len(matched) == 1
    assert matched[0]["pipeline_id"] == "reviews"


def test_list_due_skips_disabled(tenant_root):
    _write_schedule(tenant_root, "acme", [
        {"pipeline_id": "reviews", "cron": "0 9 * * *", "enabled": False,
         "last_modified_at": "...", "source": "tier_default"},
    ])
    now = datetime(2026, 4, 29, 9, 0, tzinfo=timezone.utc)
    assert ts.list_due("acme", now) == []


def test_list_due_all_skips_platform_dirs(tenant_root):
    _write_schedule(tenant_root, "acme", [
        {"pipeline_id": "reviews", "cron": "0 9 * * *", "enabled": True,
         "last_modified_at": "...", "source": "tier_default"},
    ])
    # _platform should be ignored even if it has a schedule.json.
    _write_schedule(tenant_root, "_platform", [
        {"pipeline_id": "reviews", "cron": "0 9 * * *", "enabled": True,
         "last_modified_at": "...", "source": "tier_default"},
    ])
    # _archive too.
    _write_schedule(tenant_root, "_archive", [
        {"pipeline_id": "reviews", "cron": "0 9 * * *", "enabled": True,
         "last_modified_at": "...", "source": "tier_default"},
    ])
    now = datetime(2026, 4, 29, 9, 0, tzinfo=timezone.utc)
    out = ts.list_due_all(now)
    assert "acme" in out
    assert "_platform" not in out
    assert "_archive" not in out


def test_list_due_all_filters_empty(tenant_root):
    _write_schedule(tenant_root, "acme", [
        {"pipeline_id": "reviews", "cron": "0 9 * * *", "enabled": True,
         "last_modified_at": "...", "source": "tier_default"},
    ])
    _write_schedule(tenant_root, "beta", [
        {"pipeline_id": "reviews", "cron": "0 7 * * 1", "enabled": True,
         "last_modified_at": "...", "source": "tier_default"},
    ])
    now = datetime(2026, 4, 29, 9, 0, tzinfo=timezone.utc)
    out = ts.list_due_all(now)
    assert "acme" in out
    assert "beta" not in out  # 9am Wed does not match Mon 7am


def test_list_due_all_skips_disabled_entries(tenant_root):
    _write_schedule(tenant_root, "acme", [
        {"pipeline_id": "reviews", "cron": "0 9 * * *", "enabled": False,
         "last_modified_at": "...", "source": "tier_default"},
    ])
    now = datetime(2026, 4, 29, 9, 0, tzinfo=timezone.utc)
    assert ts.list_due_all(now) == {}


def test_list_due_all_handles_missing_root(tmp_path, monkeypatch):
    bogus = tmp_path / "does_not_exist"
    monkeypatch.setenv("TENANT_ROOT", str(bogus))
    now = datetime(2026, 4, 29, 9, 0, tzinfo=timezone.utc)
    assert ts.list_due_all(now) == {}


# ---------------------------------------------------------------------------
# dispatch_one
# ---------------------------------------------------------------------------


class _FakeCompleted:
    def __init__(self, returncode: int = 0, stdout: bytes = b"", stderr: bytes = b""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def test_dispatch_one_happy_path(tenant_root):
    captured: dict[str, Any] = {}

    def fake_runner(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["env"] = kwargs.get("env", {})
        captured["timeout"] = kwargs.get("timeout")
        captured["capture_output"] = kwargs.get("capture_output")
        return _FakeCompleted(returncode=0)

    result = dispatcher.dispatch_one(
        "acme", "reviews", subprocess_runner=fake_runner,
    )
    assert result["ok"] is True
    assert result["rc"] == 0
    assert result["error"] is None
    assert result["tenant_id"] == "acme"
    assert result["pipeline_id"] == "reviews"
    # Command shape.
    assert captured["cmd"][1:] == [
        "-m", "wc_solns_pipelines.pipelines.reviews.run", "--tenant", "acme",
    ]
    # TENANT_ROOT forwarded into env.
    assert captured["env"].get("TENANT_ROOT") == str(tenant_root)
    assert captured["timeout"] == dispatcher.DEFAULT_TIMEOUT
    assert captured["capture_output"] is True


def test_dispatch_one_forwards_heartbeat_secret(tenant_root, monkeypatch):
    monkeypatch.setenv("HEARTBEAT_SHARED_SECRET", "shhh")

    captured: dict[str, Any] = {}

    def fake_runner(cmd, **kwargs):
        captured["env"] = kwargs.get("env", {})
        return _FakeCompleted(returncode=0)

    dispatcher.dispatch_one("acme", "reviews", subprocess_runner=fake_runner)
    assert captured["env"].get("HEARTBEAT_SHARED_SECRET") == "shhh"


def test_dispatch_one_nonzero_rc(tenant_root):
    def fake_runner(cmd, **kwargs):
        return _FakeCompleted(returncode=2, stderr=b"boom\nbad thing happened\n")

    result = dispatcher.dispatch_one(
        "acme", "reviews", subprocess_runner=fake_runner,
    )
    assert result["ok"] is False
    assert result["rc"] == 2
    assert "rc=2" in (result["error"] or "")
    assert "bad thing" in (result["error"] or "")


def test_dispatch_one_timeout(tenant_root):
    def fake_runner(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd=cmd, timeout=kwargs.get("timeout", 300))

    result = dispatcher.dispatch_one(
        "acme", "reviews", subprocess_runner=fake_runner, timeout=42,
    )
    assert result["ok"] is False
    assert result["rc"] is None
    assert "timeout" in (result["error"] or "").lower()
    assert "42" in (result["error"] or "")


def test_dispatch_one_oserror(tenant_root):
    def fake_runner(cmd, **kwargs):
        raise OSError("no such interpreter")

    result = dispatcher.dispatch_one(
        "acme", "reviews", subprocess_runner=fake_runner,
    )
    assert result["ok"] is False
    assert "OSError" in (result["error"] or "")


def test_dispatch_one_unexpected_exception(tenant_root):
    def fake_runner(cmd, **kwargs):
        raise ValueError("unexpected")

    result = dispatcher.dispatch_one(
        "acme", "reviews", subprocess_runner=fake_runner,
    )
    assert result["ok"] is False
    assert "ValueError" in (result["error"] or "")


def test_dispatch_one_missing_tenant_root(monkeypatch):
    monkeypatch.delenv("TENANT_ROOT", raising=False)

    def fake_runner(cmd, **kwargs):  # pragma: no cover - should not be called
        raise AssertionError("runner should not be called when TENANT_ROOT missing")

    result = dispatcher.dispatch_one(
        "acme", "reviews", env={}, subprocess_runner=fake_runner,
    )
    assert result["ok"] is False
    assert "TENANT_ROOT" in (result["error"] or "")


# ---------------------------------------------------------------------------
# run() tick
# ---------------------------------------------------------------------------


def test_run_dry_run_does_not_dispatch(tenant_root):
    calls: list[tuple[str, str]] = []

    def fake_dispatch(tid, pid):
        calls.append((tid, pid))
        return {"ok": True, "rc": 0, "error": None, "duration_ms": 0,
                "tenant_id": tid, "pipeline_id": pid}

    def fake_list_due():
        return {"acme": [{"pipeline_id": "reviews", "cron": "0 9 * * *",
                          "enabled": True}]}

    rc = dispatcher.run(
        now=datetime(2026, 4, 29, 9, 0, tzinfo=timezone.utc),
        dry_run=True,
        list_due_fn=fake_list_due,
        dispatch_fn=fake_dispatch,
    )
    assert rc == 0
    assert calls == []  # dispatch_fn never invoked in dry-run


def test_run_aggregates_counts(tenant_root):
    def fake_list_due():
        return {
            "acme": [
                {"pipeline_id": "reviews", "cron": "0 9 * * *", "enabled": True},
                {"pipeline_id": "seo", "cron": "0 9 * * *", "enabled": True},
            ],
            "beta": [
                {"pipeline_id": "reviews", "cron": "0 9 * * *", "enabled": True},
            ],
        }

    def fake_dispatch(tid, pid):
        # acme/reviews -> ok; acme/seo -> timeout; beta/reviews -> rc=1
        if tid == "acme" and pid == "reviews":
            return {"ok": True, "rc": 0, "error": None, "duration_ms": 10,
                    "tenant_id": tid, "pipeline_id": pid}
        if tid == "acme" and pid == "seo":
            return {"ok": False, "rc": None, "error": "timeout after 300s",
                    "duration_ms": 300000, "tenant_id": tid, "pipeline_id": pid}
        return {"ok": False, "rc": 1, "error": "rc=1: oops",
                "duration_ms": 5, "tenant_id": tid, "pipeline_id": pid}

    rc = dispatcher.run(
        now=datetime(2026, 4, 29, 9, 0, tzinfo=timezone.utc),
        list_due_fn=fake_list_due,
        dispatch_fn=fake_dispatch,
    )
    assert rc == 0

    # Visibility file should exist with the right counts.
    last_tick_path = tenant_root / "_platform" / "dispatcher_last_tick.json"
    assert last_tick_path.exists()
    payload = json.loads(last_tick_path.read_text(encoding="utf-8"))
    assert payload["total"] == 3
    assert payload["ok"] == 1
    assert payload["failed"] == 2
    assert payload["timed_out"] == 1


def test_run_dry_run_skips_visibility_write(tenant_root):
    def fake_list_due():
        return {"acme": [{"pipeline_id": "reviews", "cron": "0 9 * * *",
                          "enabled": True}]}

    rc = dispatcher.run(
        now=datetime(2026, 4, 29, 9, 0, tzinfo=timezone.utc),
        dry_run=True,
        list_due_fn=fake_list_due,
        dispatch_fn=lambda t, p: {"ok": True, "rc": 0, "error": None,
                                   "duration_ms": 0, "tenant_id": t, "pipeline_id": p},
    )
    assert rc == 0
    last_tick_path = tenant_root / "_platform" / "dispatcher_last_tick.json"
    assert not last_tick_path.exists()


def test_run_continues_after_dispatch_exception_in_one_entry(tenant_root):
    """The dispatch_fn returning a failure dict must not abort the loop."""
    seen: list[tuple[str, str]] = []

    def fake_list_due():
        return {
            "acme": [
                {"pipeline_id": "reviews", "cron": "0 9 * * *", "enabled": True},
                {"pipeline_id": "seo", "cron": "0 9 * * *", "enabled": True},
            ],
        }

    def fake_dispatch(tid, pid):
        seen.append((tid, pid))
        if pid == "reviews":
            return {"ok": False, "rc": 1, "error": "rc=1", "duration_ms": 1,
                    "tenant_id": tid, "pipeline_id": pid}
        return {"ok": True, "rc": 0, "error": None, "duration_ms": 1,
                "tenant_id": tid, "pipeline_id": pid}

    dispatcher.run(
        now=datetime(2026, 4, 29, 9, 0, tzinfo=timezone.utc),
        list_due_fn=fake_list_due,
        dispatch_fn=fake_dispatch,
    )
    assert ("acme", "reviews") in seen
    assert ("acme", "seo") in seen


# ---------------------------------------------------------------------------
# main() CLI
# ---------------------------------------------------------------------------


def test_main_dry_run(tenant_root, capsys):
    rc = dispatcher.main(["--dry-run", "--log-level", "WARNING"])
    assert rc == 0
