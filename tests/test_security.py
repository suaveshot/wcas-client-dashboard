"""Day 2 security-focused tests: tokens, sessions, scrubber, cost tracker."""

import os
import time

os.environ.setdefault("SESSION_SECRET", "test-session-secret-32-bytes-plus-aaaaa")

from dashboard_app.services import cost_tracker, scrubber, sessions, tokens


# --- tokens -----------------------------------------------------------------


def test_token_has_high_entropy():
    t = tokens.generate_token()
    # token_urlsafe(32) yields a 43-character string
    assert len(t) >= 32
    assert t != tokens.generate_token()  # not deterministic


def test_hash_is_deterministic_and_constant_time_compare():
    t = "abc123"
    h1 = tokens.hash_token(t)
    h2 = tokens.hash_token(t)
    assert h1 == h2
    assert tokens.hashes_match(h1, h2)
    assert not tokens.hashes_match(h1, h1[:-1] + "X")


def test_expiry_helpers():
    exp = tokens.expiry_timestamp()
    assert not tokens.is_expired(exp)
    assert tokens.is_expired("2000-01-01T00:00:00+00:00")
    assert tokens.is_expired("not a timestamp at all")


# --- sessions ---------------------------------------------------------------


def test_session_roundtrip():
    cookie = sessions.issue(tenant_id="acme", email="owner@acme.com", role="client")
    payload = sessions.verify(cookie)
    assert payload is not None
    assert payload["tid"] == "acme"
    assert payload["em"] == "owner@acme.com"
    assert payload["rl"] == "client"


def test_session_rejects_tampered_cookie():
    cookie = sessions.issue(tenant_id="acme", email="owner@acme.com")
    bad = cookie[:-2] + "XX"
    assert sessions.verify(bad) is None


def test_session_rejects_empty_cookie():
    assert sessions.verify("") is None
    assert sessions.verify(None) is None  # type: ignore[arg-type]


def test_cookie_kwargs_has_security_defaults():
    kw = sessions.cookie_kwargs()
    assert kw["httponly"] is True
    assert kw["samesite"] == "lax"  # Lax required for OAuth redirect-back; see sessions.py
    assert kw["max_age"] > 0


# --- scrubber ---------------------------------------------------------------


def test_scrubber_redacts_emails():
    out = scrubber.scrub("Please contact owner@acme.com right away.")
    assert "owner@acme.com" not in out
    assert "[email]" in out


def test_scrubber_redacts_phones():
    out = scrubber.scrub("Call (310) 555-1212 or 310-555-1212.")
    assert "555" not in out


def test_scrubber_redacts_money():
    out = scrubber.scrub("Deal value was $18,240 and follow-up closed at $1.5k.")
    assert "$18,240" not in out
    assert "$1.5k" not in out
    assert "[money]" in out


def test_scrubber_redacts_secrets():
    out = scrubber.scrub("key sk-ant-api03-abcdefghijklmnopqrstuvwx leak")
    assert "sk-ant-api03-" not in out


def test_scrubber_passthrough_when_debug_flag_on(monkeypatch):
    monkeypatch.setenv("DEBUG_LOG_PROMPTS", "true")
    out = scrubber.scrub("Email is owner@acme.com.")
    assert "owner@acme.com" in out


# --- cost tracker -----------------------------------------------------------


def test_cost_estimate_per_pricing_table():
    # 1M in + 1M out on Opus -> $15 + $75 = $90
    usd = cost_tracker.estimate_usd("claude-opus-4-7", 1_000_000, 1_000_000)
    assert usd == 90.0


def test_record_call_writes_jsonl(tmp_path, monkeypatch):
    log_path = tmp_path / "cost.jsonl"
    monkeypatch.setenv("COST_LOG_PATH", str(log_path))
    cost_tracker.record_call(
        tenant_id="americal_patrol",
        model="claude-haiku-4-5",
        input_tokens=1000,
        output_tokens=500,
        kind="message",
    )
    assert log_path.exists()
    line = log_path.read_text(encoding="utf-8").strip()
    assert "americal_patrol" in line
    assert "claude-haiku-4-5" in line


def test_cost_cap_blocks_when_tenant_cap_exceeded(tmp_path, monkeypatch):
    log_path = tmp_path / "cost.jsonl"
    monkeypatch.setenv("COST_LOG_PATH", str(log_path))
    monkeypatch.setenv("DAILY_TENANT_CAP", "0.001")
    monkeypatch.setenv("DAILY_DEV_CAP", "9999")
    cost_tracker.record_call(
        tenant_id="noisy",
        model="claude-opus-4-7",
        input_tokens=100_000,
        output_tokens=100_000,
    )
    ok, reason = cost_tracker.should_allow("noisy")
    assert ok is False
    assert reason and "tenant" in reason.lower()
