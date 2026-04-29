"""Tests for the per-tenant OAuth credential store."""

import json
import os
import time

os.environ.setdefault("SESSION_SECRET", "test-session-secret-32-bytes-plus-aaaaa")

import pytest

from dashboard_app.services import credentials, heartbeat_store, scrubber


@pytest.fixture(autouse=True)
def _isolate_cache():
    """Every test starts with a clean in-memory access-token cache."""
    credentials.clear_access_token_cache()
    yield
    credentials.clear_access_token_cache()


@pytest.fixture
def _tenant_root(tmp_path, monkeypatch):
    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    return tmp_path


def test_store_and_load_round_trip(_tenant_root):
    path = credentials.store(
        "acme",
        "google",
        refresh_token="1//0g-refresh-token-payload",
        scopes=["analytics.readonly", "gmail.modify"],
    )
    assert path.exists()
    cred = credentials.load("acme", "google")
    assert cred is not None
    assert cred["provider"] == "google"
    assert cred["refresh_token"] == "1//0g-refresh-token-payload"
    assert cred["scopes"] == ["analytics.readonly", "gmail.modify"]
    assert cred["validation_status"] == "pending"
    assert cred["connected_at"]
    assert cred["last_validated_at"] is None


def test_store_overwrites_existing(_tenant_root):
    credentials.store("acme", "google", refresh_token="old", scopes=["a"])
    credentials.store("acme", "google", refresh_token="new", scopes=["b", "c"])
    cred = credentials.load("acme", "google")
    assert cred["refresh_token"] == "new"
    assert cred["scopes"] == ["b", "c"]


def test_load_returns_none_for_missing_provider(_tenant_root):
    assert credentials.load("acme", "google") is None


def test_load_returns_none_for_unknown_tenant(_tenant_root):
    assert credentials.load("never_existed", "google") is None


def test_invalid_provider_rejected(_tenant_root):
    with pytest.raises(credentials.CredentialError):
        credentials.store("acme", "With Space", refresh_token="x")
    with pytest.raises(credentials.CredentialError):
        credentials.store("acme", "../escape", refresh_token="x")
    with pytest.raises(credentials.CredentialError):
        credentials.store("acme", "UPPER", refresh_token="x")
    with pytest.raises(credentials.CredentialError):
        credentials.load("acme", "bad/slash")


def test_invalid_tenant_rejected(_tenant_root):
    with pytest.raises(heartbeat_store.HeartbeatError):
        credentials.store("../escape", "google", refresh_token="x")
    with pytest.raises(heartbeat_store.HeartbeatError):
        credentials.store("WITH.DOTS", "google", refresh_token="x")


def test_empty_refresh_token_rejected(_tenant_root):
    with pytest.raises(credentials.CredentialError):
        credentials.store("acme", "google", refresh_token="")


def test_list_connected_sorted(_tenant_root):
    credentials.store("acme", "google", refresh_token="a")
    credentials.store("acme", "meta", refresh_token="b")
    credentials.store("acme", "ghl", refresh_token="c")
    assert credentials.list_connected("acme") == ["ghl", "google", "meta"]


def test_list_connected_ignores_non_json_and_bad_slugs(_tenant_root):
    credentials.store("acme", "google", refresh_token="a")
    # Drop a rogue file by bypassing the store() guard.
    root = heartbeat_store.tenant_root("acme") / "credentials"
    (root / "junk.txt").write_text("not a credential", encoding="utf-8")
    (root / "With.Dots.json").write_text("{}", encoding="utf-8")
    assert credentials.list_connected("acme") == ["google"]


def test_list_connected_empty_for_unknown_tenant(_tenant_root):
    assert credentials.list_connected("nothing_here") == []


def test_mark_validated_updates_fields(_tenant_root):
    credentials.store("acme", "google", refresh_token="x")
    assert credentials.mark_validated("acme", "google", "ok") is True
    cred = credentials.load("acme", "google")
    assert cred["validation_status"] == "ok"
    assert cred["last_validated_at"] is not None


def test_mark_validated_noop_when_no_credential(_tenant_root):
    assert credentials.mark_validated("acme", "google", "ok") is False


def test_delete_removes_file(_tenant_root):
    credentials.store("acme", "google", refresh_token="x")
    assert credentials.delete("acme", "google") is True
    assert credentials.load("acme", "google") is None
    assert credentials.delete("acme", "google") is False


def test_access_token_exchanges_refresh(_tenant_root, monkeypatch):
    credentials.store("acme", "google", refresh_token="refresh-abc")
    calls: list[str] = []

    def fake_exchange(refresh_token: str) -> str:
        calls.append(refresh_token)
        return "ya29.access-token-xyz"

    monkeypatch.setattr(credentials, "_exchange_google_refresh", fake_exchange)
    token = credentials.access_token("acme", "google")
    assert token == "ya29.access-token-xyz"
    assert calls == ["refresh-abc"]


def test_access_token_caches_within_ttl(_tenant_root, monkeypatch):
    credentials.store("acme", "google", refresh_token="refresh-abc")
    call_count = {"n": 0}

    def fake_exchange(refresh_token: str) -> str:
        call_count["n"] += 1
        return f"token-{call_count['n']}"

    monkeypatch.setattr(credentials, "_exchange_google_refresh", fake_exchange)
    first = credentials.access_token("acme", "google")
    second = credentials.access_token("acme", "google")
    assert first == second == "token-1"
    assert call_count["n"] == 1


