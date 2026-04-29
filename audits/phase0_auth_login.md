---
surface: /auth/login + /auth/request + /auth/verify + magic-link email + /auth/logout
audited: 2026-04-28
auditor: Larry (Claude Opus 4.7)
methodology: Phase 0 framework (function check + UX cleanup, no architecture changes)
---

# Phase 0 audit - /auth/login (magic-link flow)

## Summary

The auth flow is one of the most carefully designed surfaces in the product. Privacy is solid (neutral check_inbox screen on rate-limit, unknown email, AND unapproved onboarding), session cookies use the right flags (`HttpOnly` + `SameSite=Lax` + `Secure` in prod), tokens are SHA-256 hashed at rest, comparisons are constant-time, and the 5-per-15-minute rate limit is sensible. Magic-link email is on-brand (DM Serif + DM Sans + WCAS palette) with both HTML and text bodies.

The dominant finding is also subtle and serious: **error codes from `/auth/verify` are silently dropped.** Every redirect from a failed verification (`?e=invalid|expired|used|missing|server|incomplete`) lands the user on `/auth/login` with no error message rendered, because `login_form()` at `api/auth.py:78-80` never reads the query param. The user clicks an expired link, lands on a fresh sign-in form, and has no idea what went wrong. They re-enter their email, send another link, click it, and might hit the same wall (e.g., a slow email provider that delivered the second link before they noticed the first). This is the highest-impact UX bug on this surface.

The second-biggest concern: **Sam gets an `alert_sam` email on every successful login** (`auth.py:189-199`) — including his own. Tenant 2's normal usage will burn through Sam's inbox.

13 findings: 5 must-fix-before-tenant-2, 6 nice-to-have-pre-launch, 2 defer-to-Phase-2.

### Top 3 priorities (1-line each)

1. **Render the `?e=*` error codes on `/auth/login`.** The whole point of redirecting with a code is to tell the user what went wrong. Today the codes are written and never read.
2. **Stop emailing Sam on every login.** Trim `alert_sam` to first-login-per-tenant only.
3. **Add a "pending approval" copy path.** Today, an unapproved tenant sees the same neutral check_inbox as a rate-limited or unknown email — but their email never arrives. They sit waiting and Sam doesn't know.

---

## Findings

### F1. `/auth/verify` error codes (`?e=invalid|expired|used|missing|server|incomplete`) silently dropped on the login page - must-fix-before-tenant-2

- **Function:** Failed verification redirects to `/auth/login?e=<code>` and the login page should explain what happened (link expired, already used, etc.).
- **Today:** `api/auth.py:148-178` redirects to `/auth/login?e=expired` etc. on every failure path. `api/auth.py:78-80` `login_form()` GET handler renders `auth/login.html` with `{"error": None}` — query param `e` is never consulted. Template at `templates/auth/login.html:22-26` only shows `{{ error }}` if truthy, which is always None on the GET path.
- **Gap:** User clicks an expired magic link, lands on what looks like a fresh sign-in form. They re-enter their email, get a new link, all without ever knowing why the first one failed. Worst case: they keep clicking the same expired link from their email and never realize the link they need is the new one.
- **Smallest fix:** In `login_form()`, read `request.query_params.get("e")` and map to a friendly message:
  ```
  e_map = {
      "expired": "That link has expired. Email yourself a fresh one below.",
      "used":    "That link has already been used. Email yourself a fresh one.",
      "invalid": "That link doesn't look right. Try emailing yourself a new one.",
      "missing": "Sign-in link missing. Enter your email below to get a new one.",
      "server":  "Something went wrong on our side. Try again in a minute.",
      "incomplete": "Your account isn't quite set up yet. Email sam@... for help.",
  }
  ```
- **Estimated effort:** 0.25 day. Includes one new test that asserts each code renders the right message.

### F2. `alert_sam` fires on every successful login - inbox spam at tenant 2 scale - must-fix-before-tenant-2

