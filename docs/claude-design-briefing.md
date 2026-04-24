# WCAS Client Dashboard: Design Briefing for Claude Design

You are Claude Design. A hackathon dashboard is mid-build. The brand and the interaction spec are locked. Your job is to critique the current state and propose specific wow-factor elevations within the locked spec. Details below.

---

## 1. Mission

The WestCoast Automation Solutions (WCAS) Client Dashboard is the product surface California small-business owners log into to see every automation WCAS runs on their behalf: SEO, Google Ads, review replies, morning client reports, sales follow-up, blog drafts, social, reputation, patrol reporting, and more. One tenant today is Americal Patrol (a 1986-founded security patrol company). More are coming.

**The anchor line for every design call:** *"An owner reading their business, not a user piloting a tool."*

The owner is not technical. They answer their own phones. They open this dashboard once a day or once a week and need to feel calmer, not busier. Every visual choice serves that feeling.

---

## 2. Hackathon stakes

- **Event:** internal Opus 4.7 hackathon, judged by Boris Cherny, Lydia Hallie, and the Claude team.
- **Submission:** Saturday April 26, 2026 (about 96 hours from now).
- **Deliverable:** live URL (already up at `dashboard.westcoastautomationsolutions.com`), public repo (`suaveshot/wcas-client-dashboard`), a 5-minute demo video, and a writeup.
- **Judged partly on implementation quality and creative use of Opus 4.7 capabilities.** A polished, opinionated UI is non-negotiable for the video.
- **Signature moments for the video:** narrative paragraph + 3 hero stat cards, drill-down drawer, 10-second undo chip on a real outbound action, activation wizard rings, close on a Monday-morning Sunday Digest PDF opening on iPhone.

Sam's two admitted weak spots are visual design and deep research. He wants you to make strong, specific recommendations. Do not ask him to choose colors. Choose them and defend the choice.

---

## 3. Companion files to attach alongside this briefing

If your input supports file attachments, Sam should attach these alongside the briefing text for highest fidelity. If it does not, the embedded content below is enough to proceed.

| File | Path | Why |
|---|---|---|
| `styles.css` | `dashboard_app/static/styles.css` | Full shipped stylesheet, about 36K tokens. The `.ap-*` component library. |
| `home.html` | `dashboard_app/templates/home.html` | Hero surface 1. Embedded in full below too. |
| `role_detail.html` | `dashboard_app/templates/role_detail.html` | Hero surface 2. Embedded below. |
| `tokens.css` | `../brand-kit/tokens.css` | Brand tokens. Embedded below. |
| `brand-brief.md` | `../brand-kit/brand-brief.md` | Full brand voice + imagery + typography rules. |

---

## 4. Brand foundation

### 4.1 Tokens (verbatim from `brand-kit/tokens.css`)

```css
:root {
  /* Surfaces */
  --bg:           #FBFAF7;   /* page background, warm off-white */
  --bg-alt:       #F4EFE6;   /* alternating section, sand */
  --bg-elev:      #FFFFFF;   /* cards, panels */
  --bg-warm:      #FFF6E8;   /* highlight tint, subtle orange wash */

  /* Ink */
  --ink:          #0F2A44;   /* headlines, primary text (deep navy) */
  --ink-soft:     #3D4A5C;   /* body text */
  --ink-muted:    #6B7280;   /* secondary text */
  --ink-faint:    #97A0AC;   /* tertiary text, captions */

  /* Brand */
  --accent:       #E97B2E;   /* primary CTA / link / highlight, sunrise orange */
  --accent-deep:  #C9631E;   /* hover state */
  --accent-soft:  #FCE7D2;   /* tinted background for accent surfaces */
  --teal:         #2E8FA8;   /* supporting accent, circuit teal */
  --teal-soft:    #DEEBF0;

  /* Status */
  --ok:           #2F9E5E;   /* success green: sparklines, up-trends, verified marks */
  --warn:         #C93838;   /* error red: behind/failing states, destructive actions */

  /* Borders + shadows */
  --border:       #E8E1D4;
  --border-soft:  #F1ECE0;
  --border-strong:#D9CFB9;
  --shadow-sm:    0 1px 2px rgba(15, 42, 68, 0.04);
  --shadow-md:    0 4px 12px rgba(15, 42, 68, 0.06);
  --shadow-lg:    0 16px 40px rgba(15, 42, 68, 0.08);
  --shadow-glow:  0 8px 24px rgba(233, 123, 46, 0.18);

  /* Radii */
  --r-xs: 4px; --r-sm: 8px; --r-md: 12px; --r-lg: 16px; --r-xl: 24px; --r-pill: 999px;

  /* Spacing */
  --s-1: 4px;  --s-2: 8px;   --s-3: 12px;  --s-4: 16px;  --s-5: 24px;
  --s-6: 32px; --s-7: 48px;  --s-8: 64px;  --s-9: 96px;  --s-10: 128px;

  /* Type */
  --font-display: 'DM Serif Display', 'Georgia', serif;
  --font-body:    'DM Sans', system-ui, -apple-system, 'Segoe UI', sans-serif;

  /* Layout */
  --max-w: 1200px; --max-w-text: 720px; --gutter: 20px;

  /* Motion */
  --ease:   cubic-bezier(0.22, 0.61, 0.36, 1);
  --t-fast: 140ms; --t-base: 220ms; --t-slow: 420ms;
}
```

Gutter scales to 32px at 768px, 48px at 1100px. `prefers-reduced-motion` kills animations globally.

### 4.2 Typography

- **Display:** DM Serif Display (regular + italic). Used ONLY on H1/H2, hero stat numbers, narrative paragraph, rec headline, drawer title.
- **Body:** DM Sans (400, 500, 600, 700). Everything else.
- Body size 18px, line-height 1.65, color `--ink-soft` on `--bg`.
- H1 clamps 32 to 56px. H2 clamps 26 to 40px. H3 clamps 22 to 28px.
- Eyebrow style: DM Sans 600, uppercase, 12px, letter-spacing 0.12em, color `--accent` or `--ink-muted`.
- Numbers: tabular-nums always. No jitter on hover.

### 4.3 Voice DOs and DON'Ts

**DO:**
- Plain-spoken, specific, short sentences.
- Confident but not salesy. Results over hype.
- Warm owner-to-owner tone. The reader is another small-business operator.
- "We sell things that already work." Lean on proof.

**DON'T:**
- **No em dashes anywhere.** Sam treats them as an AI tell. Use commas, periods, or parentheses.
- **No "Claude", "Opus", "Anthropic", "AI", or any model name in rendered client HTML.** The Ask button is the ✦ spark glyph plus the word "Ask." The assistant has no name.
- No fake trust badges ("California-built", "bonded & insured", stock 5-star rows).
- No invented testimonials. The only two real ones are Don P. and Itzel.
- No medical verticals (HIPAA out of scope).
- No apologies for newsletter or lead magnets.
- No emojis in product copy. The ✦ spark glyph is the only glyph that appears alongside identity text.

