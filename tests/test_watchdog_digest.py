"""Tests for wc_solns_pipelines.platform.watchdog_digest.

The digest runner accepts injectable callables (evaluate_fn, alert_fn,
state_reader, state_writer) so we exercise the full gating logic without
touching the real watchdog, SMTP, or filesystem.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from typing import Any

os.environ.setdefault("SESSION_SECRET", "test-session-secret-32-bytes-plus-aaaaa")

import pytest

from dashboard_app.services.tenant_watchdog import (
    ISSUE_ERRORED,
    ISSUE_MISSING_FIRST_RUN,
    ISSUE_OVERDUE,
    Issue,
)
from wc_solns_pipelines.platform import watchdog_digest as wd


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def tenant_root(tmp_path, monkeypatch):
    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    return tmp_path


def _NOW(year=2026, month=4, day=29, hour=12, minute=0) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=timezone.utc)


def _issue(
    tenant_id: str = "garcia",
    pipeline_id: str = "reviews",
    kind: str = ISSUE_ERRORED,
    age_hours: float | None = 1.5,
    severity: str = "error",
    message: str = "boom",
) -> Issue:
    return Issue(
        tenant_id=tenant_id,
        pipeline_id=pipeline_id,
        kind=kind,
        age_hours=age_hours,
        severity=severity,
        message=message,
    )


class _Captured:
    """Captures (subject, body) for the most recent alert and a count."""

    def __init__(self, return_value: bool = True) -> None:
        self.calls: list[tuple[str, str]] = []
        self.return_value = return_value

    def __call__(self, subject: str, body: str) -> bool:
        self.calls.append((subject, body))
        return self.return_value


class _StateBox:
    """In-memory state reader/writer pair for tests."""

    def __init__(self, initial: dict[str, Any] | None = None) -> None:
        self.value: dict[str, Any] = dict(initial or {})
        self.write_count = 0

    def reader(self) -> dict[str, Any]:
        return dict(self.value)

    def writer(self, data: dict[str, Any]) -> None:
        self.value = dict(data)
        self.write_count += 1


# ---------------------------------------------------------------------------
# fingerprint stability
# ---------------------------------------------------------------------------


def test_fingerprint_stable_across_age_drift():
    """age_hours and message are not part of the fingerprint - only the
    (tenant_id, pipeline_id, kind) triple. An overdue pipeline that's
    been overdue 3h vs 4h is still the 'same problem'."""
    a = {"garcia": [_issue(age_hours=3.0, message="3h late")]}
    b = {"garcia": [_issue(age_hours=4.0, message="4h late")]}
    assert wd.fingerprint(a) == wd.fingerprint(b)


def test_fingerprint_changes_on_new_issue():
    a = {"garcia": [_issue()]}
    b = {"garcia": [_issue(), _issue(pipeline_id="seo", kind=ISSUE_OVERDUE)]}
    assert wd.fingerprint(a) != wd.fingerprint(b)


def test_fingerprint_invariant_to_iteration_order():
    """Tenant order in the dict shouldn't matter."""
    a = {
        "garcia": [_issue(pipeline_id="reviews")],
        "demo": [_issue(tenant_id="demo", pipeline_id="seo", kind=ISSUE_OVERDUE)],
    }
    b = {
        "demo": [_issue(tenant_id="demo", pipeline_id="seo", kind=ISSUE_OVERDUE)],
        "garcia": [_issue(pipeline_id="reviews")],
    }
    assert wd.fingerprint(a) == wd.fingerprint(b)


def test_fingerprint_empty_findings():
    assert wd.fingerprint({}) == wd.fingerprint({"garcia": []})


# ---------------------------------------------------------------------------
# render_digest
# ---------------------------------------------------------------------------


def test_render_digest_subject_contains_date_and_counts():
    findings = {
        "garcia": [_issue(pipeline_id="reviews", kind=ISSUE_ERRORED)],
        "demo": [
            _issue(tenant_id="demo", pipeline_id="seo", kind=ISSUE_OVERDUE, severity="warn"),
        ],
    }
    summary = {
        "errored": 1,
        "overdue": 1,
        "missing_first_run": 0,
        "tenants_with_issues": 2,
        "total": 2,
    }
    subject, body = wd.render_digest(findings, summary, now=_NOW())
    assert "2026-04-29" in subject
    assert "1 errored" in subject
    assert "1 overdue" in subject
    assert "across 2 tenants" in subject
    # body is plain text, no HTML
    assert "<html" not in body.lower()
    assert "<pre" not in body.lower()


