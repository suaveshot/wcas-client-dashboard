"""Tests for wc_solns_pipelines.pipelines.email_assistant.run.

Same injection-driven style as the other pipelines: fetch_unread_fn,
mark_seen_fn, draft_reply_fn, dispatch_fn, heartbeat_fn are all
swappable so the suite never opens a real IMAP connection.
"""

from __future__ import annotations

import json
import os
from typing import Any

os.environ.setdefault("SESSION_SECRET", "test-session-secret-32-bytes-plus-aaaaa")

import pytest

from dashboard_app.services import credentials as _credentials, tenant_prefs as _tenant_prefs
from wc_solns_pipelines.pipelines.email_assistant import run as ea


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _seed_app_password(tenant_id: str, **overrides) -> None:
    fields = {
        "email_address": "owner@example.com",
        "app_password": "abcd efgh ijkl mnop",
    }
    fields.update(overrides)
    _credentials.store_paste(tenant_id, "gmail_app_password", fields)


def _make_envelope(
    *,
    message_id: str = "msg-001",
    from_email: str = "alice@example.com",
    from_name: str = "Alice",
    subject: str = "Quick question",
    body: str = "Are you available next week?",
    uid: str = "1",
) -> dict[str, Any]:
    return {
        "uid": uid,
        "message_id": message_id,
        "from_email": from_email,
        "from_name": from_name,
        "subject": subject,
        "body": body,
        "date": "2026-04-29T10:00:00+00:00",
    }


class _Heartbeats(list):
    def __call__(self, **kwargs):
        self.append(kwargs)
        return 0


class _Dispatches(list):
    def __init__(self, default_action: str = "queued") -> None:
        super().__init__()
        self.default_action = default_action

    def __call__(self, tenant_id, envelope, body):
        self.append({"tenant_id": tenant_id, "envelope": envelope, "body": body})
        return {"action": self.default_action, "draft_id": f"d-{len(self)}"}


def _stub_fetch(envelopes: list[dict[str, Any]]) -> Any:
    captured: dict = {}

    def fn(**kwargs):
        captured.update(kwargs)
        return list(envelopes)

    fn.captured = captured  # type: ignore[attr-defined]
    return fn


def _stub_mark_seen() -> Any:
    captured: dict = {"calls": []}

    def fn(**kwargs):
        captured["calls"].append(kwargs)

    fn.captured = captured  # type: ignore[attr-defined]
    return fn


def _stub_draft(text: str = "Hi there!") -> Any:
    return lambda _ctx, _env: text


# ---------------------------------------------------------------------------
# guard rails
# ---------------------------------------------------------------------------


def test_run_invalid_tenant(tmp_path, monkeypatch):
    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    hb = _Heartbeats()
    rc = ea.run(
        "../bad",
        heartbeat_fn=hb,
        fetch_unread_fn=lambda **k: pytest.fail("should not fetch"),
    )
    assert rc == 0
    assert hb[-1]["status"] == "error"
    assert "Invalid tenant" in hb[-1]["summary"]


def test_run_paused_tenant_short_circuits(tmp_path, monkeypatch):
    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    tdir = tmp_path / "acme"
    tdir.mkdir(parents=True, exist_ok=True)
    (tdir / "tenant_config.json").write_text(json.dumps({"status": "paused"}), encoding="utf-8")
    _seed_app_password("acme")
    hb = _Heartbeats()
    rc = ea.run(
        "acme",
        heartbeat_fn=hb,
        fetch_unread_fn=lambda **k: pytest.fail("should not fetch"),
    )
    assert rc == 0
    assert hb[-1]["status"] == "success"
    assert "Paused" in hb[-1]["summary"]


def test_run_missing_credentials_returns_error_heartbeat(tmp_path, monkeypatch):
    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    hb = _Heartbeats()
    rc = ea.run(
        "acme",
        heartbeat_fn=hb,
        fetch_unread_fn=lambda **k: pytest.fail("should not fetch"),
    )
    assert rc == 0
    assert hb[-1]["status"] == "error"
    assert "App Password" in hb[-1]["summary"]


