"""Tests for wc_solns_pipelines.shared.push_heartbeat.

The heartbeat client is fire-and-forget: it MUST always return 0 and never
crash the calling pipeline. These tests pin that contract along with the
payload shape so the dashboard's /api/heartbeat handler keeps working.
"""

from __future__ import annotations

import io
import json
import os
from urllib.error import HTTPError, URLError

import pytest

from wc_solns_pipelines.shared import push_heartbeat


# ---------------------------------------------------------------------------
# build_payload
# ---------------------------------------------------------------------------


def test_build_payload_basic_shape():
    p = push_heartbeat.build_payload(
        tenant_id="acme",
        pipeline_id="reviews",
        status="success",
        summary="ok",
    )
    assert p["tenant_id"] == "acme"
    assert p["pipeline_id"] == "reviews"
    assert p["status"] == "success"
    assert p["summary"] == "ok"
    assert "pushed_at" in p
    # iso8601 in UTC
    assert p["pushed_at"].endswith("+00:00")
    # No optional fields when not provided
    assert "events" not in p
    assert "state_summary" not in p


def test_build_payload_includes_events_when_provided():
    events = [{"kind": "lead.created", "id": "lead_1"}]
    p = push_heartbeat.build_payload("acme", "reviews", "success", "", events=events)
    assert p["events"] == events


def test_build_payload_omits_empty_events():
    p = push_heartbeat.build_payload("acme", "reviews", "success", "", events=[])
    assert "events" not in p


def test_build_payload_includes_state_summary_when_provided():
    p = push_heartbeat.build_payload(
        "acme", "reviews", "success", "", state_summary={"runs": 7}
    )
    assert p["state_summary"] == {"runs": 7}


# ---------------------------------------------------------------------------
# env resolution
# ---------------------------------------------------------------------------


def test_resolve_prefers_os_environ_over_env_file(monkeypatch):
    monkeypatch.setenv("DASHBOARD_URL", "from-env")
    val = push_heartbeat._resolve("DASHBOARD_URL", {"DASHBOARD_URL": "from-file"})
    assert val == "from-env"


def test_resolve_falls_back_to_env_file(monkeypatch):
    monkeypatch.delenv("DASHBOARD_URL", raising=False)
    val = push_heartbeat._resolve("DASHBOARD_URL", {"DASHBOARD_URL": "from-file"})
    assert val == "from-file"


def test_resolve_returns_empty_when_neither_set(monkeypatch):
    monkeypatch.delenv("DASHBOARD_URL", raising=False)
    val = push_heartbeat._resolve("DASHBOARD_URL", {})
    assert val == ""


# ---------------------------------------------------------------------------
# push() return-code contract
# ---------------------------------------------------------------------------


def test_push_returns_zero_when_env_missing(monkeypatch):
    monkeypatch.delenv("DASHBOARD_URL", raising=False)
    monkeypatch.delenv("HEARTBEAT_SHARED_SECRET", raising=False)
    monkeypatch.setattr(push_heartbeat, "_load_env_file", lambda: {})
    rc = push_heartbeat.push("acme", "reviews")
    assert rc == 0


def test_push_dry_run_prints_payload_and_skips_post(monkeypatch, capsys):
    posted: list[tuple] = []

    def fake_post(*args, **kwargs):
        posted.append((args, kwargs))
        return True, "ok"

    monkeypatch.setattr(push_heartbeat, "_post", fake_post)
    rc = push_heartbeat.push("acme", "reviews", summary="hi", dry_run=True)
    assert rc == 0
    assert posted == []  # never called
    out = capsys.readouterr().out
    payload = json.loads(out)
    assert payload["tenant_id"] == "acme"
    assert payload["pipeline_id"] == "reviews"


def test_push_calls_post_with_correct_url_secret_tenant(monkeypatch):
    monkeypatch.setenv("DASHBOARD_URL", "https://dash.example.com")
    monkeypatch.setenv("HEARTBEAT_SHARED_SECRET", "hush")
    monkeypatch.setattr(push_heartbeat, "_load_env_file", lambda: {})

    captured: dict = {}

    def fake_post(url, secret, tenant_id, payload, timeout):
        captured["url"] = url
        captured["secret"] = secret
        captured["tenant_id"] = tenant_id
        captured["payload"] = payload
        captured["timeout"] = timeout
        return True, "HTTP 200: {}"

    monkeypatch.setattr(push_heartbeat, "_post", fake_post)
    rc = push_heartbeat.push(
        "acme",
        "reviews",
        status="success",
        summary="ok",
        events=[{"kind": "lead.created"}],
        timeout=2.5,
    )
    assert rc == 0
    assert captured["url"] == "https://dash.example.com"
    assert captured["secret"] == "hush"
    assert captured["tenant_id"] == "acme"
    assert captured["timeout"] == 2.5
    assert captured["payload"]["events"] == [{"kind": "lead.created"}]


def test_push_returns_zero_on_post_failure(monkeypatch):
    monkeypatch.setenv("DASHBOARD_URL", "https://dash.example.com")
    monkeypatch.setenv("HEARTBEAT_SHARED_SECRET", "hush")
    monkeypatch.setattr(push_heartbeat, "_load_env_file", lambda: {})

    def fake_post(*a, **kw):
        return False, "URLError: refused"

    monkeypatch.setattr(push_heartbeat, "_post", fake_post)
    rc = push_heartbeat.push("acme", "reviews")
    assert rc == 0


# ---------------------------------------------------------------------------
# _post HTTP behavior (mocked urlopen)
# ---------------------------------------------------------------------------