### 4.4 Imagery style

- Documentary 35mm film. Real people, warm daylight, slight grain, coastal California light.
- Not CG marketing render, not stock-photo teeth, not isometric illustrations, not over-saturated gradients.
- Empty states use warm documentary photography, not illustrations.
- OG previews: 1200x630, bold type over brand orange (not cream).

---

## 5. Voice samples (copy verbatim when the surface calls for it)

1. **Narrative hero (Monday):** *"Here's your week, Sam. Reviews and Morning Reports did the heavy lifting. One thing to watch is Ads pacing, and I've queued a recommendation below."*
2. **Activation first message:** *"Welcome in. I'm going to set up 14 roles for you, one at a time. Most owners finish in about 45 minutes. If you need to stop, I save as we go. Ready to start with the easy one?"*
3. **Attention banner (behind):** *"Reviews is 3 days behind on Google replies."*
4. **Attention banner (error):** *"Ads stopped running last night. I paused it and flagged it for you."*
5. **Empty state (grid, pre-activation):** *"Your roles aren't live yet. Finish setup to see them work."*
6. **Empty state (feed):** *"Nothing's run yet. Once your roles go live, you'll see every action here."*
7. **Empty state (recs):** *"Nothing needs fixing this week. I'll keep watching."*
8. **Undo chip (review reply):** *"Reply queued to Google review. Undo."*
9. **Rec headline (pacing):** *"Ads is pacing 18% under goal this month."*
10. **Data export confirmation:** *"Exported. The file's in your downloads. Nothing's leaving WCAS."*

**Section headers are questions:**
- *"What worked this week?"* for hero stats.
- *"What happened behind the scenes?"* for the activity feed.
- *"What should we fix?"* for recommendations.

**Error pattern:** *"Something went sideways on {role}. I'm retrying. If it fails twice more, I'll ping you."*

---

## 6. Locked design decisions (15)

These are settled. Do not propose changing them. Propose elevation within them.

1. **Fixed left sidebar (256px) + top bar with global search pill.** 5 nav items: Home, Roles, Activity, Recommendations, Settings. A "Pinned Roles" section auto-populates from the owner's top 3 drill-downs in the last 14 days. No hamburger-only mode at desktop.
2. **Sidebar label is "Roles," not "Pipelines."** Owners understand roles (SEO, Reviews, Ads). They do not understand infrastructure.
3. **"Ask" is a first-class topbar pill with the spark glyph (✦), NOT a floating bubble.** Floating chat bubbles are a support affordance. Navbar placement is a product affordance. The assistant is never named. The glyph is the identity. The verb is the action.
4. **Three hero stats, not one, not eight.** Weeks Saved, Revenue Influenced, Goal Progress. DM Serif Display 80px numbers, delta line, 14-day sparkline trajectory-colored by goal status, tiny verified-check inline with tooltip.
5. **Narrative paragraph sits ABOVE the hero stats.** Generated fresh per week, DM Serif 24px, max 3 sentences. This is the judge-memory moment.
6. **Attention banner is conditional and single.** One banner at a time ever. No permanent reserved strip. Four priority tiers: error (red), behind (orange), consent (navy), opportunity (teal).
7. **Pipeline drill-down should be a right-side drawer, not a new route.** (Current build uses a route; candidate for elevation.) 70/30 body split: logs + chart left, Linear-style right-rail status panel. Three-action footer with Apply, Dismiss, Ask all equal weight.
8. **Recommendation cards use GA4 Insight Card format.** Goal anchor chip, serif headline, DM Sans reason, equal-weight 3-button footer. Max 3 visible on Home, "See all" opens `/recommendations`.
9. **Activity feed has Slack-2026 Dense/Detailed toggle.** Consecutive same-role events group into a single expandable row. Every row links somewhere.
10. **Undo chip is the trust moment.** Gmail-style 10s delayed write for outbound actions only (emails, SMS, Google replies, ad bid changes). Dark navy chip bottom-left with 10-dot countdown. Post-commit the feed row gets a permanent shield-check audit glyph.
11. **Activation wizard is 45/55 chat-left / rings-right, 3x5 ring grid.** 15th role centered below. Each ring has 4 sub-state arcs (Credentials, Config, Connected, First run, clockwise from 12). Autosave every 500ms. Email resume link after 24h idle.
12. **Two status tokens only.** `--ok #2F9E5E` and `--warn #C93838`. Everything else lives in the existing tokens. No new hexes anywhere.
13. **Photography, not iso-illustration.** Every empty state uses a warm documentary image from `brand-kit/assets/` style (plumber at counter, mechanic in shop). Ramp-coded, not Notion-coded.
14. **Section headers are questions.** "What happened behind the scenes?" / "What should we fix?" / "What worked this week?"
15. **Skeleton loaders everywhere, never spinners.** 1.6s pulse, staggered delays (120ms / 240ms / 360ms) for wave effect. Static block under reduced-motion.

---

## 7. Surface map (current build, as of 2026-04-23 morning)

| Route | Template | What it is | Status |
|---|---|---|---|
| `/` | `static/index.html` | Marketing landing for the dashboard (pre-login). | Shipped Day 1. |
| `/dashboard` | `home.html` | **Hero surface 1.** Narrative + 3 hero stats + quick actions + role grid + split feed/recs. | Shipped with demo context, wiring to live telemetry in progress. |
| `/roles` | redirect | Redirects to `/dashboard` (`#roles` anchor planned). | Placeholder. |
| `/roles/{slug}` | `role_detail.html` | **Hero surface 2.** Per-role page: state grid, timeline of last run, raw logs, Ask-about-this-role form. | Shipped. Spec calls for a slide-in drawer instead of a full route. |
| `/activity` | `activity.html` | Full activity feed, WebSocket wired. | Shipped. |
| `/recommendations` | `recommendations.html` | Full rec list with evidence, confidence, reversibility, draft tab. | Shipped. |
| `/goals` | `goals.html` | Goal setting per role. | Shipped. |
| `/approvals` | `approvals.html` | Outbound-draft approval queue (A4 precursor). | Shipped. |
| `/settings` | `settings.html` | Natural-language settings plus classic toggles. | Shipped with the text input, classic panel still basic. |
| `/activate` | `placeholder.html` | Activation wizard, 3x5 ring grid, chat. | Day 3 build. Currently renders a basic placeholder. |
| `/auth/login` | `auth/login.html` | Magic-link email entry. | Shipped. Uses landing layout, not shell. |
| `/auth/check_inbox` | `auth/check_inbox.html` | "Check your email" confirmation. | Shipped. |
| `/auth/verify` | (redirect flow) | Token consumer, sets session cookie, redirects to `/dashboard`. | Shipped. |
| `/terms`, `/privacy` | `placeholder.html` | Legal stubs. | Placeholder content. |

