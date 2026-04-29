# External blockers - things only Sam can unblock

Live list of dashboard features that are coded behind a vendor-side
gate Sam needs to clear in person. Each entry has a clear "what to do"
so he can knock them out in any order, at his own pace.

Not every blocker is urgent. Each row tags how much it gates and what
ships once it clears.

---

## 2A. Meta OAuth (Facebook + Instagram)

**Plan reference:** Phase 2A in `~/.claude/plans/alright-larry-the-hackathon-kind-swing.md`.
**Unlocks:** the `social` ring closes the OAuth loop. The agent calls
`request_credential(service="meta", method="oauth")` and the owner
clicks Connect once instead of pasting an API key.

**Why it is blocked:** Meta's `pages_manage_posts` and
`instagram_content_publish` scopes are restricted. Facebook App Review
is 1 to 4 weeks for production approval.

**Status:** App not yet registered (per `reference_oauth_apps_owned`).
Review queue not yet started.

### What Sam needs to do

1. Open https://developers.facebook.com and create a new Business App
   under the WCAS Business Manager account.
2. Configure the app:
   - App type: **Business**
   - Display name: `WCAS Client Dashboard`
   - Contact email: `westcoastautomationsolutions@gmail.com`
   - Privacy policy URL: `https://westcoastautomationsolutions.com/privacy`
   - Terms of service URL: `https://westcoastautomationsolutions.com/terms`
3. Add the **Facebook Login** + **Instagram Graph API** products.
4. Set the OAuth redirect URI to whatever the dashboard hostname is
   (currently `https://dashboard.westcoastautomationsolutions.com/auth/oauth/meta/callback`
   or local equivalent for dev).
5. Submit for App Review with these production scopes:
   - `pages_show_list`
   - `pages_read_engagement`
   - `pages_manage_posts`
   - `instagram_basic`
   - `instagram_content_publish`
   - `instagram_manage_insights`
6. Provide a screen recording in the review form showing:
   - Owner clicking "Connect Meta" in the dashboard
   - Selecting their FB Page + IG account
   - WCAS posting to FB + IG on their behalf
   - Posts visible on the public profile
7. Wait. Reviews land 1-4 weeks later. Treat it as a background task
   while other work continues.

### Once approved

Tell Larry. The Meta OAuth wiring (`dashboard_app/api/oauth_meta.py`)
is a clone of the Google OAuth flow already shipped, so the dashboard
side is ~1 day of work after Sam pastes the App ID + App Secret.

---

## 2B. GHL Marketplace OAuth

**Plan reference:** Phase 2B in `~/.claude/plans/alright-larry-the-hackathon-kind-swing.md`.
**Unlocks:** any tenant OAuth-connects their GHL sub-account in one
click instead of pasting an API key + Location ID. Closes
`chat_widget` and `blog` rings if those publish via GHL.

**Why it is blocked:** GHL Marketplace App development requires
**Agency Pro tier ($497/mo)** for the admin-level Private Integration
Token + Marketplace Developer access. Per
`feedback_no_ghl_agency_access`, Sam's current WCAS agency plan does
not include that tier.

**Status:** Marketplace app not registered. Tier not upgraded.

### Decision Sam needs to make first

Is the upgrade worth it? Three considerations:

1. **Cost:** $497/mo agency tier vs. the current tier. Is Sam already
   close to filling the seats / sub-account quota that would justify
   the upgrade?
2. **Workaround that works today:** the `api_key_paste` flow shipped
   in Phase 2C lets owners paste a Location-scoped API key + Location
   ID. That works on every GHL plan, no upgrade needed. The friction
   is concierge-acceptable: 60 seconds of paste vs. one-click OAuth.
3. **Self-serve future:** Phase 3A self-serve onboarding (Stripe-driven,
   no Sam present) is a much better experience with one-click OAuth.
   So the upgrade is most defensible right around the moment self-serve
   ships, not before.

**Recommendation:** Stay on api_key_paste through Phase 2 and the
first 2-3 concierge-onboarded clients. Reassess for upgrade when
self-serve goes into design (Phase 3A).

### What Sam needs to do (when ready)

1. In GHL, upgrade the WCAS agency to Agency Pro tier.
2. Open the Marketplace Developer dashboard (Settings -> Marketplace
   in agency view, or apps.gohighlevel.com).
3. Register a new app:
   - App name: `WCAS Client Dashboard`
   - Redirect URI:
     `https://dashboard.westcoastautomationsolutions.com/auth/oauth/ghl/callback`
   - Scopes: `contacts.readonly`, `contacts.write`,
     `conversations.readonly`, `conversations/message.write`,
     `opportunities.readonly`, `opportunities.write`,
     `locations.readonly`
4. Take note of the Client ID + Client Secret. Hand them to Larry.

### Once registered

Larry wires `dashboard_app/api/oauth_ghl.py` mirroring
`oauth_google.py`. The existing GHLProvider works as-is once the
OAuth-derived access token replaces the pasted API key. Estimate is
~1 day after the Marketplace app is registered.

---

## 2C. Twilio A2P 10DLC (separate from OAuth)

**Plan reference:** Tracked in `blocker_twilio_a2p_pending` memory.
**Unlocks:** "Text us back" automation on wcas.com, Voice Agent SMS
summaries, every WCAS-side SMS feature.

**Why it is blocked:** Twilio requires brand + campaign registration
for application-to-person 10-digit long codes. Without it, outbound
SMS silently 401s.

**Status:** Application not filed (as of memory check, was due Mon
Apr 27 in the 60-day sprint).

### What Sam needs to do

1. Log into the Twilio Console.
2. Go to **Messaging > A2P 10DLC**.
3. Complete **Brand registration** for WCAS (D-U-N-S optional but
   recommended; takes 1-3 business days for verification).
4. Complete **Campaign registration** for the WCAS use case
   ("Customer Care + Account Notifications").
5. Once approved, add `TWILIO_AUTH_TOKEN` to the n8n VPS env in
   hPanel (Compose -> Environment, same place as `ANTHROPIC_API_KEY`).
6. Redeploy the n8n container.

### Fallback

Until A2P clears, route SMS through Airtable Activities table
`tblIwgtKJktxk6Cdv` instead of Twilio. Lands in Sam's daily review
queue rather than silently failing.

---

## What is NOT blocked (just needs Sam to actually use it)

These three OAuth apps are already registered. The dashboard wires
them whenever Larry needs to:

- **Google** - LIVE in dashboard 0.5.0+
- **QuickBooks Online** - registered, ready to wire
- **Connecteam** - registered (Sam confirmed 2026-04-24)

---

## How this list is maintained

When Sam clears one of the blockers above, he tells Larry. Larry:
1. Removes that section from this doc.
2. Updates the relevant memory (`reference_oauth_apps_owned` for OAuth
   apps, `blocker_*` for trackers).
3. Files the implementation work as the next dashboard ticket.

When a new external blocker shows up (vendor app, review queue, tier
upgrade), Larry adds it here and files it in memory. The convention
is: **memory tracks the fact, this doc tracks the action.**