class _FakeResp:
    def __init__(self, status: int, body: bytes):
        self.status = status
        self._body = body

    def read(self, n: int = -1) -> bytes:
        return self._body if n < 0 else self._body[:n]

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_post_returns_ok_on_200(monkeypatch):
    captured: dict = {}

    def fake_urlopen(req, timeout):
        captured["url"] = req.full_url
        captured["headers"] = dict(req.header_items())
        captured["data"] = req.data
        return _FakeResp(200, b'{"ok":true}')

    monkeypatch.setattr(push_heartbeat, "urlopen", fake_urlopen)

    ok, detail = push_heartbeat._post(
        url="https://dash.example.com",
        secret="hush",
        tenant_id="acme",
        payload={"tenant_id": "acme", "pipeline_id": "reviews"},
        timeout=2.0,
    )
    assert ok is True
    assert "HTTP 200" in detail
    assert captured["url"] == "https://dash.example.com/api/heartbeat"
    headers_lower = {k.lower(): v for k, v in captured["headers"].items()}
    assert headers_lower["x-heartbeat-secret"] == "hush"
    assert headers_lower["x-tenant-id"] == "acme"
    assert headers_lower["content-type"] == "application/json"
    sent = json.loads(captured["data"].decode("utf-8"))
    assert sent["tenant_id"] == "acme"


def test_post_strips_trailing_slash_from_url(monkeypatch):
    captured: dict = {}

    def fake_urlopen(req, timeout):
        captured["url"] = req.full_url
        return _FakeResp(200, b"{}")

    monkeypatch.setattr(push_heartbeat, "urlopen", fake_urlopen)
    push_heartbeat._post("https://dash.example.com/", "s", "acme", {}, 1.0)
    assert captured["url"] == "https://dash.example.com/api/heartbeat"


def test_post_returns_false_on_url_error(monkeypatch):
    def fake_urlopen(req, timeout):
        raise URLError("connection refused")

    monkeypatch.setattr(push_heartbeat, "urlopen", fake_urlopen)
    ok, detail = push_heartbeat._post("https://x", "s", "acme", {}, 1.0)
    assert ok is False
    assert "URLError" in detail


def test_post_returns_false_on_http_error(monkeypatch):
    def fake_urlopen(req, timeout):
        raise HTTPError(
            url="https://x/api/heartbeat",
            code=403,
            msg="Forbidden",
            hdrs=None,
            fp=io.BytesIO(b"bad secret"),
        )

    monkeypatch.setattr(push_heartbeat, "urlopen", fake_urlopen)
    ok, detail = push_heartbeat._post("https://x", "s", "acme", {}, 1.0)
    assert ok is False
    assert "HTTPError 403" in detail
    assert "bad secret" in detail


# ---------------------------------------------------------------------------
# CLI (main)
# ---------------------------------------------------------------------------


def test_main_basic_invocation(monkeypatch):
    received: dict = {}

    def fake_push(**kwargs):
        received.update(kwargs)
        return 0

    monkeypatch.setattr(push_heartbeat, "push", fake_push)
    rc = push_heartbeat.main(
        ["--tenant", "acme", "--pipeline", "reviews", "--status", "success", "--summary", "hi"]
    )
    assert rc == 0
    assert received["tenant_id"] == "acme"
    assert received["pipeline_id"] == "reviews"
    assert received["status"] == "success"
    assert received["summary"] == "hi"
    assert received["events"] is None


def test_main_parses_events_json(monkeypatch):
    received: dict = {}

    def fake_push(**kwargs):
        received.update(kwargs)
        return 0

    monkeypatch.setattr(push_heartbeat, "push", fake_push)
    rc = push_heartbeat.main(
        [
            "--tenant", "acme",
            "--pipeline", "reviews",
            "--events", '[{"kind":"review.posted","rating":5}]',
        ]
    )
    assert rc == 0
    assert received["events"] == [{"kind": "review.posted", "rating": 5}]


def test_main_ignores_invalid_events_json(monkeypatch, capsys):
    received: dict = {}

    def fake_push(**kwargs):
        received.update(kwargs)
        return 0

    monkeypatch.setattr(push_heartbeat, "push", fake_push)
    rc = push_heartbeat.main(
        ["--tenant", "acme", "--pipeline", "reviews", "--events", "not-json"]
    )
    assert rc == 0
    assert received["events"] is None
    err = capsys.readouterr().err
    assert "not valid JSON" in err


def test_main_ignores_non_array_events(monkeypatch, capsys):
    received: dict = {}

    def fake_push(**kwargs):
        received.update(kwargs)
        return 0

    monkeypatch.setattr(push_heartbeat, "push", fake_push)
    rc = push_heartbeat.main(
        ["--tenant", "acme", "--pipeline", "reviews", "--events", '{"kind":"x"}']
    )
    assert rc == 0
    assert received["events"] is None
    err = capsys.readouterr().err
    assert "must be a JSON array" in err


def test_main_passes_dry_run_through(monkeypatch):
    received: dict = {}

    def fake_push(**kwargs):
        received.update(kwargs)
        return 0

    monkeypatch.setattr(push_heartbeat, "push", fake_push)
    rc = push_heartbeat.main(
        ["--tenant", "acme", "--pipeline", "reviews", "--dry-run"]
    )
    assert rc == 0
    assert received["dry_run"] is True


def test_main_rejects_unknown_status():
    with pytest.raises(SystemExit):
        push_heartbeat.main(
            ["--tenant", "acme", "--pipeline", "reviews", "--status", "garbage"]
        )