---

## 8. Hero surface 1: Home (`dashboard_app/templates/home.html`)

Full rendered template. Jinja2 variables are populated from `/api/pipelines`, `/api/brand`, and the tenant telemetry snapshot.

```html
<!doctype html>
<html lang="en">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>{{ tenant_name }} | WCAS</title>
    <meta name="description" content="Your automation agency, in one place.">
    <meta name="theme-color" content="#FBFAF7">
    <meta name="robots" content="noindex">

    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=DM+Serif+Display:ital@0;1&family=DM+Sans:wght@400;500;600;700&display=swap" rel="stylesheet">

    <link rel="stylesheet" href="/static/styles.css?v=20260422d">
</head>
<body>
<a href="#ap-main" class="ap-sr">Skip to main content</a>

<div class="ap-shell">

    {# ============== SIDEBAR ============== #}
    <aside id="ap-shell-rail" class="ap-shell__rail" aria-label="Primary navigation">
        <div class="ap-shell__rail-brand">
            <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 2 2 7l10 5 10-5-10-5z"/><path d="m2 17 10 5 10-5"/><path d="m2 12 10 5 10-5"/></svg>
            <span>{{ tenant_name }}</span>
        </div>

        {% if rail_health and rail_health.total %}
        <div class="ap-shell__rail-health" role="status" aria-label="Roles health summary">
            <span class="ap-rail-health-strong">{{ rail_health.total }}</span> roles ·
            <span class="ap-rail-health-ok">{{ rail_health.running }}</span> running
            {% if rail_health.attention %}· <span class="ap-rail-health-warn">{{ rail_health.attention }}</span> attention{% endif %}
            {% if rail_health.error %}· <span class="ap-rail-health-err">{{ rail_health.error }}</span> error{% endif %}
            {% if rail_health.paused %}· <span class="ap-rail-health-muted">{{ rail_health.paused }}</span> paused{% endif %}
        </div>
        {% endif %}

        <nav aria-label="Main">
            <ul class="ap-shell__rail-nav">
                <li><a class="ap-shell__rail-item ap-shell__rail-item--active" href="/dashboard">Home</a></li>
                <li><a class="ap-shell__rail-item" href="/roles">Roles</a></li>
                <li><a class="ap-shell__rail-item" href="/activity">Activity</a></li>
                <li><a class="ap-shell__rail-item" href="/recommendations">Recommendations</a></li>
                <li><a class="ap-shell__rail-item" href="/settings">Settings</a></li>
            </ul>
        </nav>

        <div class="ap-shell__rail-eyebrow">Pinned roles</div>
        <ul class="ap-shell__rail-pinned">
            {% for role in pinned_roles %}
            <li>
                <a class="ap-shell__rail-pinned-item {% if role.active %}ap-shell__rail-pinned-item--active{% endif %}" href="/roles/{{ role.slug }}">
                    <span class="ap-shell__rail-dot ap-shell__rail-dot--{{ role.state|default('active') }}{% if role.pulse %} ap-shell__rail-dot--pulse{% endif %}" aria-hidden="true"></span>
                    <span class="ap-shell__rail-pinned-name">{{ role.name }}</span>
                    {% if role.auto %}<span class="ap-shell__rail-pinned-auto">auto</span>{% endif %}
                </a>
            </li>
            {% endfor %}
        </ul>

        {% if recent_asks %}
        <div class="ap-shell__rail-eyebrow ap-shell__rail-eyebrow--recent">Recent asks</div>
        <ul class="ap-shell__rail-recent">
            {% for ask in recent_asks %}
            <li>
                <button type="button" class="ap-shell__rail-recent-pill" data-question="{{ ask.question }}">
                    <span class="ap-shell__rail-recent-spark" aria-hidden="true">✦</span>
                    <span class="ap-shell__rail-recent-text">{{ ask.question }}</span>
                </button>
            </li>
            {% endfor %}
        </ul>
        {% endif %}

        <div class="ap-shell__rail-footer">
            <button type="button" class="ap-shell__rail-account-btn" aria-haspopup="menu" aria-label="Account menu">
                <span class="ap-shell__rail-avatar" aria-hidden="true">{{ owner_initials }}</span>
                <span class="ap-shell__rail-account">
                    <span class="ap-shell__rail-account-name ap-priv">{{ owner_name }}</span>
                    <span class="ap-shell__rail-account-meta">Plan: Operator</span>
                </span>
            </button>
        </div>
    </aside>

    {# ============== MAIN ============== #}
    <div class="ap-shell__main">

        {# Topbar #}
        <header class="ap-shell__topbar">
            <button type="button" class="ap-shell__rail-trigger" aria-label="Open navigation" aria-controls="ap-shell-rail" aria-expanded="false">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" width="20" height="20" aria-hidden="true"><line x1="3" y1="6" x2="21" y2="6"/><line x1="3" y1="12" x2="21" y2="12"/><line x1="3" y1="18" x2="21" y2="18"/></svg>
            </button>
            <div class="ap-shell__breadcrumb">
                <span>Home</span>
                <span class="ap-shell__breadcrumb-sep">/</span>
                <span class="ap-shell__breadcrumb-current">This week</span>
            </div>

            <form class="ap-search-pill" role="search" onsubmit="return false">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="11" cy="11" r="8"/><path d="m21 21-4.35-4.35"/></svg>
                <input class="ap-search-pill__input" type="search" placeholder="Search or ask…" aria-label="Search or ask">
                <span class="ap-search-pill__shortcut">⌘K</span>
            </form>

            <div class="ap-shell__topbar-actions">
                <button class="ap-ask" type="button">
                    <span class="ap-ask__spark" aria-hidden="true">✦</span>
                    Ask
                </button>
                <button class="ap-shell__bell" type="button" aria-label="Notifications">
                    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M6 8a6 6 0 0 1 12 0c0 7 3 9 3 9H3s3-2 3-9"/><path d="M10.3 21a1.94 1.94 0 0 0 3.4 0"/></svg>
                    {% if notifications_count %}<span class="ap-shell__bell-badge">{{ notifications_count if notifications_count < 10 else '9+' }}</span>{% endif %}
                </button>
            </div>
        </header>

        {# Canvas #}
        <main id="ap-main" class="ap-canvas">

            {% if broadcast %}
            <div class="ap-broadcast" role="status">
                <div class="ap-broadcast__inner">{{ broadcast.text }}</div>
            </div>
            {% endif %}

            {# Row 0, Attention banner (conditional, single) #}
            {% if attention %}
            <div class="ap-attention ap-attention--{{ attention.kind }}" role="status">
                <svg class="ap-attention__icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M10.29 3.86 1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>
                <span class="ap-attention__text">{{ attention.text }}</span>
                <div class="ap-attention__actions">
                    <button class="ap-attention__btn ap-attention__btn--primary" type="button">Apply</button>
                    <button class="ap-attention__btn" type="button">Dismiss</button>
                    <button class="ap-attention__btn" type="button">Snooze 24h</button>
                </div>
            </div>
            {% endif %}

            {# Row 1, Narrative summary #}
            <section class="ap-narrative" aria-labelledby="ap-narrative-label">
                <span id="ap-narrative-label" class="ap-narrative__eyebrow">This week · {{ today_date }}</span>
                <p class="ap-narrative__body">{{ narrative }}</p>
                <p class="ap-narrative__meta">Updated {{ refresh_ago }} · Next refresh {{ next_refresh }}</p>
            </section>

            {# Row 2, Hero stats strip #}
            <section class="ap-hero-stats" aria-label="Key results">
                {% for stat in hero_stats %}
                <article class="ap-hero-stat {% if stat.trajectory == 'warn' %}ap-hero-stat--warn{% elif stat.trajectory == 'flat' %}ap-hero-stat--flat{% endif %}"
                         aria-label="{{ stat.label }}: {{ stat.value }}, {{ stat.delta_text }}, {{ stat.status_text }}">
                    <span class="ap-hero-stat__eyebrow">{{ stat.label }}</span>
                    <div class="ap-hero-stat__value">
                        <span class="ap-priv">{{ stat.value }}</span>
                        <svg class="ap-hero-stat__verified" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"/><polyline points="22 4 12 14.01 9 11.01"/><title>{{ stat.verified_tip }}</title></svg>
                    </div>
                    <div class="ap-hero-stat__delta ap-hero-stat__delta--{{ stat.direction }}">
                        {% if stat.direction == 'up' %}↗{% elif stat.direction == 'down' %}↘{% else %}→{% endif %}
                        <span>{{ stat.delta_text }}</span>
                    </div>
                    <svg class="ap-hero-stat__spark" viewBox="0 0 200 28" preserveAspectRatio="none" aria-hidden="true">
                        <path d="{{ stat.spark_path }}"/>
                    </svg>
                </article>
                {% endfor %}
            </section>

            {# Row 3, Quick action chips #}
            <nav class="ap-quick-actions" aria-label="Quick actions">
                <button class="ap-chip" type="button">Set a goal</button>
                <button class="ap-chip" type="button">Pause a role</button>
                <button class="ap-chip" type="button">Request something</button>
                <button class="ap-chip ap-chip--ask" type="button">
                    <span class="ap-chip__icon" aria-hidden="true">✦</span>
                    Ask
                </button>
            </nav>

            {# Row 4, Role grid #}
            <section aria-labelledby="ap-roles-h">
                <div class="ap-section-head">
                    <div class="ap-section-head__titles">
                        <span class="ap-section-head__eyebrow">What's running</span>
                        <h2 id="ap-roles-h">Your roles</h2>
                    </div>
                    <div class="ap-section-head__controls">
                        <span style="font-size:13px; color:var(--ink-muted);">View: <a href="#" style="color:var(--ink); font-weight:500; text-decoration:none;">All</a> · <a href="#" style="color:var(--ink-muted); text-decoration:none;">Pinned only</a></span>
                    </div>
                </div>

                <div class="ap-role-grid">
                    {% for role in roles %}
                    <a class="ap-role-card ap-role-card--{{ role.state }}" href="/roles/{{ role.slug }}" aria-label="{{ role.name }}, {{ role.state_text }}">
                        {% if role.grade %}
                        <span class="ap-role-card__grade ap-role-card__grade--{{ role.grade|lower }}" aria-label="Grade {{ role.grade }}">{{ role.grade }}</span>
                        {% endif %}
                        <div class="ap-role-card__head">
                            <span class="ap-role-card__dot" aria-hidden="true"></span>
                            <span class="ap-role-card__title">{{ role.name }}</span>
                        </div>
                        <div class="ap-role-card__activity">
                            <strong class="ap-num">{{ role.actions }}</strong> actions
                            <span class="ap-role-card__sep">·</span>
                            <em class="ap-priv ap-num">${{ role.influenced }}</em> influenced
                        </div>
                        <svg class="ap-role-card__spark" viewBox="0 0 200 24" preserveAspectRatio="none" aria-hidden="true">
                            <path d="{{ role.spark_path }}"/>
                        </svg>
                        <div class="ap-role-card__meta">Last run {{ role.last_run }}</div>
                    </a>
                    {% endfor %}
                </div>
            </section>

            {# Row 5, Split feed 60% + recs 40% #}
            <div class="ap-split">
                <section class="ap-split__feed" aria-labelledby="ap-feed-h">
                    <div class="ap-section-head">
                        <div class="ap-section-head__titles">
                            <h2 id="ap-feed-h">What happened behind the scenes?</h2>
                        </div>
                        <div class="ap-section-head__controls">
                            <div class="ap-feed__toggle" role="tablist" aria-label="Feed density">
                                <button class="ap-feed__toggle-btn ap-feed__toggle-btn--active" type="button" role="tab" aria-selected="true">Detailed</button>
                                <button class="ap-feed__toggle-btn" type="button" role="tab" aria-selected="false">Dense</button>
                            </div>
                        </div>
                    </div>

                    <div class="ap-feed" role="log" aria-live="polite">
                        <div class="ap-feed__live">Live · streaming</div>
                        {% for row in feed %}
                        <article class="ap-feed__row">
                            <span class="ap-feed__time">{{ row.time }}</span>
                            <svg class="ap-feed__icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="{{ row.icon_path }}"/></svg>
                            <div class="ap-feed__body">
                                <div class="ap-feed__text">
                                    <a class="ap-feed__role-pill" href="/roles/{{ row.role_slug }}">{{ row.role }}</a>
                                    {{ row.action }}
                                </div>
                                {% if row.link %}
                                <a class="ap-feed__link" href="{{ row.link }}">{{ row.link_text }} ↗</a>
                                {% endif %}
                            </div>
                            <span style="font-size:12px;color:var(--ink-faint);">{{ row.relative }}</span>
                        </article>
                        {% endfor %}
                    </div>
                </section>

                <section class="ap-split__recs" aria-labelledby="ap-recs-h">
                    <div class="ap-section-head">
                        <div class="ap-section-head__titles">
                            <h2 id="ap-recs-h">What should we fix?</h2>
                        </div>
                    </div>

                    <div class="ap-rec-stack">
                        {% for rec in recommendations %}
                        <article class="ap-rec">
                            {% if rec.goal %}
                            <span class="ap-goal-chip">GOAL: {{ rec.goal }}</span>
                            {% endif %}
                            <h3 class="ap-rec__headline">{{ rec.headline }}</h3>
                            <p class="ap-rec__reason">{{ rec.reason }}</p>
                            <div class="ap-rec__footer">
                                <div class="ap-btn-group">
                                    <button class="ap-btn ap-btn--primary" type="button">Apply</button>
                                    <button class="ap-btn ap-btn--ghost" type="button">Dismiss</button>
                                    <button class="ap-btn ap-btn--ghost ap-btn--spark" type="button">
                                        <span aria-hidden="true" style="color:var(--accent);font-size:14px;">✦</span>
                                        Ask
                                    </button>
                                </div>
                            </div>
                        </article>
                        {% else %}
                        <div class="ap-rec-empty">
                            <p class="ap-rec-empty__title">Nothing needs your attention right now.</p>
                            <p class="ap-rec-empty__body">Your roles are all running within their expected cadence. We'll queue a rec here the moment something drifts.</p>
                        </div>
                        {% endfor %}
                        {% if total_recs %}
                        <a class="ap-rec__see-all" href="/recommendations">See all ({{ total_recs }}) →</a>
                        {% endif %}
                    </div>
                </section>
            </div>
        </main>
    </div>
</div>

<div class="ap-toast-stack" aria-live="polite" aria-atomic="true"></div>

<script src="/static/undo.js?v=20260422d" defer></script>
<script src="/static/shell.js?v=20260422d" defer></script>
</body>
</html>
```