def test_render_digest_groups_by_tenant_with_one_line_per_finding():
    findings = {
        "garcia": [
            _issue(pipeline_id="reviews", kind=ISSUE_ERRORED, message="reviews errored"),
            _issue(pipeline_id="seo", kind=ISSUE_OVERDUE, severity="warn",
                   message="no heartbeat in 5h"),
        ],
        "demo": [
            _issue(tenant_id="demo", pipeline_id="email_assistant",
                   kind=ISSUE_MISSING_FIRST_RUN, severity="warn",
                   message="never pinged"),
        ],
    }
    summary = {
        "errored": 1,
        "overdue": 1,
        "missing_first_run": 1,
        "tenants_with_issues": 2,
        "total": 3,
    }
    _, body = wd.render_digest(findings, summary, now=_NOW())
    assert "Tenant: garcia" in body
    assert "Tenant: demo" in body
    # one line per finding (severity tag, pipeline_id, kind)
    assert "[error] reviews (errored" in body
    assert "[warn] seo (overdue" in body
    assert "[warn] email_assistant (missing_first_run" in body
    # footer with route
    assert "/admin/tenant/garcia" in body
    assert "/admin/tenant/demo" in body


def test_render_digest_all_clear_when_total_zero():
    subject, body = wd.render_digest({}, {"total": 0}, now=_NOW())
    assert "all clear" in subject.lower()
    assert "healthy" in body.lower()


# ---------------------------------------------------------------------------
# run() gating
# ---------------------------------------------------------------------------


def test_run_no_findings_no_prior_state_no_email(tenant_root):
    captured = _Captured()
    box = _StateBox()
    rc = wd.run(
        now=_NOW(),
        evaluate_fn=lambda: {},
        alert_fn=captured,
        state_reader=box.reader,
        state_writer=box.writer,
    )
    assert rc == 0
    assert captured.calls == []
    assert box.write_count == 0
    assert box.value == {}


def test_run_new_findings_no_prior_state_sends_and_persists(tenant_root):
    findings = {"garcia": [_issue()]}
    captured = _Captured()
    box = _StateBox()

    rc = wd.run(
        now=_NOW(),
        evaluate_fn=lambda: findings,
        alert_fn=captured,
        state_reader=box.reader,
        state_writer=box.writer,
    )
    assert rc == 0
    assert len(captured.calls) == 1
    subject, body = captured.calls[0]
    assert "2026-04-29" in subject
    assert "garcia" in body
    # state persisted
    assert box.write_count == 1
    assert box.value["last_fingerprint"] == wd.fingerprint(findings)
    assert box.value["last_sent_date"] == "2026-04-29"
    assert "last_sent_at" in box.value
    assert isinstance(box.value["last_summary"], dict)


def test_run_same_findings_same_day_no_email(tenant_root):
    findings = {"garcia": [_issue()]}
    captured = _Captured()
    box = _StateBox(
        {
            "last_fingerprint": wd.fingerprint(findings),
            "last_sent_date": "2026-04-29",
            "last_sent_at": "2026-04-29T08:00:00+00:00",
            "last_summary": {"total": 1, "errored": 1},
        }
    )

    rc = wd.run(
        now=_NOW(),
        evaluate_fn=lambda: findings,
        alert_fn=captured,
        state_reader=box.reader,
        state_writer=box.writer,
    )
    assert rc == 0
    assert captured.calls == []
    assert box.write_count == 0


def test_run_same_findings_new_day_resends(tenant_root):
    """A new UTC day with the same fingerprint still emits, so Sam gets
    a daily heartbeat that the digest itself is alive when issues exist."""
    findings = {"garcia": [_issue()]}
    captured = _Captured()
    box = _StateBox(
        {
            "last_fingerprint": wd.fingerprint(findings),
            "last_sent_date": "2026-04-28",  # yesterday
            "last_sent_at": "2026-04-28T08:00:00+00:00",
            "last_summary": {"total": 1, "errored": 1},
        }
    )

    rc = wd.run(
        now=_NOW(),
        evaluate_fn=lambda: findings,
        alert_fn=captured,
        state_reader=box.reader,
        state_writer=box.writer,
    )
    assert rc == 0
    assert len(captured.calls) == 1
    assert box.write_count == 1
    assert box.value["last_sent_date"] == "2026-04-29"


def test_run_different_findings_sends_even_same_day(tenant_root):
    prior_findings = {"garcia": [_issue(pipeline_id="reviews")]}
    new_findings = {
        "garcia": [_issue(pipeline_id="reviews"), _issue(pipeline_id="seo", kind=ISSUE_OVERDUE)],
    }
    captured = _Captured()
    box = _StateBox(
        {
            "last_fingerprint": wd.fingerprint(prior_findings),
            "last_sent_date": "2026-04-29",
            "last_sent_at": "2026-04-29T08:00:00+00:00",
            "last_summary": {"total": 1, "errored": 1},
        }
    )

    rc = wd.run(
        now=_NOW(hour=14),
        evaluate_fn=lambda: new_findings,
        alert_fn=captured,
        state_reader=box.reader,
        state_writer=box.writer,
    )
    assert rc == 0
    assert len(captured.calls) == 1
    assert box.value["last_fingerprint"] == wd.fingerprint(new_findings)


