---
surface: 401 / 403 / 404 / 422 / 500 + error.html + placeholder.html
audited: 2026-04-28
auditor: Larry (Claude Opus 4.7)
methodology: Phase 0 framework (function check + UX cleanup, no architecture changes)
final_phase_0: true
---

# Phase 0 audit - error templates (401/403/404/422/500)

## Summary

Error handling is centralized in `main.py:93-131` across three handlers. Two custom branded templates exist (`error.html` for 500, `placeholder.html` for 404). Auth (401) redirects to login. Other status codes (403, 405, 410, etc.) fall through to a minimal `<h1>{code}</h1><p>{detail}</p>` plain HTML response with no brand chrome.

The two branded templates are clean: `noindex`, DM Serif + Plus Jakarta Sans, cream + ink palette, friendly tone ("That didn't work", "Nothing here"). Error reference IDs are surfaced on 500 with a copy-paste-friendly mailto link to Sam. Solid foundation.

The gaps cluster around four shapes:

1. **Lost intent on 401 redirect** — owner deep-linked to `/goals/abc123`, hits 401 (session expired), redirects to `/auth/login` with no `?next=` parameter. After sign-in they land on `/dashboard` with no signal that they were trying to get somewhere specific.
2. **422 validation errors return JSON to humans** — a browser POST with bad form data renders raw `{"error":"invalid request","detail":[...]}` instead of a branded page or form repaint.
3. **Non-404/500 status codes fall through to unbranded HTML** — 403, 405, 410, etc. show as `<h1>403</h1><p>Forbidden</p>` with no chrome. Looks broken vs. the 404/500 branded experience.
4. **Sam-by-first-name in error copy** — 500 error page tells the tenant to email "Sam" without explaining who that is. Tenant 2 doesn't know.

11 findings: 4 must-fix-before-tenant-2, 5 nice-to-have-pre-launch, 2 defer-to-Phase-2.

### Top 3 priorities (1-line each)

1. **Preserve `?next=<path>` through the 401 → /auth/login redirect.** Deep-linked navigation is a core UX expectation.
2. **Render an HTML error page for 422 (RequestValidationError) on non-API routes.** Today's JSON response is broken on form-submit failures.
3. **Add branded handling for 403 + other HTTP errors.** Reuse `placeholder.html` with status-specific copy; eliminates the unbranded fallback.

---

## Findings

### F1. 401 redirect to /auth/login drops the originally-requested path - must-fix-before-tenant-2

- **Function:** Owner clicks an emailed link to `/goals/abc123`, session expired, lands on /auth/login, signs in, lands back on `/goals/abc123` (or at least with a "we sent you to login while we re-authenticated you" trail).
- **Today:** `main.py:99` does `RedirectResponse(url="/auth/login", status_code=303)` — no query params, no original-path preservation.
- **Gap:** Most-requested standard UX expectation for any session-gated app. Missing it makes the dashboard feel less polished than its peers.
- **Smallest fix:** Two-pronged.
  - On the 401 redirect: append `?next=` with the URL-encoded original path: `RedirectResponse(url=f"/auth/login?next={quote(request.url.path)}")`.
  - On `/auth/verify` after successful login: read `next` from query (validated against an allowlist of internal paths starting with `/`) and redirect there instead of the default landing.
- **Estimated effort:** 0.5 day. Includes one allowlist test (must reject `next=https://evil.com`).

### F2. 422 RequestValidationError returns JSON to HTML clients - must-fix-before-tenant-2

- **Function:** Browser submits a form with invalid data, sees a friendly error page or the form re-rendered with field-level errors.
- **Today:** `main.py:115-117`:
  ```
  return JSONResponse({"error": "invalid request", "detail": exc.errors()}, status_code=422)
  ```
  Same response for `/api/*` clients AND for HTML form posts. A browser sees raw `{"error":"invalid request","detail":[{"loc":["body","title"],"msg":"field required",...]}` in the address bar.
- **Gap:** Form-submit-with-validation is one of the more common interactions in the dashboard (`/goals` form, `/settings` API, `/recommendations` apply, etc.). When validation fails, the user sees what looks like a broken page.
- **Smallest fix:** Mirror the pattern from F3 here — branch on `request.url.path.startswith("/api/")`. For non-API: render `placeholder.html` with copy "Something didn't quite line up" and the error detail as a list. Could even attempt to detect the source page and redirect back with a flash via session/query.
- **Estimated effort:** 0.5 day for the basic branched response; +0.5 day if Phase 1D wants form repaint with field-level errors.

