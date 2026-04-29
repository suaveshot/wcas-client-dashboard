---
surface: / (public homepage at dashboard.westcoastautomationsolutions.com)
audited: 2026-04-28
auditor: Larry (Claude Opus 4.7)
methodology: Phase 0 framework (function check + UX cleanup, no architecture changes)
---

# Phase 0 audit - / (public homepage)

## Summary

The public homepage is the entry point for any prospect or client typing the dashboard URL directly. Sam's Apr 27 edits (committed as part of Apr 28 Part A `020bc52`) cleaned it up well: "Try as a judge" CTA removed, "14 → 7 automation roles", "Hackathon build → Live · April 2026" footer pill. Single conversion path is "Sign in with your email" → `/auth/login`. Brand chrome matches the rest of WCAS (DM Serif Display + DM Sans, FBFAF7 cream, accent orange).

The page is small (181 lines, all inline CSS + HTML), well-structured, and accurate to current state. The audit findings are mostly hygiene around indexing posture, social-share previews, and one cross-page inconsistency (different contact emails on different pages).

The one structural question worth flagging: **should this page be indexable?** Today it has no `<meta name="robots" content="noindex">`. The page reads as marketing-lite ("Your automation agency, in one place"), but it's not the WCAS marketing site (that lives at `westcoastautomationsolutions.com`). The dashboard subdomain's homepage in Google search results would compete with the real marketing site for the same keywords. Recommendation: `noindex` and let the marketing site own SEO; this page is the auth portal.

10 findings: 4 must-fix-before-tenant-2, 5 nice-to-have-pre-launch, 1 defer-to-Phase-2.

### Top 3 priorities (1-line each)

1. **Add `<meta name="robots" content="noindex">`.** This is the auth portal, not the marketing site. Don't compete with `westcoastautomationsolutions.com` for the same SERPs.
2. **Add Open Graph + Twitter Card meta tags.** Anyone sharing the dashboard URL today gets a default unbranded preview. One social-share screenshot fixes 90% of the issue.
3. **Reconcile contact emails.** Page uses `info@westcoastautomationsolutions.com`; legal pages and login error path use `sam@westcoastautomationsolutions.com`. Pick one.

---

## Findings

### F1. No `<meta name="robots" content="noindex">` - dashboard auth portal indexable - must-fix-before-tenant-2

- **Function:** Search engines should not index the auth portal of a private SaaS.
- **Today:** `static/index.html:1-15` head has only `meta charset`, viewport, title, description, theme-color. No robots directive.
- **Gap:** Two-pronged.
  - SEO confusion: Google indexes `dashboard.westcoastautomationsolutions.com` for "WCAS automation" queries, competing with the real marketing site at the apex domain.
  - Trust: a tenant-2 prospect Googling for the marketing site might land on the dashboard's auth portal, see "Sign in with your email" first, and wonder if they're at the right place.
- **Smallest fix:** Add `<meta name="robots" content="noindex,follow">` (follow lets Google still trust outbound links to the marketing site).
- **Estimated effort:** 0.05 day.

### F2. No Open Graph or Twitter Card meta tags - social-share previews unbranded - must-fix-before-tenant-2

- **Function:** Anyone sharing the dashboard URL on Slack, iMessage, LinkedIn, etc. sees a branded preview card.
- **Today:** No `og:title`, `og:description`, `og:image`, `og:url`, `twitter:card` tags. Default preview falls back to the page title + first paragraph + nothing for the image.
- **Gap:** Sam pastes the URL in a sales conversation; the unfurl looks generic. Compare to the WCAS marketing site (`westcoastautomationsolutions.com`) which has a branded OG image per Sam's preference (`feedback_client_site_og_image.md`).
- **Smallest fix:** Generate a 1200x630 OG image with "Sign in to your dashboard" + WCAS logo. Add the standard 5-7 OG/Twitter meta tags. Reuse the OG-image build script at `WC Solns/Website/scripts/build_og_image.py`.
- **Estimated effort:** 0.5 day.

### F3. Contact email inconsistency across pages - must-fix-before-tenant-2

- **Function:** Sam should monitor one inbox for tenant-2 questions.
- **Today:**
  - `static/index.html:170` directs prospects to `info@westcoastautomationsolutions.com`
  - `templates/auth/login.html:40` directs sign-in-help to `sam@westcoastautomationsolutions.com`
  - `templates/legal/terms.html:44` and `templates/legal/privacy.html:48` use `sam@westcoastautomationsolutions.com` for legal questions
  - `templates/emails/magic_link.html:60` uses `sam@westcoastautomationsolutions.com`