def test_run_creds_missing_email_returns_error_heartbeat(tmp_path, monkeypatch):
    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    _credentials.store_paste("acme", "gmail_app_password", {"app_password": "x"})
    hb = _Heartbeats()
    rc = ea.run(
        "acme",
        heartbeat_fn=hb,
        fetch_unread_fn=lambda **k: pytest.fail("should not fetch"),
    )
    assert rc == 0
    assert hb[-1]["status"] == "error"


def test_run_imap_fetch_failure_returns_error_heartbeat(tmp_path, monkeypatch):
    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    _seed_app_password("acme")

    def boom(**_kw):
        raise RuntimeError("connection reset")

    hb = _Heartbeats()
    rc = ea.run("acme", heartbeat_fn=hb, fetch_unread_fn=boom)
    assert rc == 0
    assert hb[-1]["status"] == "error"
    assert "IMAP fetch failed" in hb[-1]["summary"]


# ---------------------------------------------------------------------------
# happy path
# ---------------------------------------------------------------------------


@pytest.fixture
def tenant_with_app_password(tmp_path, monkeypatch):
    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    _seed_app_password("acme")
    return tmp_path


def test_run_no_inbox_messages_emits_empty_summary(tenant_with_app_password):
    hb = _Heartbeats()
    rc = ea.run(
        "acme",
        heartbeat_fn=hb,
        fetch_unread_fn=_stub_fetch([]),
        mark_seen_fn=_stub_mark_seen(),
        dispatch_fn=_Dispatches(),
    )
    assert rc == 0
    assert hb[-1]["status"] == "success"
    assert "Inbox empty" in hb[-1]["summary"]


def test_run_drafts_and_dispatches_each_unread(tenant_with_app_password):
    envelopes = [
        _make_envelope(message_id="msg-1", from_email="a@x.com", uid="10"),
        _make_envelope(message_id="msg-2", from_email="b@x.com", uid="11"),
    ]
    hb = _Heartbeats()
    dispatches = _Dispatches()
    fetch = _stub_fetch(envelopes)
    mark_seen = _stub_mark_seen()
    rc = ea.run(
        "acme",
        heartbeat_fn=hb,
        fetch_unread_fn=fetch,
        mark_seen_fn=mark_seen,
        draft_reply_fn=_stub_draft("Reply text"),
        dispatch_fn=dispatches,
    )
    assert rc == 0
    assert len(dispatches) == 2
    for entry in dispatches:
        assert entry["body"] == "Reply text"
    # mark_seen called with the UIDs we drafted against
    assert mark_seen.captured["calls"][-1]["uids"] == ["10", "11"]
    # IMAP creds pass-through
    assert fetch.captured["email_address"] == "owner@example.com"
    assert fetch.captured["imap_host"] == "ea.fake.com" or fetch.captured["imap_host"] == "imap.gmail.com"


def test_run_skips_already_seen_messages(tenant_with_app_password):
    state_path = tenant_with_app_password / "acme" / "pipeline_state" / "email_assistant.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(
        json.dumps({"seen_message_ids": ["msg-old"]}), encoding="utf-8"
    )
    envelopes = [
        _make_envelope(message_id="msg-old", uid="5"),
        _make_envelope(message_id="msg-new", from_email="c@x.com", uid="6"),
    ]
    hb = _Heartbeats()
    dispatches = _Dispatches()
    rc = ea.run(
        "acme",
        heartbeat_fn=hb,
        fetch_unread_fn=_stub_fetch(envelopes),
        mark_seen_fn=_stub_mark_seen(),
        draft_reply_fn=_stub_draft(),
        dispatch_fn=dispatches,
    )
    assert rc == 0
    assert len(dispatches) == 1
    assert dispatches[0]["envelope"]["message_id"] == "msg-new"
    assert "1 already-seen" in hb[-1]["summary"]


