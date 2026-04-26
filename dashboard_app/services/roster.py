"""Per-tenant activation roster (the role slugs the ring grid renders).

Hackathon demo roster (2026-04-24 pivot) = the 7 tenant-generic WCAS
automations that onboard to any client. Dropped from the earlier 9-slot
roster: `sales_pipeline` (AP-specific GHL follow-up logic), `ads` (no
per-client spend baseline yet + `feedback_ap_no_roi_calculator.md`), and
`qbr` (post-activation artifact, not an onboarding primitive). They
return to the roster once a tenant-generic version exists.

The architecture thesis: every one of these reads from the per-tenant KB
at runtime. Filling the KB during activation is the generalization
mechanism; each automation becomes client-specific the moment its voice,
services, policies, and pricing sections are filled.
"""

from __future__ import annotations


# `logo` keys map to vendor-branded inline SVGs rendered in the ring center.
# The `templates/activate.html` LOGOS dict is the source of truth for the
# actual SVG markup; these keys just pick which logo to show.
ACTIVATION_ROSTER: list[dict[str, str]] = [
    {"slug": "gbp",             "name": "Google Business", "logo": "google"},
    {"slug": "seo",             "name": "SEO Reports",     "logo": "google_search_console"},
    {"slug": "reviews",         "name": "Review Engine",   "logo": "google"},
    {"slug": "email_assistant", "name": "Email Assistant", "logo": "email_assistant"},
    {"slug": "chat_widget",     "name": "Chat Widget",     "logo": "chat_widget"},
    {"slug": "blog",            "name": "Blog Automation", "logo": "blog"},
    {"slug": "social",          "name": "Social Manager",  "logo": "meta"},
]


def role_slugs() -> list[str]:
    return [r["slug"] for r in ACTIVATION_ROSTER]
