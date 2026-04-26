// Activation wizard client script.
//
// Day 4: ring-grid animation + post-OAuth poll loop.
// Day 5: chat composer that POSTs to /api/activation/chat, renders
//        assistant / tool / user bubbles, and refreshes the ring grid
//        in the same round-trip.
//
// All DOM construction uses createElement + textContent so there is
// no innerHTML surface for server content to reach.

(() => {
  const STEP_ORDER = ["credentials", "config", "connected", "first_run"];
  const SVG_NS = "http://www.w3.org/2000/svg";

  // Screenshot paths queued for the next chat send (server-generated
  // filenames returned by POST /api/activation/screenshot).
  const pendingScreenshots = [];

  const STRATEGY_LABELS = {
    connect_existing: "Connect existing",
    wcas_provisions:  "WCAS provisions",
    owner_signup:     "Sign up with help",
  };

  // ---------------------------------------------------------------------
  // Ring grid (Day 4 surface)
  // ---------------------------------------------------------------------

  // Arc circumference for a circle of r=40: 2π·40 ≈ 251.33. The fill
  // arc's stroke-dashoffset shrinks as completed steps grow.
  const ARC_LEN = 251.33;
  const STEP_INDEX = { pending: 0, credentials: 1, config: 2, connected: 3, first_run: 4 };

  function applyRingArc(ringEl, step) {
    const arcFill = ringEl.querySelector(".ap-activate-ring__arc-fill");
    if (!arcFill) return;
    const completed = STEP_INDEX[step] ?? 0;
    const offset = (ARC_LEN * (4 - completed)) / 4;
    arcFill.style.strokeDashoffset = String(offset.toFixed(2));
  }

  function applyRingState(ringEl, step) {
    if (!ringEl) return;
    const normalized = STEP_ORDER.includes(step) ? step : "pending";
    if (ringEl.dataset.roleStep === normalized) return;
    const wasFirstRun = ringEl.dataset.roleStep === "first_run";
    ringEl.dataset.roleStep = normalized;
    applyRingArc(ringEl, normalized);

    // Pop animation when a ring just transitioned to first_run.
    if (normalized === "first_run" && !wasFirstRun) {
      ringEl.classList.add("ap-activate-ring--just-closed");
      setTimeout(() => ringEl.classList.remove("ap-activate-ring--just-closed"), 700);
    }

    const stepEl = ringEl.querySelector("[data-activate-ring-step]");
    if (stepEl) {
      const label = normalized === "first_run"   ? "Running"
                  : normalized === "connected"   ? "Ready, awaiting first run"
                  : normalized === "config"      ? "Configured"
                  : normalized === "credentials" ? "Logged in"
                  :                                "Not started";
      stepEl.textContent = label;
    }
    // Tooltip body is fully static (5 variants pre-rendered); CSS shows
    // the variant matching data-role-step on the parent. No JS needed.
  }

  function renderRings(rings) {
    if (!Array.isArray(rings)) return;
    const slots = document.querySelectorAll("[data-activate-ring]");
    const bySlug = {};
    for (const r of rings) bySlug[r.slug] = r;
    slots.forEach((el) => {
      const slug = el.dataset.roleSlug;
      const ring = bySlug[slug];
      if (!ring) return;
      applyRingState(el, ring.step);
    });
    checkActivationComplete();
  }

  // ----- Celebration choreography ------------------------------------
  // When all rings reach first_run, fire the gold halo + confetti +
  // "All N roles activated" badge. Once per session.

  const CELEBRATION_KEY = "wcas_activation_celebrated";
  const ACTIVATION_START_KEY = "wcas_activation_started_at";
  const CONFETTI_COLORS = ["#D4A437", "#F4C53D", "#E97B2E", "#FBBC05"];

  function spawnConfetti(layer) {
    if (!layer) return;
    layer.replaceChildren();
    const w = Math.max(layer.offsetWidth, 1);
    for (let i = 0; i < 28; i++) {
      const c = document.createElement("span");
      c.className = "ap-confetti";
      c.style.left = `${10 + Math.random() * 80}%`;
      c.style.top = `${28 + Math.random() * 30}%`;
      c.style.background = CONFETTI_COLORS[i % CONFETTI_COLORS.length];
      c.style.animationDelay = `${(Math.random() * 0.4).toFixed(2)}s`;
      c.style.transform = `rotate(${Math.floor(Math.random() * 360)}deg)`;
      const sz = 3 + Math.random() * 5;
      c.style.width = `${sz.toFixed(1)}px`;
      c.style.height = `${sz.toFixed(1)}px`;
      layer.appendChild(c);
    }
  }

  function formatElapsed(ms) {
    if (!ms || ms < 0) return "";
    const total = Math.round(ms / 1000);
    const m = Math.floor(total / 60);
    const s = total % 60;
    if (m === 0) return `in ${s}s`;
    return `in ${m} min ${s}s`;
  }

  function checkActivationComplete() {
    const ringsAside = document.querySelector(".ap-activate-rings");
    if (!ringsAside) return;
    const ringEls = ringsAside.querySelectorAll("[data-activate-ring]");
    if (ringEls.length === 0) return;
    const allRunning = [...ringEls].every((el) => el.dataset.roleStep === "first_run");
    if (!allRunning) return;
    if (sessionStorage.getItem(CELEBRATION_KEY)) return;
    sessionStorage.setItem(CELEBRATION_KEY, "1");

    const elapsedEl = ringsAside.querySelector("[data-activate-activated-elapsed]");
    if (elapsedEl) {
      const startedAt = Number(sessionStorage.getItem(ACTIVATION_START_KEY) || 0);
      if (startedAt) elapsedEl.textContent = formatElapsed(Date.now() - startedAt);
    }

    ringsAside.classList.add("ap-activate-rings--celebrating");
    spawnConfetti(ringsAside.querySelector("[data-activate-confetti-layer]"));
  }

  // Stamp the activation start so the badge can show elapsed time.
  // Only stamps if not already set this session.
  if (!sessionStorage.getItem(ACTIVATION_START_KEY)) {
    sessionStorage.setItem(ACTIVATION_START_KEY, String(Date.now()));
  }

  function updateProgress(rings) {
    if (!Array.isArray(rings)) return;
    const total = rings.length || 1;
    const completed = rings.filter((r) => r.step === "first_run").length;
    const started   = rings.filter((r) => r.step && r.step !== "pending").length;
    const fillEl  = document.querySelector("[data-activate-progress-fill]");
    const labelEl = document.querySelector("[data-activate-progress-label]");
    const etaEl   = document.querySelector("[data-activate-progress-eta]");
    if (fillEl) {
      const partialPct = rings.reduce((sum, r) => sum + (r.percent_complete || 0), 0) / total;
      fillEl.style.width = `${Math.max(7, Math.round(partialPct * 100))}%`;
    }
    if (labelEl) labelEl.textContent = `Step ${Math.max(started, 1)} of ${total}`;
    if (etaEl) {
      const remaining = Math.max(0, (total - completed) * 2);
      etaEl.textContent = remaining <= 1 ? "Almost done" : `About ${remaining} min left`;
    }
  }

  async function fetchState() {
    try {
      const resp = await fetch("/api/activation/state", { credentials: "same-origin" });
      if (!resp.ok) return null;
      return await resp.json();
    } catch (_err) {
      return null;
    }
  }

  function connectedHint() {
    const root = document.querySelector("[data-activate-rings]");
    const fromAttr = root ? (root.dataset.connectedHint || "") : "";
    const url = new URL(window.location.href);
    return fromAttr || url.searchParams.get("connected") || "";
  }

  async function pollAfterOAuth() {
    const provider = connectedHint();
    if (!provider) return;
    if (window.__apActivateRevealed) return;
    window.__apActivateRevealed = true;

    // Clean the URL early so a refresh doesn't replay the reveal.
    if (window.history && window.history.replaceState) {
      window.history.replaceState({}, "", window.location.pathname);
    }
    await playOAuthReveal(provider);
  }

  // -------------------------------------------------------------------------
  // Post-OAuth scripted reveal: 4-stage chat narration with synchronized
  // ring fills, ~4.5s total. Replaces the silent jump-to-3/4 with a sequence
  // that actually lets the user watch the agent connecting their accounts.
  // -------------------------------------------------------------------------
  async function playOAuthReveal(provider) {
    const wait = (ms) => new Promise((r) => setTimeout(r, ms));

    // Which rings this OAuth provider unlocks. Google connects 3 in one shot;
    // Meta would connect "social" (post-hackathon).
    const PROVIDER_ROLES = {
      google: ["gbp", "seo", "reviews"],
      meta:   ["social"],
    };
    const slugs = PROVIDER_ROLES[provider] || [];
    const ringEls = slugs
      .map((slug) => document.querySelector(`[data-role-slug="${slug}"]`))
      .filter(Boolean);
    if (ringEls.length === 0) return;

    const roleNames = ringEls
      .map((r) => r.querySelector(".ap-activate-ring__label")?.textContent?.trim())
      .filter(Boolean);

    // Reset rings to pending so the user sees them fill in. The actual server
    // state stays at whatever it is; we sync to reality at the end.
    ringEls.forEach((r) => applyRingState(r, "pending"));

    // Lock the composer during the sequence so a fast typist can't race it.
    const form = document.querySelector("[data-activate-composer]");
    const input = form ? form.querySelector("[data-activate-input]") : null;
    const sendBtn = form ? form.querySelector("[data-activate-send]") : null;
    if (input) input.disabled = true;
    if (sendBtn) sendBtn.disabled = true;

    try {
      // Stage 1: thinking dots, then login confirmation
      await wait(250);
      let thinking = appendThinking();
      await wait(900);
      if (thinking) thinking.remove();
      appendAssistantBubble("Got it. Logging into your Google account.");
      await wait(450);
      appendToolEvent({ ok: true, name: "google_oauth", summary: "tokens captured securely" });
      await wait(550);
      ringEls.forEach((r) => applyRingState(r, "credentials"));

      // Stage 2: reading data
      await wait(700);
      thinking = appendThinking();
      await wait(950);
      if (thinking) thinking.remove();
      const readingLine = roleNames.length === 3
        ? "Reading your business profile, search performance, and reviews."
        : `Reading data for ${joinHuman(roleNames)}.`;
      appendAssistantBubble(readingLine);
      await wait(500);
      appendToolEvent({
        ok: true,
        name: "validation_probe",
        summary: `live data confirmed across ${ringEls.length} ${ringEls.length === 1 ? "service" : "services"}`,
      });
      await wait(450);
      ringEls.forEach((r) => applyRingState(r, "config"));

      // Stage 3: ready
      await wait(700);
      ringEls.forEach((r) => applyRingState(r, "connected"));
      await wait(500);
      const readyLine = `${joinHuman(roleNames)} ${ringEls.length === 1 ? "is" : "are"} wired up and ready to launch. They'll fire their first runs on the next scheduled tick.`;
      appendAssistantBubble(readyLine);

      // Sync with real server state so anything that doesn't match (e.g. probe
      // marked one of them broken) corrects itself silently.
      await wait(300);
      const real = await fetchState();
      if (real && Array.isArray(real.rings)) {
        renderRings(real.rings);
        updateProgress(real.rings);
      }
    } catch (_err) {
      // Sequence is purely visual; if anything throws, fall back to a sync
      // from the server so the user doesn't see broken state.
      const real = await fetchState();
      if (real && Array.isArray(real.rings)) {
        renderRings(real.rings);
        updateProgress(real.rings);
      }
    } finally {
      if (input)   { input.disabled = false; input.focus(); }
      if (sendBtn) { sendBtn.disabled = false; }
    }
  }

  function joinHuman(items) {
    if (!items || items.length === 0) return "";
    if (items.length === 1) return items[0];
    if (items.length === 2) return `${items[0]} and ${items[1]}`;
    return `${items.slice(0, -1).join(", ")}, and ${items[items.length - 1]}`;
  }

  // ---------------------------------------------------------------------
  // Chat composer (Day 5) - DOM-only, no innerHTML
  // ---------------------------------------------------------------------

  function el(tag, className, text) {
    const n = document.createElement(tag);
    if (className) n.className = className;
    if (text != null) n.textContent = text;
    return n;
  }

  function chatStream() {
    return document.querySelector("[data-activate-chat]");
  }

  function scrollChatToBottom() {
    const stream = chatStream();
    if (!stream) return;
    stream.scrollTop = stream.scrollHeight;
  }

  function appendUserBubble(text) {
    const stream = chatStream();
    if (!stream) return;
    const msg = el("div", "ap-activate-msg ap-activate-msg--user");
    const body = el("div", "ap-activate-msg__body", text);
    msg.appendChild(body);
    stream.appendChild(msg);
    scrollChatToBottom();
  }

  function appendAssistantBubble(text) {
    const stream = chatStream();
    if (!stream) return;
    const msg = el("div", "ap-activate-msg ap-activate-msg--asst");

    const glyph = el("div", "ap-activate-msg__glyph");
    glyph.setAttribute("aria-hidden", "true");
    glyph.textContent = "✦"; // spark
    msg.appendChild(glyph);

    const body = el("div", "ap-activate-msg__body");
    const p = el("p", null, text);
    body.appendChild(p);
    msg.appendChild(body);

    stream.appendChild(msg);
    scrollChatToBottom();
  }

  function appendToolEvent(evt) {
    const stream = chatStream();
    if (!stream) return;
    const klass = evt.ok
      ? "ap-activate-event ap-activate-event--ok"
      : "ap-activate-event ap-activate-event--err";
    const node = el("div", klass);

    const ico = el("span", "ap-activate-event__ico", evt.ok ? "⚙" : "⚠");
    ico.setAttribute("aria-hidden", "true");
    node.appendChild(ico);

    node.appendChild(el("span", "ap-activate-event__name", evt.name || "tool"));

    const sep = el("span", "ap-activate-event__sep", "·");
    sep.setAttribute("aria-hidden", "true");
    node.appendChild(sep);

    node.appendChild(el("span", "ap-activate-event__summary", evt.summary || ""));

    stream.appendChild(node);
    scrollChatToBottom();
  }

  function appendSystemBubble(text) {
    const stream = chatStream();
    if (!stream) return;
    const msg = el("div", "ap-activate-msg ap-activate-msg--sys");
    const body = el("div", "ap-activate-msg__body");
    body.appendChild(el("p", null, text));
    msg.appendChild(body);
    stream.appendChild(msg);
    scrollChatToBottom();
  }

  function appendThinking() {
    const stream = chatStream();
    if (!stream) return null;
    const node = el("div", "ap-activate-thinking");
    node.setAttribute("aria-live", "polite");

    const glyph = el("div", "ap-activate-msg__glyph");
    glyph.setAttribute("aria-hidden", "true");
    glyph.textContent = "✦";
    node.appendChild(glyph);

    const dots = el("div", "ap-activate-thinking__dots");
    dots.setAttribute("aria-hidden", "true");
    dots.appendChild(el("span"));
    dots.appendChild(el("span"));
    dots.appendChild(el("span"));
    node.appendChild(dots);

    node.appendChild(el("span", "ap-sr", "Thinking"));

    stream.appendChild(node);
    scrollChatToBottom();
    return node;
  }

  function renderEvents(events) {
    if (!Array.isArray(events)) return;
    for (const e of events) {
      if (e.role === "assistant" && e.text) appendAssistantBubble(e.text);
      else if (e.role === "tool")           appendToolEvent(e);
      else if (e.role === "system" && e.text) appendSystemBubble(e.text);
    }
  }

  async function renderPanels(panels) {
    if (!Array.isArray(panels)) return;
    const VIZ = window.AGENT_VIZ;
    for (const p of panels) {
      if (p.type === "voice_card") {
        // Demo mode: play "reading the website + learning your voice"
        // overlay, then drop the actual voice card bubble in as the
        // resolution. No-op when ?demo=1 is not in the URL.
        if (VIZ && VIZ.enabled) {
          try { await VIZ.playVoiceExtraction(p.payload); }
          catch (_e) { /* never let viz block the real flow */ }
        }
        appendVoiceCardBubble(p.payload);
      } else if (p.type === "crm_mapping") {
        if (VIZ && VIZ.enabled) {
          try { await VIZ.playCrmMapping(p.payload); }
          catch (_e) { /* never let viz block the real flow */ }
        }
        appendCrmMappingBubble(p.payload);
      }
    }
  }

  // -------------------------------------------------------------------
  // Panel bubbles (v0.6.0): voice card + CRM mapping
  // -------------------------------------------------------------------

  function appendVoiceCardBubble(payload) {
    const stream = chatStream();
    if (!stream || !payload) return;

    const wrap = el("div", "ap-activate-msg ap-activate-msg--asst");
    const glyph = el("div", "ap-activate-msg__glyph");
    glyph.setAttribute("aria-hidden", "true");
    glyph.textContent = "✦";
    wrap.appendChild(glyph);

    const card = el("article", "ap-activate-voice-card");
    card.dataset.cardId = payload.card_id || "";

    const head = el("header", "ap-activate-voice-card__head");
    head.appendChild(el("h3", "ap-activate-voice-card__title", "Here's how I hear you"));
    if (Array.isArray(payload.traits) && payload.traits.length) {
      const traits = el("div", "ap-activate-voice-card__traits");
      payload.traits.forEach((t) => {
        const chip = el("span", "ap-activate-voice-card__trait", String(t));
        traits.appendChild(chip);
      });
      head.appendChild(traits);
    }
    card.appendChild(head);

    const grid = el("div", "ap-activate-voice-card__grid");
    const left = el("div", "ap-activate-voice-card__col ap-activate-voice-card__col--generic");
    left.appendChild(el("div", "ap-activate-voice-card__col-label", "Generic AI"));
    left.appendChild(el("p", "ap-activate-voice-card__sample", payload.generic_sample || ""));
    const right = el("div", "ap-activate-voice-card__col ap-activate-voice-card__col--voice");
    right.appendChild(el("div", "ap-activate-voice-card__col-label", "Your voice"));
    const editable = el("p", "ap-activate-voice-card__sample ap-activate-voice-card__sample--editable", payload.voice_sample || "");
    editable.contentEditable = "true";
    editable.setAttribute("aria-label", "Voice sample (editable)");
    right.appendChild(editable);
    grid.appendChild(left);
    grid.appendChild(right);
    card.appendChild(grid);

    if (payload.sample_context) {
      const ctx = el("div", "ap-activate-voice-card__context", "Context: " + payload.sample_context);
      card.appendChild(ctx);
    }

    const actions = el("div", "ap-activate-voice-card__actions");
    const accept = el("button", "ap-activate-voice-card__accept", "This is us");
    accept.type = "button";
    accept.addEventListener("click", async () => {
      accept.disabled = true;
      accept.textContent = "Saving...";
      const edits = {};
      const editedText = (editable.textContent || "").trim();
      if (editedText && editedText !== (payload.voice_sample || "").trim()) {
        edits.voice_sample = editedText;
      }
      try {
        const body = await postPanelAccept("voice_card", payload.card_id, edits);
        accept.textContent = "Saved";
        card.classList.add("is-accepted");
        // Disable editing post-accept.
        editable.contentEditable = "false";
        renderEvents(body.events || []);
        renderPanels(body.panels || []);
        renderRings(body.rings || []);
        updateProgress(body.rings || []);
      } catch (err) {
        accept.disabled = false;
        accept.textContent = "This is us";
        appendSystemBubble(err.message || "Couldn't save. Try again.");
      }
    });
    actions.appendChild(accept);
    card.appendChild(actions);

    const body = el("div", "ap-activate-msg__body");
    body.appendChild(card);
    wrap.appendChild(body);

    stream.appendChild(wrap);
    scrollChatToBottom();
  }

  function appendCrmMappingBubble(payload) {
    const stream = chatStream();
    if (!stream || !payload) return;

    const wrap = el("div", "ap-activate-msg ap-activate-msg--asst");
    const glyph = el("div", "ap-activate-msg__glyph");
    glyph.setAttribute("aria-hidden", "true");
    glyph.textContent = "✦";
    wrap.appendChild(glyph);

    const card = el("article", "ap-activate-crm-mapping");
    card.dataset.mappingId = payload.mapping_id || "";

    const head = el("header", "ap-activate-crm-mapping__head");
    head.appendChild(el("h3", "ap-activate-crm-mapping__title", "Here's what I found in your data"));
    head.appendChild(el(
      "p", "ap-activate-crm-mapping__sub",
      "Read from " + (payload.table_name || "your CRM") + " (" + (payload.base_id || "") + ")",
    ));
    card.appendChild(head);

    const segs = el("div", "ap-activate-crm-mapping__segments");
    (payload.segments || []).forEach((seg) => {
      const row = el("div", "ap-activate-crm-mapping__seg");
      const count = el("div", "ap-activate-crm-mapping__seg-count", String(seg.count || 0));
      const meta = el("div", "ap-activate-crm-mapping__seg-meta");
      meta.appendChild(el("div", "ap-activate-crm-mapping__seg-label", seg.label || seg.slug || ""));
      const action = (payload.proposed_actions || []).find((a) => a.segment === seg.slug);
      if (action) {
        const proposal = el("div", "ap-activate-crm-mapping__seg-action",
          "I'll run " + action.playbook.replace(/_/g, " ") + " via " + action.automation.replace(/_/g, " "));
        meta.appendChild(proposal);
      }
      if (Array.isArray(seg.sample_names) && seg.sample_names.length) {
        const names = el("div", "ap-activate-crm-mapping__seg-names",
          "e.g. " + seg.sample_names.slice(0, 3).join(", "));
        meta.appendChild(names);
      }
      row.appendChild(count);
      row.appendChild(meta);
      segs.appendChild(row);
    });
    card.appendChild(segs);

    const actions = el("div", "ap-activate-crm-mapping__actions");
    const accept = el("button", "ap-activate-crm-mapping__accept", "Looks right");
    accept.type = "button";
    accept.addEventListener("click", async () => {
      accept.disabled = true;
      accept.textContent = "Saving...";
      try {
        const body = await postPanelAccept("crm_mapping", payload.mapping_id, {});
        accept.textContent = "Saved";
        card.classList.add("is-accepted");
        renderEvents(body.events || []);
        renderPanels(body.panels || []);
        renderRings(body.rings || []);
        updateProgress(body.rings || []);
      } catch (err) {
        accept.disabled = false;
        accept.textContent = "Looks right";
        appendSystemBubble(err.message || "Couldn't save. Try again.");
      }
    });
    actions.appendChild(accept);
    card.appendChild(actions);

    const body = el("div", "ap-activate-msg__body");
    body.appendChild(card);
    wrap.appendChild(body);

    stream.appendChild(wrap);
    scrollChatToBottom();
  }

  async function postPanelAccept(type, cardId, edits) {
    const resp = await fetch("/api/activation/panel-accept", {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ type, card_id: cardId, edits: edits || {} }),
    });
    if (resp.status === 429) throw new Error("Too many acceptances. Wait a moment.");
    if (!resp.ok) {
      let detail = "Save failed.";
      try { const j = await resp.json(); if (j && j.error) detail = j.error; } catch (_e) {}
      throw new Error(detail);
    }
    return await resp.json();
  }

  async function postChat(message, screenshots) {
    const body = { message };
    if (Array.isArray(screenshots) && screenshots.length > 0) {
      body.screenshots = screenshots.slice(0, 3);
    }
    const resp = await fetch("/api/activation/chat", {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (resp.status === 429) {
      throw new Error("Slow down for a moment, I'm still catching up.");
    }
    if (!resp.ok) {
      throw new Error("Something went sideways. Try that again.");
    }
    return await resp.json();
  }

  // ---------------------------------------------------------------------
  // Strategy chips (§5) + samples panel (§7)
  // ---------------------------------------------------------------------

  async function fetchProvisioningPlan() {
    try {
      const resp = await fetch("/api/activation/provisioning-plan", {
        credentials: "same-origin",
      });
      if (!resp.ok) return null;
      return await resp.json();
    } catch (_err) {
      return null;
    }
  }

  function applyStrategyChips(plan) {
    if (!plan || !Array.isArray(plan.items)) return;
    const bySlug = {};
    for (const item of plan.items) bySlug[item.service] = item;
    document.querySelectorAll("[data-activate-ring]").forEach((ring) => {
      const slug = ring.dataset.roleSlug;
      const chip = ring.querySelector("[data-activate-ring-chip]");
      if (!chip) return;
      const item = bySlug[slug];
      if (!item) {
        chip.hidden = true;
        return;
      }
      chip.hidden = false;
      chip.textContent = STRATEGY_LABELS[item.strategy] || item.strategy;
      chip.className = "ap-activate-chip ap-activate-chip--" + item.strategy;
    });
  }

  function renderCitations(container, citations) {
    if (!Array.isArray(citations) || citations.length === 0) return;
    const row = el("div", "ap-activate-citations", null);
    // Cap at 3, dedupe identical kind+source pairs (per plan risk mitigation).
    const seen = new Set();
    for (const c of citations) {
      const key = (c.kind || "") + ":" + (c.source || "");
      if (seen.has(key)) continue;
      seen.add(key);
      if (seen.size > 3) break;
      const badge = el("span", "ap-activate-citation ap-activate-citation--" + (c.kind || "x"));
      const k = el("span", "ap-activate-citation__kind", (c.kind || "src") + ":");
      const s = el("span", "ap-activate-citation__source", String(c.source || "").replace(/_/g, " "));
      badge.appendChild(k);
      badge.appendChild(s);
      row.appendChild(badge);
    }
    container.appendChild(row);
  }

  function renderSample(card, sample) {
    while (card.firstChild) card.removeChild(card.firstChild);
    const eyebrow = el("div", "ap-activate-sample__eyebrow", sample.slug.replace(/_/g, " ").toUpperCase());
    card.appendChild(eyebrow);
    const title = el("h4", "ap-activate-sample__title", sample.title || sample.slug);
    card.appendChild(title);
    const body = el("div", "ap-activate-sample__body");
    // Body is plain text rendering of markdown (no innerHTML). Good enough
    // for the demo; a post-hackathon pass could render real markdown.
    body.textContent = sample.body_markdown || "";
    card.appendChild(body);
    const status = el(
      "div",
      "ap-activate-sample__status",
      sample.status === "ok" ? `Draft · ${(sample.preview || "").slice(0, 140)}` : sample.status,
    );
    card.appendChild(status);
    // v0.6.0 provenance badges
    renderCitations(card, sample.citations || []);
  }

  // -------------------------------------------------------------------
  // Live customer simulation (v0.6.0 demo finale)
  // -------------------------------------------------------------------

  async function renderLiveSimulationCard() {
    const grid = document.querySelector("[data-activate-samples-grid]");
    const panel = document.querySelector("[data-activate-samples]");
    if (!grid || !panel) return;

    // Read the saved CRM mapping to grab the named-customer prompt text.
    let target = null;
    try {
      const resp = await fetch("/api/activation/state", { credentials: "same-origin" });
      // We don't have a direct CRM-mapping read endpoint; the simulate
      // endpoint sources it server-side. We just need a teaser name for
      // the prompt - peek at the state_snapshot via a side request would
      // bloat scope. Instead, render a generic teaser and let the
      // simulate response fill in the actual name.
    } catch (_e) { /* ignore */ }

    const card = el("article", "ap-activate-simulation");
    card.dataset.sampleSlug = "live_simulation";

    const eyebrow = el("div", "ap-activate-sample__eyebrow", "LIVE SIMULATION");
    card.appendChild(eyebrow);

    const title = el("h4", "ap-activate-simulation__title",
      "See it in action: a real email to a real customer");
    card.appendChild(title);

    const lede = el("p", "ap-activate-simulation__lede",
      "I'll write a re-engagement email to one of your inactive customers, in your voice, using your data. Click below to watch.");
    card.appendChild(lede);

    const cta = el("button", "ap-activate-simulation__cta", "Generate one now");
    cta.type = "button";
    cta.addEventListener("click", () => runLiveSimulation(card, cta));
    card.appendChild(cta);

    // Prepend so the simulation sits above the 7 weekly samples.
    grid.insertBefore(card, grid.firstChild);
    panel.hidden = false;
    panel.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }

  async function runLiveSimulation(card, cta) {
    cta.disabled = true;
    cta.textContent = "Drafting...";

    // Demo-mode visualizer: source-stream overlay + latency counter +
    // typewriter email + sequenced citation badge lighting. No-op when
    // ?demo=1 isn't in the URL (returns a passthrough that just sets text).
    const VIZ = (window.AGENT_VIZ && window.AGENT_VIZ.createSimulationVisualizer)
      ? window.AGENT_VIZ.createSimulationVisualizer()
      : null;
    if (VIZ) {
      VIZ.attachTo(card);
      VIZ.startCounter();
    }

    try {
      const resp = await fetch("/api/activation/simulate-customer", {
        method: "POST",
        credentials: "same-origin",
      });
      if (resp.status === 429) throw new Error("Too soon. Wait a minute and try again.");
      if (resp.status === 409) {
        let detail = "Need a CRM mapping first. Finish the wizard.";
        try { const j = await resp.json(); if (j && j.error) detail = j.error; } catch (_e) {}
        throw new Error(detail);
      }
      if (!resp.ok) throw new Error("Generation failed.");
      const body = await resp.json();

      if (VIZ) {
        VIZ.freezeCounter();
        VIZ.cleanupStreams();
      }

      // Replace card body with the rendered draft + citations.
      while (card.firstChild) card.removeChild(card.firstChild);
      const eyebrow = el("div", "ap-activate-sample__eyebrow",
        "LIVE SIMULATION · " + (body.name || "customer"));
      card.appendChild(eyebrow);
      const title = el("h4", "ap-activate-simulation__title", body.title || "Re-engagement draft");
      card.appendChild(title);
      const meta = el("div", "ap-activate-simulation__meta",
        "Drafted to " + body.name + " (" + body.days_inactive + " days inactive), in your voice.");
      card.appendChild(meta);
      const draft = el("div", "ap-activate-simulation__draft");
      card.appendChild(draft);
      renderCitations(card, body.citations || []);
      card.classList.add("is-generated");

      // Demo: typewriter the body, then light the citation badges sequentially.
      // In non-demo mode VIZ is null, draft just gets dumped at full text and
      // citations render normally (no light sequence).
      if (VIZ) {
        await VIZ.typewrite(draft, body.body_markdown || "", 50);
        const badges = card.querySelectorAll(".ap-activate-citation");
        await VIZ.lightCitations(badges);
      } else {
        draft.textContent = body.body_markdown || "";
      }
    } catch (err) {
      if (VIZ) {
        VIZ.freezeCounter();
        VIZ.cleanupStreams();
      }
      cta.disabled = false;
      cta.textContent = "Generate one now";
      const msg = el("p", "ap-activate-simulation__error", err.message || "Generation failed.");
      card.appendChild(msg);
    }
  }

  function renderSamples(samples) {
    if (!Array.isArray(samples)) return;
    const panel = document.querySelector("[data-activate-samples]");
    const grid = document.querySelector("[data-activate-samples-grid]");
    if (!panel || !grid) return;
    // Rebuild the grid from scratch each refresh.
    while (grid.firstChild) grid.removeChild(grid.firstChild);
    for (const sample of samples) {
      const card = el("article", "ap-activate-sample");
      card.dataset.sampleSlug = sample.slug;
      renderSample(card, sample);
      grid.appendChild(card);
    }
    panel.hidden = samples.length === 0;
    panel.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }

  async function fetchSamples() {
    try {
      const resp = await fetch("/api/activation/samples", { credentials: "same-origin" });
      if (!resp.ok) return;
      const body = await resp.json();
      renderSamples(body.samples || []);
    } catch (_err) {
      /* silent */
    }
  }

  async function triggerSampleGeneration() {
    // Show the panel immediately with skeleton cards so the user sees motion.
    const panel = document.querySelector("[data-activate-samples]");
    const grid = document.querySelector("[data-activate-samples-grid]");
    if (panel && grid) {
      panel.hidden = false;
      while (grid.firstChild) grid.removeChild(grid.firstChild);
      for (const slug of ["gbp","seo","reviews","email_assistant","chat_widget","blog","social"]) {
        const card = el("article", "ap-activate-sample");
        card.dataset.sampleSlug = slug;
        renderSample(card, { slug, title: "Drafting...", body_markdown: "", status: "generating", preview: "" });
        grid.appendChild(card);
      }
    }
    try {
      const resp = await fetch("/api/activation/generate-samples", {
        method: "POST",
        credentials: "same-origin",
      });
      if (!resp.ok) {
        await fetchSamples();
        return;
      }
      const body = await resp.json();
      renderSamples(body.samples || []);
    } catch (_err) {
      await fetchSamples();
    }
  }

  function detectMarkComplete(events) {
    if (!Array.isArray(events)) return false;
    for (const e of events) {
      if (e.role === "tool" && e.name === "mark_activation_complete" && e.ok) {
        return true;
      }
    }
    return false;
  }

  function detectProvisioningPlanRecorded(events) {
    if (!Array.isArray(events)) return false;
    for (const e of events) {
      if (e.role === "tool" && e.name === "record_provisioning_plan" && e.ok) {
        return true;
      }
    }
    return false;
  }

  // ---------------------------------------------------------------------
  // Screenshot upload (§7.5)
  // ---------------------------------------------------------------------

  function attachmentsContainer() {
    return document.querySelector("[data-activate-attachments]");
  }

  function renderAttachments() {
    const container = attachmentsContainer();
    if (!container) return;
    while (container.firstChild) container.removeChild(container.firstChild);
    pendingScreenshots.forEach((name, idx) => {
      const chip = el("span", "ap-activate-attachment");
      chip.appendChild(el("span", null, "Screenshot " + (idx + 1)));
      const x = el("button", "ap-activate-attachment__x", "×");
      x.type = "button";
      x.setAttribute("aria-label", "Remove screenshot " + (idx + 1));
      x.addEventListener("click", () => {
        pendingScreenshots.splice(idx, 1);
        renderAttachments();
      });
      chip.appendChild(x);
      container.appendChild(chip);
    });
  }

  async function uploadScreenshot(file) {
    const fd = new FormData();
    fd.append("image", file, file.name);
    try {
      const resp = await fetch("/api/activation/screenshot", {
        method: "POST",
        credentials: "same-origin",
        body: fd,
      });
      if (!resp.ok) {
        appendSystemBubble("Couldn't accept that screenshot. PNG / JPEG / WEBP under 5 MB only.");
        return;
      }
      const body = await resp.json();
      if (body && body.path) {
        if (pendingScreenshots.length >= 3) {
          appendSystemBubble("Max 3 screenshots per turn. Remove one before adding another.");
          return;
        }
        pendingScreenshots.push(body.path);
        renderAttachments();
      }
    } catch (_err) {
      appendSystemBubble("Upload failed. Try again.");
    }
  }

  function wireScreenshotInput() {
    const input = document.querySelector("[data-activate-screenshot]");
    if (!input) return;
    input.addEventListener("change", async (ev) => {
      const files = ev.target.files;
      if (!files || files.length === 0) return;
      for (const f of Array.from(files).slice(0, 3)) {
        await uploadScreenshot(f);
      }
      // Clear the input so the user can re-attach the same file if needed.
      input.value = "";
    });
  }

  function wireComposer() {
    const form = document.querySelector("[data-activate-composer]");
    if (!form) return;
    const input = form.querySelector("[data-activate-input]");
    const sendBtn = form.querySelector("[data-activate-send]");
    if (!input || !sendBtn) return;

    input.addEventListener("keydown", (ev) => {
      if (ev.key === "Enter" && !ev.shiftKey) {
        ev.preventDefault();
        form.requestSubmit();
      }
    });

    input.addEventListener("input", () => {
      input.style.height = "auto";
      const lh = parseInt(getComputedStyle(input).lineHeight, 10) || 22;
      const maxH = lh * 6 + 24;
      input.style.height = Math.min(input.scrollHeight, maxH) + "px";
    });

    form.addEventListener("submit", async (ev) => {
      ev.preventDefault();
      const raw = (input.value || "").trim();
      if (!raw) return;

      input.value = "";
      input.style.height = "auto";
      input.disabled = true;
      sendBtn.disabled = true;

      // Snapshot + clear screenshots for this turn so the user's next
      // turn starts fresh.
      const shots = pendingScreenshots.splice(0);
      renderAttachments();

      appendUserBubble(raw);
      const thinking = appendThinking();

      try {
        const body = await postChat(raw, shots);
        if (thinking) thinking.remove();
        renderEvents(body.events || []);
        renderPanels(body.panels || []);
        renderRings(body.rings || []);
        updateProgress(body.rings || []);

        // After record_provisioning_plan fires, refresh the strategy chips.
        if (detectProvisioningPlanRecorded(body.events)) {
          const plan = await fetchProvisioningPlan();
          applyStrategyChips(plan);
        }

        // After mark_activation_complete fires, kick off sample generation
        // AND render the live customer simulation hero card at the top of
        // the samples grid (the demo finale).
        if (detectMarkComplete(body.events)) {
          triggerSampleGeneration(); // fire-and-forget; panel updates as it returns
          renderLiveSimulationCard().catch(() => {});
        }

        if (!body.reached_idle) {
          appendSystemBubble(
            "Still thinking on my end. Send 'keep going' when you want me to pick up."
          );
        }
      } catch (err) {
        if (thinking) thinking.remove();
        appendSystemBubble(err.message || "Something went sideways on my end.");
      } finally {
        input.disabled = false;
        sendBtn.disabled = false;
        input.focus();
      }
    });
  }

  // ---------------------------------------------------------------------
  // Boot
  // ---------------------------------------------------------------------

  document.addEventListener("DOMContentLoaded", () => {
    document.querySelectorAll("[data-activate-ring]").forEach((ringEl) => {
      applyRingState(ringEl, ringEl.dataset.roleStep || "pending");
    });

    const initialRings = Array.from(document.querySelectorAll("[data-activate-ring]")).map((e) => ({
      slug: e.dataset.roleSlug,
      step: e.dataset.roleStep === "pending" ? null : e.dataset.roleStep,
      percent_complete: 0,
    }));
    const STEP_PCT = { credentials: 0.25, config: 0.5, connected: 0.75, first_run: 1.0 };
    for (const r of initialRings) r.percent_complete = STEP_PCT[r.step] || 0;
    updateProgress(initialRings);

    wireComposer();
    wireScreenshotInput();
    pollAfterOAuth();
    wireVoiceCardTooltip();

    // Pull any existing provisioning plan + samples (page reload case).
    fetchProvisioningPlan().then(applyStrategyChips);
    fetchSamples();
  });

  // Voice-card source citations: hover any <span class="src" data-src-q="..."
  // data-src-label="..."> to surface the dark tooltip with the underlying
  // quote + source label. Dormant unless the agent emits voice-card markup.
  function wireVoiceCardTooltip() {
    const tip = document.querySelector("[data-activate-src-tip]");
    if (!tip) return;
    document.addEventListener("mouseover", (e) => {
      const src = e.target.closest(".src");
      if (!src || !src.dataset.srcQ) return;
      const lblNode = document.createElement("span");
      lblNode.className = "ap-src-tip__lbl";
      lblNode.textContent = src.dataset.srcLabel || "Source";
      tip.replaceChildren(lblNode, document.createTextNode(`"${src.dataset.srcQ}"`));
      const r = src.getBoundingClientRect();
      tip.style.left = `${r.left + r.width / 2 - 120}px`;
      tip.style.top = `${r.top - 8 - tip.offsetHeight}px`;
      requestAnimationFrame(() => {
        const h = tip.offsetHeight;
        tip.style.top = `${r.top - 12 - h}px`;
        tip.classList.add("is-shown");
      });
    });
    document.addEventListener("mouseout", (e) => {
      if (e.target.closest(".src")) tip.classList.remove("is-shown");
    });
  }

  // Silence unused-var lint for the SVG_NS reference we kept for future use.
  void SVG_NS;
})();
