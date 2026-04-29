"""
Per-tenant OAuth credential storage + access-token resolution.

Each connected provider is one file:
    /opt/wc-solns/<tenant_id>/credentials/<provider>.json

Shape:
    {
      "provider": "google",
      "refresh_token": "1//0g...",
      "scopes": ["gbp.readonly", "adwords", ...],
      "connected_at": "2026-04-23T15:04:05+00:00",
      "last_validated_at": "2026-04-23T15:04:08+00:00",
      "validation_status": "ok"
    }

Only the refresh_token is stored. Access tokens are exchanged on demand
and held in a process-local cache for _ACCESS_TOKEN_TTL_SECONDS (50 min),
so every caller that asks within the window hits RAM, not Google.

Invariants:
  1. tenant_id + provider both pass the safe-slug regex before any I/O
  2. Files are chmod 0600 on POSIX (best-effort on Windows)
  3. Atomic writes via tmp + os.replace so a crash never leaves a partial file
"""

from __future__ import annotations

import json
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from . import heartbeat_store


_SAFE_PROVIDER = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")

# Pattern B providers - paste-credentials (API keys, App Passwords) that
# the client owns and whose vendor doesn't offer OAuth. Whitelisted so a
# buggy caller can't accidentally store a free-form record under any slug.
PASTE_PROVIDERS: frozenset[str] = frozenset({
    "gmail_app_password",
    "wordpress",
    "connecteam",
    "vapi",
    "twilio_paste",
    "brightlocal",
    "airtable",
    "ghl",
    "hubspot",
    "pipedrive",
})

# Google access tokens live 3600s. Cache for 50 min so the next refresh
# happens before the token expires, not at the cliff.
_ACCESS_TOKEN_TTL_SECONDS = 3000

# Process-local cache. Key: (tenant_id, provider). Value: (token, epoch_expiry).
_access_token_cache: dict[tuple[str, str], tuple[str, float]] = {}


class CredentialError(ValueError):
    """Something about the stored credential is wrong, missing, or unsupported."""


class ProviderExchangeError(RuntimeError):
    """The vendor's OAuth token endpoint rejected our refresh request."""


def _credentials_root(tenant_id: str) -> Path:
    return heartbeat_store.tenant_root(tenant_id) / "credentials"


def _validate_provider(provider: str) -> None:
    if not _SAFE_PROVIDER.match(provider or ""):
        raise CredentialError("invalid provider slug")


def store(
    tenant_id: str,
    provider: str,
    *,
    refresh_token: str,
    scopes: list[str] | None = None,
    validation_status: str = "pending",
) -> Path:
    """Atomically persist a refresh token. Overwrites any existing file."""
    _validate_provider(provider)
    if not refresh_token:
        raise CredentialError("refresh_token is required")
    root = _credentials_root(tenant_id)
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"{provider}.json"
    now = datetime.now(timezone.utc)
    payload = {
        "provider": provider,
        "refresh_token": refresh_token,
        "scopes": list(scopes) if scopes else [],
        "connected_at": now.isoformat(),
        "last_validated_at": None,
        "validation_status": validation_status,
    }
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    os.replace(tmp, path)
    try:
        os.chmod(path, 0o600)
    except (PermissionError, NotImplementedError, OSError):
        # Windows / some FUSE filesystems don't honor chmod. Prod is Linux
        # where this succeeds; dev on Windows is fine with default ACLs.
        pass
    _access_token_cache.pop((tenant_id, provider), None)
    return path


def store_paste(
    tenant_id: str,
    provider: str,
    fields: dict[str, Any],
    *,
    validation_status: str = "pending",
) -> Path:
    """Atomically persist a Pattern B paste credential (API key / App Password).

    Unlike `store()` which is OAuth-shaped, this accepts a generic field
    dict (e.g. {"email_address": "...", "app_password": "..."}). Provider
    must be on PASTE_PROVIDERS so we never accept arbitrary shapes by
    accident. Atomic write + chmod 600 mirrors the OAuth path.
    """
    _validate_provider(provider)
    if provider not in PASTE_PROVIDERS:
        raise CredentialError(
            f"provider {provider!r} not on PASTE_PROVIDERS; "
            f"add it explicitly to allow paste-credential storage"
        )
    if not isinstance(fields, dict) or not fields:
        raise CredentialError("fields must be a non-empty dict")

    root = _credentials_root(tenant_id)
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"{provider}.json"
    now = datetime.now(timezone.utc)
    payload: dict[str, Any] = {
        "provider": provider,
        "auth_kind": "paste",
        "connected_at": now.isoformat(),
        "last_validated_at": None,
        "validation_status": validation_status,
    }
    # Caller fields go LAST so the caller can't override the bookkeeping
    # fields (provider/auth_kind/connected_at) - any conflict is overwritten
    # back. Note: scopes are intentionally NOT inherited here; paste creds
    # don't have OAuth scopes.
    for k, v in fields.items():
        if k in {"provider", "auth_kind", "connected_at"}:
            continue
        payload[k] = v

    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    os.replace(tmp, path)
    try:
        os.chmod(path, 0o600)
    except (PermissionError, NotImplementedError, OSError):
        pass
    return path


