"""WCAS automation catalog - the single source of truth for every
automation we offer.

Replaces the hardcoded 7-ring `roster.ACTIVATION_ROSTER` with a richer
data layer so:
  - Tier-default seeding works (Starter / Pro / Ultra each light up a
    different subset)
  - Add-ons can join after onboarding without code changes
  - Promo opt-ins (Phase 3F) can attach time-bounded entries
  - AP-only systems stay scoped (never offered to non-AP tenants)

The 22 entries below mirror the per-system audit in
~/.claude/plans/alright-larry-the-hackathon-kind-swing.md. They are
data only; the dashboard UI + provisioning code reads from this list.

Status conventions:
  shipped   - generic per-tenant version exists today
  beta      - generic version partial; AP runs it but tenant-ization gaps
  planned   - on the roadmap; tenant-generic version not built yet
  ap_only   - tenant_scope=="ap_only" (security vertical specific)
  internal  - WCAS-side tooling; never offered to tenants

Tier conventions (default_tiers):
  starter   - the simplest plan; gets the core 7
  pro       - adds add-ons commonly bought together
  ultra     - top tier; everything except AP-only systems
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable


VALID_STATUSES = frozenset({"shipped", "beta", "planned", "ap_only", "internal"})
VALID_TIERS = frozenset({"starter", "pro", "ultra"})
VALID_CATEGORIES = frozenset({"core", "add_on", "concierge", "internal"})
VALID_TENANT_SCOPES = frozenset({"any", "ap_only", "wcas_internal"})


@dataclass(frozen=True)
class Automation:
    """One automation in the catalog. Frozen so the registry can be
    safely shared across requests."""

    id: str
    name: str
    status: str
    default_tiers: tuple[str, ...]
    category: str
    description: str
    tenant_scope: str = "any"
    per_minute_billing: bool = False

    def __post_init__(self) -> None:
        if self.status not in VALID_STATUSES:
            raise ValueError(f"automation {self.id!r}: status {self.status!r} not in {VALID_STATUSES}")
        if self.category not in VALID_CATEGORIES:
            raise ValueError(f"automation {self.id!r}: category {self.category!r} not in {VALID_CATEGORIES}")
        if self.tenant_scope not in VALID_TENANT_SCOPES:
            raise ValueError(
                f"automation {self.id!r}: tenant_scope {self.tenant_scope!r} not in {VALID_TENANT_SCOPES}"
            )
        bad_tiers = set(self.default_tiers) - VALID_TIERS
        if bad_tiers:
            raise ValueError(f"automation {self.id!r}: bad tiers {bad_tiers}")


# Catalog. Order is render order in admin UI when sorted by category,
# but no UI relies on this ordering today.
_AUTOMATIONS: tuple[Automation, ...] = (
    # --- Core 7 (default for Starter+) ---
    Automation(
        id="gbp",
        name="Google Business Profile",
        status="shipped",
        default_tiers=("starter", "pro", "ultra"),
        category="core",
        description="Weekly 'What's New' posts in your voice; NAP audit on directory listings.",
    ),
    Automation(
        id="seo",
        name="SEO Reports",
        status="shipped",
        default_tiers=("starter", "pro", "ultra"),
        category="core",
        description="Weekly digest of GA4 traffic + GSC search performance, plain-language.",
    ),
    Automation(
        id="seo_recs",
        name="SEO Recommendations Engine",
        status="shipped",
        default_tiers=("pro", "ultra"),
        category="core",
        description="AI ranks specific website changes by traffic lift, in your voice.",
    ),
    Automation(
        id="reviews",
        name="Review Engine",
        status="shipped",
        default_tiers=("starter", "pro", "ultra"),
        category="core",
        description="Drafts replies to every Google review in your voice; queues for approval.",
    ),
    Automation(
        id="email_assistant",
        name="Email Assistant",
        status="shipped",
        default_tiers=("starter", "pro", "ultra"),
        category="core",
        description="Drafts reply to every inbound client email in your voice; you approve.",
    ),
    Automation(
        id="chat_widget",
        name="Chat Widget",
        status="planned",
        default_tiers=("starter", "pro", "ultra"),
        category="core",
        description="Site chat widget answering with your voice; logs every conversation.",
    ),
    Automation(
        id="blog",
        name="Blog Automation",
        status="planned",
        default_tiers=("pro", "ultra"),
        category="core",
        description="Bi-weekly blog drafts informed by SEO opportunity gaps.",
    ),
    Automation(
        id="social",
        name="Social Media Manager",
        status="planned",
        default_tiers=("pro", "ultra"),
        category="core",
        description="Tue/Thu/Sat posts to Facebook + Instagram; calendar-aware.",
    ),

    # --- Add-ons ---
    Automation(
        id="sales_autopilot",
        name="Sales Autopilot",
        status="planned",
        default_tiers=("ultra",),
        category="add_on",
        description="Cold outreach + adaptive follow-ups + proposal tracking.",
    ),
    Automation(
        id="voice_ai",
        name="Voice AI Agent",
        status="beta",
        default_tiers=(),
        category="add_on",
        per_minute_billing=True,
        description="AI receptionist that answers in your voice, takes messages, schedules calls.",
    ),
    Automation(
        id="qbo_sync",
        name="QuickBooks Sync",
        status="planned",
        default_tiers=("ultra",),
        category="add_on",
        description="Two-way sync between your CRM and QuickBooks Online.",
    ),
    Automation(
        id="google_ads_manager",
        name="Google Ads Manager",
        status="planned",
        default_tiers=(),
        category="add_on",
        description="Campaign management with 5-gate safety wall (gated, opt-in only).",
    ),
    Automation(
        id="clarity_optimizer",
        name="Clarity Ad Optimizer",
        status="planned",
        default_tiers=(),
        category="add_on",
        description="EQS-scored Clarity-to-Google-Ads feedback loop (gated, opt-in only).",
    ),
    Automation(
        id="missed_call_tracker",
        name="Missed Call Tracker",
        status="planned",
        default_tiers=("pro", "ultra"),
        category="add_on",
        description="Auto-text every missed call from your business number, log to CRM.",
    ),
    Automation(
        id="weekly_digest",
        name="Weekly Digest",
        status="planned",
        default_tiers=("starter", "pro", "ultra"),
        category="add_on",
        description="Friday recap email aggregating every automation's activity.",
    ),
    Automation(
        id="qbr_generator",
        name="QBR Generator",
        status="planned",
        default_tiers=("pro", "ultra"),
        category="add_on",
        description="Quarterly business review PDF aggregating outcomes across automations.",
    ),

    # --- Concierge sales (sold case-by-case, never a default ring) ---
    Automation(
        id="crm_hub",
        name="CRM Hub",
        status="planned",
        default_tiers=(),
        category="concierge",
        description="Airtable-based CRM (custom). Hybrid: client-owned or WCAS-workspace.",
    ),
    Automation(
        id="custom_reporting",
        name="Custom Reporting",
        status="planned",
        default_tiers=(),
        category="concierge",
        description="Bespoke reporting dashboard (sold per-engagement).",
    ),

    # --- Internal / WCAS-only ---
    Automation(
        id="status_dashboard",
        name="Status Dashboard",
        status="shipped",
        default_tiers=("starter", "pro", "ultra"),
        category="internal",
        tenant_scope="wcas_internal",
        description="This dashboard. Always present.",
    ),
    Automation(
        id="system_watchdog",
        name="System Watchdog",
        status="planned",
        default_tiers=(),
        category="internal",
        tenant_scope="wcas_internal",
        description="Monitors per-tenant pipeline health; alerts Sam on drift.",
    ),

    # --- AP-only (security vertical) ---
    Automation(
        id="daily_reports",
        name="Daily Patrol Reports",
        status="ap_only",
        default_tiers=(),
        category="add_on",
        tenant_scope="ap_only",
        description="Connecteam-driven security DAR pipeline (AP only).",
    ),
    Automation(
        id="guard_compliance",
        name="Guard Compliance",
        status="ap_only",
        default_tiers=(),
        category="add_on",
        tenant_scope="ap_only",
        description="BSIS license + cert expiry tracking (AP only).",
    ),
    Automation(
        id="incident_trends",
        name="Incident Trends",
        status="ap_only",
        default_tiers=(),
        category="add_on",
        tenant_scope="ap_only",
        description="Security incident analysis + executive reporting (AP only).",
    ),
)

_BY_ID: dict[str, Automation] = {a.id: a for a in _AUTOMATIONS}


# ---------------------------------------------------------------------------
# public API
# ---------------------------------------------------------------------------


def all() -> tuple[Automation, ...]:
    """Every automation in the catalog (immutable)."""
    return _AUTOMATIONS


def get(automation_id: str) -> Automation | None:
    """Lookup by id; None if unknown."""
    return _BY_ID.get(automation_id)


def exists(automation_id: str) -> bool:
    return automation_id in _BY_ID


def for_tier(tier: str) -> tuple[Automation, ...]:
    """Automations that are tier-defaults for the given tier slug.
    Excludes ap_only-scoped entries automatically."""
    if tier not in VALID_TIERS:
        return ()
    return tuple(
        a for a in _AUTOMATIONS
        if tier in a.default_tiers and a.tenant_scope != "ap_only"
    )


def visible_to(tenant_kind: str) -> tuple[Automation, ...]:
    """Automations a tenant of this kind is even allowed to see.

    tenant_kind:
      - "any" or "" -> everything except ap_only and wcas_internal
      - "ap"        -> includes ap_only
      - "wcas"      -> includes wcas_internal
    """
    if tenant_kind == "ap":
        return tuple(a for a in _AUTOMATIONS if a.tenant_scope != "wcas_internal")
    if tenant_kind == "wcas":
        return _AUTOMATIONS
    return tuple(a for a in _AUTOMATIONS if a.tenant_scope == "any")


def by_status(status: str) -> tuple[Automation, ...]:
    return tuple(a for a in _AUTOMATIONS if a.status == status)


def by_category(category: str) -> tuple[Automation, ...]:
    if category not in VALID_CATEGORIES:
        return ()
    return tuple(a for a in _AUTOMATIONS if a.category == category)


def ids() -> tuple[str, ...]:
    return tuple(a.id for a in _AUTOMATIONS)


def tier_default_ids(tier: str) -> tuple[str, ...]:
    return tuple(a.id for a in for_tier(tier))


def names_for(automation_ids: Iterable[str]) -> dict[str, str]:
    """Return {id: name} for each known id; unknown ids are skipped."""
    return {aid: _BY_ID[aid].name for aid in automation_ids if aid in _BY_ID}


__all__ = [
    "Automation",
    "VALID_STATUSES",
    "VALID_TIERS",
    "VALID_CATEGORIES",
    "VALID_TENANT_SCOPES",
    "all",
    "get",
    "exists",
    "for_tier",
    "visible_to",
    "by_status",
    "by_category",
    "ids",
    "tier_default_ids",
    "names_for",
]