*Inline SVGs and minor blocks have been condensed. The full file is 343 lines; the structural content and every class on every element is preserved above.*

---

## 9. Hero surface 2: Role detail (`dashboard_app/templates/role_detail.html`)

Drill-down page for an individual role. Shows state, the last run's timeline, raw logs, and a role-scoped Ask form.

```html
<!doctype html>
<html lang="en">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>{{ role_name }} | WCAS</title>
    <meta name="robots" content="noindex">
    <link href="https://fonts.googleapis.com/css2?family=DM+Serif+Display:ital@0;1&family=DM+Sans:wght@400;500;600;700&display=swap" rel="stylesheet">
    <link rel="stylesheet" href="/static/styles.css?v=20260422d">
</head>
<body>
<a href="#ap-main" class="ap-sr">Skip to main content</a>

<div class="ap-shell">

    <aside id="ap-shell-rail" class="ap-shell__rail" aria-label="Primary navigation">
        <div class="ap-shell__rail-brand">
            <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" aria-hidden="true"><path d="M12 2 2 7l10 5 10-5-10-5z"/><path d="m2 17 10 5 10-5"/><path d="m2 12 10 5 10-5"/></svg>
            <span>Americal Patrol</span>
        </div>
        <nav aria-label="Main">
            <ul class="ap-shell__rail-nav">
                <li><a class="ap-shell__rail-item" href="/dashboard">Home</a></li>
                <li><a class="ap-shell__rail-item ap-shell__rail-item--active" href="/roles">Roles</a></li>
                <li><a class="ap-shell__rail-item" href="/activity">Activity</a></li>
                <li><a class="ap-shell__rail-item" href="/recommendations">Recommendations</a></li>
                <li><a class="ap-shell__rail-item" href="/settings">Settings</a></li>
            </ul>
        </nav>
    </aside>

    <div class="ap-shell__main">

        <header class="ap-shell__topbar">
            <div class="ap-shell__breadcrumb">
                <a href="/dashboard">Home</a>
                <span class="ap-shell__breadcrumb-sep">/</span>
                <span class="ap-shell__breadcrumb-current">{{ role_name }}</span>
            </div>
        </header>

        <main id="ap-main" class="ap-canvas">

            <section class="ap-role-detail">
                <div class="ap-role-detail__head">
                    <span class="ap-role-detail__eyebrow">Role</span>
                    <h1 class="ap-role-detail__title">{{ role_name }}</h1>
                    <p class="ap-role-detail__meta">
                        Status: <strong class="ap-role-detail__status ap-role-detail__status--{{ status }}">{{ status_text }}</strong>
                        &middot; Last run {{ last_run }}
                    </p>
                    {% if summary %}
                    <p class="ap-role-detail__summary">{{ summary }}</p>
                    {% endif %}
                </div>

                {% if has_snapshot %}
                    {% if state_rows %}
                    <div class="ap-role-detail__grid" aria-label="State summary">
                        {% for row in state_rows %}
                        <div class="ap-role-detail__cell">
                            <span class="ap-role-detail__cell-label">{{ row.label }}</span>
                            <span class="ap-role-detail__cell-value ap-priv">{{ row.value }}</span>
                        </div>
                        {% endfor %}
                    </div>
                    {% endif %}

                    {% if timeline %}
                    <div class="ap-role-detail__timeline" aria-label="What happened on the last run">
                        <h2 class="ap-role-detail__timeline-title">What happened on the last run</h2>
                        <ol class="ap-timeline">
                            {% for event in timeline %}
                            <li class="ap-timeline__item ap-timeline__item--{{ event.level }}">
                                <span class="ap-timeline__dot" aria-hidden="true"></span>
                                <div class="ap-timeline__body">
                                    <span class="ap-timeline__time">{{ event.time }}</span>
                                    <span class="ap-timeline__message">{{ event.message }}</span>
                                </div>
                            </li>
                            {% endfor %}
                        </ol>
                    </div>
                    {% endif %}

                    {% if log_tail %}
                    <details class="ap-role-detail__logs-details">
                        <summary class="ap-role-detail__logs-summary">Show the raw technical log</summary>
                        <pre class="ap-role-detail__logs-pre">{{ log_tail }}</pre>
                    </details>
                    {% endif %}

                    <div class="ap-role-detail__receipts-row">
                        <button class="ap-btn ap-btn--ghost ap-receipts-trigger"
                                type="button"
                                data-pipeline-id="{{ role_slug }}"
                                data-role-name="{{ role_name }}">
                            Show the last 25 receipts
                        </button>
                        <span class="ap-role-detail__receipts-hint">
                            The actual text of everything this role sent on your behalf.
                        </span>
                    </div>
                {% else %}
                    <p class="ap-role-detail__empty">
                        This role is connected and queued for its first run. Its next scheduled execution will push the first heartbeat; this page wakes up automatically when that lands.
                    </p>
                {% endif %}

                <div class="ap-role-detail__ask" data-role-slug="{{ role_slug }}">
                    <h2 class="ap-role-detail__ask-title">
                        <span class="ap-ask__spark" aria-hidden="true">✦</span>
                        Ask a question about this role
                    </h2>
                    <p class="ap-role-detail__ask-hint">
                        One or two sentences back, grounded in this role's most recent telemetry.
                    </p>
                    <form class="ap-ask-form" onsubmit="return false;">
                        <label for="ap-ask-input" class="ap-sr">Your question</label>
                        <textarea id="ap-ask-input" class="ap-ask-input" rows="2"
                                  placeholder="e.g. Did the last run finish cleanly? Anything I should act on?"></textarea>
                        <button type="button" class="ap-btn ap-btn--primary ap-ask-submit">Ask</button>
                    </form>
                    <div class="ap-ask-result" aria-live="polite"></div>
                </div>

            </section>
        </main>
    </div>
</div>

<div class="ap-toast-stack" aria-live="polite" aria-atomic="true"></div>

<script src="/static/undo.js?v=20260422d" defer></script>
<script src="/static/shell.js?v=20260422d" defer></script>
<script src="/static/ask.js?v=20260422d" defer></script>
</body>
</html>
```

