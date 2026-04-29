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
            } else if (onRolePage && !queryText) {
                askText.textContent = 'Type your question, Enter to ask this role';
                askLi.dataset.action = 'noop';
            } else if (queryText) {
                askText.textContent = 'Ask your business: "' + queryText + '"';
                askLi.dataset.action = 'ask-global';
                askLi.dataset.question = queryText;
            } else {
                askText.textContent = 'Type a question, Enter to ask your whole business';
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
        } else if (action === 'ask-global') {
            askGlobal(li.dataset.question);
        }
    }

    function askGlobal(question) {
        question = (question || '').trim();
        if (!question) return;
        renderPaletteAnswer({state: 'loading'});

        fetch('/api/ask_global', {
            method: 'POST',
            credentials: 'same-origin',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({question: question}),
        })
        .then(function (r) {
            if (r.status === 429) {
                return r.json().then(function (d) { throw new Error(d.error || 'Take a breath.'); });
            }
            if (!r.ok) throw new Error('HTTP ' + r.status);
            return r.json();
        })
        .then(function (data) {
            renderPaletteAnswer({state: 'answer', data: data, question: question});
        })
        .catch(function (err) {
            renderPaletteAnswer({state: 'error', message: err.message, question: question});
        });
    }

    function renderPaletteAnswer(opts) {
        while (paletteList.firstChild) paletteList.removeChild(paletteList.firstChild);
        paletteItems = [];

        var li = document.createElement('li');
        li.className = 'ap-palette__answer';
        li.dataset.action = 'noop';

        if (opts.state === 'loading') {
            var loading = document.createElement('span');
            loading.className = 'ap-palette__answer-loading';
            loading.textContent = 'Thinking across your whole business...';
            li.appendChild(loading);
        } else if (opts.state === 'error') {
            var err = document.createElement('span');
            err.className = 'ap-palette__answer-error';
            err.textContent = opts.message || 'Could not reach the assistant.';
            li.appendChild(err);
        } else {
            var data = opts.data || {};
            var qLine = document.createElement('div');
            qLine.className = 'ap-palette__answer-question';
            qLine.textContent = 'Q: ' + (opts.question || '');
            li.appendChild(qLine);

            var aText = document.createElement('div');
            aText.className = 'ap-palette__answer-text';
            aText.textContent = data.answer || '(no answer)';
            li.appendChild(aText);

            if (data.sources && data.sources.length) {
                var chipRow = document.createElement('div');
                chipRow.className = 'ap-palette__answer-chips';
                data.sources.slice(0, 6).forEach(function (src) {
                    var chip = document.createElement('span');
                    chip.className = 'ap-palette__source-chip';
                    chip.textContent = (src.source || 'source') + ' · ' + (src.label || '');
                    chipRow.appendChild(chip);
                });
                li.appendChild(chipRow);
            }

            var foot = document.createElement('div');
            foot.className = 'ap-palette__answer-foot';
            if (typeof data.cost_usd === 'number') {
                var pill = document.createElement('span');
                pill.className = 'ap-palette__cost-pill';
                pill.textContent = '$' + data.cost_usd.toFixed(4);
                foot.appendChild(pill);
            }
            var hint = document.createElement('span');
            hint.className = 'ap-palette__answer-hint';
            hint.textContent = 'Ask again · Esc to close';
            foot.appendChild(hint);
            li.appendChild(foot);
        }

        paletteList.appendChild(li);
        paletteItems.push(li);
        paletteHighlight = 0;
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
    // RECEIPTS DRAWER
    // -----------------------------------------------------------------------

    var receiptsDrawer = null;
    var receiptsDrawerBody = null;
    var receiptsDrawerTitle = null;

    function buildReceiptsDrawer() {
        if (receiptsDrawer) return;
        receiptsDrawer = document.createElement('div');
        receiptsDrawer.className = 'ap-receipts-drawer';
        receiptsDrawer.hidden = true;
        receiptsDrawer.setAttribute('role', 'dialog');
        receiptsDrawer.setAttribute('aria-modal', 'true');
        receiptsDrawer.setAttribute('aria-label', 'Receipts');

        var backdrop = document.createElement('div');
        backdrop.className = 'ap-receipts-drawer__backdrop';
        backdrop.addEventListener('click', closeReceiptsDrawer);
        receiptsDrawer.appendChild(backdrop);

        var panel = document.createElement('aside');
        panel.className = 'ap-receipts-drawer__panel';

        var head = document.createElement('header');
        head.className = 'ap-receipts-drawer__head';

        receiptsDrawerTitle = document.createElement('h2');
        receiptsDrawerTitle.className = 'ap-receipts-drawer__title';
        receiptsDrawerTitle.textContent = 'Receipts';
        head.appendChild(receiptsDrawerTitle);

        var closeBtn = document.createElement('button');
        closeBtn.type = 'button';
        closeBtn.className = 'ap-receipts-drawer__close';
        closeBtn.setAttribute('aria-label', 'Close');
        closeBtn.textContent = '✕';
        closeBtn.addEventListener('click', closeReceiptsDrawer);
        head.appendChild(closeBtn);

        panel.appendChild(head);

        var lead = document.createElement('p');
        lead.className = 'ap-receipts-drawer__lead';
        lead.textContent = 'Every outbound message this role sent on your behalf. Privacy mode blurs recipient details.';
        panel.appendChild(lead);

        receiptsDrawerBody = document.createElement('div');
        receiptsDrawerBody.className = 'ap-receipts-drawer__body';
        panel.appendChild(receiptsDrawerBody);

        receiptsDrawer.appendChild(panel);
        document.body.appendChild(receiptsDrawer);
    }

    function openReceiptsDrawer(pipelineId, roleName) {
        buildReceiptsDrawer();
        receiptsDrawer.hidden = false;
        document.body.classList.add('ap-receipts-drawer-open');
        receiptsDrawerTitle.textContent = 'Receipts · ' + (roleName || pipelineId);
        receiptsDrawerBody.textContent = '';
        var loading = document.createElement('p');
        loading.className = 'ap-receipts-drawer__loading';
        loading.textContent = 'Loading the last 25 receipts...';
        receiptsDrawerBody.appendChild(loading);

        fetch('/api/receipts/' + encodeURIComponent(pipelineId) + '?limit=25', {
            method: 'GET',
            credentials: 'same-origin',
        })
        .then(function (r) { if (!r.ok) throw new Error('HTTP ' + r.status); return r.json(); })
        .then(function (data) { renderReceipts(data.receipts || []); })
        .catch(function (err) { renderReceiptsError(err.message); });
    }

    function closeReceiptsDrawer() {
        if (!receiptsDrawer) return;
        receiptsDrawer.hidden = true;
        document.body.classList.remove('ap-receipts-drawer-open');
    }

    function renderReceiptsError(msg) {
        receiptsDrawerBody.textContent = '';
        var err = document.createElement('p');
        err.className = 'ap-receipts-drawer__error';
        err.textContent = 'Could not load receipts: ' + msg;
        receiptsDrawerBody.appendChild(err);
    }

    function renderReceipts(rows) {
        receiptsDrawerBody.textContent = '';
        if (!rows.length) {
            var empty = document.createElement('p');
            empty.className = 'ap-receipts-drawer__empty';
            empty.textContent = 'No receipts yet. They start accumulating on this role\'s first send.';
            receiptsDrawerBody.appendChild(empty);
            return;
        }
        rows.forEach(function (row) {
            var card = document.createElement('article');
            card.className = 'ap-receipt';

            var head = document.createElement('div');
            head.className = 'ap-receipt__head';

            var when = document.createElement('span');
            when.className = 'ap-receipt__when';
            when.textContent = formatTs(row.ts);
            head.appendChild(when);

            var channel = document.createElement('span');
            channel.className = 'ap-receipt__channel';
            channel.textContent = row.channel || 'message';
            head.appendChild(channel);

            if (row.recipient_hint) {
                var recipient = document.createElement('span');
                recipient.className = 'ap-receipt__recipient ap-priv';
                recipient.textContent = ' to ' + row.recipient_hint;
                head.appendChild(recipient);
            }

            card.appendChild(head);

            if (row.subject) {
                var subj = document.createElement('div');
                subj.className = 'ap-receipt__subject';
                subj.textContent = row.subject;
                card.appendChild(subj);
            }

            var body = document.createElement('pre');
            body.className = 'ap-receipt__body';
            var text = row.body || '';
            if (text.length > 500) {
                body.textContent = text.slice(0, 500) + '...';
                var readMore = document.createElement('button');
                readMore.type = 'button';
                readMore.className = 'ap-receipt__read-more';
                readMore.textContent = 'Read full';
                readMore.addEventListener('click', function () {
                    body.textContent = text;
                    readMore.remove();
                });
                card.appendChild(body);
                card.appendChild(readMore);
            } else {
                body.textContent = text;
                card.appendChild(body);
            }

            if (typeof row.cost_usd === 'number' && row.cost_usd > 0) {
                var cost = document.createElement('span');
                cost.className = 'ap-receipt__cost';
                cost.textContent = '$' + row.cost_usd.toFixed(4) + ' to generate';
                card.appendChild(cost);
            }

            receiptsDrawerBody.appendChild(card);
        });
    }

    function formatTs(iso) {
        if (!iso) return '';
        try {
            var d = new Date(iso);
            if (isNaN(d.getTime())) return iso;
            var now = new Date();
            var same = d.toDateString() === now.toDateString();
            var time = d.toLocaleTimeString([], {hour: '2-digit', minute: '2-digit'});
            if (same) return 'Today ' + time;
            return d.toLocaleDateString() + ' ' + time;
        } catch (e) { return iso; }
    }

    function wireReceiptsTriggers() {
        document.querySelectorAll('.ap-receipts-trigger').forEach(function (btn) {
            btn.addEventListener('click', function () {
                var pipelineId = btn.dataset.pipelineId || '';
                var roleName = btn.dataset.roleName || pipelineId;
                openReceiptsDrawer(pipelineId, roleName);
            });
        });
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
            if (e.key === 'Escape' && receiptsDrawer && !receiptsDrawer.hidden) {
                e.preventDefault();
                closeReceiptsDrawer();
                return;
            }
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

    function _readPref(localKey, serverKey) {
        // localStorage wins when it's been explicitly set on this device;
        // otherwise fall back to the server-side tenant pref (rendered
        // into window.WCAS_PREFS by templates/_prefs.html). This means a
        // brand-new tab on a new device honors the owner's saved default
        // immediately, instead of waiting for them to toggle once first.
        var stored = null;
        try { stored = localStorage.getItem(localKey); } catch (e) {}
        if (stored === '1') return true;
        if (stored === '0') return false;
        var serverPrefs = (window.WCAS_PREFS || {});
        return Boolean(serverPrefs[serverKey]);
    }

    function restore() {
        try {
            applyPrivacy(_readPref(LS_PRIVACY, 'privacy_default'));
            applyFocus(localStorage.getItem(LS_FOCUS) === '1');
            applyFeedDensity(_readPref(LS_FEED_DENSE, 'feed_dense_default'));
        } catch (e) {}
    }

    function boot() {
        restore();
        ensurePrivacyButton();
        wireTopbarAsk();
        wireQuickChips();
        wireAttentionBanner();
        wireReceiptsTriggers();
        wireRecentAsks();
        wireRailTrigger();
        wireAccountPopover();
        wireFeedToggle();
        wireKeybinds();
    }

    // -----------------------------------------------------------------------
    // RECENT ASK PILLS
    // -----------------------------------------------------------------------

    function wireRecentAsks() {
        document.querySelectorAll('.ap-shell__rail-recent-pill').forEach(function (btn) {
            btn.addEventListener('click', function () {
                var q = btn.dataset.question || (btn.textContent || '').trim();
                openPalette('? ' + q);
            });
        });
    }

    // -----------------------------------------------------------------------
    // MOBILE RAIL TRIGGER (hamburger)
    // -----------------------------------------------------------------------

    function wireRailTrigger() {
        var trigger = document.querySelector('.ap-shell__rail-trigger');
        var rail = document.getElementById('ap-shell-rail');
        if (!trigger || !rail) return;
        trigger.addEventListener('click', function () {
            var open = rail.classList.toggle('ap-shell__rail--open');
            trigger.setAttribute('aria-expanded', open ? 'true' : 'false');
        });
        // Close the rail if a nav link inside it is activated.
        rail.addEventListener('click', function (e) {
            var a = e.target.closest('a');
            if (!a) return;
            rail.classList.remove('ap-shell__rail--open');
            trigger.setAttribute('aria-expanded', 'false');
        });
    }

    // -----------------------------------------------------------------------
    // ACCOUNT POPOVER (sidebar footer -> log out)
    // -----------------------------------------------------------------------

    function wireAccountPopover() {
        var btn = document.querySelector('.ap-shell__rail-account-btn');
        if (!btn) return;

        var pop = document.createElement('div');
        pop.className = 'ap-account-popover';
        pop.hidden = true;
        pop.setAttribute('role', 'menu');

        var logout = document.createElement('form');
        logout.method = 'post';
        logout.action = '/auth/logout';
        logout.style.margin = '0';

        var logoutBtn = document.createElement('button');
        logoutBtn.type = 'submit';
        logoutBtn.className = 'ap-account-popover__item';
        logoutBtn.setAttribute('role', 'menuitem');
        logoutBtn.textContent = 'Log out';
        logout.appendChild(logoutBtn);
        pop.appendChild(logout);

        btn.parentElement.appendChild(pop);

        btn.addEventListener('click', function (e) {
            e.stopPropagation();
            pop.hidden = !pop.hidden;
            btn.setAttribute('aria-expanded', pop.hidden ? 'false' : 'true');
        });
        document.addEventListener('click', function (e) {
            if (pop.hidden) return;
            if (!pop.contains(e.target) && e.target !== btn && !btn.contains(e.target)) {
                pop.hidden = true;
                btn.setAttribute('aria-expanded', 'false');
            }
        });
    }

    // Public API: other scripts (rec_actions.js, etc) can open the palette
    // pre-filled with a question via window.apShell.openPalette('? text').
    window.apShell = {
        openPalette: openPalette,
        closePalette: closePalette,
    };
    document.addEventListener('ap-open-palette', function (e) {
        var prefix = (e && e.detail && e.detail.prefix) || '';
        openPalette(prefix);
    });

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', boot);
    } else {
        boot();
    }
})();