def test_run_skips_noreply_senders(tenant_with_app_password):
    envelopes = [
        _make_envelope(message_id="msg-noreply", from_email="noreply@notify.example.com", uid="7"),
        _make_envelope(message_id="msg-real", from_email="alice@x.com", uid="8"),
    ]
    hb = _Heartbeats()
    dispatches = _Dispatches()
    mark_seen = _stub_mark_seen()
    rc = ea.run(
        "acme",
        heartbeat_fn=hb,
        fetch_unread_fn=_stub_fetch(envelopes),
        mark_seen_fn=mark_seen,
        draft_reply_fn=_stub_draft(),
        dispatch_fn=dispatches,
    )
    assert rc == 0
    assert len(dispatches) == 1
    assert dispatches[0]["envelope"]["from_email"] == "alice@x.com"
    assert "1 no-reply skipped" in hb[-1]["summary"]
    # noreply UID still gets marked Seen so we don't see it again
    assert "7" in mark_seen.captured["calls"][-1]["uids"]


def test_run_persists_state(tenant_with_app_password):
    envelopes = [_make_envelope(message_id="msg-1", uid="1")]
    rc = ea.run(
        "acme",
        heartbeat_fn=_Heartbeats(),
        fetch_unread_fn=_stub_fetch(envelopes),
        mark_seen_fn=_stub_mark_seen(),
        draft_reply_fn=_stub_draft(),
        dispatch_fn=_Dispatches(),
    )
    assert rc == 0
    state_path = tenant_with_app_password / "acme" / "pipeline_state" / "email_assistant.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert "msg-1" in state["seen_message_ids"]
    assert state["drafted_total"] == 1
    assert "last_check" in state


def test_run_caps_seen_message_ids(tenant_with_app_password):
    state_path = tenant_with_app_password / "acme" / "pipeline_state" / "email_assistant.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    starting = [f"old-{i}" for i in range(ea.SEEN_IDS_CAP - 1)]
    state_path.write_text(json.dumps({"seen_message_ids": starting}), encoding="utf-8")
    envelopes = [_make_envelope(message_id=f"new-{i}", uid=str(100 + i)) for i in range(5)]
    ea.run(
        "acme",
        heartbeat_fn=_Heartbeats(),
        fetch_unread_fn=_stub_fetch(envelopes),
        mark_seen_fn=_stub_mark_seen(),
        draft_reply_fn=_stub_draft(),
        dispatch_fn=_Dispatches(),
    )
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert len(state["seen_message_ids"]) == ea.SEEN_IDS_CAP
    assert state["seen_message_ids"][-1] == "new-4"
    assert "old-0" not in state["seen_message_ids"]


# ---------------------------------------------------------------------------
# events: lead.created on sales-intent classifications
# ---------------------------------------------------------------------------


def test_run_emits_lead_event_for_sales_intent(tenant_with_app_password):
    envelopes = [
        _make_envelope(
            message_id="msg-sales",
            subject="Looking for a quote",
            body="Can you send pricing for AC repair?",
            uid="1",
        ),
        _make_envelope(
            message_id="msg-thanks",
            subject="Thanks!",
            body="Just wanted to say thank you for the great service.",
            from_email="happy@x.com",
            uid="2",
        ),
    ]
    hb = _Heartbeats()
    rc = ea.run(
        "acme",
        heartbeat_fn=hb,
        fetch_unread_fn=_stub_fetch(envelopes),
        mark_seen_fn=_stub_mark_seen(),
        draft_reply_fn=_stub_draft(),
        dispatch_fn=_Dispatches(),
    )
    assert rc == 0
    events = hb[-1]["events"]
    assert events is not None
    assert len(events) == 1
    assert events[0]["kind"] == "lead.created"
    assert events[0]["intent"] == "lead"


