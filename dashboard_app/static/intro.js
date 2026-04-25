// Activation wizard intro carousel.
//
// Shows a 4-slide overview of the flow on the owner's first visit.
// Suppressed on returning visits via localStorage. Force-show with ?intro=1
// (used during demo recording).
//
// Manual advance only. Right arrow / Next button advance, Esc / Skip dismiss.
// On finish/skip we set the localStorage flag and fade the carousel out
// so the underlying chat surface becomes interactive.

(() => {
  const SLIDE_COUNT = 4;
  const STORAGE_PREFIX = "ap_intro_seen_";

  function $(sel, root) { return (root || document).querySelector(sel); }
  function $$(sel, root) { return Array.from((root || document).querySelectorAll(sel)); }

  function tenantId() {
    const body = document.body;
    return (body && body.dataset && body.dataset.tenantId) || "_anon";
  }

  function shouldShow() {
    try {
      const params = new URLSearchParams(window.location.search);
      if (params.get("intro") === "1") return true;
      const flag = window.localStorage.getItem(STORAGE_PREFIX + tenantId());
      return !flag;
    } catch (_err) {
      // localStorage blocked / unavailable: default to NOT showing so we
      // don't badger users on every page load.
      return false;
    }
  }

  function markSeen() {
    try {
      window.localStorage.setItem(STORAGE_PREFIX + tenantId(), String(Date.now()));
    } catch (_err) { /* ignore */ }
  }

  function setSlide(intro, idx) {
    const slides = $$("[data-intro-slide]", intro);
    const dots = $$("[data-activate-intro-dot]", intro);
    const nextBtn = $("[data-activate-intro-next]", intro);
    const nextLabel = $("[data-activate-intro-next-label]", intro);
    if (!slides.length) return;
    const clamped = Math.max(0, Math.min(SLIDE_COUNT - 1, idx));
    slides.forEach((s, i) => s.classList.toggle("is-active", i === clamped));
    dots.forEach((d, i) => {
      d.classList.toggle("is-active", i === clamped);
      d.setAttribute("aria-current", i === clamped ? "true" : "false");
    });
    intro.dataset.activeSlide = String(clamped);
    if (nextLabel) {
      nextLabel.textContent = clamped >= SLIDE_COUNT - 1 ? "Let's go" : "Next";
    }
    if (nextBtn) {
      nextBtn.classList.toggle("ap-activate-intro__next--final", clamped >= SLIDE_COUNT - 1);
    }
  }

  function dismiss(intro) {
    if (!intro || intro.dataset.dismissed === "true") return;
    intro.dataset.dismissed = "true";
    intro.setAttribute("aria-hidden", "true");
    intro.classList.add("is-leaving");
    markSeen();
    document.removeEventListener("keydown", keyHandler);
    // Match CSS transition duration; remove from a11y tree fully after fade.
    setTimeout(() => { intro.hidden = true; intro.classList.remove("is-leaving"); }, 320);
  }

  function advance(intro) {
    const cur = parseInt(intro.dataset.activeSlide || "0", 10);
    if (cur >= SLIDE_COUNT - 1) {
      dismiss(intro);
    } else {
      setSlide(intro, cur + 1);
    }
  }

  let keyHandler = null;

  function bindKeys(intro) {
    keyHandler = (ev) => {
      if (ev.key === "Escape") {
        ev.preventDefault();
        dismiss(intro);
      } else if (ev.key === "ArrowRight" || ev.key === "Enter") {
        ev.preventDefault();
        advance(intro);
      } else if (ev.key === "ArrowLeft") {
        ev.preventDefault();
        const cur = parseInt(intro.dataset.activeSlide || "0", 10);
        setSlide(intro, cur - 1);
      }
    };
    document.addEventListener("keydown", keyHandler);
  }

  function show(intro) {
    intro.hidden = false;
    intro.setAttribute("aria-hidden", "false");
    // requestAnimationFrame to let the unhide paint before adding the
    // entrance class; otherwise the transition won't fire on first show.
    requestAnimationFrame(() => intro.classList.add("is-entering"));
    setSlide(intro, 0);
    bindKeys(intro);
    // Focus the Next button so keyboard users can immediately advance.
    const nextBtn = $("[data-activate-intro-next]", intro);
    if (nextBtn) nextBtn.focus();
  }

  function wire(intro) {
    $("[data-activate-intro-next]", intro)?.addEventListener("click", (ev) => {
      ev.preventDefault();
      advance(intro);
    });
    $("[data-activate-intro-skip]", intro)?.addEventListener("click", (ev) => {
      ev.preventDefault();
      dismiss(intro);
    });
    $$("[data-activate-intro-dot]", intro).forEach((dot) => {
      dot.addEventListener("click", (ev) => {
        ev.preventDefault();
        const idx = parseInt(dot.dataset.activateIntroDot || "0", 10);
        setSlide(intro, idx);
      });
    });
  }

  document.addEventListener("DOMContentLoaded", () => {
    const intro = document.querySelector("[data-activate-intro]");
    if (!intro) return;
    wire(intro);
    if (shouldShow()) {
      show(intro);
    }
  });
})();