def test_access_token_re_exchanges_when_cache_expired(_tenant_root, monkeypatch):
    credentials.store("acme", "google", refresh_token="refresh-abc")
    call_count = {"n": 0}

    def fake_exchange(refresh_token: str) -> str:
        call_count["n"] += 1
        return f"token-{call_count['n']}"

    monkeypatch.setattr(credentials, "_exchange_google_refresh", fake_exchange)
    t0 = time.time()
    # Prime the cache.
    credentials.access_token("acme", "google")
    # Manually expire the cached entry.
    key = ("acme", "google")
    cached_token, _expiry = credentials._access_token_cache[key]
    credentials._access_token_cache[key] = (cached_token, t0 - 1.0)
    second = credentials.access_token("acme", "google")
    assert second == "token-2"
    assert call_count["n"] == 2


def test_access_token_raises_when_no_credential(_tenant_root):
    with pytest.raises(credentials.CredentialError):
        credentials.access_token("acme", "google")


def test_access_token_raises_for_unimplemented_provider(_tenant_root):
    credentials.store("acme", "meta", refresh_token="fb-refresh")
    with pytest.raises(credentials.CredentialError):
        credentials.access_token("acme", "meta")


def test_store_invalidates_access_token_cache(_tenant_root, monkeypatch):
    credentials.store("acme", "google", refresh_token="refresh-old")
    tokens = iter(["token-old", "token-new"])
    monkeypatch.setattr(credentials, "_exchange_google_refresh", lambda _r: next(tokens))
    assert credentials.access_token("acme", "google") == "token-old"
    # Rotating the refresh token must drop the cached access token, so the
    # next caller exchanges the new refresh instead of handing out a stale access.
    credentials.store("acme", "google", refresh_token="refresh-new")
    assert credentials.access_token("acme", "google") == "token-new"


def test_delete_invalidates_access_token_cache(_tenant_root, monkeypatch):
    credentials.store("acme", "google", refresh_token="refresh-x")
    monkeypatch.setattr(credentials, "_exchange_google_refresh", lambda _r: "token-x")
    credentials.access_token("acme", "google")
    assert ("acme", "google") in credentials._access_token_cache
    credentials.delete("acme", "google")
    assert ("acme", "google") not in credentials._access_token_cache


def test_stored_credential_payload_is_valid_json(_tenant_root):
    path = credentials.store("acme", "google", refresh_token="x", scopes=["a"])
    # Re-read directly to ensure round-trip without helper.
    raw = path.read_text(encoding="utf-8")
    parsed = json.loads(raw)
    assert parsed["provider"] == "google"
    assert parsed["refresh_token"] == "x"


def test_google_refresh_token_scrubbed(_tenant_root):
    fake_log_line = "stored refresh 1//0gAbCdEfGhIjKlMnOpQrStUvWxYz0123456789AbCdEfGhIjKlMnOpQrStUv for tenant acme"
    cleaned = scrubber.scrub(fake_log_line)
    assert "1//0g" not in cleaned
    assert "[secret]" in cleaned


def test_google_access_token_scrubbed(_tenant_root):
    fake_log_line = "Authorization: Bearer ya29.AbCdEfGhIjKlMnOpQrStUvWxYz0123456789"
    cleaned = scrubber.scrub(fake_log_line)
    assert "ya29." not in cleaned
    assert "[secret]" in cleaned


# ---------------------------------------------------------------------------
# Pattern B paste credentials (store_paste)
# ---------------------------------------------------------------------------


def test_store_paste_persists_arbitrary_fields(_tenant_root):
    path = credentials.store_paste(
        "acme",
        "gmail_app_password",
        {
            "email_address": "owner@example.com",
            "app_password": "abcd efgh ijkl mnop",
            "imap_host": "imap.gmail.com",
        },
    )
    assert path.exists()
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["provider"] == "gmail_app_password"
    assert data["auth_kind"] == "paste"
    assert data["email_address"] == "owner@example.com"
    assert data["app_password"] == "abcd efgh ijkl mnop"
    assert data["imap_host"] == "imap.gmail.com"
    # OAuth fields are absent
    assert "refresh_token" not in data
    assert "scopes" not in data


def test_store_paste_rejects_unknown_provider(_tenant_root):
    with pytest.raises(credentials.CredentialError):
        credentials.store_paste("acme", "made_up_provider", {"key": "x"})


def test_store_paste_rejects_empty_fields(_tenant_root):
    with pytest.raises(credentials.CredentialError):
        credentials.store_paste("acme", "gmail_app_password", {})


def test_store_paste_rejects_invalid_slug(_tenant_root):
    with pytest.raises(credentials.CredentialError):
        credentials.store_paste("acme", "BAD-Slug", {"key": "x"})


def test_store_paste_overwrites_previous(_tenant_root):
    credentials.store_paste("acme", "gmail_app_password", {"app_password": "v1"})
    credentials.store_paste("acme", "gmail_app_password", {"app_password": "v2"})
    data = credentials.load("acme", "gmail_app_password")
    assert data is not None
    assert data["app_password"] == "v2"


def test_store_paste_caller_cant_override_bookkeeping_fields(_tenant_root):
    """A bad caller passing {"provider": "evil"} mustn't stomp on the
    canonical bookkeeping fields written by the function."""
    credentials.store_paste(
        "acme",
        "gmail_app_password",
        {
            "provider": "evil",
            "auth_kind": "evil",
            "connected_at": "1900-01-01",
            "app_password": "real-value",
        },
    )
    data = credentials.load("acme", "gmail_app_password")
    assert data["provider"] == "gmail_app_password"
    assert data["auth_kind"] == "paste"
    assert data["connected_at"] != "1900-01-01"
    assert data["app_password"] == "real-value"


def test_paste_provider_load_returns_full_record(_tenant_root):
    credentials.store_paste(
        "acme",
        "gmail_app_password",
        {"email_address": "x@y.com", "app_password": "pw"},
    )
    data = credentials.load("acme", "gmail_app_password")
    assert data is not None
    assert data["email_address"] == "x@y.com"
    assert data["app_password"] == "pw"
