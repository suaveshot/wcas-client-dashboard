"""Per-tenant activation roster (the role slugs the ring grid renders).

Hackathon demo roster = the 9 generic WCAS pipelines every client gets,
rendered as a clean 3x3 grid. AP-specific vertical pipelines (patrol,
harbor_lights, guard_compliance, weekly_update, watchdog) are deliberately
omitted from the activation surface since they're security-only and would
mislead a generic judge. Post-hackathon we'll derive per tenant from a
`clients.json` template, but for the Apr 26 submission one list is
authoritative across `main.py`, `api/activation_chat.py`, and anything
else that renders the ring grid.
"""

from __future__ import annotations


ACTIVATION_ROSTER: list[dict[str, str]] = [
    {"slug": "gbp",            "name": "Google Business"},
    {"slug": "seo",            "name": "SEO"},
    {"slug": "reviews",        "name": "Reviews"},
    {"slug": "sales_pipeline", "name": "Sales Pipeline"},
    {"slug": "blog",           "name": "Blog Posts"},
    {"slug": "social",         "name": "Social Posts"},
    {"slug": "ads",            "name": "Ads"},
    {"slug": "chat_widget",    "name": "Chat Widget"},
    {"slug": "qbr",            "name": "QBR Generator"},
]


def role_slugs() -> list[str]:
    return [r["slug"] for r in ACTIVATION_ROSTER]
