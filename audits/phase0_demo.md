---
surface: /demo, /demo/activation, /demo/dashboard (cinematic prototypes)
audited: 2026-04-28
auditor: Larry (Claude Opus 4.7)
methodology: Phase 0 framework (function check + UX cleanup, no architecture changes)
---

# Phase 0 audit - /demo/*

## Summary

The `/demo/*` cinematics are the hackathon Day-6 LATE-evening artifacts: a 5-scene activation walkthrough (`demo_activation.html`, 952 lines) and a 6-scene dashboard walkthrough (`demo_dashboard.html`, 836 lines). Standalone prototypes, no auth, no tenant — synthetic "Riverbend Barbershop" data baked into HTML+CSS. Used for the hackathon submission video and future portfolio reviewers.

The Apr 28 Part A deploy verified the regression invariant: `/demo`, `/demo/activation`, `/demo/dashboard` all return 404 when `JUDGE_DEMO=false` (the prod default). `POST /auth/judge` also 404s. Privacy is preserved.

The bulk of this audit is **fail-open risk analysis**: if `JUDGE_DEMO=true` ever gets set on prod (intentionally for a portfolio review, OR accidentally during a config copy/paste), what does the dashboard expose to Google's crawler and to anyone with the URL? Today: a beautifully designed cinematic with no `noindex` meta tag. The synthetic Riverbend data is harmless content, but indexed marketing surfaces with brand-mismatched copy ("Sleep well, Itzel") would be embarrassing on a search results page if Sam's real owner-tenant happened to be searching.

Also: 13 `&#8212;` HTML entities across both demo files (2 in user-visible copy, 11 in HTML/CSS comments). Same brand-rule pattern as the legal pages, lower stakes.

9 findings: 3 must-fix-before-tenant-2, 4 nice-to-have-pre-launch, 2 defer-to-Phase-2.

### Top 3 priorities (1-line each)

1. **Add `<meta name="robots" content="noindex">`.** Cheap insurance against the day someone flips `JUDGE_DEMO=true` for an hour and Google scrapes.
2. **Strip the 2 user-visible `&#8212;` entities at `demo_dashboard.html:750` + `:793`.** Brand-rule consistency with the legal-pages fix.
3. **Smoke-test the regression in CI.** A test that does `os.environ.pop("JUDGE_DEMO")` and asserts `/demo*` returns 404 prevents silent regressions on future env-var refactors.

---

## Findings

### F1. No `<meta name="robots" content="noindex">` on demo pages - SEO indexing risk if `JUDGE_DEMO=true` ever flips - must-fix-before-tenant-2

- **Function:** Demo pages should never appear in search results, even when temporarily enabled.
- **Today:** Neither `templates/demo_activation.html` nor `templates/demo_dashboard.html` has a `noindex` meta tag. Compare to every other private surface (`templates/home.html`, `templates/dashboard.html`, etc.) which DO have `<meta name="robots" content="noindex">`.
- **Gap:** When Sam flips `JUDGE_DEMO=true` to send a portfolio reviewer the link, even a single hour of public visibility is enough for Google to crawl. The synthetic content includes "Sleep well, Itzel. The studio runs itself tonight" — branded copy that doesn't match a real WCAS marketing message and would confuse the search-results SERP.
- **Smallest fix:** Add `<meta name="robots" content="noindex">` to both `<head>` blocks. ~2 lines.
- **Estimated effort:** 0.05 day.

### F2. 2 user-visible `&#8212;` HTML entities violate brand no-em-dash rule - must-fix-before-tenant-2

- **Function:** All client-facing copy avoids em dashes per `feedback_no_em_dashes.md`.
- **Today:** `demo_dashboard.html:750` reads "Applied: +2 slots, 1pm and 4pm — coverage now 9am to 9pm" (rendered em dash via `&#8212;`). Line 793: "— I'll have your morning brief ready by 7." (rendered em dash via `&#8212;`).
- **Gap:** Same pattern as legal pages F1; pre-commit hook checks for U+2014 only, not numeric/named entities. Two visible violations in user copy.
- **Smallest fix:** Replace each `&#8212;` in visible copy with " - " (space-hyphen-space). Comment-only `&#8212;` instances (11 of 13) can be left or scrubbed; recommend scrubbing all 13 for hygiene + to land alongside the legal-pages fix.
- **Estimated effort:** 0.1 day. Couple this with `audits/phase0_legal.md::F1` as one PR.

