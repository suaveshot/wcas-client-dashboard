# Concierge Onboarding Runbook

Audience: Sam, on a screen-share with a new WCAS client.
Tone: pilot's checklist. Run it top to bottom.

Hard rules (memorize):

1. The client shares their screen, not Sam.
2. The client clicks every "Connect" button.
3. The client types every password and 2FA code.
4. Sam never sees a cleartext credential. Ever.
5. If it can't be done with the client's own fingers, it gets deferred or provisioned Pattern C.

---

## 1. Pre-call prep (T-24h)

Run the day before the call. Allow 30 minutes.

- [ ] Confirm signed service agreement is on file (limited authority + DPA + data-portability clauses).
- [ ] Create the Clients row in Airtable base `appLAObkCBjDxSQg2`. Fields: `tenant_id` (slug, lowercase, no spaces), `Owner Name`, `Owner Email`, `Tier` (starter / pro / ultra), `Onboarding Approved` set to true.
- [ ] Email the magic-link signup. Confirm the client received it and can land on `/dashboard` before the call. If they can't log in, fix that async, do not burn call time on it.
- [ ] Send the 5-min Loom "what to expect on our call" + the 60-min Calendly slot.
- [ ] Pre-fill the stack-inventory questions in your notes so you know which providers are likely `client_owned` vs `wcas_provisioned` per tenant.
- [ ] Open `/opt/wc-solns/<tenant_id>/` on garcia-vps. If it doesn't exist, create the tenant directory now (Phase 3 self-serve provisioning has not shipped).
- [ ] Verify the Onboarding Approved flag is true. Provisioning tools (`create_ga4_property`, `verify_gsc_domain`) refuse to run otherwise per the §0e gate in `activation_tools.py`.
- [ ] Skim the client's website yourself. Note: platform, language, obvious voice cues, NAP. You should not be discovering surprises on the call.
- [ ] Check `/healthz` returns the expected version. Deploy any pending feature branch the night before, never the morning of.

---

## 2. Call setup (T-0)

First 5 minutes. Do not skip the framing.

- [ ] Client joins, shares their screen, not yours.
- [ ] Frame the call: "I'll guide, you click. You type every password. I never see them. This protects both of us."
- [ ] Client navigates to `https://dashboard.westcoastautomationsolutions.com` and logs in via their magic link.
- [ ] Confirm the dashboard loaded with their tenant, not a stale session.
- [ ] Client clicks "Activate" or visits `/activate?intro=1`.
- [ ] Stack inventory: walk the "I have it / WCAS sets it up / Skip" toggle for each hybrid-capable provider (GHL, Twilio, Hostinger, Airtable). Default to "WCAS sets it up" for clients without an existing account.

---

## 3. The activation flow

Map each step to the agent's tool surface in `dashboard_app/services/activation_tools.py`. The agent runs the conversation; you narrate when it pauses.

### Turn 1: site read

- Agent calls `fetch_site_facts(url)` then `detect_website_platform(url)` then `detect_crm(url)`.
- Result feeds the voice card and CRM expectations.
- If `detect_website_platform` returns `takeover_feasible: false` (Shopify / Wix / Squarespace), do not promise migration. Set hosting to `connect_existing`.
- If `detect_crm` returns `supported: false` for their CRM, set the CRM ring to `owner_signup` or skip. Do not promise integration we don't have today.

### Turn 2: voice card

- Agent calls `propose_voice_card(traits, generic_sample, voice_sample, sample_context, source_pages)`.
- Side-by-side renders in the chat. Owner can edit, then accept.
- On accept, voice persists to `/opt/wc-solns/<tenant_id>/kb/voice.md` and the structured panel JSON.
- Coach the owner: "Read the voice sample. Does this sound like you? Edit anything that doesn't."

### Turn 3: facts + CRM mapping

- Agent calls `confirm_company_facts(name, phone, address, hours, ...)`. Persists to `kb/company.md`.
- If the tenant has a whitelisted Airtable base, agent calls `fetch_airtable_schema()` then `propose_crm_mapping(base_id, table_name, field_mapping, segments, proposed_actions)`.
- Owner accepts the segment counts and proposed automations.
- Agent calls `record_provisioning_plan(items=[...])` so each of the 7 pipelines has a strategy + credential method recorded for the handoff doc.

### Turn 4: connect Google + activate rings

- Agent calls `request_credential(service="google", method="oauth")`. UI renders the button.
- Owner clicks "Connect your Google account". OAuth round-trip happens in their browser, with their 2FA, on their screen.
- After callback: agent calls `activate_pipeline(role_slug, step)` for each of `gbp`, `seo`, `reviews`, `email_assistant` to advance them through credentials -> config -> connected.
- Agent calls `capture_baseline()` to run the validation probe and freeze the Day-1 numbers.
- Agent calls `mark_activation_complete(tier, owner_name, owner_email, business_name, note)`. This seeds `automations.json` from the tier catalog and emails the handoff letter.