def test_run_findings_cleared_sends_all_clear_and_updates_state(tenant_root):
    """N issues yesterday, zero today -> send 'All clear' and persist
    the empty fingerprint so subsequent zero-issue runs stay silent."""
    prior_findings = {"garcia": [_issue()]}
    captured = _Captured()
    box = _StateBox(
        {
            "last_fingerprint": wd.fingerprint(prior_findings),
            "last_sent_date": "2026-04-28",
            "last_sent_at": "2026-04-28T08:00:00+00:00",
            "last_summary": {"total": 1, "errored": 1},
        }
    )

    rc = wd.run(
        now=_NOW(),
        evaluate_fn=lambda: {},
        alert_fn=captured,
        state_reader=box.reader,
        state_writer=box.writer,
    )
    assert rc == 0
    assert len(captured.calls) == 1
    subject, body = captured.calls[0]
    assert "all clear" in subject.lower() or "all clear" in body.lower()
    assert box.write_count == 1
    assert box.value["last_fingerprint"] == wd.fingerprint({})
    # next run with still-zero findings should now stay silent
    captured2 = _Captured()
    rc2 = wd.run(
        now=_NOW(day=30),
        evaluate_fn=lambda: {},
        alert_fn=captured2,
        state_reader=box.reader,
        state_writer=box.writer,
    )
    assert rc2 == 0
    # Same fingerprint, but new day - the gating only goes silent when
    # BOTH current and prior had zero issues. After the all-clear send,
    # last_summary.total is zero, so the next zero-run should match the
    # quiet path.
    assert captured2.calls == []


def test_run_dry_run_prints_no_send(tenant_root, capsys):
    findings = {"garcia": [_issue()]}
    captured = _Captured()
    box = _StateBox()

    rc = wd.run(
        dry_run=True,
        now=_NOW(),
        evaluate_fn=lambda: findings,
        alert_fn=captured,
        state_reader=box.reader,
        state_writer=box.writer,
    )
    assert rc == 0
    assert captured.calls == []
    # state untouched in dry-run
    assert box.write_count == 0

    out = capsys.readouterr().out
    assert "2026-04-29" in out
    assert "garcia" in out


def test_run_alert_fn_failure_does_not_persist_state(tenant_root):
    findings = {"garcia": [_issue()]}
    captured = _Captured(return_value=False)
    box = _StateBox()

    rc = wd.run(
        now=_NOW(),
        evaluate_fn=lambda: findings,
        alert_fn=captured,
        state_reader=box.reader,
        state_writer=box.writer,
    )
    assert rc == 0
    assert len(captured.calls) == 1
    # send returned False -> do NOT persist state, so we'll retry next run
    assert box.write_count == 0


def test_run_alert_fn_raises_does_not_crash(tenant_root):
    """An SMTP failure in alert_fn must not kill the cron - we log and
    return 0 so cron doesn't tag the task red."""
    findings = {"garcia": [_issue()]}

    def boom(subject: str, body: str) -> bool:
        raise RuntimeError("smtp down")

    box = _StateBox()
    rc = wd.run(
        now=_NOW(),
        evaluate_fn=lambda: findings,
        alert_fn=boom,
        state_reader=box.reader,
        state_writer=box.writer,
    )
    assert rc == 0
    assert box.write_count == 0


def test_run_evaluate_raises_returns_zero(tenant_root):
    """evaluate_fn blowing up still exits 0 - cron must never see red."""

    def boom() -> dict:
        raise RuntimeError("watchdog crashed")

    captured = _Captured()
    box = _StateBox()
    rc = wd.run(
        now=_NOW(),
        evaluate_fn=boom,
        alert_fn=captured,
        state_reader=box.reader,
        state_writer=box.writer,
    )
    assert rc == 0
    assert captured.calls == []


# ---------------------------------------------------------------------------
# state path / IO
# ---------------------------------------------------------------------------


def test_state_path_uses_tenant_root_env(tenant_root):
    p = wd.state_path()
    assert p.parent.name == "_platform"
    assert p.name == "watchdog_digest_state.json"
    # parent of _platform is the configured TENANT_ROOT
    assert str(p.parent.parent) == str(tenant_root)


def test_read_state_missing_returns_empty(tenant_root):
    assert wd.read_state() == {}


def test_read_state_corrupt_returns_empty(tenant_root):
    p = wd.state_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("not json {", encoding="utf-8")
    assert wd.read_state() == {}


def test_write_state_atomic_and_roundtrip(tenant_root):
    payload = {
        "last_fingerprint": "abc",
        "last_sent_date": "2026-04-29",
        "last_sent_at": "2026-04-29T12:00:00+00:00",
        "last_summary": {"total": 2, "errored": 1, "overdue": 1},
    }
    wd.write_state(payload)
    p = wd.state_path()
    assert p.exists()
    # no leftover .tmp
    assert not p.with_suffix(p.suffix + ".tmp").exists()
    got = wd.read_state()
    assert got == payload


def test_run_uses_default_state_io_when_not_injected(tenant_root):
    """End-to-end smoke through the real read_state/write_state helpers."""
    findings = {"garcia": [_issue()]}
    captured = _Captured()

    rc = wd.run(
        now=_NOW(),
        evaluate_fn=lambda: findings,
        alert_fn=captured,
    )
    assert rc == 0
    assert len(captured.calls) == 1
    persisted = wd.read_state()
    assert persisted["last_fingerprint"] == wd.fingerprint(findings)
    assert persisted["last_sent_date"] == "2026-04-29"