---

## 10. Component inventory (the `.ap-*` library)

Every shipped class, grouped by surface. Pull the full definitions from `styles.css` if attached.

### 10.1 Utilities

| Class | Purpose |
|---|---|
| `.ap-sr` | Screen-reader only text (visually hidden). |
| `.ap-num` | `font-variant-numeric: tabular-nums`. Applied on every number. |
| `.ap-priv` | Dollar / PII targets. Blurs under Privacy Mode. |
| `.ap-skel`, `.ap-skel--delay-1/2/3` | Skeleton loader pulses with staggered 120/240/360ms delay. |

### 10.2 Shell

| Class | Purpose |
|---|---|
| `.ap-shell` | 256px sidebar + main grid layout. |
| `.ap-shell__rail` | Sticky sidebar, `--bg-elev` background. |
| `.ap-shell__rail-brand` | Tenant logo + name row at top of rail. |
| `.ap-shell__rail-health` | Inline rollup: "14 roles · 12 running · 1 attention." |
| `.ap-shell__rail-nav`, `.ap-shell__rail-item`, `.ap-shell__rail-item--active` | Primary nav items + 2px left accent bar on active. |
| `.ap-shell__rail-eyebrow`, `.ap-shell__rail-eyebrow--recent` | Uppercase section labels in the rail. |
| `.ap-shell__rail-pinned`, `.ap-shell__rail-pinned-item`, `.ap-shell__rail-pinned-item--active`, `.ap-shell__rail-pinned-auto` | Mercury-style pinned-roles list with dot + auto micro-label. |
| `.ap-shell__rail-recent`, `.ap-shell__rail-recent-pill` | Recent Ask history pills. |
| `.ap-shell__rail-footer`, `.ap-shell__rail-avatar`, `.ap-shell__rail-account` | Pinned account row. |
| `.ap-shell__main` | Right column (topbar + canvas). |
| `.ap-shell__topbar` | 64px sticky top bar. |
| `.ap-shell__rail-trigger` | Mobile hamburger. |
| `.ap-shell__breadcrumb`, `.ap-shell__breadcrumb-sep`, `.ap-shell__breadcrumb-current` | Breadcrumb left of search pill. |
| `.ap-search-pill`, `.ap-search-pill__input`, `.ap-search-pill__shortcut` | 480px centered search with ⌘K chip. |
| `.ap-shell__topbar-actions` | Right cluster (Ask, notifications). |
| `.ap-ask`, `.ap-ask__spark`, `.ap-ask--active` | The ✦ Ask pill. Spark rotates on active. |
| `.ap-shell__bell`, `.ap-shell__bell-badge` | Notification icon + count badge. |
| `.ap-canvas` | Main content container. |