### F3. No CI test asserting `/demo*` 404 when `JUDGE_DEMO=false` - must-fix-before-tenant-2

- **Function:** Regression-prevent the demo-gate.
- **Today:** Apr 28 Part A added 3 new gate-tests in `tests/test_smoke.py`: judge tests use `monkeypatch.setenv("JUDGE_DEMO", "true")` to confirm enabled-path, and 3 new tests confirm 404 when missing. **Verify these are passing locally and on the next test run.**
- **Gap:** Without the test (or if the test was scoped wrongly), a future env-var refactor could silently flip the default to "open" and expose synthetic data publicly.
- **Smallest fix:** Confirm the 3 added tests cover all three demo URLs (`/demo`, `/demo/activation`, `/demo/dashboard`) plus `POST /auth/judge`. Add explicit assertions on response status (404) and that `os.getenv("JUDGE_DEMO")` is genuinely unset for the assertion (not pre-set in conftest).
- **Estimated effort:** 0.25 day to verify + extend if needed. (Likely already covered, but worth a confirmation pass.)

### F4. Demo templates are 952 + 836-line single files - hard to maintain - nice-to-have-pre-launch

- **Function:** Future Sam edits a scene without scrolling 700 lines of CSS.
- **Today:** Both files inline all CSS (~600 lines) + HTML scenes (~250 lines) + JS scene-pickers (~50 lines) in one file. Compare to the modular pattern used elsewhere.
- **Gap:** Sam (or future Larry) wants to fix a single recap line; finding it requires text-search through nearly a thousand lines.
- **Smallest fix:** Phase 1D minimum: add a small in-file table of contents at the top of each file with line numbers (e.g., `<!-- Scene 1: 250-300 | Scene 2: 300-400 | ... -->`). Phase 2: extract per-scene partials.
- **Estimated effort:** 0.25 day (TOC). Defer the partial-extraction to Phase 2.

### F5. No mobile-specific scene fallback - cinematics designed for desktop - nice-to-have-pre-launch

- **Function:** Portfolio reviewer opens the demo on their phone, sees the cinematic stack readably.
- **Today:** Both files use `width:1240px;max-width:100%` on the main canvas. On a 390px-wide phone, the canvas shrinks but the embedded scene compositions (especially side-by-side voice-card layouts) become unreadable.
- **Gap:** Reduces the surface where the demo "works." Most portfolio reviewers will be on desktop, but mobile is the silent baseline expectation.
- **Smallest fix:** Add a "Demo best on desktop. Resize your browser or open on a laptop for full experience." banner that appears under 800px viewports. ~10 lines of CSS + HTML.
- **Estimated effort:** 0.25 day.

### F6. No "replay from start" affordance after the final scene - nice-to-have-pre-launch

- **Function:** Portfolio reviewer reaches scene 5 (activation) or scene 6 (dashboard), wants to see scene 1 again.
- **Today:** Each file has a scene-picker at the top with numbered buttons (1-5 or 1-6) and a small "↻" replay icon. Replay loops the current scene, not the whole sequence.
- **Gap:** Mild. The picker is the path; users will figure it out. Worth a small "Restart from Scene 1" button at the end of the final scene.
- **Smallest fix:** Inline JS handler that calls `setActiveScene(1)` on a button at the bottom of the canvas. ~15 lines.
- **Estimated effort:** 0.25 day.

### F7. Speaker notes are embedded inline; can't be exported separately - nice-to-have-pre-launch

- **Function:** Sam wants the speaker notes (currently inline `<aside>` blocks in the HTML) as a separate doc for the recording session.
- **Today:** Speaker notes live inside the demo HTML as styled asides; printing them requires print mode + filtering. The Apr 25 Day-6 work intentionally embedded them inline for visibility during recording.
- **Gap:** Mild. Sam can copy/paste; future Larry could grep them.
- **Smallest fix:** Add a `?notes=raw` query param that renders only the speaker notes as plain markdown for copy/paste. ~30 lines.
- **Estimated effort:** 0.25 day.

