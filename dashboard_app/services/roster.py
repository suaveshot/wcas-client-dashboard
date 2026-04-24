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


# `logo` keys map to vendor-branded inline SVGs rendered in the ring center.
# The `templates/activate.html` LOGOS dict is the source of truth for the
# actual SVG markup; these keys just pick which logo to show.
ACTIVATION_ROSTER: list[dict[str, str]] = [
    {"slug": "gbp",            "name": "Google Business", "logo": "google"},
    {"slug": "seo",            "name": "SEO",             "logo": "google_search_console"},
    {"slug": "reviews",        "name": "Reviews",         "logo": "google"},
    {"slug": "sales_pipeline", "name": "Sales Pipeline",  "logo": "ghl"},
    {"slug": "blog",           "name": "Blog Posts",      "logo": "wordpress"},
    {"slug": "social",         "name": "Social Posts",    "logo": "meta"},
    {"slug": "ads",            "name": "Ads",             "logo": "google_ads"},
    {"slug": "chat_widget",    "name": "Chat Widget",     "logo": "wcas"},
    {"slug": "qbr",            "name": "QBR Generator",   "logo": "wcas"},
]


def role_slugs() -> list[str]:
    return [r["slug"] for r in ACTIVATION_ROSTER]