### 10.3 Home rows

| Class | Purpose |
|---|---|
| `.ap-broadcast`, `.ap-broadcast__inner` | Admin broadcast slot (reserved for v6). |
| `.ap-attention`, `.ap-attention--{error,behind,consent,opportunity}` | Row 0 attention banner. Four priority variants. |
| `.ap-attention__icon`, `.ap-attention__text`, `.ap-attention__actions`, `.ap-attention__btn`, `.ap-attention__btn--primary` | Banner innards + 3 equal-weight action buttons. |
| `.ap-narrative`, `.ap-narrative__eyebrow`, `.ap-narrative__body`, `.ap-narrative__meta` | Row 1 narrative paragraph (DM Serif 24px body). |
| `.ap-hero-stats`, `.ap-hero-stat`, `.ap-hero-stat--warn`, `.ap-hero-stat--flat`, `.ap-hero-stat--empty` | Row 2 three-stat strip. Variant colors the sparkline. |
| `.ap-hero-stat__eyebrow`, `.ap-hero-stat__value`, `.ap-hero-stat__verified` | Label + 80px number + verified-✓ tooltip. |
| `.ap-hero-stat__delta`, `.ap-hero-stat__delta--{up,down,flat}` | Delta line with up/down/flat color. |
| `.ap-hero-stat__spark` | 14-day sparkline, trajectory-colored path. |
| `.ap-quick-actions`, `.ap-chip`, `.ap-chip__icon`, `.ap-chip--ask` | Row 3 four-chip strip. |
| `.ap-section-head`, `.ap-section-head__titles`, `.ap-section-head__eyebrow`, `.ap-section-head__controls` | Section header with optional eyebrow + right-aligned controls. |

### 10.4 Role cards (grid)

| Class | Purpose |
|---|---|
| `.ap-role-grid` | 4-column grid (responsive down to 1). |
| `.ap-role-card`, `.ap-role-card--{active,attention,error,paused}` | Card with 4 states. |
| `.ap-role-card__grade`, `.ap-role-card__grade--{a,b,c,d,f}` | A-F scorecard chip (top-right). |
| `.ap-role-card__head`, `.ap-role-card__dot`, `.ap-role-card__title` | Status dot + title. |
| `.ap-role-card__activity`, `.ap-role-card__sep` | "23 actions · $1,840 influenced" line. |
| `.ap-role-card__spark` | Card sparkline, state-colored. |
| `.ap-role-card__meta` | "Last run 2 min ago." |
| `.ap-role-card__menu` | Hover-revealed 3-dot overflow. |

### 10.5 Split row (feed + recs)

