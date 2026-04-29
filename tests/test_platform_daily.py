"""Tests for wc_solns_pipelines.platform.daily."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from wc_solns_pipelines.platform import daily


def _fixed_now() -> datetime:
    return datetime(2026, 4, 29, 8, 5, tzinfo=timezone.utc)


def test_run_calls_both_in_order():
    calls: list[str] = []

    def digest_fn(*, now, dry_run):
        calls.append(f"digest:{dry_run}")
        return 0

    def sweep_fn(*, now):
        calls.append(f"sweep:{now.isoformat()}")
        return {"tenants_swept": 0, "rows_pruned": 0, "by_tenant": {}}

    rc = daily.run(now=_fixed_now(), digest_fn=digest_fn, sweep_fn=sweep_fn)

    assert rc == 0
    assert calls[0].startswith("digest:")
    assert calls[1].startswith("sweep:")


def test_dry_run_calls_digest_dry_skips_sweep():
    digest_calls: list[bool] = []
    sweep_calls: list[datetime] = []

    def digest_fn(*, now, dry_run):
        digest_calls.append(dry_run)
        return 0

    def sweep_fn(*, now):
        sweep_calls.append(now)
        return {}

    rc = daily.run(
        now=_fixed_now(),
        dry_run=True,
        digest_fn=digest_fn,
        sweep_fn=sweep_fn,
    )

    assert rc == 0
    assert digest_calls == [True]
    assert sweep_calls == []  # sweep is skipped in dry-run


def test_run_continues_when_digest_raises():
    sweep_called = []

    def digest_fn(*, now, dry_run):
        raise RuntimeError("digest exploded")

    def sweep_fn(*, now):
        sweep_called.append(True)
        return {"tenants_swept": 0, "rows_pruned": 0}

    rc = daily.run(now=_fixed_now(), digest_fn=digest_fn, sweep_fn=sweep_fn)

    assert rc == 0
    assert sweep_called == [True], "sweep must run even when digest raises"


def test_run_continues_when_sweep_raises():
    digest_called = []

    def digest_fn(*, now, dry_run):
        digest_called.append(True)
        return 0

    def sweep_fn(*, now):
        raise RuntimeError("sweep exploded")

    rc = daily.run(now=_fixed_now(), digest_fn=digest_fn, sweep_fn=sweep_fn)

    assert rc == 0
    assert digest_called == [True]


def test_run_returns_zero_when_both_raise():
    def digest_fn(*, now, dry_run):
        raise ValueError("oops")

    def sweep_fn(*, now):
        raise OSError("disk")

    rc = daily.run(now=_fixed_now(), digest_fn=digest_fn, sweep_fn=sweep_fn)

    assert rc == 0


def test_main_dry_run(monkeypatch, capsys):
    captured = {"digest_dry_run": None, "sweep_called": False}

    def fake_digest_run(*, now=None, dry_run=False, **_kwargs):
        captured["digest_dry_run"] = dry_run
        return 0

    def fake_sweep(*, now):
        captured["sweep_called"] = True
        return {}

    monkeypatch.setattr(
        "wc_solns_pipelines.platform.daily.watchdog_digest.run",
        fake_digest_run,
    )
    monkeypatch.setattr(
        "wc_solns_pipelines.platform.daily.promo_lifecycle.sweep_expired_all_tenants",
        fake_sweep,
    )

    rc = daily.main(["--dry-run"])

    assert rc == 0
    assert captured["digest_dry_run"] is True
    assert captured["sweep_called"] is False


def test_main_real_run_invokes_both(monkeypatch):
    captured = {"digest_called": False, "sweep_called": False}

    def fake_digest_run(*, now=None, dry_run=False, **_kwargs):
        captured["digest_called"] = True
        return 0

    def fake_sweep(*, now):
        captured["sweep_called"] = True
        return {"tenants_swept": 0, "rows_pruned": 0, "by_tenant": {}}

    monkeypatch.setattr(
        "wc_solns_pipelines.platform.daily.watchdog_digest.run",
        fake_digest_run,
    )
    monkeypatch.setattr(
        "wc_solns_pipelines.platform.daily.promo_lifecycle.sweep_expired_all_tenants",
        fake_sweep,
    )

    rc = daily.main([])

    assert rc == 0
    assert captured["digest_called"] is True
    assert captured["sweep_called"] is True
