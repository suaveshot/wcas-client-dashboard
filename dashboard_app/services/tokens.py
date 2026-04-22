"""
Magic-link token service.

Tokens are high-entropy random strings. We never store the plaintext.
Airtable holds only the SHA-256 hash plus expiry + consumed flag,
so a leaked Airtable dump still can't log anyone in.

- Token: secrets.token_urlsafe(32) -> 256 bits of entropy
- Hash: SHA-256 hex (64 chars, constant-time compare on redeem)
- TTL: MAGIC_LINK_TTL_SECONDS env (default 3600 = 1 hour)
- Redeem: single-use; on successful verify we mark consumed=True
"""

import hashlib
import hmac
import os
import secrets
from datetime import datetime, timedelta, timezone


def generate_token() -> str:
    """Return a URL-safe token with 256 bits of entropy."""
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    """SHA-256 hex of the token. This is what Airtable stores."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def hashes_match(candidate_hash: str, stored_hash: str) -> bool:
    """Constant-time compare."""
    return hmac.compare_digest(candidate_hash, stored_hash)


def ttl_seconds() -> int:
    try:
        return int(os.getenv("MAGIC_LINK_TTL_SECONDS", "3600"))
    except ValueError:
        return 3600


def expiry_timestamp() -> str:
    """ISO-8601 UTC timestamp MAGIC_LINK_TTL_SECONDS from now."""
    expiry = datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds())
    return expiry.isoformat()


def is_expired(iso_expiry: str) -> bool:
    """True if the expiry timestamp is in the past. Malformed -> treat as expired."""
    try:
        expiry_dt = datetime.fromisoformat(iso_expiry)
    except (ValueError, TypeError):
        return True
    if expiry_dt.tzinfo is None:
        expiry_dt = expiry_dt.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) >= expiry_dt