| Class | Purpose |
|---|---|
| `.ap-split` | 60/40 two-column grid (stacks on mobile). |
| `.ap-feed`, `.ap-feed__live`, `.ap-feed__live--offline` | Feed container + live indicator (teal dot pulse). |
| `.ap-feed__toggle`, `.ap-feed__toggle-btn`, `.ap-feed__toggle-btn--active` | Dense/Detailed toggle. |
| `.ap-feed__row`, `.ap-feed__time`, `.ap-feed__icon`, `.ap-feed__body`, `.ap-feed__text`, `.ap-feed__role-pill`, `.ap-feed__link` | Feed row anatomy. |
| `.ap-rec-stack`, `.ap-rec`, `.ap-rec-empty`, `.ap-rec-empty__title`, `.ap-rec-empty__body` | Recommendations column. |
| `.ap-goal-chip` | Teal goal anchor chip. |
| `.ap-rec__headline`, `.ap-rec__reason`, `.ap-rec__footer` | GA4 card anatomy. |
| `.ap-btn-group`, `.ap-btn`, `.ap-btn--primary`, `.ap-btn--ghost`, `.ap-btn--spark` | Equal-weight 3-button footer. |
| `.ap-rec__see-all` | "See all (7) →" link. |

### 10.6 Drawer (spec'd, currently rendered as full route)

| Class | Purpose |
|---|---|
| `.ap-drawer-overlay`, `.ap-drawer-overlay--open` | Fade overlay. |
| `.ap-drawer`, `.ap-drawer--open` | 880px right-slide drawer. |
| `.ap-drawer__header`, `.ap-drawer__head-titles`, `.ap-drawer__title`, `.ap-drawer__sub`, `.ap-drawer__close` | Header with status dot, DM Serif 32px title, one-liner. |
| `.ap-drawer__body`, `.ap-drawer__content`, `.ap-drawer__side` | 70/30 body split. |
| `.ap-drawer__status-row`, `.ap-drawer__status-label`, `.ap-drawer__status-value` | Linear-style right-rail status. |
| `.ap-role-ask`, `.ap-role-ask__title`, `.ap-role-ask__sub` | Drawer-scoped Ask card at bottom of right rail. |
| `.ap-drawer__footer` | Sticky Apply / Dismiss / Ask 3-action footer. |

### 10.7 Undo + toast system

| Class | Purpose |
|---|---|
| `.ap-toast-stack` | Bottom-left stack, max 3 visible. |
| `.ap-toast`, `.ap-toast--ok`, `.ap-toast--err`, `.ap-toast--info` | Base + three status variants (left-border color). |
| `.ap-toast__row`, `.ap-toast__text`, `.ap-toast__undo`, `.ap-toast__count` | Undo chip anatomy with 10s countdown. |
| `.ap-toast__dots`, `.ap-toast__dot`, `.ap-toast__dot--lit` | 10-dot countdown animation. |

### 10.8 Activation wizard (CSS ready, template is Day 3)

| Class | Purpose |
|---|---|
| `.ap-activate`, `.ap-activate__bar`, `.ap-activate__back`, `.ap-activate__step`, `.ap-activate__save`, `.ap-activate__progress`, `.ap-activate__progress-fill` | Full-screen wizard frame. |
| `.ap-activate__body`, `.ap-activate__chat`, `.ap-activate__messages`, `.ap-activate__input`, `.ap-activate__input-row`, `.ap-activate__textarea`, `.ap-activate__send`, `.ap-activate__hint` | 45% chat column. |
| `.ap-msg`, `.ap-msg--assist`, `.ap-msg__spark`, `.ap-msg__lead`, `.ap-msg__body`, `.ap-msg--user`, `.ap-msg__bubble` | Assistant vs user message styles. No name label, ✦ glyph only. |
| `.ap-ring-grid`, `.ap-ring`, `.ap-ring__svg`, `.ap-ring__track`, `.ap-ring__arc`, `.ap-ring__arc--pending`, `.ap-ring__arc--active`, `.ap-ring__arc--done`, `.ap-ring__label`, `.ap-ring__step` | 3x5 ring grid, 4 sub-state arcs per ring. |
| `.ap-welcome`, `.ap-welcome__content`, `.ap-welcome__spark`, `.ap-welcome__text` | First-login flourish. |

### 10.9 Role detail surface

| Class | Purpose |
|---|---|
| `.ap-role-detail`, `.ap-role-detail__head`, `.ap-role-detail__eyebrow`, `.ap-role-detail__title`, `.ap-role-detail__meta`, `.ap-role-detail__summary` | Header stack. |
| `.ap-role-detail__status`, `.ap-role-detail__status--{active,error,paused}` | Inline status chip. |
| `.ap-role-detail__grid`, `.ap-role-detail__cell`, `.ap-role-detail__cell-label`, `.ap-role-detail__cell-value` | State summary KPI grid. |
| `.ap-timeline`, `.ap-timeline__item`, `.ap-timeline__item--{success,warn,error,start,info}`, `.ap-timeline__dot`, `.ap-timeline__body`, `.ap-timeline__time`, `.ap-timeline__message` | Last-run event timeline. |
| `.ap-role-detail__logs-details`, `.ap-role-detail__logs-summary`, `.ap-role-detail__logs-pre` | Collapsible raw-log reveal. |
| `.ap-role-detail__ask`, `.ap-role-detail__ask-title`, `.ap-role-detail__ask-hint`, `.ap-ask-form`, `.ap-ask-input`, `.ap-ask-submit`, `.ap-ask-result`, `.ap-ask-result__empty`, `.ap-ask-result__error`, `.ap-ask-result__answer`, `.ap-ask-result__text`, `.ap-ask-result__meta`, `.ap-ask-result__meta--cost` | Per-role Ask form with streamed answer + optional cost tooltip meta. |
| `.ap-role-detail__receipts-row`, `.ap-role-detail__receipts-hint`, `.ap-receipts-trigger` | "Show the last 25 receipts" button. |

### 10.10 Palette + Privacy toggle

| Class | Purpose |
|---|---|
| `.ap-privacy-toggle` | Toggle button, `aria-pressed` drives the state. |
| `.ap-palette`, `.ap-palette__backdrop`, `.ap-palette__panel`, `.ap-palette__input-row`, `.ap-palette__spark`, `.ap-palette__input`, `.ap-palette__hint`, `.ap-palette__list`, `.ap-palette__item`, `.ap-palette__item--active`, `.ap-palette__item-label`, `.ap-palette__item-spark`, `.ap-palette__item--ask` | ⌘K command palette. |

---

## 11. What is NOT built yet (do not recommend redoing)

