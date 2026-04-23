"""
Per-tenant knowledge base (ADR-006).

Each tenant has a small set of canonical markdown files at:
    /opt/wc-solns/<tenant_id>/kb/<section>.md

The Activation Orchestrator writes to these as it collects facts
from the client. Every downstream Opus surface (voice agent,
chatbot, email drafts, QBR narratives, review replies, blog posts)
reads from the same KB so voice + facts stay consistent across
every channel.

Sections are strictly whitelisted. An unknown section is a caller
bug, not a gap we silently fill.

    company          -> NAP, hours, categories, timezone
    services         -> what the business sells, in the owner's words
    voice            -> tone + style samples pulled from their own site
    policies         -> warranty, refund, cancellation, after-hours
    pricing          -> rate card, minimum charge, estimate policy
    faq              -> common customer questions + canonical answers
    known_contacts   -> recurring callers the owner wants recognized
"""

from __future__ import annotations

import os
import re
from datetime import datetime, timezone
from pathlib import Path

from . import heartbeat_store


SECTIONS: frozenset[str] = frozenset({
    "company",
    "services",
    "voice",
    "policies",
    "pricing",
    "faq",
    "known_contacts",
})

_SAFE_SECTION = re.compile(r"^[a-z][a-z_]{0,31}$")


class KbError(ValueError):
    """Invalid tenant, section, or attempted write outside the KB root."""


def _kb_root(tenant_id: str) -> Path:
    return heartbeat_store.tenant_root(tenant_id) / "kb"


def _validate_section(section: str) -> None:
    if not _SAFE_SECTION.match(section or ""):
        raise KbError(f"invalid section slug: {section!r}")
    if section not in SECTIONS:
        raise KbError(f"unknown section: {section!r} (expected one of {sorted(SECTIONS)})")


def write_section(tenant_id: str, section: str, content: str) -> Path:
    """Atomically write markdown content to a KB section. Overwrites."""
    _validate_section(section)
    root = _kb_root(tenant_id)
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"{section}.md"
    header = (
        f"<!-- updated: {datetime.now(timezone.utc).isoformat()} -->\n"
        f"# {section.replace('_', ' ').title()}\n\n"
    )
    tmp = path.with_suffix(".md.tmp")
    tmp.write_text(header + (content or "").rstrip() + "\n", encoding="utf-8")
    os.replace(tmp, path)
    return path


def read_section(tenant_id: str, section: str) -> str | None:
    """Return section content (including the generated header) or None."""
    _validate_section(section)
    try:
        root = _kb_root(tenant_id)
    except heartbeat_store.HeartbeatError:
        return None
    path = root / f"{section}.md"
    if not path.exists():
        return None
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return None


def list_sections(tenant_id: str) -> list[str]:
    """Names of sections that have content on disk, sorted alphabetically."""
    try:
        root = _kb_root(tenant_id)
    except heartbeat_store.HeartbeatError:
        return []
    if not root.exists():
        return []
    out: list[str] = []
    for path in root.glob("*.md"):
        stem = path.stem
        if stem in SECTIONS:
            out.append(stem)
    return sorted(out)


def delete_section(tenant_id: str, section: str) -> bool:
    """Drop a section file. Returns True if a file was deleted."""
    _validate_section(section)
    try:
        root = _kb_root(tenant_id)
    except heartbeat_store.HeartbeatError:
        return False
    path = root / f"{section}.md"
    if not path.exists():
        return False
    path.unlink()
    return True
