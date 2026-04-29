---
surfaces: /legal/terms (also /terms), /legal/privacy (also /privacy)
audited: 2026-04-28
auditor: Larry (Claude Opus 4.7)
methodology: Phase 0 framework (function check + UX cleanup, no architecture changes)
---

# Phase 0 audit - /legal/terms + /legal/privacy

## Summary

Both legal pages exist as honest "working drafts" pending lawyer review. The framing is appropriate ("These terms are a working draft. Sam's lawyer-reviewed version replaces this placeholder once final"), the brand chrome matches the rest of the marketing surfaces, and they cover the core contracts a tenant would want to see (data ownership, OAuth scopes, retention, subprocessors, cancellation, liability cap).

The dominant findings are mechanical, not substantive:

1. **The pages contain HTML em-dash entities (`&mdash;`)** — 5 total across both files (1 in terms, 4 in privacy). The WCAS brand rule (`feedback_no_em_dashes.md`) bans em dashes in client-facing writing. The pre-commit hook only catches literal U+2014 characters, so `&mdash;` slipped through. These render as em dashes in the browser, violating the rule visually even though they pass the byte check.
2. **Four routes serve identical content** — `/terms` AND `/legal/terms`, `/privacy` AND `/legal/privacy` (`main.py:366-383`). Tenant-2 confusion plus DRY violation. Pick one canonical URL and 301 the others.
3. **Cookie disclosure is missing entirely** — we set `wcas_session` cookie at `/auth/verify`, but neither legal page mentions cookies. CCPA + most lawyer-reviewed templates require this.
4. **AI subprocessor named generically** — privacy says "an AI model provider (for drafting content in your voice)" but doesn't say Anthropic. Named subprocessors are required for CCPA + GDPR compliance.
5. **AI training claim needs proof** — privacy line 34 ("Train AI models on your content") promises we don't, but Anthropic's standard ToS allows training unless explicitly opted out via Zero Data Retention. Sam needs to verify the actual API plan before this claim ships in the lawyer-reviewed version.

The legal copy is otherwise solid for a placeholder. Most findings are housekeeping ahead of the real lawyer pass scheduled for Phase 1 W1.

14 findings: 6 must-fix-before-tenant-2, 6 nice-to-have-pre-launch, 2 defer-to-Phase-2.

### Top 3 priorities (1-line each)

1. **Strip the 5 `&mdash;` entities.** Brand rule violation, easy fix, lands today.
2. **Verify Anthropic API plan supports the "no AI training" claim.** Either confirm Zero Data Retention is enabled or rewrite the privacy line. This is the highest-stakes substantive item before the lawyer review.
3. **Add cookie disclosure.** One paragraph in privacy.html naming `wcas_session` (the only cookie we set) and its purpose / lifetime / scope.

---

## Findings

### F1. 5 `&mdash;` HTML entities in legal templates - violates WCAS no-em-dash rule - must-fix-before-tenant-2

- **Function:** All client-facing writing avoids em dashes per `~/.claude/projects/.../feedback_no_em_dashes.md` ("Sam considers them an AI giveaway").
- **Today:** `templates/legal/terms.html:34` has 1 `&mdash;`. `templates/legal/privacy.html:24,25,26,27` has 4. The pre-commit hook (`tests/test_smoke.py::test_no_em_dashes_in_source`) checks for literal U+2014 only, so `&mdash;` passes.
- **Gap:** The entities render as em dashes in the browser. From the tenant's view, the rule is broken.
- **Smallest fix:** Replace each `&mdash;` with " - " (space-hyphen-space) or restructure the sentence. Add `&mdash;` to the pre-commit em-dash check. One sed pass plus one test addition.
- **Estimated effort:** 0.25 day. Includes the test guard so this never recurs.

### F2. AI subprocessor named generically; CCPA + GDPR require named subprocessors - must-fix-before-tenant-2

- **Function:** Privacy policy must name third parties that process tenant data.
- **Today:** `templates/legal/privacy.html:45` says "an AI model provider (for drafting content in your voice)". Doesn't name Anthropic.
- **Gap:** Lawyer-reviewed version will require this anyway. Better to ship the truthful list now than re-issue a major version next month.
- **Smallest fix:** Update the subprocessors section to name each provider explicitly: Anthropic (for AI generation), Google (Workspace + GBP + OAuth), Meta (Facebook OAuth), Airtable (CRM storage), Hostinger (hosting), Twilio (SMS), Resend or Gmail (email delivery). Include each provider's privacy URL.
- **Estimated effort:** 0.5 day. Includes the URL audit per provider.