### Roles outside Google's one-click

- `chat_widget` and `blog`: KB-only, no external creds. Should auto-advance to `connected` once company + voice + services KB sections are written.
- `social`: Meta OAuth ships in Phase 2. For now, set strategy `owner_signup` in the provisioning plan and skip. Be explicit with the client about the timeline.

---

## 4. The "never log in for them" rule

Hard rule. Enforced verbally and structurally.

- [ ] If the client says "just do it for me," restate the rule. "I can't take your password. It violates Google's terms, it breaks your 2FA, and it removes the audit trail that protects you."
- [ ] If the client cannot be talked out of it for a non-OAuth provider, use 1Password Item Sharing or Bitwarden Send. Never email. Document written authorization in the call notes. Rotate the credential immediately after use.
- [ ] If a `wcas_provisioned` provider is required (Twilio sub-account, GHL sub-account, Hostinger container), Sam clicks "Provision [service]" in `/admin` (Phase 3, not yet shipped). Until `/admin` ships, do this manually on the VPS and log it in `audit_log.record`. The client never sees a paste box for these.
- [ ] If you slip and see a cleartext credential, rotate it that day, write it up in `lessons/`, and tell the client.

---

## 5. Credential capture playbook

| Provider | Pattern | Method | Who clicks | Notes |
|---|---|---|---|---|
| Google (Gmail, GBP, GSC, GA4, Calendar) | A | OAuth | Client | One click closes 4 of 7 rings. `request_credential(service="google")`. |
| Meta (FB, IG) | A | OAuth | Client | Phase 2. For now skip the `social` ring. |
| QuickBooks Online | A | OAuth | Client | Out of hackathon scope. Owner-signup if needed. |
| GHL | A or C | OAuth or sub-account | Client (A) / Sam (C) | Decided at stack inventory. Pattern C provisions on the WCAS agency. |
| Twilio | B or C | API token paste or sub-account | Client (B) / Sam (C) | Pattern C uses the WCAS master account SID (see `reference_twilio_account` memory). |
| Hostinger | B or C | App password / SFTP or container | Client (B) / Sam (C) | Pattern C is ToS-clean under Annex 1. Don't use Hostinger trademarks in client copy. |
| Airtable | A or limited C | OAuth / PAT or interface-only | Client | No true white-label tier. |
| Vapi | B only | API key paste | Client | Skip Pattern C. Client pays Vapi directly. |
| BrightLocal | C only | Master + per-tenant location | Sam | `/opt/wc-solns/_platform/brightlocal/master.json`. |
| Connecteam | B only | API key paste | Client | AP edge case. |
| Gmail App Password | B | 16-char paste | Client | Workaround when OAuth gets revoked. Client visits `myaccount.google.com/apppasswords` and pastes themselves. |
| WordPress (blog publishing) | B | App password paste | Client | Per-site Application Password. |

For every Pattern C row, master credentials live ONLY at `/opt/wc-solns/_platform/<provider>/master.json` (chmod 600, root-owned). Tenant code path is forbidden from reading `_platform/`.

For every Pattern A or B row, credentials land at `/opt/wc-solns/<tenant_id>/credentials/<provider>.json` chmod 600 via `services/credentials.py`.

---

## 6. What success looks like

End of call, all of these must be true:

- [ ] `mark_activation_complete` fired with the correct `tier`, `owner_name`, `owner_email`, `business_name`.
- [ ] `/opt/wc-solns/<tenant_id>/state_snapshot/activation.json` shows `activated_at` set.
- [ ] `/opt/wc-solns/<tenant_id>/config/automations.json` has tier-default entries seeded (count matches `tier_default_count` in the tool result).
- [ ] `/opt/wc-solns/<tenant_id>/kb/` has populated `company.md`, `voice.md`, `provisioning_plan.md`, and a CRM mapping doc if one was made.
- [ ] All accepted rings reached `connected` or `first_run` in the wizard UI.
- [ ] Handoff letter delivered to the owner's inbox. `handoff_sent: true` in the tool result.
- [ ] Sam's alert email fired (rate-limited per `email_sender.alert_sam`).
- [ ] Onboarding Completed flag set on the Airtable Clients row.

If any of these are false, the activation is incomplete. Do not declare done. Stay on the call or schedule a 15-minute follow-up.

---

## 7. Post-call follow-through

### T+24h

- [ ] Open the dashboard via the planned tenant impersonation surface (read-only). Sanity check every ring rendered correctly on mobile.
- [ ] Verify the first scheduled pipeline run executed. Check heartbeat on the dashboard home stats.
- [ ] Read the activation transcript in the audit log. Look for any tool that returned `not_yet_implemented`, `reconnect_required`, or `partial`.
- [ ] If anything is off, send the owner a short note before they notice. Don't wait for them to ping.

