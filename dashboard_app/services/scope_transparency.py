"""
Maps OAuth scope URLs to plain-English "what WCAS will do / will NOT do"
bullets. Drives the pre-OAuth transparency screen so owners see the grant
in their language, not Google's generic consent phrasing.

Every scope we actually request in `api/oauth.py` has an entry here. If a
scope is requested without an entry, the fallback message is deliberately
conservative ("we will read and write data in this service") so owners are
always warned, never surprised.

Keep this file as the single source of truth that the consent screen
renders from. When Meta / Twilio OAuth lands, add provider branches here.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ScopePromise:
    """One entry per scope URL, shown to the owner before they click through."""

    # What WCAS will do with the grant (one line each, plain English).
    will_do: tuple[str, ...]
    # What WCAS will NOT do (hard commitments).
    will_not: tuple[str, ...] = ()


# ---------------------------------------------------------------------------
# Google Workspace scopes
# ---------------------------------------------------------------------------

_GOOGLE: dict[str, ScopePromise] = {
    "openid": ScopePromise(
        will_do=("Confirm your Google identity so we know which account connected.",),
    ),
    "https://www.googleapis.com/auth/userinfo.email": ScopePromise(
        will_do=("Read the email address on the Google account you connect.",),
    ),
    "https://www.googleapis.com/auth/userinfo.profile": ScopePromise(
        will_do=("Read your Google name + photo to personalize the dashboard.",),
    ),
    "https://www.googleapis.com/auth/business.manage": ScopePromise(
        will_do=(
            "Read your Google Business Profile to generate review replies.",
            "Draft GBP posts (you approve each one before it publishes).",
        ),
        will_not=(
            "Change your business info or hours without your approval.",
            "Publish anything you haven't signed off on.",
        ),
    ),
    "https://www.googleapis.com/auth/analytics.edit": ScopePromise(
        will_do=(
            "Read your Google Analytics traffic + conversion data for monthly reports.",
            "Create an analytics property for your site if you don't have one yet.",
        ),
        will_not=("Delete any existing data or change your historical reports.",),
    ),
    "https://www.googleapis.com/auth/webmasters": ScopePromise(
        will_do=(
            "Read search performance data from Google Search Console.",
            "Register your domain in Search Console if it isn't already.",
        ),
        will_not=("Remove verified properties you already have connected.",),
    ),
    "https://www.googleapis.com/auth/gmail.modify": ScopePromise(
        will_do=(
            "Read inbound client emails to draft replies in your voice.",
            "Save drafts into your Gmail for your review + send.",
        ),
        will_not=(
            "Send any email without your review + send click.",
            "Read personal emails (filtered to business keywords only).",
            "Delete or permanently archive any email.",
        ),
    ),
    "https://www.googleapis.com/auth/calendar": ScopePromise(
        will_do=("Read your calendar to schedule client touchpoints intelligently.",),
        will_not=("Create or cancel meetings without your approval.",),
    ),
}


# ---------------------------------------------------------------------------
# Meta (Facebook + Instagram) scopes
# ---------------------------------------------------------------------------

_META: dict[str, ScopePromise] = {
    "pages_show_list": ScopePromise(
        will_do=("List the Facebook Pages you manage so you can pick which one to connect.",),
    ),
    "pages_manage_posts": ScopePromise(
        will_do=("Draft Facebook posts for your review + approval.",),
        will_not=("Publish anything automatically without your approval.",),
    ),
    "pages_read_engagement": ScopePromise(
        will_do=("Read post performance metrics so the dashboard can report on what's working.",),
    ),
    "instagram_basic": ScopePromise(
        will_do=("Read your Instagram business account profile + recent posts.",),
    ),
    "instagram_content_publish": ScopePromise(
        will_do=("Draft Instagram posts for your review + approval.",),
        will_not=("Publish anything automatically without your approval.",),
    ),
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


_PROVIDERS: dict[str, dict[str, ScopePromise]] = {
    "google": _GOOGLE,
    "meta": _META,
}


_FALLBACK = ScopePromise(
    will_do=(
        "Read and write data in this service on your behalf to run the automations "
        "you approved during onboarding.",
    ),
    will_not=(
        "Publish anything to your audience without your approval.",
        "Share your data with any third party.",
    ),
)


def promises_for(provider: str, scopes: list[str]) -> tuple[list[str], list[str]]:
    """Return (will_do_lines, will_not_lines) for the provider's requested scopes.

    De-duplicates line text so two scopes promising the same thing don't
    double up in the UI. Always appends a universal 'will NOT' promise so
    the no-go list never comes up empty.
    """
    registry = _PROVIDERS.get(provider.lower(), {})
    will_do: list[str] = []
    will_not: list[str] = []

    for scope in scopes:
        entry = registry.get(scope) or _FALLBACK
        for line in entry.will_do:
            if line not in will_do:
                will_do.append(line)
        for line in entry.will_not:
            if line not in will_not:
                will_not.append(line)

    universal_no = "Share your data with any third party."
    if universal_no not in will_not:
        will_not.append(universal_no)
    universal_no2 = "Keep your data after you cancel - we export + delete on request."
    if universal_no2 not in will_not:
        will_not.append(universal_no2)

    return will_do, will_not


def provider_display_name(provider: str) -> str:
    """Human-friendly provider name for the consent screen heading."""
    return {
        "google": "Google",
        "meta": "Facebook + Instagram",
    }.get(provider.lower(), provider.title())