- **Gap:** Tenant 2 emails `info@...` from the homepage; Sam reads `sam@...`; question goes unread until Sam's filters surface it. Or vice versa.
- **Smallest fix:** Pick one canonical address. Recommend `sam@westcoastautomationsolutions.com` (matches everywhere else, personal-touch consistent with WCAS positioning). Update homepage line 170 to use it. Verify the inbox is monitored.
- **Estimated effort:** 0.1 day.

### F4. "Source on GitHub" link in footer of public auth portal - confused signal for prospects - must-fix-before-tenant-2

- **Function:** Transparency about the dashboard codebase.
- **Today:** `static/index.html:174` has `<a href="https://github.com/suaveshot/wcas-client-dashboard">Source on GitHub</a>` in the footer.
- **Gap:** Two-pronged.
  - Tenant-2 prospect sees "Source on GitHub" near "Terms / Privacy" and might wonder if their data lives in a public repo. (It doesn't; the repo is dashboard code only, no client data.)
  - The link is also a hint to attackers that the codebase is public. Not a security flaw (open source is fine), but worth Sam's deliberate choice rather than a footer afterthought.
- **Smallest fix:** Either remove the link, OR rephrase to "Built in public — view the dashboard source on GitHub" so the framing is intentional rather than unexplained. Keep the actual link if Sam values the transparency signal.
- **Estimated effort:** 0.1 day. Recommend: keep the link, reframe the copy.

### F5. No favicon - browser tabs show default globe icon - nice-to-have-pre-launch

- **Function:** Browser tab shows a recognizable WCAS mark.
- **Today:** No `<link rel="icon" href="...">` in the head. Browsers fall back to the default globe.
- **Gap:** Mild brand polish gap. Owner with 6 tabs open can't visually find the dashboard.
- **Smallest fix:** Add a 32x32 + 192x192 favicon SVG/PNG to `/static/favicon.ico` (or .svg) and link from each page's `<head>`. Reuse the WCAS mark from the brand kit.
- **Estimated effort:** 0.25 day. Should land on every page template, not just the homepage — single fix across the app.

### F6. No structured-data (JSON-LD `Organization` schema) - nice-to-have-pre-launch

- **Function:** Search engines understand the brand entity for rich snippets.
- **Today:** No JSON-LD block.
- **Gap:** Marginal SEO. Mostly relevant if F1 (noindex) is rejected and the page IS indexed.
- **Smallest fix:** ~10-line `<script type="application/ld+json">` block with Organization name, URL, logo, contact. Skip if F1 lands.
- **Estimated effort:** 0.25 day. Likely defer if F1 lands.

### F7. "0 agency retainers" pill is jargon despite "owner to owner, no jargon" lead - nice-to-have-pre-launch

- **Function:** Pills express the value-prop in plain language.
- **Today:** `static/index.html:166` has "0 agency retainers" - which references the SaaS-vs-agency framing that's clear to Sam but opaque to a non-marketing owner ("a what?").
- **Gap:** Mild. Lead reads "Owner to owner, no jargon" then four lines down hits a B2B-SaaS-ism.
- **Smallest fix:** Reframe as something concrete: "0 long-term contracts" or "0 setup fees over $X" or just drop the pill and let the other three carry the value.
- **Estimated effort:** 0.1 day.

### F8. No `<link rel="canonical">` - SEO hygiene - nice-to-have-pre-launch

- **Function:** Tells search engines which URL is canonical when the page is reachable via multiple paths.
- **Today:** No canonical link. Page reachable as `/`, possibly `/index.html` from some referrers.
- **Gap:** Minor. Resolved if F1 (noindex) lands.
- **Smallest fix:** `<link rel="canonical" href="https://dashboard.westcoastautomationsolutions.com/">` — only relevant if F1 is rejected.
- **Estimated effort:** 0.05 day or skip.

### F9. Inline CSS on every page render - cache miss vs. external stylesheet - nice-to-have-pre-launch

- **Function:** Performance.
- **Today:** `static/index.html:16-148` has 132 lines of inline CSS. Every page load re-downloads it. Compare to other pages that link to `/static/styles.css?v=...`.
- **Gap:** Mild. Index page is small (~5KB extra). Larger benefit is keeping the design tokens centralized.
- **Smallest fix:** Move `.home*` classes to `styles.css` with a `?v=` cache-buster. Eliminate the duplicated inline `<style>` block.
- **Estimated effort:** 0.25 day.

### F10. No future "marketing-lite" expansion (testimonials, screenshots) - defer-to-Phase-2

- **Function:** Prospect-facing landing with social proof.
- **Today:** Page is the auth portal, not marketing. Marketing lives at `westcoastautomationsolutions.com`.
- **Gap:** Out of scope; clear separation of concerns is right.
- **Smallest fix:** Defer. If Sam ever wants this page to do double-duty as marketing, that's a Phase 2 decision.
- **Estimated effort:** N/A.

---

## Methodology checks (per parent plan B1)

| Check | Result |
|---|---|
| Function check | Page renders. Sam's Apr 27 edits applied (verified during Apr 28 Part A deploy). 7-roles count + Live footer + single CTA all match current state. |
| UX gap | F1 (indexable), F2 (no OG), F3 (contact-email inconsistency), F4 ("Source on GitHub" without framing) are the visible-to-prospect ones. |
| Smallest fix | All findings sized in fractions of a day. Total: ~1.6 days for must-fix + nice-to-have. |
| Phase 1 priority bucket | Assigned per finding. |
| Composer empty state | N/A - static landing. |
| Mobile pass | `clamp()` on padding + h1 + lead. `@media (max-width: 640px)` rule for pill column. Looks responsive; should browser-test in Phase 1D. |
| Confused-state recovery | N/A - read-only entry. |
| Demo gate | This page is public (correct - it's the auth portal). |
| Sidebar consistency | N/A - landing-style chrome. |

---

## Phase 1D effort total

| Bucket | Effort |
|---|---|
| must-fix (F1-F4) | ~0.75 day |
| nice-to-have (F5-F9) | ~0.65 day |
| defer (F10) | N/A |
| **Total in scope** | **~1.4 days** for Phase 1D |

Land alongside auth/legal/healthz/demo mechanical fixes in week 1 of Phase 1D. With F5 (favicon) shared across every template and F2 (OG image) reused for tenant-specific dashboards, this is high-leverage cleanup.

## Cumulative Phase 0 progress

| # | Surface | Status | Findings | Phase 1D effort |
|---|---|---|---|---|
| 1 | /activate | done | 10 (3+5+2) | ~4 days |
| 2 | /dashboard | done | 12 (3+6+3) | ~2.5-3.5 days |
| 3 | /roles | done | 8 (2+5+1) | ~1.5 days |
| 4 | /roles/{slug} | done | 11 (3+6+2) | ~2 days |
| 5 | /approvals | done | 13 (3+7+3) | ~5.5 days |
| 6 | /recommendations | done | 13 (2+8+3) | ~4-5 days |
| 7 | /goals | done | 13 (4+7+2) | ~6.5 days |
| 8 | /settings | done | 12 (5+5+2) | ~6 days |
| 9 | /activity | done | 13 (5+6+2) | ~5.25 days |
| 10 | /auth/login + magic-link | done | 13 (5+6+2) | ~3.15 days |
| 11 | /legal/terms + /legal/privacy | done | 14 (6+6+2) | ~3.7 days |
| 12 | /healthz | done | 7 (4+2+1) | ~2.1 days |
| 13 | /demo/* | done | 9 (3+4+2) | ~1.4 days |
| 14 | / (public homepage) | done | 10 (4+5+1) | ~1.4 days |
| 15 | 401/403/404/500 error templates | next | - | - |

**Running totals:** 158 findings, ~48-50 days Phase 1D work mapped. With shared-dispatcher dedupe + shared prefs-partial + shared favicon-and-OG: ~38-40 days.

## Cross-cutting themes (cumulative, updated)

1-14. (See prior audits.)
15. **NEW: Single-shared-fix opportunities multiply** - Phase 1D will benefit from the sequence: shared dispatcher (4 surfaces) + shared prefs partial (2 surfaces) + favicon (every template) + OG image (every public template). What looked like 14 separate audits collapses into roughly 8-10 actual Phase 1D work-streams once shared fixes are mapped.

---

## Next surface to audit

**401/403/404/500 error templates** - the last surface in the parent plan's Phase 0 table. Need to check:
- Do custom error templates exist, or does FastAPI's default JSON error response leak?
- Brand consistency (cream + DM Serif if templated)
- Copy tone (matches the "owner to owner, no jargon" lead from public homepage)
- Auth-redirect on 401 (should land on /auth/login with `?next=`)
- 500-page leaks (no stack traces or library names visible to user)
- Demo-gate noindex on error pages
- Mobile pass