- **Activation wizard template.** CSS is ready (`.ap-activate*`, `.ap-ring*`, `.ap-msg*`). Jinja template is still a placeholder. Day 3 build.
- **Drill-down as a slide-in drawer.** Currently `/roles/{slug}` renders a full-page surface (`role_detail.html`). The locked spec wants a right-side drawer from the Home page. Drawer CSS is shipped. Template integration is pending.
- **Undo chip lifecycle.** CSS exists (`.ap-toast*`). WebSocket commit/undo round-trip is not fully wired into outbound actions yet.
- **Command palette (⌘K).** CSS shipped. JS controller is next.
- **Privacy Mode toggle.** `.ap-priv` blur targets are on every dollar amount and number. Global toggle wiring is pending.
- **Vacation Mode.** Sidebar toggle, not yet rendered.
- **What-if sandbox card on Home.** Not rendered.
- **Sunday Digest PDF (signature video close).** Service not built.
- **Role Scorecard grades rendering live.** A-F CSS exists (`.ap-role-card__grade--{a..f}`). Server-side scoring is pending.
- **AI cost transparency chip.** Cost tracker middleware is logging JSONL. Per-action tooltip UI is pending.
- **Skeleton loaders wired per surface.** CSS is ready (`.ap-skel--delay-*`). Not yet applied per loading state.

Only critique what IS built. If you propose a new feature, attach it to one of the above gaps rather than inventing a new surface.

---

## 12. Reference board (inspirations already vetted)

The locked spec converged on these independent precedents. Crit the build against them.

- **Mercury** demo dashboard: `https://demo.mercury.com/dashboard`. Serif-on-numbers gravitas. The primary serif-warm pattern reference.
- **Linear Pulse:** `https://linear.app/homepage`. Right-rail status panel reference for the drill-down drawer.
- **Tableau Pulse:** `https://help.tableau.com/current/online/en-us/pulse_intro.htm`. Narrative-above-metrics pattern.
- **Customer.io April 2026 refresh:** question-form section headers ("What happened behind the scenes?").
- **Microsoft Clarity** demo: `https://clarity.microsoft.com/demo/projects/view/3t0wlogvdz/dashboard`. Skeleton loading + watchlist card rhythm.
- **Ramp:** `https://ramp.com`. Editorial empty-state voice. Documentary photography, not illustrations.
- **Klaviyo / GA4:** insight-card format for recommendations.

---

## 13. Constraints (hard rules, never propose breaking these)

1. **Zero "Claude", "Opus", "Anthropic", "AI", or any model name in rendered client HTML.** The Ask button is the ✦ spark glyph plus the word "Ask." The assistant has no name.
2. **Zero em dashes.** Anywhere. Sam grep-checks pre-commit.
3. **Sunrise orange (`--accent`) is rationed.** Allowed on: Apply CTAs, attention dots/borders, in-flight sparklines, the ✦ spark glyph, attention banner left bar. Never decorative. Never body text. Never on sand (`--bg-alt`) backgrounds because contrast is too low.
4. **DM Serif Display** is allowed only on: H1/H2, hero stat numbers, narrative paragraph body, rec headline, drawer title. DM Sans everywhere else. Never mix weights randomly.
5. **No new hexes.** Two new tokens (`--ok #2F9E5E`, `--warn #C93838`) have been added. Anything else must come from the existing tokens.
6. **Warm canvas (`--bg #FBFAF7`)** never flips to dark on client surfaces in demo scope. No dark mode for this hackathon. The tokens were designed to flip later, but that is a post-hackathon decision.
7. **WCAG AA minimum.** Body/bg 10.8:1 today. Orange on white 3.3:1 UI-only. White-on-orange 4.07:1 AA. Never remove focus outlines (`2px solid --accent`, 2px offset).
8. **Color-only never.** Every status dot is paired with icon or text.
9. **Reduced motion honored.** All animations die under `prefers-reduced-motion: reduce`. Undo countdown becomes text only. Ring arcs fill instantly.
10. **Touch targets ≥ 44x44px on mobile.** Strict Lighthouse bar: Performance ≥ 90, Accessibility ≥ 95 on 375px viewport.
11. **Photography not illustration.** Documentary 35mm film look. No iso-illustrations, no CG renders, no stock-photo teeth.
12. **No fake trust badges.** No "California-built" ribbons, no stock-star rows, no bonded/insured seals.
13. **Skeleton loaders, never spinners.** 1.6s pulse, staggered per line.
14. **Tabular-nums everywhere numbers live.** No jitter on hover.
15. **Section headers are questions** whenever the surface summarizes data. ("What happened behind the scenes?" / "What should we fix?" / "What worked this week?")

---

## 14. Signature demo moments (bias recommendations here)

These are the 5 moments Sam will lean into hardest on camera. Wow-factor elevations should make at least one of these land harder.

1. **The narrative paragraph plus 3 stat cards hero shot.** First 15 seconds of the video. Serif warmth does the work.
2. **The "What if?" sandbox.** Opus 4.7's 1M context window, live, ungated, inside a Home card. Owner types *"What if we doubled the ads budget?"* and gets a streamed paragraph back. Not built yet.
3. **The undo chip on a real outbound action.** *"Nothing leaves this system without a 10-second window."* CSS ready, wiring pending.
4. **Natural-language settings.** Owner types *"Change the morning report to 7am"* and the system applies it with a 10s undo. Settings page has the input, tool-calling backend in progress.
5. **Close with the Sunday Digest PDF opening on an iPhone.** *"Monday morning. This is the week of your life, written in your voice, on paper you can forward."* Service not built.

---

## 15. Your job

Critique the current Home and Role detail surfaces (embedded above) against the locked spec. Then propose **5 wow-factor elevations** within the locked spec. For each proposal, deliver:

1. **One-paragraph rationale** rooted in the reference board and the signature moments above.
2. **Pixel-fidelity description** (which surface, which row, which class, what changes: spacing, type size, color, motion, micro-interaction). Name specific tokens from the palette.
3. **Which files would change** (`home.html`, `role_detail.html`, `styles.css`, or new partial/service).
4. **How it lands in the 5-minute demo video** (which second, which moment).
5. **Any spec drift you noticed** in the current build (places where the shipped HTML does not match the locked decisions above). Flag it plainly so Sam can correct before video shoots Friday night.

### Prioritization rules

- Bias toward the 5 signature moments. If a proposal does not move the needle on one of them, cut it.
- Bias toward elevations that reuse existing `.ap-*` classes over elevations that require new classes.
- Do not propose a dark mode. Do not propose a new typeface. Do not propose a chat bubble. Do not propose naming the assistant.
- Do not propose onboarding tours, tooltips, or welcome modals beyond the one-time spark-glyph flourish already in spec.
- If something in the current build feels derivative or generic (common SaaS tropes, admin-dashboard clichés), say so bluntly. Sam hired you for taste, not politeness.

### Deliverable format

Structure your response as:

1. **Overall read** (3 to 5 sentences: does this feel like the locked spec? where does it feel short?).
2. **Spec drift list** (bullets, each one naming a file and a line-level issue).
3. **The 5 wow-factor elevations** (numbered, in priority order, formatted as specified in §15.1-5 above).
4. **One risk call** (the single most likely way this build ends up feeling generic on video, and how to avoid it).

No preamble. No restating the brief. Start with the overall read.
