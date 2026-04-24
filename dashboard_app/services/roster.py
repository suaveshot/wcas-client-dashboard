"""Per-tenant activation roster (the 14 role slugs the ring grid renders).

For the hackathon demo this is hardcoded to AP's pipeline set; post-hackathon
it reads from a `clients.json` manifest per tenant. Keep the list here so
`main.py`, `api/activation_chat.py`, and anything else that renders the ring
grid all share one source of truth.
"""

from __future__ import annotations


ACTIVATION_ROSTER: list[dict[str, str]] = [
    {"slug": "gbp",              "name": "Google Business"},
    {"slug": "seo",              "name": "SEO"},
    {"slug": "reviews",          "name": "Reviews"},
    {"slug": "sales_pipeline",   "name": "Sales Pipeline"},
    {"slug": "blog",             "name": "Blog Posts"},
    {"slug": "social",           "name": "Social Posts"},
    {"slug": "ads",              "name": "Ads"},
    {"slug": "chat_widget",      "name": "Chat Widget"},
    {"slug": "qbr",              "name": "QBR Generator"},
    {"slug": "patrol",           "name": "Morning Reports"},
    {"slug": "harbor_lights",    "name": "HOA Parking"},
    {"slug": "guard_compliance", "name": "Guard Compliance"},
    {"slug": "weekly_update",    "name": "Weekly Update"},
    {"slug": "watchdog",         "name": "Watchdog"},
]


def role_slugs() -> list[str]:
    return [r["slug"] for r in ACTIVATION_ROSTER]