### T+7d

- [ ] First full week of automation output reviewed. Confirm nothing auto-sent that shouldn't have. The 30-day all-drafts policy means every outbound stays in drafts for the first month.
- [ ] Read the feedback-button submissions for this tenant.
- [ ] Send the "week one" check-in note. Two questions: "Anything sound off?" and "Anything missing?"
- [ ] If a ring is still stuck before `first_run`, fix the underlying issue (usually a missing scope or a misnamed Airtable field) before the 14-day mark.
- [ ] Schedule the 30-day review meeting on the calendar now.

---

## 8. Failure modes and escapes

### OAuth flow breaks mid-redirect

- Symptom: client clicks Connect, lands on a Google error or hits the callback with no session.
- Likely cause: SameSite cookie reset, third-party cookie blocker, the signed state cookie expired (5 min TTL).
- Escape: have them retry once. If it fails again, switch their browser (Chrome -> Safari or vice versa). If still failing, end the OAuth attempt for that provider on this call, schedule a 15-min follow-up.

### Owner doesn't have credentials

- Symptom: "What's my Google Business login?" or "I don't know who set up our domain."
- Escape: pause that ring. Mark it `owner_signup` in the provisioning plan. Send them the recovery instructions for the relevant provider, schedule a follow-up. Do not offer to recover credentials yourself.

### Client asks Sam to type the password

- Restate the rule. If they push, fall back to 1Password Item Sharing with written authorization, rotate after use. Log it in the call notes. This is the exception, not the pattern.

### Provisioning tool refuses to run

- Symptom: `error: "onboarding_not_approved"` from `create_ga4_property` or `verify_gsc_domain`.
- Cause: `Onboarding Approved` flag is false on the Airtable Clients row.
- Escape: flip the flag on the row, retry the tool call. The §0e gate in `dispatch()` reads this on every call.

### Voice card sounds wrong

- Symptom: owner reads the voice sample and says "that's not me."
- Escape: ask one targeted question ("how do you greet a regular customer?"), have the agent re-call `propose_voice_card` with the corrected traits and sample. Iterate up to 3 times. If it still sounds off, capture their literal phrasing in `kb/voice.md` via `write_kb_entry(section="voice", content=...)` and move on.

### CRM mapping is wrong

- Symptom: segment counts don't match the owner's mental model.
- Escape: ask which Airtable view they actually use. Rerun `propose_crm_mapping` with the corrected `field_mapping` and `segments`. Don't fight the system to match a stale base; whitelist the right base in `tenant_config.json` first.

### Mark activation complete fails

- Symptom: tool returns with `tier_seed_error` populated or `handoff_sent: false`.
- Escape: state is the source of truth, the activation flag is set even when the side effects fail. Manually re-run the seed via `tenant_automations.seed_for_tier(tenant_id, tier)` from a `docker exec` shell and resend the handoff letter via the email composer. Note the failure in `lessons/`.

---

## 9. Tier-specific differences

All tiers include setup, onboarding, and maintenance. The flow above is the same; the scope and call length differ.

### Starter ($499.99/mo)

- Target call length: 30-40 minutes.
- Active rings: GBP, monthly performance report, baseline. The "plug the leaks" tier.
- Stack inventory: usually all `client_owned`. Most Starter clients already have a Google account and a basic site.
- Skip: chat_widget customization, blog, social, deep CRM mapping. Mark them as future tier upgrades in `provisioning_plan.md`.
- Handoff letter focus: "we're watching, monthly report incoming, here's how to reach us."

### Pro ($999.99/mo, recommended)

- Target call length: 60 minutes.
- Active rings: 5 of 7. GBP, SEO, reviews, email_assistant, and one of (chat_widget / blog / social / sales_pipeline depending on fit).
- Stack inventory: hybrid is common. Run the full "I have it / WCAS sets it up / Skip" matrix.
- Voice card and CRM mapping are required. The Pro tier's value depends on personalized output.
- Handoff letter focus: "5 roles activated, what runs this week, who approves what."

### Ultra ($2,499.99/mo)

- Target call length: 75-90 minutes. Schedule a 90-min Calendly slot for Ultra.
- Active rings: all 7 plus paid ads and deep analytics.
- Stack inventory: assume the client wants WCAS to provision everything they don't already own. Pattern C heavy.
- Run a second pass on voice and CRM with extra owner input. The Ultra tier's monthly strategy call with Sam starts from these artifacts.
- Handoff letter focus: "full agency replacement, monthly strategy cadence, escalation path."

Pricing rule: never invent new tiers or premium upcharges on a concierge call. New features ship into existing tiers, never as separate upsells. If the client asks for something outside their tier, note it in the call doc and follow up async with a tier-upgrade conversation.

---

End of runbook. Update the Corrections Log in the project CLAUDE.md after any deviation.