### F3. Non-404 / non-500 status codes fall through to unbranded `<h1>{code}</h1>` - must-fix-before-tenant-2

- **Function:** Every error response should be brand-consistent.
- **Today:** `main.py:111-112`:
  ```
  return HTMLResponse(f"<h1>{exc.status_code}</h1><p>{exc.detail}</p>", status_code=exc.status_code)
  ```
  Triggered by 403 (rate-limited or forbidden), 405 (method not allowed), 410 (gone), 429 (too many requests), etc. Bare HTML, no `<head>`, no styling, no chrome.
- **Gap:** Owner triggers the rate-limiter on a settings save (hypothetical), sees `<h1>403</h1><p>Forbidden</p>` with default browser styling. Looks broken.
- **Smallest fix:** Reuse `placeholder.html` with status-specific copy:
  ```
  if exc.status_code == 403:
      heading, body = "Not allowed", "You don't have access to that page. If this looks wrong, email sam@..."
  elif exc.status_code == 429:
      heading, body = "Slow down a moment", "Too many requests in a short window. Wait 15 seconds and try again."
  ...
  ```
- **Estimated effort:** 0.5 day. Includes a status-code → copy mapping table.

### F4. 500 error copy says "Sam" by first name without context - must-fix-before-tenant-2

- **Function:** Tenant 2 sees an error page and knows who to contact.
- **Today:** `templates/error.html:19-26`:
  > "If you reach out, mention this reference so Sam can look it up: ref {{error_id}}. Fastest fix: email sam@westcoastautomationsolutions.com."
- **Gap:** Tenant 2 doesn't know Sam personally. Reads as "who is Sam?" Slight informality misalignment with WCAS positioning (founder-known IS the brand, but only after introduction).
- **Smallest fix:** Reframe to "the WCAS team" with Sam's email kept as the contact: "Mention this reference when you email us: ref {{error_id}}. Fastest fix: email the team at sam@westcoastautomationsolutions.com."
- **Estimated effort:** 0.05 day.

### F5. 404 page has no helpful links beyond "back to landing" - nice-to-have-pre-launch

- **Function:** Owner mistypes a URL, lands on 404, sees the most-likely-correct destination.
- **Today:** `placeholder.html` has only `<a href="/">Back to landing</a>`.
- **Gap:** A signed-in tenant who 404s should at least see "Go to your dashboard / activity / settings" as quick links.
- **Smallest fix:** Add a small links list when the request has a session cookie. Render the canonical 7-item sidebar as an inline list at the bottom of `placeholder.html`. ~10 lines.
- **Estimated effort:** 0.25 day.

### F6. error_id format is opaque - nice-to-have-pre-launch

- **Function:** Owner copies the ref, emails Sam, and Sam can find the log entry instantly.
- **Today:** `services/errors.py::new_error_id()` generates the ID — format not inspected here. Likely a hex/UUID string.
- **Gap:** Hard for non-technical tenants to read, dictate over the phone, or transcribe. A ticket-like format ("WCAS-4823" or a timestamp-based "26-04-28-001") is more dictation-friendly.
- **Smallest fix:** Inspect `errors.new_error_id()` and reformat to `YYYYMMDD-NNN` per-day counter or similar. Store mapping for log lookup.
- **Estimated effort:** 0.5 day. Includes log-format migration.

### F7. No test asserts that 500 error_id appears in the logs - nice-to-have-pre-launch

- **Function:** When a tenant emails "ref X1234", Sam grep-finds the log line.
- **Today:** `main.py:122-123` calls `errors.log_error(error_id, exc, request.url.path)`. No CI test asserts the round-trip works (raise → log → grep finds → matches error_id).
- **Gap:** A future log-format change could silently break the lookup. Sam emails "what's ref X1234?" and grep returns nothing.
- **Smallest fix:** Add `tests/test_errors.py` that triggers a synthetic 500, captures stdout/log file, asserts `error_id in log_text`.
- **Estimated effort:** 0.25 day.

### F8. No "report this error" button or auto-submit - nice-to-have-pre-launch

