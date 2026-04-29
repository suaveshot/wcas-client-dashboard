"""Platform-master credential loader.

The "Pattern C" providers from the plan (BrightLocal, Twilio master,
GHL agency, Hostinger, Airtable workspace) belong to WCAS, not the
tenant. Their credentials live in a single root that NO tenant code
path is allowed to read:

    /opt/wc-solns/_platform/
        brightlocal/master.json   <-  Sam's master BrightLocal API key
        twilio/master.json        <-  account SID + auth token
        ghl/agency.json           <-  agency API key + agency id
        airtable/workspace.json   <-  PAT + workspace id
        hostinger/api.json        <-  API token

Only services that operate on Sam's behalf (e.g. seo_recommender ->
brightlocal_master) read this. TenantContext + every pipeline must
go through services.credentials for tenant-owned creds and never
reach in here. Tests pin that boundary.

The directory is provisioned out-of-band (Sam's deploy step) with
chmod 600 / root-owned files. This module just gives services a
consistent way to read them.
"""

from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

DEFAULT_PLATFORM_ROOT = "/opt/wc-solns/_platform"

# Slug rule mirrors the tenant slug rule for safety: lowercase + digits +
# underscore + hyphen, no traversal sequences, no dots. Provider names
# come from a small whitelist below, so this is mostly defense-in-depth.
_SAFE_PROVIDER = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")

# Whitelist of provider names that have a master credential. Anything
# outside this list is rejected by load_master so a buggy caller can't
# accidentally read /etc/passwd via "../../../etc/passwd".
ALLOWED_PROVIDERS: frozenset[str] = frozenset({
    "brightlocal",
    "twilio",
    "ghl",
    "airtable",
    "hostinger",
})

# Per-provider expected file name. master.json is the default; some have
# a more specific name to make grep-ability obvious in the file system.
_PROVIDER_FILES: dict[str, str] = {
    "brightlocal": "master.json",
    "twilio": "master.json",
    "ghl": "agency.json",
    "airtable": "workspace.json",
    "hostinger": "api.json",
}


class PlatformMasterError(LookupError):
    """Raised when the caller asks for a provider that isn't whitelisted
    or otherwise violates the platform-master contract."""


def platform_root() -> Path:
    """Return the platform-master root directory.

    Override via PLATFORM_ROOT env var (used in tests + dev). Production
    uses the OS default `/opt/wc-solns/_platform`. Does NOT create the
    directory - that's Sam's deploy step (so the chmod 600 + root-owned
    invariant is preserved).
    """
    return Path(os.environ.get("PLATFORM_ROOT") or DEFAULT_PLATFORM_ROOT)


def load_master(provider: str) -> dict[str, Any] | None:
    """Read the master credential file for a Pattern C provider.

    Returns:
      - the parsed JSON dict on success
      - None if the file simply doesn't exist (Sam hasn't provisioned
        this provider yet)

    Raises:
      - PlatformMasterError if the provider isn't on the whitelist or
        the slug fails the safe regex (defense-in-depth against
        path-traversal attempts).
    """
    if not isinstance(provider, str) or not _SAFE_PROVIDER.match(provider):
        raise PlatformMasterError(f"invalid provider slug: {provider!r}")
    if provider not in ALLOWED_PROVIDERS:
        raise PlatformMasterError(
            f"provider {provider!r} not in ALLOWED_PROVIDERS; "
            f"add it explicitly before reading platform creds"
        )

    filename = _PROVIDER_FILES.get(provider, "master.json")
    path = platform_root() / provider / filename
    if not path.exists():
        return None

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        log.warning("platform_master load failed for %s: %s", provider, exc)
        return None

    return data if isinstance(data, dict) else None


def is_provisioned(provider: str) -> bool:
    """Cheap "is there a master file at all?" check. Useful for surfacing
    "provision X first" errors in admin UI without exposing the contents."""
    try:
        return load_master(provider) is not None
    except PlatformMasterError:
        return False


__all__ = [
    "ALLOWED_PROVIDERS",
    "DEFAULT_PLATFORM_ROOT",
    "PlatformMasterError",
    "is_provisioned",
    "load_master",
    "platform_root",
]
