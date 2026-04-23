// Activation wizard client script.
// - Reflects live ring state after an OAuth round-trip by polling
//   /api/activation/state while ?connected=<provider> is in the URL.
// - Animates arcs filling as roles advance through credentials ->
//   config -> connected -> first_run.

(() => {
  const STEP_ORDER = ["credentials", "config", "connected", "first_run"];

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
    const total = rings.length || 1;
    const completed = rings.filter((r) => r.step === "first_run").length;
    const started   = rings.filter((r) => r.step && r.step !== "pending").length;
    const fillEl  = document.querySelector("[data-activate-progress-fill]");
    const labelEl = document.querySelector("[data-activate-progress-label]");
    const etaEl   = document.querySelector("[data-activate-progress-eta]");
    if (fillEl) {
      // Weight partial progress across sub-steps so rings at "connected"
      // show meaningful progress even before first_run.
      const partialPct = rings.reduce((sum, r) => sum + (r.percent_complete || 0), 0) / total;
      fillEl.style.width = `${Math.max(7, Math.round(partialPct * 100))}%`;
    }
    if (labelEl) labelEl.textContent = `Step ${Math.max(started, 1)} of ${total}`;
    if (etaEl) {
      const remaining = Math.max(0, (total - completed) * 2); // rough 2 min / role
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

  // If we just came back from OAuth, poll briefly so the ring grid
  // reflects the server-side advance + probe outcome.
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
    // Once we've landed stable state, scrub the query string so a reload
    // doesn't re-trigger the poll loop.
    if (window.history && window.history.replaceState) {
      const clean = window.location.pathname;
      window.history.replaceState({}, "", clean);
    }
  }

  document.addEventListener("DOMContentLoaded", () => {
    // Seed the per-ring animation state from the initial DOM (server-rendered).
    document.querySelectorAll("[data-activate-ring]").forEach((el) => {
      const step = el.dataset.roleStep;
      applyRingState(el, step || "pending");
    });

    // Initial progress bar from the server-rendered rings.
    const initialRings = Array.from(document.querySelectorAll("[data-activate-ring]")).map((el) => ({
      slug: el.dataset.roleSlug,
      step: el.dataset.roleStep === "pending" ? null : el.dataset.roleStep,
      percent_complete: 0,  // filled by applyRingState via CSS, not needed for bar math below
    }));
    // For the bar, re-derive percent per ring from its step.
    const STEP_PCT = { credentials: 0.25, config: 0.5, connected: 0.75, first_run: 1.0 };
    for (const r of initialRings) r.percent_complete = STEP_PCT[r.step] || 0;
    updateProgress(initialRings);

    pollAfterOAuth();
  });
})();