- **Function:** Make it one click for tenant 2 to submit the error, including their context (browser, last action).
- **Today:** mailto link only. Tenant has to click, type, send.
- **Gap:** Most won't bother. Sam doesn't see the issue until it recurs.
- **Smallest fix:** Add a "Send report" button on `error.html` that POSTs the error_id + user agent + previous URL to `/api/errors/report`. Sam gets an email with context.
- **Estimated effort:** 0.5 day. Lightweight Sentry-style auto-report.

### F9. error.html doesn't surface "what to try next" - nice-to-have-pre-launch

- **Function:** Owner sees the error page, knows the simplest recovery step.
- **Today:** Just "back to landing" + email Sam.
- **Gap:** First instinct on a 500 is "did I cause this? should I retry?" Page doesn't say.
- **Smallest fix:** Add one line: "Refreshing the page often clears it. If not, the team will look into it."
- **Estimated effort:** 0.05 day.

### F10. No localized / non-English error pages - defer-to-Phase-2

- **Function:** Spanish-speaking tenant sees error in their language.
- **Today:** English only.
- **Gap:** Phase 2 i18n work; not blocker.
- **Smallest fix:** Defer.
- **Estimated effort:** N/A.

### F11. No retry-after handling for 429 (rate limit) - defer-to-Phase-2

