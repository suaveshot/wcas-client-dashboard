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

  async function postChat(message) {
    const resp = await fetch("/api/activation/chat", {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message }),
    });
    if (resp.status === 429) {
      throw new Error("Slow down for a moment, I'm still catching up.");
    }
    if (!resp.ok) {
      throw new Error("Something went sideways. Try that again.");
    }
    return await resp.json();
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

      appendUserBubble(raw);
      const thinking = appendThinking();

      try {
        const body = await postChat(raw);
        if (thinking) thinking.remove();
        renderEvents(body.events || []);
        renderRings(body.rings || []);
        updateProgress(body.rings || []);

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
    pollAfterOAuth();
  });

  // Silence unused-var lint for the SVG_NS reference we kept for future use.
  void SVG_NS;
})();