def load(tenant_id: str, provider: str) -> dict[str, Any] | None:
    """Return the stored credential dict, or None if nothing on disk."""
    _validate_provider(provider)
    try:
        root = _credentials_root(tenant_id)
    except heartbeat_store.HeartbeatError:
        return None
    path = root / f"{provider}.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def granted_scopes(tenant_id: str, provider: str) -> list[str]:
    """Return the OAuth scopes actually granted in the stored credential."""
    cred = load(tenant_id, provider)
    if not cred:
        return []
    raw = cred.get("scopes") or []
    return [str(s) for s in raw if s]


def has_scope(tenant_id: str, provider: str, required: str) -> bool:
    """True when the stored credential was granted `required` at consent time.

    Google often emits broader scopes than requested (analytics.edit implies
    analytics.readonly access). We match exact scope URLs only; callers that
    need fuzzy matching should compute it themselves.
    """
    if not required:
        return True
    return required in granted_scopes(tenant_id, provider)


def list_connected(tenant_id: str) -> list[str]:
    """Provider slugs that have a credential file, sorted alphabetically."""
    try:
        root = _credentials_root(tenant_id)
    except heartbeat_store.HeartbeatError:
        return []
    if not root.exists():
        return []
    names: list[str] = []
    for path in root.glob("*.json"):
        stem = path.stem
        if _SAFE_PROVIDER.match(stem):
            names.append(stem)
    return sorted(names)


def mark_validated(tenant_id: str, provider: str, status: str) -> bool:
    """Record a validation probe outcome on the stored credential.

    status is conventionally 'ok', 'broken', or 'stale'. Returns True if
    a file was updated, False if no credential existed.
    """
    cred = load(tenant_id, provider)
    if cred is None:
        return False
    cred["last_validated_at"] = datetime.now(timezone.utc).isoformat()
    cred["validation_status"] = status
    path = _credentials_root(tenant_id) / f"{provider}.json"
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(cred, indent=2), encoding="utf-8")
    os.replace(tmp, path)
    return True


def delete(tenant_id: str, provider: str) -> bool:
    """Remove a stored credential. Returns True if a file was deleted."""
    _validate_provider(provider)
    try:
        root = _credentials_root(tenant_id)
    except heartbeat_store.HeartbeatError:
        return False
    path = root / f"{provider}.json"
    if not path.exists():
        return False
    path.unlink()
    _access_token_cache.pop((tenant_id, provider), None)
    return True


def access_token(tenant_id: str, provider: str) -> str:
    """Return a fresh access token, exchanging the refresh token if needed.

    Caches per (tenant_id, provider) for _ACCESS_TOKEN_TTL_SECONDS.

    Raises:
        CredentialError: no stored refresh token, or provider has no exchange
        ProviderExchangeError: vendor rejected the refresh (revoked, scope drift, etc.)
    """
    _validate_provider(provider)
    key = (tenant_id, provider)
    now_epoch = time.time()
    cached = _access_token_cache.get(key)
    if cached and cached[1] > now_epoch:
        return cached[0]
    cred = load(tenant_id, provider)
    if cred is None:
        raise CredentialError(f"no stored credential for {provider}")
    refresh = cred.get("refresh_token")
    if not refresh:
        raise CredentialError(f"stored credential for {provider} has no refresh_token")
    if provider == "google":
        token = _exchange_google_refresh(refresh)
    else:
        raise CredentialError(f"no exchange implemented for provider {provider}")
    _access_token_cache[key] = (token, now_epoch + _ACCESS_TOKEN_TTL_SECONDS)
    return token


def _exchange_google_refresh(refresh_token: str) -> str:
    """Trade a Google refresh token for a fresh access token.

    Monkeypatched in tests; the only function here that touches the network.
    """
    client_id = os.getenv("GOOGLE_OAUTH_CLIENT_ID")
    client_secret = os.getenv("GOOGLE_OAUTH_CLIENT_SECRET")
    if not client_id or not client_secret:
        raise CredentialError("GOOGLE_OAUTH_CLIENT_ID / GOOGLE_OAUTH_CLIENT_SECRET not configured")
    try:
        response = httpx.post(
            "https://oauth2.googleapis.com/token",
            data={
                "client_id": client_id,
                "client_secret": client_secret,
                "refresh_token": refresh_token,
                "grant_type": "refresh_token",
            },
            timeout=10.0,
        )
    except httpx.HTTPError as exc:
        raise ProviderExchangeError(f"google token endpoint unreachable: {exc}") from exc
    if response.status_code != 200:
        raise ProviderExchangeError(
            f"google rejected refresh_token: HTTP {response.status_code}"
        )
    body = response.json()
    token = body.get("access_token")
    if not token:
        raise ProviderExchangeError("google response missing access_token")
    return token


def clear_access_token_cache() -> None:
    """Test helper: drop the in-process access-token cache."""
    _access_token_cache.clear()