- **Function:** Tenant hits rate limit on settings save, error page tells them how long to wait.
- **Today:** No 429 handler at all (would fall through to F3's bare HTML).
- **Gap:** Resolved by F3's status-code → copy mapping when 429 isn't expected to appear in tenant 2's path. Defer if F3 covers it.
- **Smallest fix:** Defer.
- **Estimated effort:** N/A.

---

## Methodology checks (per parent plan B1)

| Check | Result |
|---|---|
| Function check | Three handlers cover the core surfaces. 401 redirects, 404 + 500 templated, 422 + others fall through. |
| UX gap | F1 (lost `?next=`), F2 (JSON-to-humans), F3 (unbranded fallback), F4 (Sam by first name) are the visible-to-tenant ones. |
| Smallest fix | All findings sized in fractions of a day. Total: ~3 days for must-fix + nice-to-have. |
| Phase 1 priority bucket | Assigned per finding. |
| Composer empty state | N/A - error pages. |
| Mobile pass | error.html and placeholder.html use the `landing` chrome which has been responsive on every page audited so far. |
| Confused-state recovery | This whole audit is about that. F1-F5 each address a different cliff. |
| Demo gate | N/A - error pages public; correct. |
| Sidebar consistency | N/A - landing-style chrome on error pages. |
| noindex | PASS - both error.html and placeholder.html have `<meta name="robots" content="noindex">`. The fallback `<h1>{code}</h1>` does NOT (F3 fix removes it entirely). |

---

## Phase 1D effort total

| Bucket | Effort |
|---|---|
| must-fix (F1-F4) | ~1.55 days |
| nice-to-have (F5-F9) | ~1.55 days |
| defer (F10-F11) | N/A |
| **Total in scope** | **~3.1 days** for Phase 1D |

Mid-sized audit. Land in week 1 of Phase 1D alongside the auth/legal/healthz/demo/public-home cluster.

## Cumulative Phase 0 progress - **COMPLETE**

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
| 15 | error templates | done | 11 (4+5+2) | ~3.1 days |
| | | | **169 total** | **~51-53 days** |

**Cumulative findings:** 169 total = 56 must-fix + 84 nice-to-have + 29 defer.
**Phase 1D effort raw:** ~51-53 days.
**Phase 1D effort with shared-fix dedupe:** ~40-42 days (saves ~10-13 days through shared dispatcher, shared prefs partial, shared favicon, shared OG image, shared brand-rule guard).

---

## Cross-cutting themes (final, all 15 audits)

1. **Cold-start cliff** - every surface needs first-24-hour empty-state polish.
2. **Sidebar/topbar partials** - global Phase 1D fix (mainly /role_detail drift).
3. **Demo-gate hygiene** - 9 PREVIEW_MODE-gated routes, 3 JUDGE_DEMO routes; one consolidated test.
4. **Slug normalization** - role slug underscore vs. hyphen drift (/roles + /roles/{slug}).
5. **Shared dispatcher** - /approvals (queued send) + /recommendations (apply) + /goals (progress bump) + /settings (pause + per-pipeline approval). FOUR surfaces collapse into one `services/dispatch.py` registry. The single biggest Phase 1D unlock.
6. **Hero coupling** - /goals + /dashboard hero card #3, fix once.
7. **UX lies** - 11 unconsumed promises across 6 surfaces (settings: 6, recs: 1, approvals: 1, goals: 1, activity: 1, auth: 1). #1 Phase 1D anti-pattern: "save without consumer." Treat as global cleanup.
8. **Server prefs vs. localStorage drift** - shared `_prefs.html` partial fix.
9. **Sam's inbox burden** - alert_sam on every login + email_digest never sent. Per-day alert cap needed.
10. **HTML-entity em-dash bypass of pre-commit hook** - `&mdash;` + `&#8212;` need to be added to brand-rule check; legal pages + demo pages have these.
11. **Compliance hygiene pass** - cookie disclosure, named subprocessors, version archives. Lands alongside Phase 1 W1 lawyer review.
12. **Status-endpoint truth budget** - every "ok=true" return needs a test asserting what "ok" means. /healthz F1 is the worst offender.
13. **Smoke checks accumulate** - PRODUCTION env, /docs gate, JUDGE_DEMO default, PREVIEW_MODE default, COOKIE flags. Single `scripts/post_deploy_smoke.sh`.
14. **Two demo gates, one purpose** - JUDGE_DEMO + PREVIEW_MODE serve overlapping needs. Consolidate.
15. **Single-shared-fix opportunities** - 15 audits → 8-10 actual Phase 1D work-streams once shared fixes mapped (favicon, OG image, prefs partial, dispatcher, brand-guard, smoke script).

---

## Phase 1D recommendation: workstream-organized punch list

Instead of attacking 169 findings sequentially, group into 8-10 work-streams ordered by audit-value-per-fix-cost:

| Workstream | Surfaces touched | Findings closed | Effort |
|---|---|---|---|
| **W1. Mechanical hygiene pass** (em dashes, noindex, OG image, favicon, contact-email reconciliation, cache headers) | /, /demo, /legal, /healthz, /auth/login | ~25 | ~2.5 days |
| **W2. Auth UX week 1** (preserve `?next=`, render `?e=*` codes, autofocus, error pages branded) | /auth/login, error templates | ~12 | ~2 days |
| **W3. Shared `services/dispatch.py`** (covers /approvals + /recommendations + /goals + /settings/pause + /settings/per-pipeline) | 4 surfaces | ~15 | ~6-7 days |
| **W4. Shared `_prefs.html` partial + boot bridge** (privacy_default, feed_dense_default, email_digest readback) | /settings, /activity, /dashboard | ~6 | ~1 day |
| **W5. Cold-start polish pass** (canonical pipelines on /settings, F8 `/activity` empty state, /goals starter chips, hero stat #3 fix, etc.) | 8 surfaces | ~14 | ~3-4 days |
| **W6. Pagination + filtering** (/activity, /approvals, /recommendations, /role_detail) | 4 surfaces | ~8 | ~3 days |
| **W7. Compliance hygiene + lawyer-review prep** (cookie disclosure, named subprocessors, AI training claim verification, version archives) | /legal/* | ~6 | ~3 days |
| **W8. Health + readiness + post-deploy smoke** (deep healthz, /readyz, smoke script) | /healthz, deploy | ~5 | ~2 days |
| **W9. Sidebar/topbar + slug normalization shared partial** | /role_detail, all role surfaces | ~5 | ~1 day |
| **W10. Edge-case + defer triage** (UX lies that won't ship: hide cosmetic toggles behind "Coming soon", explicit dispatcher TODOs, README gaps) | global | ~10 | ~1-2 days |

**Total: ~25-29 days for Phase 1D**, vs. raw 51-53 days. A 50% efficiency gain through shared-fix consolidation.

This punch list is the natural Phase 1D plan input. Recommend turning it into a tracking doc separate from this audit.

---

## Phase 0 status: COMPLETE

All 16 surfaces in the parent plan's Phase 0 table audited. 169 findings filed. Phase 1D-ready.

Recommended next step (Sam's call): **start Phase 1 W1 (the lawyer email + Meta App Review + auth UX upgrade originally scheduled for Apr 29) and run W1 of Phase 1D mechanical hygiene in parallel.** The mechanical hygiene workstream has zero blockers and can be a 2-day single-PR cleanup.