### F3. AI-training claim ("we don't train AI models on your content") may not match the API plan - must-fix-before-tenant-2

- **Function:** Privacy promise that tenant data isn't used to train upstream AI.
- **Today:** `templates/legal/privacy.html:34`: "Train AI models on your content." (in the "What we do NOT do" list).
- **Gap:** Anthropic's standard API ToS allows training on customer inputs unless the customer is on a plan that includes Zero Data Retention (ZDR). Sam needs to verify which plan is active. If the answer is "we use the Anthropic API but ZDR is not enabled" then this claim is technically false and a tenant could call it out.
- **Smallest fix:** Two-pronged.
  - Sam verifies plan status with Anthropic. (Action item for Sam, not Phase 1D code.)
  - If ZDR is on, leave the claim. If not, soften to "We don't train **our own** AI models on your content. Subprocessors (e.g., Anthropic) operate under their own published policies, summarized below."
- **Estimated effort:** 0.1 day for the copy update once Sam confirms the plan; 0 days for the verification call. **Block before lawyer review.**

### F4. Cookie disclosure missing - CCPA / EU cookie law gap - must-fix-before-tenant-2

- **Function:** Owner reads privacy policy and learns what cookies the dashboard sets.
- **Today:** Neither legal page mentions cookies. Dashboard sets `wcas_session` (signed, HttpOnly, SameSite=Lax, 24h max-age) at `/auth/verify`. No analytics cookies, no third-party cookies.
- **Gap:** Tenant 2 with any compliance background will look for this section first. Absence reads as "haven't thought about it."
- **Smallest fix:** Add a "Cookies + tracking" section to privacy.html. One paragraph: name the `wcas_session` cookie, its purpose (auth), its lifetime (24h), HttpOnly + SameSite=Lax flags, and a "we set no analytics or advertising cookies" line.
- **Estimated effort:** 0.25 day.

### F5. Four routes serve identical content - DRY + canonical URL violation - must-fix-before-tenant-2