def test_run_no_events_when_no_lead_intent(tenant_with_app_password):
    envelopes = [
        _make_envelope(
            message_id="msg-thanks",
            subject="Thanks",
            body="Thank you for fixing it!",
            uid="1",
        )
    ]
    hb = _Heartbeats()
    ea.run(
        "acme",
        heartbeat_fn=hb,
        fetch_unread_fn=_stub_fetch(envelopes),
        mark_seen_fn=_stub_mark_seen(),
        draft_reply_fn=_stub_draft(),
        dispatch_fn=_Dispatches(),
    )
    assert hb[-1].get("events") is None


# ---------------------------------------------------------------------------
# dispatch outcome handling
# ---------------------------------------------------------------------------


def test_run_counts_failed_dispatch(tenant_with_app_password):
    envelopes = [_make_envelope(message_id="msg-1", uid="1")]
    hb = _Heartbeats()

    def fail(*_a, **_kw):
        return {"action": "failed", "reason": "boom"}

    rc = ea.run(
        "acme",
        heartbeat_fn=hb,
        fetch_unread_fn=_stub_fetch(envelopes),
        mark_seen_fn=_stub_mark_seen(),
        draft_reply_fn=_stub_draft(),
        dispatch_fn=fail,
    )
    assert rc == 0
    assert hb[-1]["status"] == "error"
    assert "1 failed" in hb[-1]["summary"]


def test_run_breaks_on_mid_run_pause(tenant_with_app_password):
    envelopes = [_make_envelope(message_id=f"msg-{i}", uid=str(i)) for i in range(3)]
    dispatched: list[str] = []

    def dispatch_with_pause(tenant_id, env, body):
        dispatched.append(env["message_id"])
        if len(dispatched) == 2:
            return {"action": "skipped", "reason": "tenant_paused"}
        return {"action": "queued", "draft_id": "x"}

    ea.run(
        "acme",
        heartbeat_fn=_Heartbeats(),
        fetch_unread_fn=_stub_fetch(envelopes),
        mark_seen_fn=_stub_mark_seen(),
        draft_reply_fn=_stub_draft(),
        dispatch_fn=dispatch_with_pause,
    )
    assert dispatched == ["msg-0", "msg-1"]


# ---------------------------------------------------------------------------
# dry run
# ---------------------------------------------------------------------------


def test_dry_run_skips_dispatch_and_heartbeat(tenant_with_app_password, capsys):
    envelopes = [_make_envelope(message_id="msg-1", uid="1")]

    def hb_fn(**_kw):
        pytest.fail("dry-run must not push heartbeat")

    def dispatch_fn(*_a, **_kw):
        pytest.fail("dry-run must not dispatch")

    rc = ea.run(
        "acme",
        dry_run=True,
        heartbeat_fn=hb_fn,
        fetch_unread_fn=_stub_fetch(envelopes),
        mark_seen_fn=_stub_mark_seen(),
        draft_reply_fn=_stub_draft("Drafted body"),
        dispatch_fn=dispatch_fn,
    )
    assert rc == 0
    out = capsys.readouterr().out
    assert "Drafted body" in out


# ---------------------------------------------------------------------------
# require_approval real dispatch
# ---------------------------------------------------------------------------


def test_run_queues_when_require_approval_set(tenant_with_app_password):
    _tenant_prefs.set_require_approval("acme", "email_assistant", True)
    envelopes = [_make_envelope(message_id="msg-1", uid="1")]
    hb = _Heartbeats()
    rc = ea.run(
        "acme",
        heartbeat_fn=hb,
        fetch_unread_fn=_stub_fetch(envelopes),
        mark_seen_fn=_stub_mark_seen(),
        draft_reply_fn=_stub_draft("Hi"),
        # No dispatch_fn override - real dispatch.send
    )
    assert rc == 0
    from dashboard_app.services import outgoing_queue
    pending = outgoing_queue.list_pending("acme")
    assert len(pending) == 1
    assert pending[0]["pipeline_id"] == "email_assistant"
    assert pending[0]["body"] == "Hi"


