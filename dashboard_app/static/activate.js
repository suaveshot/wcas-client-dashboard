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

  function applyRingState(ringEl, step) {
    if (!ringEl) return;
    const normalized = STEP_ORDER.includes(step) ? step : "pending";
    if (ringEl.dataset.roleStep === normalized) return;
    ringEl.dataset.roleStep = normalized;

    const stepEl = ringEl.querySelector("[data-activate-ring-step]");
    if (stepEl) {
      const label = normalized === "first_run"   ? "Running"
                  : normalized === "connected"   ? "Connected"
                  : normalized === "config"      ? "Configuring"
                  : normalized === "credentials" ? "Credentials set"
                  :                                "Not started";
      stepEl.textContent = label;
    }
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
    if (!connectedHint()) return;
    let attempts = 0;
    const maxAttempts = 5;
    while (attempts < maxAttempts) {
      const state = await fetchState();
      if (state) {
        renderRings(state.rings || []);
        updateProgress(state.rings || []);
        if (state.google_validation_status === "ok") break;
        if (state.google_validation_status === "broken") break;
      }
      attempts += 1;
      await new Promise((r) => setTimeout(r, 1200));
    }
    if (window.history && window.history.replaceState) {
      window.history.replaceState({}, "", window.location.pathname);
    }
    // Once rings have settled, nudge the agent so it takes the lead instead
    // of leaving the user staring at the composer wondering what's next.
    await nudgeAgentAfterOAuth();
  }

  async function nudgeAgentAfterOAuth() {
    // Fire once per page load. A URL-param-seeded flag keeps refreshes quiet.
    if (window.__apActivateNudged) return;
    window.__apActivateNudged = true;

    const form = document.querySelector("[data-activate-composer]");
    const input = form ? form.querySelector("[data-activate-input]") : null;
    const sendBtn = form ? form.querySelector("[data-activate-send]") : null;

    const thinking = appendThinking();
    if (input) input.disabled = true;
    if (sendBtn) sendBtn.disabled = true;

    try {
      const body = await postChat(
        "Google is connected now. Tell me what's next and run it."
      );
      if (thinking) thinking.remove();
      renderEvents(body.events || []);
      renderRings(body.rings || []);
      updateProgress(body.rings || []);
    } catch (err) {
      if (thinking) thinking.remove();
      appendSystemBubble(
        "I'll pick up from here once you send a message."
      );
    } finally {
      if (input) { input.disabled = false; input.focus(); }
      if (sendBtn) sendBtn.disabled = false;
    }
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
        renderRings(body.rings || []);
        updateProgress(body.rings || []);

        // After record_provisioning_plan fires, refresh the strategy chips.
        if (detectProvisioningPlanRecorded(body.events)) {
          const plan = await fetchProvisioningPlan();
          applyStrategyChips(plan);
        }

        // After mark_activation_complete fires, kick off sample generation.
        if (detectMarkComplete(body.events)) {
          triggerSampleGeneration(); // fire-and-forget; panel updates as it returns
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

    // Pull any existing provisioning plan + samples (page reload case).
    fetchProvisioningPlan().then(applyStrategyChips);
    fetchSamples();
  });

  // Silence unused-var lint for the SVG_NS reference we kept for future use.
  void SVG_NS;
})();
