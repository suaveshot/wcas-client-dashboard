// Rec card actions: Apply / Dismiss / Ask.
//
// Wires every <article data-rec-id="..."> on the page so its three
// buttons (Apply / Dismiss / Ask) hit /api/recommendations/<id>/act
// or open the Cmd-K palette pre-filled with the rec headline.
//
// DOM-only, no innerHTML with dynamic content.

(function () {
    'use strict';

    function postAction(recId, action) {
        return fetch('/api/recommendations/' + encodeURIComponent(recId) + '/act', {
            method: 'POST',
            credentials: 'same-origin',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({action: action}),
        }).then(function (r) {
            if (!r.ok) throw new Error('HTTP ' + r.status);
            return r.json();
        });
    }

    function flash(kind, text) {
        if (window.apToast && typeof window.apToast.flash === 'function') {
            window.apToast.flash({kind: kind, text: text});
        }
    }

    function pushUndo(text, onCommit, onUndo) {
        if (window.apToast && typeof window.apToast.push === 'function') {
            window.apToast.push({
                kind: 'undo',
                text: text,
                delayMs: 10_000,
                onCommit: onCommit,
                onUndo: onUndo,
            });
        } else {
            // No undo primitive on this page: commit immediately.
            onCommit();
        }
    }

    function findCard(btn) {
        var el = btn;
        while (el && el !== document.body) {
            if (el.dataset && el.dataset.recId) return el;
            el = el.parentElement;
        }
        return null;
    }

    function findHeadline(card) {
        var h = card.querySelector('.ap-rec__headline');
        return h ? (h.textContent || '').trim() : '';
    }

    function collapseCard(card) {
        card.style.transition = 'opacity 240ms ease, transform 240ms ease';
        card.style.opacity = '0';
        card.style.transform = 'translateY(-4px)';
        setTimeout(function () {
            if (card.parentElement) card.parentElement.removeChild(card);
        }, 260);
    }

    function restoreCard(card) {
        card.style.opacity = '';
        card.style.transform = '';
        var btns = card.querySelectorAll('button');
        btns.forEach(function (b) { b.disabled = false; });
    }

    function handleAction(btn, action) {
        var card = findCard(btn);
        if (!card) return;
        var recId = card.dataset.recId;
        if (!recId) return;

        var btns = card.querySelectorAll('button');
        btns.forEach(function (b) { b.disabled = true; });

        var pastTense = action === 'apply' ? 'Applied. Undo to revert.' : 'Dismissed.';
        card.style.transition = 'opacity 180ms ease';
        card.style.opacity = '0.5';

        pushUndo(
            pastTense,
            function () {
                postAction(recId, action).then(function () {
                    collapseCard(card);
                }).catch(function (err) {
                    flash('err', 'Could not save: ' + err.message);
                    restoreCard(card);
                });
            },
            function () {
                restoreCard(card);
            }
        );
    }

    function handleAsk(btn) {
        var card = findCard(btn);
        if (!card) return;
        var headline = findHeadline(card);
        if (!headline) return;
        // Stash the question on the URL hash so shell.js can pre-fill the
        // palette; or just open palette via custom event.
        var ev = new CustomEvent('ap-open-palette', {detail: {prefix: '? ' + headline}});
        document.dispatchEvent(ev);
    }

    function wire(card) {
        if (card.dataset.recWired === '1') return;
        card.dataset.recWired = '1';
        card.querySelectorAll('[data-rec-action]').forEach(function (btn) {
            var action = btn.dataset.recAction;
            btn.addEventListener('click', function (e) {
                e.preventDefault();
                if (action === 'ask') {
                    handleAsk(btn);
                } else if (action === 'apply' || action === 'dismiss') {
                    handleAction(btn, action);
                }
            });
        });
    }

    function boot() {
        document.querySelectorAll('[data-rec-id]').forEach(wire);
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', boot);
    } else {
        boot();
    }
})();