# ---------------------------------------------------------------------------
# helper functions
# ---------------------------------------------------------------------------


def test_should_skip_sender_blocks_noreply():
    assert ea.should_skip_sender("noreply@x.com") is True
    assert ea.should_skip_sender("no-reply@x.com") is True
    assert ea.should_skip_sender("notifications@google.com") is True
    assert ea.should_skip_sender("MAILER-DAEMON@x.com") is True
    assert ea.should_skip_sender("alerts@bounces.x.com") is True


def test_should_skip_sender_allows_real():
    assert ea.should_skip_sender("alice@example.com") is False
    assert ea.should_skip_sender("Sales@business.com") is False


def test_should_skip_sender_blocks_blank():
    assert ea.should_skip_sender("") is True
    assert ea.should_skip_sender("notanemail") is True


def test_classify_intent_lead():
    assert ea._classify_intent("Pricing question", "How much for AC repair?") == "lead"
    assert ea._classify_intent("Request quote", "Need a quote.") == "lead"


def test_classify_intent_thanks():
    assert ea._classify_intent("Thanks", "Just thank you for the help.") == "thanks"


def test_classify_intent_billing():
    assert ea._classify_intent("Invoice", "Got the invoice today.") == "billing"


def test_classify_intent_other_default():
    assert ea._classify_intent("Hello", "Random text.") == "other"


def test_fallback_reply_uses_first_name():
    out = ea._fallback_reply({"from_name": "Alice Smith", "from_email": "a@x.com"})
    assert "Alice" in out
    assert "Smith" not in out


def test_extract_plain_body_from_text_part():
    raw = (
        b"From: a@x.com\r\nSubject: t\r\nMessage-Id: <m1@x>\r\n"
        b"Content-Type: text/plain; charset=utf-8\r\n\r\n"
        b"Hello world"
    )
    msg = ea.email.message_from_bytes(raw)
    assert ea._extract_plain_body(msg) == "Hello world"


def test_extract_plain_body_strips_html_when_no_text_part():
    raw = (
        b"From: a@x.com\r\nSubject: t\r\nMessage-Id: <m1@x>\r\n"
        b"Content-Type: text/html; charset=utf-8\r\n\r\n"
        b"<p>Hello <b>world</b></p>"
    )
    msg = ea.email.message_from_bytes(raw)
    body = ea._extract_plain_body(msg)
    assert "Hello" in body
    assert "world" in body
    assert "<p>" not in body


def test_dispatch_subject_re_prefix():
    """Reply subject should be 'Re: ...' unless the incoming already
    starts with 'Re:'."""
    captured: dict = {}

    def cap(tenant_id, pipeline_id, **kwargs):
        captured.update(kwargs)
        return {"action": "queued"}

    import dashboard_app.services.dispatch as dispatch_mod
    orig = dispatch_mod.send
    try:
        dispatch_mod.send = cap
        ea._dispatch_one(
            "acme",
            _make_envelope(subject="Hello"),
            "body",
        )
        assert captured["subject"] == "Re: Hello"

        captured.clear()
        ea._dispatch_one(
            "acme",
            _make_envelope(subject="Re: Hello"),
            "body",
        )
        assert captured["subject"] == "Re: Hello"
    finally:
        dispatch_mod.send = orig


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def test_main_passes_args_through(tenant_with_app_password, monkeypatch):
    received: dict = {}

    def fake_run(**kwargs):
        received.update(kwargs)
        return 0

    monkeypatch.setattr(ea, "run", fake_run)
    rc = ea.main(["--tenant", "acme", "--max", "10", "--dry-run"])
    assert rc == 0
    assert received["tenant_id"] == "acme"
    assert received["max_messages"] == 10
    assert received["dry_run"] is True