### F8. No subtitle / a11y track for video recording - defer-to-Phase-2

- **Function:** Recorded demo video has captions for accessibility.
- **Today:** Captions belong to the post-production tool (Remotion / DaVinci), not this template.
- **Gap:** Out of scope; tooling concern.
- **Smallest fix:** Defer.
- **Estimated effort:** N/A.

### F9. `_demo_home_context()` for `PREVIEW_MODE` uses real Americal Patrol names - defer-to-Phase-2

- **Function:** Demo data should be obviously synthetic to avoid confusion with real tenant data.
- **Today:** `main.py:659-782` `_demo_home_context()` uses "Sam Alarcon" + "Americal Patrol" (real names). When `PREVIEW_MODE=true`, anyone hitting `/dashboard` sees Sam's name attached to fabricated metrics.
- **Gap:** This is the OTHER demo path (PREVIEW_MODE-gated, not JUDGE_DEMO-gated). Used for video recording. Different purpose from `/demo/*`. Currently OFF on prod (verified during Apr 28 deploy). Risk is symmetric to F1 here: if `PREVIEW_MODE=true` flips on prod, anyone gets to see "Sam Alarcon" with fabricated numbers.
- **Smallest fix:** Replace the demo data with obviously-synthetic names ("Demo Tenant", "Owner Name") OR move the `PREVIEW_MODE` mock surface to a `/demo/home` route under the `JUDGE_DEMO` gate (consolidates two gates into one).
- **Estimated effort:** 0.5 day. Defer until consolidating both gates is on the Phase 1D punch list.

---

## Methodology checks (per parent plan B1)

| Check | Result |
|---|---|
| Function check | All 3 routes 404 by default. Verified Apr 28 prod deploy. Templates render when env flips on. |
| UX gap | F1 (no noindex), F2 (em-dash entities) are the visible-to-tenant-2 ones. F3 (test coverage) is the regression-prevention. |
| Smallest fix | All findings sized in fractions of a day. Total: ~1.5 days for must-fix + nice-to-have. |
| Phase 1 priority bucket | Assigned per finding. |
| Composer empty state | N/A - cinematic prototypes. |
| Mobile pass | F5 (no mobile fallback). |
| Confused-state recovery | F6 (no full restart). |
| Demo gate | Working as designed. F1 + F3 harden it. |
| Sidebar consistency | N/A - standalone canvas. |

---

## Phase 1D effort total

| Bucket | Effort |
|---|---|
| must-fix (F1-F3) | ~0.4 day |
| nice-to-have (F4-F7) | ~1 day |
| defer (F8-F9) | N/A |
| **Total in scope** | **~1.4 days** for Phase 1D |

Smallest audit yet alongside `/healthz`. Land alongside auth/legal/healthz mechanical fixes in week 1 of Phase 1D.

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
| 14 | / (public homepage) | next | - | - |

**Running totals:** 148 findings, ~47-49 days Phase 1D work mapped. With shared-dispatcher dedupe + shared prefs-partial: ~38-40 days.

## Cross-cutting themes (cumulative, updated)

1-13. (See prior audits.)
14. **NEW: Two demo gates, two surfaces** - `JUDGE_DEMO` gates `/demo/*` and `/auth/judge`; `PREVIEW_MODE` gates `_demo_home_context` and the 9 PREVIEW_MODE-gated dashboard routes. Both serve "show off the dashboard without auth" but have different shapes. Phase 1D consolidate-into-one task: pick a single env gate, route both flows through it, document one pattern.

---

## Next surface to audit

**`/` (public homepage)** - per parent plan, last narrative surface. Sam's pre-existing edits (already committed in Part A) removed "Try as a judge" and updated "14 → 7 automation roles" + "Hackathon → Live · April 2026". Need to check:
- Does the page still scan for a tenant-2 prospect coming from a referral link?
- Trust signals (testimonials, founders' photo, real address)
- CTA path (/auth/login is the only CTA; is that the right sole conversion path?)
- Mobile pass
- SEO basics (title, description, OG tags, structured data)
- Any leftover hackathon copy
