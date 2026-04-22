// Shell-level primitives for every authenticated page.
//
//   - Privacy mode (Cmd/Ctrl + Shift + P, topbar eye button)
//   - Focus mode  (Cmd/Ctrl + Shift + F)
//   - Cmd-K palette (Cmd/Ctrl + K, topbar Ask button)
//   - Attention banner Apply / Dismiss / Snooze (wires to /api/attention/act)
//   - Quick-action chips
//   - Feed density toggle
//
// Depends on: undo.js for window.apToast.
// DOM-only rendering (textContent, never innerHTML with dynamic data).

(function () {
    'use strict';

    var LS_PRIVACY = 'wcas_privacy';
    var LS_FOCUS = 'wcas_focus';
    var LS_FEED_DENSE = 'wcas_feed_dense';

    // -----------------------------------------------------------------------
    // PRIVACY MODE
    // -----------------------------------------------------------------------

    function applyPrivacy(on) {
        document.body.classList.toggle('ap-privacy', on);
        var btn = document.querySelector('.ap-privacy-toggle');
        if (btn) {
            btn.setAttribute('aria-pressed', on ? 'true' : 'false');
            btn.setAttribute('title', on ? 'Privacy mode on (Ctrl+Shift+P)' : 'Privacy mode off (Ctrl+Shift+P)');
        }
    }

    function togglePrivacy() {
        var on = !document.body.classList.contains('ap-privacy');
        applyPrivacy(on);
        try { localStorage.setItem(LS_PRIVACY, on ? '1' : '0'); } catch (e) {}
        if (window.apToast) {
            window.apToast.flash({kind: 'info', text: on ? 'Privacy mode on.' : 'Privacy mode off.'});
        }
    }

    // -----------------------------------------------------------------------
    // FOCUS MODE
    // -----------------------------------------------------------------------

    function applyFocus(on) {
        document.body.classList.toggle('ap-focus', on);
    }

    function toggleFocus() {
        var on = !document.body.classList.contains('ap-focus');
        applyFocus(on);
        try { localStorage.setItem(LS_FOCUS, on ? '1' : '0'); } catch (e) {}
        if (window.apToast) {
            window.apToast.flash({kind: 'info', text: on ? 'Focus mode on.' : 'Focus mode off.'});
        }
    }

    // -----------------------------------------------------------------------
    // FEED DENSITY
    // -----------------------------------------------------------------------

    function applyFeedDensity(dense) {
        document.body.classList.toggle('ap-feed-dense', dense);
        var buttons = document.querySelectorAll('.ap-feed__toggle-btn');
        buttons.forEach(function (btn) {
            var isDense = (btn.textContent || '').trim().toLowerCase() === 'dense';
            var active = (dense && isDense) || (!dense && !isDense);
            btn.classList.toggle('ap-feed__toggle-btn--active', active);
            btn.setAttribute('aria-selected', active ? 'true' : 'false');
        });
    }

    function wireFeedToggle() {
        document.querySelectorAll('.ap-feed__toggle-btn').forEach(function (btn) {
            btn.addEventListener('click', function () {
                var wantDense = (btn.textContent || '').trim().toLowerCase() === 'dense';
                applyFeedDensity(wantDense);
                try { localStorage.setItem(LS_FEED_DENSE, wantDense ? '1' : '0'); } catch (e) {}
            });
        });
    }

    // -----------------------------------------------------------------------
    // CMD-K PALETTE
    // -----------------------------------------------------------------------

    var palette = null;
    var paletteInput = null;
    var paletteList = null;
    var paletteItems = [];
    var paletteHighlight = 0;

    function collectRoles() {
        var out = [];
        var seen = new Set();
        document.querySelectorAll('.ap-role-card').forEach(function (card) {
            var href = card.getAttribute('href');
            var titleEl = card.querySelector('.ap-role-card__title');
            if (!href || !titleEl) return;
            var name = (titleEl.textContent || '').trim();
            if (!name || seen.has(href)) return;
            seen.add(href);
            out.push({href: href, name: name, slug: href.replace(/^\/roles\//, '')});
        });
        document.querySelectorAll('.ap-shell__rail-pinned-item').forEach(function (a) {
            var href = a.getAttribute('href');
            if (!href || seen.has(href)) return;
            var name = (a.childNodes[0] ? a.childNodes[0].textContent : '').trim();
            if (!name) return;
            seen.add(href);
            out.push({href: href, name: name, slug: href.replace(/^\/roles\//, '')});
        });
        return out;
    }

    function fuzzyScore(query, target) {
        if (!query) return 1;
        var q = query.toLowerCase();
        var t = target.toLowerCase();
        if (t.startsWith(q)) return 100;
        if (t.indexOf(q) !== -1) return 60;
        var qi = 0;
        for (var i = 0; i < t.length && qi < q.length; i++) {
            if (t[i] === q[qi]) qi++;
        }
        return qi === q.length ? 10 : 0;
    }

    function buildPalette() {
        if (palette) return;

        palette = document.createElement('div');
        palette.className = 'ap-palette';
        palette.setAttribute('role', 'dialog');
        palette.setAttribute('aria-modal', 'true');
        palette.setAttribute('aria-label', 'Search or ask');
        palette.hidden = true;

        var backdrop = document.createElement('div');
        backdrop.className = 'ap-palette__backdrop';
        backdrop.addEventListener('click', closePalette);
        palette.appendChild(backdrop);

        var panel = document.createElement('div');
        panel.className = 'ap-palette__panel';

        var inputRow = document.createElement('div');
        inputRow.className = 'ap-palette__input-row';

        var spark = document.createElement('span');
        spark.className = 'ap-palette__spark';
        spark.setAttribute('aria-hidden', 'true');
        spark.textContent = '✦';
        inputRow.appendChild(spark);

        paletteInput = document.createElement('input');
        paletteInput.className = 'ap-palette__input';
        paletteInput.type = 'search';
        paletteInput.setAttribute('placeholder', 'Jump to a role or type ? to ask');
        paletteInput.setAttribute('aria-label', 'Search');
        paletteInput.setAttribute('autocomplete', 'off');
        inputRow.appendChild(paletteInput);

        var hint = document.createElement('span');
        hint.className = 'ap-palette__hint';
        hint.textContent = 'Esc';
        inputRow.appendChild(hint);

        panel.appendChild(inputRow);

        paletteList = document.createElement('ul');
        paletteList.className = 'ap-palette__list';
        paletteList.setAttribute('role', 'listbox');
        panel.appendChild(paletteList);

        var footer = document.createElement('div');
        footer.className = 'ap-palette__footer';
        footer.textContent = 'Enter to open · ↑↓ to navigate · Esc to close';
        panel.appendChild(footer);

        palette.appendChild(panel);
        document.body.appendChild(palette);

        paletteInput.addEventListener('input', renderPalette);
        paletteInput.addEventListener('keydown', paletteKeydown);
    }

    function renderPalette() {
        var q = paletteInput.value.trim();
        var askMode = q.charAt(0) === '?' || q.charAt(0) === '/';
        var queryText = askMode ? q.slice(1).trim() : q;

        while (paletteList.firstChild) paletteList.removeChild(paletteList.firstChild);
        paletteItems = [];

        if (askMode) {
            var onRolePage = /^\/roles\/[a-z0-9_-]+$/.test(window.location.pathname);
            var askLi = document.createElement('li');
            askLi.className = 'ap-palette__item ap-palette__item--ask';
            askLi.setAttribute('role', 'option');

            var askLabel = document.createElement('div');
            askLabel.className = 'ap-palette__item-label';

            var askSpark = document.createElement('span');
            askSpark.className = 'ap-palette__item-spark';
            askSpark.setAttribute('aria-hidden', 'true');
            askSpark.textContent = '✦';
            askLabel.appendChild(askSpark);

            var askText = document.createElement('span');
            if (onRolePage && queryText) {
                askText.textContent = 'Ask this role: "' + queryText + '"';
                askLi.dataset.action = 'ask-here';
                askLi.dataset.question = queryText;
            } else if (onRolePage) {
                askText.textContent = 'Type your question, Enter to ask this role';
                askLi.dataset.action = 'noop';
            } else {
                askText.textContent = 'Open a role card, then type ? to ask about it';
                askLi.dataset.action = 'noop';
            }
            askLabel.appendChild(askText);
            askLi.appendChild(askLabel);
            paletteList.appendChild(askLi);
            paletteItems.push(askLi);
        }

        var allRoles = collectRoles();
        var scored = allRoles.map(function (r) {
            var s = Math.max(fuzzyScore(queryText, r.name), fuzzyScore(queryText, r.slug));
            return {role: r, score: s};
        }).filter(function (x) { return !queryText || x.score > 0; })
          .sort(function (a, b) { return b.score - a.score; })
          .slice(0, 8);

        if (scored.length === 0 && !askMode) {
            var empty = document.createElement('li');
            empty.className = 'ap-palette__empty';
            empty.textContent = queryText ? 'Nothing matched that.' : 'No roles yet. They show up as soon as your first heartbeat arrives.';
            paletteList.appendChild(empty);
        }

        scored.forEach(function (x) {
            var li = document.createElement('li');
            li.className = 'ap-palette__item';
            li.setAttribute('role', 'option');
            li.dataset.action = 'navigate';
            li.dataset.href = x.role.href;

            var label = document.createElement('div');
            label.className = 'ap-palette__item-label';
            var name = document.createElement('span');
            name.textContent = x.role.name;
            label.appendChild(name);
            li.appendChild(label);

            var kind = document.createElement('span');
            kind.className = 'ap-palette__item-kind';
            kind.textContent = 'Role';
            li.appendChild(kind);

            li.addEventListener('click', function () { activatePaletteItem(li); });
            li.addEventListener('mouseenter', function () {
                paletteHighlight = paletteItems.indexOf(li);
                refreshHighlight();
            });

            paletteList.appendChild(li);
            paletteItems.push(li);
        });

        paletteHighlight = 0;
        refreshHighlight();
    }

    function refreshHighlight() {
        paletteItems.forEach(function (li, idx) {
            li.classList.toggle('ap-palette__item--active', idx === paletteHighlight);
            if (idx === paletteHighlight) li.setAttribute('aria-selected', 'true');
            else li.removeAttribute('aria-selected');
        });
    }

    function paletteKeydown(e) {
        if (e.key === 'ArrowDown') {
            e.preventDefault();
            if (paletteItems.length) {
                paletteHighlight = (paletteHighlight + 1) % paletteItems.length;
                refreshHighlight();
            }
        } else if (e.key === 'ArrowUp') {
            e.preventDefault();
            if (paletteItems.length) {
                paletteHighlight = (paletteHighlight - 1 + paletteItems.length) % paletteItems.length;
                refreshHighlight();
            }
        } else if (e.key === 'Enter') {
            e.preventDefault();
            if (paletteItems[paletteHighlight]) {
                activatePaletteItem(paletteItems[paletteHighlight]);
            }
        } else if (e.key === 'Escape') {
            e.preventDefault();
            closePalette();
        }
    }

    function activatePaletteItem(li) {
        var action = li.dataset.action;
        if (action === 'navigate') {
            window.location.assign(li.dataset.href);
        } else if (action === 'ask-here') {
            askCurrentRole(li.dataset.question);
        }
    }

    function askCurrentRole(question) {
        var match = window.location.pathname.match(/^\/roles\/([a-z0-9_-]+)$/);
        if (!match) return;
        var slug = match[1];
        closePalette();

        var input = document.querySelector('.ap-ask-input');
        var submit = document.querySelector('.ap-ask-submit');
        if (input && submit) {
            input.value = question;
            submit.click();
            input.focus();
        } else {
            fetch('/api/ask', {
                method: 'POST',
                credentials: 'same-origin',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({role_slug: slug, question: question}),
            }).then(function (r) { return r.json(); })
              .then(function (data) {
                  if (window.apToast) {
                      window.apToast.flash({kind: 'info', text: data.answer || 'Assistant had nothing to add.'});
                  }
              })
              .catch(function () {
                  if (window.apToast) {
                      window.apToast.flash({kind: 'err', text: 'Could not reach the assistant.'});
                  }
              });
        }
    }

    function openPalette(prefix) {
        buildPalette();
        palette.hidden = false;
        document.body.classList.add('ap-palette-open');
        paletteInput.value = prefix || '';
        renderPalette();
        setTimeout(function () { paletteInput.focus(); }, 0);
    }

    function closePalette() {
        if (!palette) return;
        palette.hidden = true;
        document.body.classList.remove('ap-palette-open');
    }

    // -----------------------------------------------------------------------
    // ATTENTION BANNER HANDLERS
    // -----------------------------------------------------------------------

    function wireAttentionBanner() {
        var banner = document.querySelector('.ap-attention');
        if (!banner) return;
        var buttons = banner.querySelectorAll('.ap-attention__btn');
        buttons.forEach(function (btn) {
            var label = (btn.textContent || '').trim().toLowerCase();
            var action = label.indexOf('apply') === 0 ? 'apply'
                       : label.indexOf('dismiss') === 0 ? 'dismiss'
                       : label.indexOf('snooze') === 0 ? 'snooze' : null;
            if (!action) return;

            btn.addEventListener('click', function () {
                btn.disabled = true;
                if (!window.apToast) { postAttention(action); return; }
                window.apToast.push({
                    kind: 'undo',
                    text: actionPastTense(action),
                    delayMs: 10_000,
                    onCommit: function () {
                        postAttention(action).then(function () {
                            banner.style.display = 'none';
                        }).catch(function (err) {
                            window.apToast.flash({kind: 'err', text: 'Could not save: ' + err.message});
                            btn.disabled = false;
                        });
                    },
                    onUndo: function () {
                        btn.disabled = false;
                    },
                });
            });
        });
    }

    function actionPastTense(action) {
        if (action === 'apply') return 'Applied. Undo to revert.';
        if (action === 'dismiss') return 'Dismissed.';
        if (action === 'snooze') return 'Snoozed for 24 hours.';
        return 'Action queued.';
    }

    function postAttention(action) {
        return fetch('/api/attention/act', {
            method: 'POST',
            credentials: 'same-origin',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({action: action}),
        }).then(function (r) {
            if (!r.ok) throw new Error('HTTP ' + r.status);
            return r.json();
        });
    }

    // -----------------------------------------------------------------------
    // QUICK-ACTION CHIPS
    // -----------------------------------------------------------------------

    function wireQuickChips() {
        document.querySelectorAll('.ap-quick-actions .ap-chip').forEach(function (chip) {
            chip.addEventListener('click', function () {
                var label = (chip.textContent || '').trim().toLowerCase();
                if (label.indexOf('ask') === 0) {
                    openPalette('? ');
                } else if (label.indexOf('set a goal') === 0) {
                    window.location.assign('/goals');
                } else if (label.indexOf('pause a role') === 0) {
                    openPalette('');
                    if (window.apToast) {
                        window.apToast.flash({kind: 'info', text: 'Pick a role, then use Pause on its detail page.'});
                    }
                } else if (label.indexOf('request something') === 0) {
                    window.location.assign('mailto:info@westcoastautomationsolutions.com?subject=Dashboard%20request');
                }
            });
        });
    }

    // -----------------------------------------------------------------------
    // TOPBAR ASK BUTTON + SEARCH PILL
    // -----------------------------------------------------------------------

    function wireTopbarAsk() {
        var askBtn = document.querySelector('.ap-shell__topbar-actions .ap-ask');
        if (askBtn) {
            askBtn.addEventListener('click', function () { openPalette('? '); });
        }
        var searchInput = document.querySelector('.ap-search-pill__input');
        if (searchInput) {
            searchInput.addEventListener('focus', function () {
                searchInput.blur();
                openPalette('');
            });
        }
    }

    // -----------------------------------------------------------------------
    // PRIVACY TOGGLE BUTTON
    // -----------------------------------------------------------------------

    function ensurePrivacyButton() {
        var actions = document.querySelector('.ap-shell__topbar-actions');
        if (!actions) return;
        if (actions.querySelector('.ap-privacy-toggle')) return;

        var btn = document.createElement('button');
        btn.className = 'ap-privacy-toggle';
        btn.type = 'button';
        btn.setAttribute('aria-label', 'Toggle privacy mode');
        btn.setAttribute('aria-pressed', 'false');
        btn.setAttribute('title', 'Privacy mode (Ctrl+Shift+P)');

        var NS = 'http://www.w3.org/2000/svg';
        var svg = document.createElementNS(NS, 'svg');
        svg.setAttribute('width', '20'); svg.setAttribute('height', '20');
        svg.setAttribute('viewBox', '0 0 24 24');
        svg.setAttribute('fill', 'none');
        svg.setAttribute('stroke', 'currentColor');
        svg.setAttribute('stroke-width', '1.5');
        svg.setAttribute('stroke-linecap', 'round');
        svg.setAttribute('stroke-linejoin', 'round');
        svg.setAttribute('aria-hidden', 'true');
        var path1 = document.createElementNS(NS, 'path');
        path1.setAttribute('d', 'M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z');
        svg.appendChild(path1);
        var circle = document.createElementNS(NS, 'circle');
        circle.setAttribute('cx', '12'); circle.setAttribute('cy', '12'); circle.setAttribute('r', '3');
        svg.appendChild(circle);
        btn.appendChild(svg);

        btn.addEventListener('click', togglePrivacy);

        var bell = actions.querySelector('.ap-shell__bell');
        if (bell) actions.insertBefore(btn, bell);
        else actions.appendChild(btn);
    }

    // -----------------------------------------------------------------------
    // GLOBAL KEYBINDS
    // -----------------------------------------------------------------------

    function wireKeybinds() {
        document.addEventListener('keydown', function (e) {
            var mod = e.metaKey || e.ctrlKey;
            if (!mod) return;
            if (e.key === 'k' || e.key === 'K') {
                e.preventDefault();
                openPalette('');
                return;
            }
            if (e.shiftKey && (e.key === 'p' || e.key === 'P')) {
                e.preventDefault();
                togglePrivacy();
                return;
            }
            if (e.shiftKey && (e.key === 'f' || e.key === 'F')) {
                e.preventDefault();
                toggleFocus();
                return;
            }
        });
    }

    // -----------------------------------------------------------------------
    // BOOT
    // -----------------------------------------------------------------------

    function restore() {
        try {
            applyPrivacy(localStorage.getItem(LS_PRIVACY) === '1');
            applyFocus(localStorage.getItem(LS_FOCUS) === '1');
            applyFeedDensity(localStorage.getItem(LS_FEED_DENSE) === '1');
        } catch (e) {}
    }

    function boot() {
        restore();
        ensurePrivacyButton();
        wireTopbarAsk();
        wireQuickChips();
        wireAttentionBanner();
        wireFeedToggle();
        wireKeybinds();
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', boot);
    } else {
        boot();
    }
})();