- **Function:** One canonical URL per page; aliases redirect.
- **Today:** `main.py:366-383` defines four GET routes (`/terms`, `/privacy`, `/legal/terms`, `/legal/privacy`) all calling the same templates. Public homepage links use `/terms` and `/privacy` (`static/index.html:175-176`).
- **Gap:** SEO duplicate content. Tenant confusion ("which is the real URL?"). Future maintenance pain.
- **Smallest fix:** Pick canonical (recommend `/legal/terms` and `/legal/privacy` because it's namespaced for future legal pages like `/legal/dpa`, `/legal/security`). Make `/terms` and `/privacy` 301 redirects. Update the public homepage to link the canonical URLs.
- **Estimated effort:** 0.25 day.

### F6. No version-control / changelog of past versions - compliance gap - must-fix-before-tenant-2

- **Function:** Tenant disputes a clause from version 1.0 after we've shipped 1.1; the platform can prove the text in effect on the day they accepted.
- **Today:** "Version 1.0" string is hardcoded in template. No changelog. No archive of past versions. The activation-terms acceptance flow (`api/activation_terms.py`) records a version string but doesn't snapshot the text.
- **Gap:** Compliance auditor or legal dispute = no proof of what version 1.0 actually said.
- **Smallest fix:** Two-pronged.
  - Move terms + privacy text to versioned markdown files (`legal_versions/terms_v1.0.md` etc.) under git history. Template renders the latest. Old versions stay queryable by version string.
  - On version bump, ALSO snapshot rendered HTML at `legal_versions/terms_v1.0.html` so the exact bytes shown to the user are preserved.
- **Estimated effort:** 1 day. Includes one test that asserts the version-string in `<p class="landing__lead">` matches the latest file's frontmatter.

### F7. No table of contents / anchor links for long-form pages - mobile friction - nice-to-have-pre-launch

- **Function:** Mobile reader scans long-form ToS and jumps to a section.
- **Today:** Both pages render as a single linear flow. No TOC, no `id` attrs on `<h2>`, no jump-links.
- **Gap:** On mobile, owner has to scroll past 4-6 sections to find "Cancellation". Friction at the moment they're trying to evaluate trust.
- **Smallest fix:** Add `id="data-collection"` etc. to each `<h2>`. Render a small TOC at the top of each page (sticky on desktop, scrollable on mobile).
- **Estimated effort:** 0.5 day.

### F8. "Last updated" date hardcoded in template - drift risk - nice-to-have-pre-launch

- **Function:** Date matches reality.
- **Today:** `templates/legal/terms.html:17` and `templates/legal/privacy.html:17` hardcode "Effective 2026-04-24 - Version 1.0".
- **Gap:** Future content changes could ship without bumping the date. Tenant 2 sees stale "Effective" date and infers the document is fresh when it actually changed yesterday.
- **Smallest fix:** Drive the date + version from a Python constant (or the legal_versions file from F6) so a content change forces a version bump in the same commit.
- **Estimated effort:** 0.25 day. Lands as part of F6.

### F9. Privacy policy doesn't specifically address `credentials.json` - nice-to-have-pre-launch

- **Function:** Privacy policy explains where OAuth refresh tokens are stored, encrypted with what, accessible by whom.
- **Today:** `templates/legal/privacy.html:25` says "OAuth refresh tokens for the services you explicitly connect (Google, Meta, etc.). Stored encrypted at rest, never shared." Encryption-at-rest claim is general; doesn't name the mechanism.
- **Gap:** A security-minded tenant (e.g., a CPA managing tax data) wants specifics: at-rest crypto algorithm, key custody, file path, who has access. Today the dashboard stores OAuth tokens in `/opt/wc-solns/<tenant>/credentials.json` with the secret-shop pattern at the FastAPI layer (encryption confirmed in code but not surfaced in copy).
- **Smallest fix:** One paragraph: "OAuth tokens stored encrypted with AES-256 in per-tenant files on Hostinger VPS. Decryption keys held in-memory only at request time. Sam (founder) is the only human with VPS shell access."
- **Estimated effort:** 0.25 day, dependent on Sam confirming the actual encryption pattern (verify the code path).

### F10. No language about deletion guarantees on third-party services - nice-to-have-pre-launch

- **Function:** Tenant cancels. Privacy policy promises export + delete after 30 days. But Google still has emails the dashboard sent through their API; Meta still has posts published; Twilio still has call records.
- **Today:** `templates/legal/privacy.html:42` discusses retention as if WCAS controls all the data. Doesn't acknowledge the third-party residue.
- **Gap:** Honest tenant question: "If I cancel, does Google forget the calendar events my dashboard created?" Today's privacy policy implies yes; reality is no.
- **Smallest fix:** One paragraph clarifying: "Data inside third-party platforms (e.g., emails sent through your Gmail, posts published to your Facebook) remains under the control of those vendors per their respective privacy policies. We delete only what we hold ourselves."
- **Estimated effort:** 0.25 day.

### F11. ToS missing arbitration / dispute resolution clause - nice-to-have-pre-launch

- **Function:** Standard SaaS ToS has either binding arbitration or specifies CA Superior Court venue + jurisdiction.
- **Today:** `templates/legal/terms.html` has no dispute clause. Liability section caps damages but doesn't specify forum.
- **Gap:** Lawyer-reviewed version will add this. Flag for Phase 1 W1 lawyer kickoff.
- **Smallest fix:** Out of scope for Phase 1D (lawyer territory). Capture as input to the lawyer brief.
- **Estimated effort:** 0 (handled by lawyer review).

### F12. /auth/login + check_inbox templates have no link to legal pages - nice-to-have-pre-launch

- **Function:** Tenant 2 about to sign in for the first time should be one click away from "what am I agreeing to?"
- **Today:** `templates/auth/login.html` and `templates/auth/check_inbox.html` link only to "Back to landing." Public homepage at `static/index.html:175-176` has Terms + Privacy in the footer.
- **Gap:** Owner who lands on /auth/login directly (e.g., from the email link before they've explored the homepage) has no legal access. Surface friction at exactly the moment they're forming trust.
- **Smallest fix:** Add a footer to `auth/login.html` and `auth/check_inbox.html` with the canonical legal URLs. ~0.1 day.
- **Estimated effort:** 0.1 day.

### F13. No print-friendly stylesheet - defer-to-Phase-2

- **Function:** Tenant prints the ToS for their files.
- **Today:** No `@media print` rules. Default would print the full landing chrome.
- **Gap:** Minor; most tenants will save as PDF.
- **Smallest fix:** Add `@media print { body { background: white; } /* etc */ }` to `styles.css`. ~0.25 day.
- **Estimated effort:** Defer.

### F14. No PDF export option - defer-to-Phase-2

- **Function:** "Download as PDF" button on each legal page.
- **Today:** None.
- **Gap:** Minor; users can `Cmd+P → Save as PDF`.
- **Smallest fix:** WeasyPrint route or static pre-generated PDF. ~0.5 day.
- **Estimated effort:** Defer.

---

## Methodology checks (per parent plan B1)

| Check | Result |
|---|---|
| Function check | Both pages render. Both versioned strings exist. Two URLs each (alias pair). Public homepage links to /terms + /privacy. |
| UX gap | F1 (em dashes), F4 (no cookie disclosure), F5 (4 routes for 2 pages), F12 (login has no legal link) are the visible-to-tenant ones. F2 + F3 + F6 are compliance-driven. |
| Smallest fix | Most items sized in fractions of a day. Total: ~3 days for must-fix + nice-to-have. |
| Phase 1 priority bucket | Assigned per finding. |
| Composer empty state | N/A - static content. |
| Mobile pass | F7 (TOC for long-form). Otherwise body styling already responsive. |
| Confused-state recovery | N/A - read-only pages. |
| Demo gate | Both pages are public (no PREVIEW_MODE / JUDGE_DEMO gating). Correct. |
| Sidebar consistency | N/A - landing-style chrome. |
| Last-updated dates | Both pages stamped 2026-04-24, V1.0. F6 + F8 ensure this stays accurate. |

---

## Phase 1D effort total

| Bucket | Effort |
|---|---|
| must-fix (F1-F6) | ~2.35 days |
| nice-to-have (F7-F12) | ~1.35 days |
| defer (F13-F14) | N/A |
| **Total in scope** | **~3.7 days** for Phase 1D |

This surface (like /auth/login) has good audit-value-per-fix-cost ratio. Land in week 1 of Phase 1D. Phase 1 W1 lawyer review will handle F11 + arbitration + jurisdiction + the substantive language pass; Phase 1D handles the mechanical hygiene.

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
| 12 | /healthz | next | - | - |

**Running totals:** 132 findings, ~44-46 days Phase 1D work mapped. With shared-dispatcher dedupe + shared prefs-partial: ~35-37 days.

## Cross-cutting themes (cumulative, updated)

1. **Cold-start cliff** - not relevant to legal pages (static content).
2. **Sidebar/topbar partials** - not relevant.
3. **Demo-gate hygiene** - legal pages public; correct.
4. **Slug normalization** - not relevant.
5. **Shared dispatcher** - not relevant.
6. **Hero coupling** - not relevant.
7. **UX lies** - not present here.
8. **Server prefs vs. localStorage drift** - not relevant.
9. **Sam's inbox burden** - not relevant.
10. **NEW: HTML-entity encoding bypasses brand-rule pre-commit hook.** Em-dash rule check needs to also reject `&mdash;`, `&#x2014;`, `&#8212;`. Adding to global Phase 1D punch list.
11. **NEW: Compliance hygiene** - cookie disclosure, named subprocessors, version archives — collectively a single Phase 1D task ("legal-compliance pass") that lands alongside the lawyer-reviewed copy update.

---

## Next surface to audit

**`/healthz`** - per parent plan, the version + status endpoint. Should be quick. Need to check:
- Currently returns `{"status":"ok","version":"0.7.1"}` (verified during Part A deploy)
- Demo gate (should be public; no auth)
- Caching headers
- What "ok" actually proves (DB ping? Airtable ping? heartbeat-store ping?)
- Is this exposed for external uptime monitoring? Should it auth?
- Surface area for a future `/readyz` (deeper liveness)