- **Function:** Sam knows when a new tenant first signs in.
- **Today:** `api/auth.py:189-199` calls `email_sender.alert_sam(... event_type='onboarding_started' ...)` on every `/auth/verify` success. Including Sam's own logins. Including tenant 2's daily logins.
- **Gap:** With 1 active tenant, Sam already gets an email every time he logs into the dashboard. Tenant 2 added = 2x. Tenant 5 = ~5 emails/day at minimum. The "onboarding_started" subject is misleading once it's the tenant's 50th sign-in.
- **Smallest fix:** Two-pronged.
  - Skip the alert if `role == "admin"` (Sam shouldn't get alerts for himself).
  - Track first-login-per-tenant in `audit_log` and only fire `alert_sam` on the first event ever for that tenant_id. After that, daily logins are silent. Sam can still query the audit log for "who signed in today."
- **Estimated effort:** 0.5 day (audit_log query for first-event-detection + the role gate).

### F3. No "pending approval" copy path - unapproved tenants get the same neutral page and no signal - must-fix-before-tenant-2

- **Function:** Owner whose `Onboarding Approved=false` in Airtable submits the form, sees the neutral check_inbox screen, but never receives an email (correctly, by design). They have no way to know the dashboard is waiting on Sam.
- **Today:** `api/auth.py:110-124` logs the denial server-side (`audit_log.record(event="magic_link_denied_unapproved")`) and shows the same `auth/check_inbox.html`. Sam gets no proactive notification when a not-yet-approved tenant tries to sign in.
- **Gap:** Privacy posture (don't enumerate email existence) bleeds into UX failure (legitimate tenant thinks the dashboard is broken). The denial log exists but isn't routed to Sam.
- **Smallest fix:** When `is_active(record) AND not is_onboarding_approved(record)`:
  - Still show the neutral check_inbox page (privacy preserved).
  - **Send a low-priority alert email to Sam** ("Tenant X tried to sign in - they're waiting on you to flip the Onboarding Approved bit") with a link to Airtable. Throttle to once per tenant per day.
  - Optional Phase 1D: tiny on-screen hint after 60 seconds on the check_inbox page: "Email might take a minute. If it doesn't arrive in 5, contact sam@..." This gives a real escape hatch without leaking enumeration data.
- **Estimated effort:** 0.5 day.

### F4. `_magic_link_url` falls back to literal prod hostname when no Host header - must-fix-before-tenant-2

- **Function:** Magic link URLs are generated against the request's host so dev/staging environments work.
- **Today:** `api/auth.py:54-58`:
  ```
  base = (request.headers.get("x-forwarded-host")
          or request.headers.get("host")
          or "dashboard.westcoastautomationsolutions.com")
  ```
- **Gap:** If a future staging instance somehow lacks both headers (test runner, oddly-configured proxy), the email links will point to **production**. Tenant on staging clicks the link, lands on prod, signs in to prod against staging's stashed token hash, fails. Subtle but bad.
- **Smallest fix:** Either raise `RuntimeError("missing host header")` on the fallback path (fail-loud), OR drive the base URL from a required `PUBLIC_BASE_URL` env var that staging/prod each set explicitly. Recommend the env-var path since it also helps F12 (prod testing without polluting prod data).
- **Estimated effort:** 0.5 day. Includes one new env var, doc update, and a guard test.

### F5. Login email input has no autofocus - must-fix-before-tenant-2

- **Function:** Page loads, cursor is in the email input, owner types and tabs to submit.
- **Today:** `templates/auth/login.html:30` has no `autofocus` attribute on the email input.
- **Gap:** Tenant 2 lands on `/auth/login`, has to click the input before typing. Not a deal-breaker but small UX papercut on the very first surface a tenant sees, repeated every login. Easy fix.
- **Smallest fix:** Add `autofocus` to the input on line 30. Add `autocomplete="email"` is already there. Done.
- **Estimated effort:** 0.05 day.

### F6. 1-hour magic link TTL is on the long side for security; 15 min is more conventional - nice-to-have-pre-launch

- **Function:** Magic links should expire fast enough that intercepted/forgotten emails don't leak access for hours.
- **Today:** `services/tokens.py:36-40` defaults to 3600 seconds (1 hour). Env-overridable via `MAGIC_LINK_TTL_SECONDS`.
- **Gap:** OWASP and most magic-link references suggest 10-15 minutes. 1 hour is OK if email delivery is sometimes slow but probably tighter than necessary for tenant 2's typical "click within 30 seconds" pattern.
- **Smallest fix:** Drop the default to 900 (15 min). Update the email copy ("works for the next 15 minutes" already templated via `ttl_minutes`). Test that the message in `auth/check_inbox.html` updates accordingly.
- **Estimated effort:** 0.1 day.

### F7. No "resend the link" button on check_inbox.html - nice-to-have-pre-launch

- **Function:** Owner doesn't see the email after 30 seconds, wants to resend.
- **Today:** `templates/auth/check_inbox.html:26-29` has only "Use a different email" and "Back to landing" links. No resend.
- **Gap:** The owner has to navigate back to /auth/login, retype their email, submit again. Friction at the most fragile point of the funnel.
- **Smallest fix:** Add a "Resend the link" form (same email pre-filled, same POST /auth/request) below the help text. Rate-limited by the same login_limiter so it doesn't enable abuse.
- **Estimated effort:** 0.25 day.

### F8. Login form uses inline styles instead of shared chrome - nice-to-have-pre-launch

- **Function:** Brand consistency.
- **Today:** `templates/auth/login.html:28-37` uses heavy inline styles (padding, color, border, font, etc.) instead of the `.ap-btn` / `.ap-input` patterns used everywhere else in the app.
- **Gap:** Login is the first impression. Inline styles drift from the shared design system; if the brand palette ever shifts (Phase 2), this template won't follow.
- **Smallest fix:** Extract a `.landing__email-input` and `.landing__submit` class set, move the styles to `styles.css`. The `.landing__*` namespace already exists.
- **Estimated effort:** 0.25 day.

### F9. No CSRF token on the POST forms - nice-to-have-pre-launch

- **Function:** Standard CSRF protection.
- **Today:** `templates/auth/login.html` POST form has no CSRF token. Logout POST also unguarded. The state-changing risk is low (a CSRF-driven `/auth/request` would just send a magic link to the victim's *own* email; `/auth/logout` would just log the user out), but framework-wise it's the missing belt for the suspenders that already exist.
- **Gap:** Defense in depth. Today, SameSite=Lax cookies block cross-origin POST so the practical attack surface is small.
- **Smallest fix:** Add itsdangerous-signed CSRF tokens to both forms. ~0.5 day. Could defer to Phase 2 if reviewing the actual risk.
- **Estimated effort:** 0.5 day. Or defer to Phase 2.

### F10. No per-IP rate limit, only per-email - nice-to-have-pre-launch

- **Function:** Prevent botnet from hammering many emails from one IP.
- **Today:** `services/rate_limit.py:39` has `login_limiter = SlidingWindowLimiter(max_events=5, window_seconds=900)` keyed on email address. No IP-keyed limiter.
- **Gap:** A botnet could test 1000 emails from 1000 different IPs and hit each email's limiter exactly once - no per-IP throttle. The neutral check_inbox response prevents enumeration of valid emails, but absent IP throttling, a bot could still cause real magic-link emails to be sent to many real customers.
- **Smallest fix:** Add a second `SlidingWindowLimiter(max_events=20, window_seconds=900)` keyed on `request.client.host`. If either limiter rejects, return the neutral page.
- **Estimated effort:** 0.25 day.

### F11. Magic-link email links don't include UTM / tracking params - mobile-app fallback - nice-to-have-pre-launch

- **Function:** Tenant clicks the link in mobile Gmail, fallback opens the link in their default browser. If a tracking param were added, Sam could correlate magic-link clicks to tenants without scraping logs.
- **Today:** Plain URL with only `?token=...`.
- **Gap:** Marginal. Privacy-friendly to NOT add tracking. Listed for completeness only.
- **Smallest fix:** Skip; aligns with privacy posture.
- **Estimated effort:** 0 (recommend NOT doing this).

### F12. Email body has no friendly fallback for "open in your installed dashboard app" - defer-to-Phase-2

- **Function:** If we later ship a desktop or mobile shell, magic links could open in-app vs. browser.
- **Today:** Plain web link only.
- **Gap:** Hypothetical; Phase 2.
- **Smallest fix:** Defer.
- **Estimated effort:** N/A.

### F13. No 2FA / WebAuthn / passkey path - defer-to-Phase-2

- **Function:** Owner adds a phone-as-second-factor for high-stakes tenants (e.g., financial verticals).
- **Today:** Magic-link only. Sufficient for tenant 1-5; required for some compliance frameworks.
- **Gap:** Per CLAUDE.md "no medical / HIPAA verticals" - this is not a current blocker.
- **Smallest fix:** Defer until tenant 10+ or compliance request.
- **Estimated effort:** N/A.

---

## Methodology checks (per parent plan B1)

| Check | Result |
|---|---|
| Function check | Magic-link generation + verification + cookie issuance all work. Token storage uses SHA-256 hash, constant-time compare. Privacy posture solid. |
| UX gap | F1 (silently dropped error codes) is the showstopper for tenant 2. F2 (Sam inbox spam) is the operational drag. F3 (pending-approval invisible) is the support-ticket generator. |
| Smallest fix | All findings sized in fractions of a day. Total: ~3 days for must-fix + nice-to-have. |
| Phase 1 priority bucket | Assigned per finding. |
| Composer empty state | N/A - login is single-input. |
| Mobile pass | Inline `min-width:260px` on email input could overflow narrow viewports. F8 fix would address. |
| Confused-state recovery | F1 is the entire confused-state-recovery story for this surface. Critical fix. |
| Demo gate | `JUDGE_DEMO` interplay: when on, `/auth/judge` POST mints a Garcia session bypassing this entire flow. When off (default post-hackathon), this flow is the only path. `PREVIEW_MODE` does not affect login (login is unauthenticated). No regression. |
| Sidebar consistency | N/A - login is pre-auth, no sidebar. |
| Session cookie flags | PASS - HttpOnly + SameSite=Lax + Secure (prod) + 24-hour max_age. Session refresh on activity is a known Phase 1 W1 item ("rolling 30-day sessions") so excluded from this audit. |

---

## Phase 1D effort total

| Bucket | Effort |
|---|---|
| must-fix (F1-F5) | ~1.8 days |
| nice-to-have (F6-F11) | ~1.35 days (most are very small) |
| defer (F12-F13) | N/A |
| **Total in scope** | **~3.15 days** for Phase 1D |

This surface is one of the cleanest ratios of audit value vs. fix cost: 5 must-fix items in under 2 days. Land it as the single Phase 1D week 1 morning before any of the bigger dispatcher work.

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
| 11 | /legal/terms + /legal/privacy | next | - | - |

**Running totals:** 118 findings, ~40-42 days Phase 1D work mapped. With shared-dispatcher dedupe + shared prefs-partial: ~31-33 days.

## Cross-cutting themes (cumulative, updated)

1. **Cold-start cliff** - login flow has its own version: F3 unapproved tenant sees no signal.
2. **Sidebar/topbar partials** - drift not present here (no sidebar).
3. **Demo-gate hygiene** - `JUDGE_DEMO` correctly affects only `/auth/judge`; this flow unaffected.
4. **Slug normalization** - not relevant.
5. **Shared dispatcher** - not relevant.
6. **Hero coupling** - not relevant.
7. **UX lies** - F1 (error codes silently dropped) is a new variant: write something, never read it. Same shape as the settings-toggle pattern, in a different domain.
8. **Server prefs vs. localStorage drift** - not relevant.
9. **NEW: Sam's inbox burden** - F2 (alert_sam on every login) joins the digest pipeline gap (settings F2 `email_digest`) as the second mention of "Sam's email volume scales linearly with tenant count without throttle." Worth a separate Phase 1D item: "Sam's notification rate budget — every alert path needs a per-day cap."

---

## Next surface to audit

**`/legal/terms` + `/legal/privacy`** - per parent plan, the legal pages are required before any tenant 2 onboarding. Need to check:
- Whether the pages exist or are stub
- ToS coverage of the data the dashboard collects + processes (esp. credentials, voice transcripts, customer PII)
- Privacy policy alignment with the actual data flows (heartbeats, decisions, KB entries, GBP / Google / Airtable OAuth scopes)
- Cookie disclosure (we set 1 cookie: `wcas_session`; HttpOnly + SameSite=Lax)
- Last-updated dates / version control
- Mobile readability (long-form pages on phones)
- Demo gate (these pages should be public)
- Sidebar / chrome (likely landing-style, no sidebar)
