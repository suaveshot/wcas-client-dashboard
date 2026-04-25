/* agent_viz.js
 * AI-thinking animations for the WCAS Client Dashboard demo mode.
 *
 * Activated only when ?demo=1 is in the URL (sets window.DEMO_VIZ = true).
 * All animations are no-ops otherwise, so production behavior is unchanged.
 *
 * Surfaces:
 *   - Voice extraction overlay (fires before voice_card panel renders)
 *   - CRM mapping overlay (fires before crm_mapping panel renders)
 *   - Live simulation visualizer (latency counter + source streams + typewriter)
 *   - Recommendation scan (hero pulse + scanline sweep on /dashboard)
 *
 * Honors prefers-reduced-motion: animations fall back to instant-on.
 */
(function () {
  "use strict";

  const PREFERS_REDUCED_MOTION = window.matchMedia &&
    window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const VIZ_ENABLED = !!window.DEMO_VIZ && !PREFERS_REDUCED_MOTION;

  function wait(ms) { return new Promise(function (r) { setTimeout(r, ms); }); }

  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text != null) node.textContent = text;
    return node;
  }

  // ===================================================================
  // Voice Extraction Overlay
  // Pages flip in, phrases highlight + fly to a voice fingerprint panel.
  // ~5 seconds. Plays before the voice_card chat bubble renders.
  // ===================================================================
  async function playVoiceExtraction(_payload) {
    if (!VIZ_ENABLED) return;

    const overlay = el("div", "ap-viz-overlay ap-viz-overlay--voice");
    overlay.setAttribute("aria-hidden", "true");

    const header = el("div", "ap-viz-overlay__header");
    header.appendChild(el("span", "ap-viz-overlay__eyebrow", "READING THE WEBSITE"));
    header.appendChild(el("h3", "ap-viz-overlay__title", "Learning your voice"));
    overlay.appendChild(header);

    const body = el("div", "ap-viz-overlay__body");
    const pagesCol = el("div", "ap-viz-pages");
    const fingerprint = el("div", "ap-viz-fingerprint");
    fingerprint.appendChild(el("div", "ap-viz-fingerprint__label", "YOUR VOICE"));
    const fpItems = el("div", "ap-viz-fingerprint__items");
    fingerprint.appendChild(fpItems);
    body.appendChild(pagesCol);
    body.appendChild(fingerprint);
    overlay.appendChild(body);
    document.body.appendChild(overlay);

    // Force reflow then fade in
    overlay.offsetHeight;
    overlay.classList.add("is-visible");

    // Real Garcia-flavored phrases, four pages worth
    const PAGES = [
      { name: "Home", phrases: ["welcome to our studio", "we'd love to have you back"] },
      { name: "About", phrases: ["serving Ventura since 2010", "family-owned"] },
      { name: "Classes", phrases: ["Tuesday 6pm Folklorico", "we saved your spot"] },
      { name: "FAQ", phrases: ["just reply yes", "we get it"] },
    ];

    for (let i = 0; i < PAGES.length; i++) {
      const page = PAGES[i];
      const pageEl = el("div", "ap-viz-page");
      pageEl.appendChild(el("div", "ap-viz-page__header", page.name));
      const lines = el("div", "ap-viz-page__lines");
      page.phrases.forEach(function (phrase) {
        const line = el("div", "ap-viz-page__line");
        line.appendChild(document.createTextNode("... "));
        const span = el("span", "ap-viz-page__phrase", phrase);
        line.appendChild(span);
        line.appendChild(document.createTextNode(" ..."));
        lines.appendChild(line);
      });
      pageEl.appendChild(lines);
      pagesCol.appendChild(pageEl);

      pageEl.offsetHeight;
      pageEl.classList.add("is-in");
      await wait(250);

      // Highlight phrases on this page
      const phraseEls = pageEl.querySelectorAll(".ap-viz-page__phrase");
      for (let j = 0; j < phraseEls.length; j++) {
        phraseEls[j].classList.add("is-highlighted");
        await wait(120);
      }
      await wait(180);

      // Fly each phrase to the fingerprint
      for (let j = 0; j < phraseEls.length; j++) {
        const fpItem = el("div", "ap-viz-fingerprint__item", phraseEls[j].textContent);
        fpItems.appendChild(fpItem);
        fpItem.offsetHeight;
        fpItem.classList.add("is-settled");
      }
      await wait(150);
      pageEl.classList.add("is-out");
    }

    await wait(400);
    overlay.classList.add("is-leaving");
    await wait(400);
    overlay.remove();
  }

  // ===================================================================
  // CRM Mapping Overlay
  // Rows scroll, dots tag them, buckets fill, counters tick.
  // ~5 seconds. Plays before the crm_mapping chat bubble renders.
  // ===================================================================
  async function playCrmMapping(_payload) {
    if (!VIZ_ENABLED) return;

    const overlay = el("div", "ap-viz-overlay ap-viz-overlay--crm");
    overlay.setAttribute("aria-hidden", "true");

    const header = el("div", "ap-viz-overlay__header");
    header.appendChild(el("span", "ap-viz-overlay__eyebrow", "READING YOUR CUSTOMER LIST"));
    header.appendChild(el("h3", "ap-viz-overlay__title", "Grouping customers by what they need"));
    overlay.appendChild(header);

    const body = el("div", "ap-viz-overlay__body");
    const rowsCol = el("div", "ap-viz-crm-rows");
    body.appendChild(rowsCol);

    const buckets = el("div", "ap-viz-buckets");
    const BUCKETS = [
      { id: "inactive", label: "Inactive 30+ days", dot: "red", target: 12, playbook: "Re-engagement email" },
      { id: "active", label: "Active in Summer 2026", dot: "green", target: 15, playbook: "Blog + GBP post" },
      { id: "new", label: "Brand-new (under 14 days)", dot: "blue", target: 3, playbook: "Chat welcome" },
    ];
    const bucketState = {};
    BUCKETS.forEach(function (b) {
      const wrap = el("div", "ap-viz-bucket");
      wrap.appendChild(el("span", "ap-viz-dot ap-viz-dot--" + b.dot));
      const meta = el("div", "ap-viz-bucket__meta");
      meta.appendChild(el("div", "ap-viz-bucket__label", b.label));
      meta.appendChild(el("div", "ap-viz-bucket__playbook", "→ " + b.playbook));
      wrap.appendChild(meta);
      const count = el("div", "ap-viz-bucket__count", "0");
      wrap.appendChild(count);
      buckets.appendChild(wrap);
      bucketState[b.id] = { wrap: wrap, count: count, value: 0 };
    });
    body.appendChild(buckets);
    overlay.appendChild(body);
    document.body.appendChild(overlay);

    overlay.offsetHeight;
    overlay.classList.add("is-visible");
    await wait(200);

    // 30 records: 12 inactive, 15 active, 3 new (matches Garcia seed)
    const NAMES = ["Maria S.", "Carmen R.", "Lucia M.", "Sofia V.", "Isabella G.",
                   "Emma T.", "Olivia C.", "Ana B.", "Camila L.", "Valentina D."];
    const ROWS = [];
    for (let i = 0; i < 30; i++) {
      let bucket;
      if (i < 12) bucket = "inactive";
      else if (i < 27) bucket = "active";
      else bucket = "new";
      ROWS.push({ name: NAMES[i % NAMES.length], bucket: bucket });
    }

    for (let i = 0; i < ROWS.length; i++) {
      const row = ROWS[i];
      const def = BUCKETS.find(function (b) { return b.id === row.bucket; });
      const rowEl = el("div", "ap-viz-crm-row");
      rowEl.appendChild(el("span", "ap-viz-crm-row__name", row.name));
      rowEl.appendChild(el("span", "ap-viz-dot ap-viz-dot--" + def.dot));
      rowsCol.appendChild(rowEl);

      bucketState[row.bucket].value += 1;
      bucketState[row.bucket].count.textContent = String(bucketState[row.bucket].value);
      bucketState[row.bucket].wrap.classList.add("is-active");
      setTimeout((function (b) {
        return function () { bucketState[b].wrap.classList.remove("is-active"); };
      })(row.bucket), 280);

      // Cap visible rows; let the older ones scroll off
      while (rowsCol.children.length > 7) {
        rowsCol.firstChild.classList.add("is-leaving");
        const stale = rowsCol.firstChild;
        setTimeout(function () { stale.remove(); }, 280);
        break;
      }
      await wait(75);
    }

    await wait(500);
    overlay.classList.add("is-leaving");
    await wait(400);
    overlay.remove();
  }

  // ===================================================================
  // Live Simulation Visualizer
  // Returns a controller the caller drives around its API call.
  // Includes:
  //   - source-stream overlay attached to the card (3 streams converging)
  //   - latency counter ticking until freeze
  //   - typewriter for the email body
  //   - sequenced citation badge lighting
  // ===================================================================
  function createSimulationVisualizer() {
    const counter = el("div", "ap-viz-counter", "0.0s");
    let counterTimer = null;
    let counterStart = 0;
    const streams = el("div", "ap-viz-streams");
    if (VIZ_ENABLED) {
      streams.appendChild(el("div", "ap-viz-stream ap-viz-stream--voice"));
      streams.appendChild(el("div", "ap-viz-stream ap-viz-stream--data"));
      streams.appendChild(el("div", "ap-viz-stream ap-viz-stream--playbook"));
    }

    return {
      attachTo(card) {
        if (!VIZ_ENABLED) return;
        card.appendChild(streams);
        card.appendChild(counter);
        streams.offsetHeight;
        streams.classList.add("is-active");
      },
      startCounter() {
        if (!VIZ_ENABLED) return;
        counterStart = performance.now();
        counterTimer = setInterval(function () {
          const elapsed = (performance.now() - counterStart) / 1000;
          counter.textContent = elapsed.toFixed(1) + "s";
        }, 100);
      },
      freezeCounter() {
        if (!VIZ_ENABLED) return;
        if (counterTimer) clearInterval(counterTimer);
        const final = ((performance.now() - counterStart) / 1000).toFixed(1);
        counter.textContent = "Drafted in " + final + "s";
        counter.classList.add("is-done");
      },
      cleanupStreams() {
        if (!VIZ_ENABLED) return;
        streams.classList.remove("is-active");
        setTimeout(function () {
          if (streams.parentNode) streams.parentNode.removeChild(streams);
        }, 400);
      },
      async typewrite(target, text, charsPerSecond) {
        if (!VIZ_ENABLED) {
          target.textContent = text;
          return;
        }
        const cps = charsPerSecond || 50;
        const delay = 1000 / cps;
        target.textContent = "";
        for (let i = 0; i < text.length; i++) {
          target.textContent += text.charAt(i);
          // Skip waits on whitespace blocks for a more natural feel
          if (text.charAt(i) !== " " && text.charAt(i) !== "\n") {
            await wait(delay);
          }
        }
      },
      async lightCitations(badgeNodes) {
        if (!VIZ_ENABLED) {
          for (let i = 0; i < badgeNodes.length; i++) {
            badgeNodes[i].classList.add("is-lit");
          }
          return;
        }
        for (let i = 0; i < badgeNodes.length; i++) {
          await wait(350);
          badgeNodes[i].classList.add("is-lit");
        }
      },
    };
  }

  // ===================================================================
  // Recommendation Scan
  // Hero stat pulse + scanline sweep + chips light sequentially.
  // Fires once on dashboard load when ?demo=1.
  // ===================================================================
  async function playRecommendationScan() {
    if (!VIZ_ENABLED) return;

    const heroNumbers = document.querySelectorAll(
      ".ap-hero-stat__number, .ap-hero-stat__value, .ap-hero-stat-num, .ap-hero__num"
    );
    heroNumbers.forEach(function (n) { n.classList.add("ap-viz-stat-examine"); });

    // Sweep scanline across the main shell
    const main = document.querySelector(".ap-shell__main, .ap-main, main") || document.body;
    const scanline = el("div", "ap-viz-scanline");
    main.appendChild(scanline);
    scanline.offsetHeight;
    scanline.classList.add("is-sweeping");
    await wait(1200);
    scanline.remove();

    await wait(300);
    heroNumbers.forEach(function (n) { n.classList.remove("ap-viz-stat-examine"); });

    // After the sweep, ripple a soft highlight across the recommendation
    // cards (left to right, top to bottom). Reads as "the agent considered
    // each of these and surfaced them just for you."
    const recCards = document.querySelectorAll(".ap-rec");
    for (let i = 0; i < recCards.length; i++) {
      recCards[i].classList.add("ap-viz-rec-considered");
      await wait(280);
      // Auto-clear after a moment so the cards return to baseline
      setTimeout((function (card) {
        return function () { card.classList.remove("ap-viz-rec-considered"); };
      })(recCards[i]), 1400);
    }
  }

  // Auto-fire recommendation scan once on dashboard / home pages
  function maybeAutoScan() {
    if (!VIZ_ENABLED) return;
    const path = window.location.pathname;
    if (path === "/" || path === "/dashboard" || path === "/home") {
      // Wait for layout + initial paint
      setTimeout(playRecommendationScan, 700);
    }
  }
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", maybeAutoScan);
  } else {
    maybeAutoScan();
  }

  // Public surface
  window.AGENT_VIZ = {
    enabled: VIZ_ENABLED,
    playVoiceExtraction: playVoiceExtraction,
    playCrmMapping: playCrmMapping,
    createSimulationVisualizer: createSimulationVisualizer,
    playRecommendationScan: playRecommendationScan,
  };
})();
