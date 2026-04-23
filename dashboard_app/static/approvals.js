// /approvals page - per-draft Approve / Edit / Skip handlers with undo toast.
// Keyboard shortcuts when page is focused: A approve, E edit, S skip, J/K navigate.

(function () {
    'use strict';

    var root = document.querySelector('.ap-approvals');
    if (!root) return;

    var drafts = Array.prototype.slice.call(root.querySelectorAll('.ap-approval'));
    if (!drafts.length) return;

    var focusIdx = 0;
    highlight(focusIdx);

    drafts.forEach(function (card, idx) {
        card.addEventListener('click', function () {
            focusIdx = idx;
            highlight(focusIdx);
        });
        card.querySelectorAll('[data-action]').forEach(function (btn) {
            btn.addEventListener('click', function (e) {
                e.stopPropagation();
                var action = btn.dataset.action;
                fireAction(card, action);
            });
        });
    });

    document.addEventListener('keydown', function (e) {
        // Don't hijack keystrokes while typing in the edit textarea or a form field.
        var target = e.target;
        var tag = (target && target.tagName || '').toLowerCase();
        if (tag === 'textarea' || tag === 'input' || tag === 'select') return;

        if (e.key === 'j' || e.key === 'J') {
            e.preventDefault();
            focusIdx = Math.min(drafts.length - 1, focusIdx + 1);
            highlight(focusIdx);
        } else if (e.key === 'k' || e.key === 'K') {
            e.preventDefault();
            focusIdx = Math.max(0, focusIdx - 1);
            highlight(focusIdx);
        } else if (e.key === 'a' || e.key === 'A') {
            e.preventDefault();
            fireAction(drafts[focusIdx], 'approve');
        } else if (e.key === 'e' || e.key === 'E') {
            e.preventDefault();
            fireAction(drafts[focusIdx], 'edit');
        } else if (e.key === 's' || e.key === 'S') {
            e.preventDefault();
            fireAction(drafts[focusIdx], 'skip');
        }
    });

    function highlight(idx) {
        drafts.forEach(function (d, i) {
            d.classList.toggle('ap-approval--focused', i === idx);
        });
        if (drafts[idx]) drafts[idx].scrollIntoView({block: 'nearest'});
    }

    function fireAction(card, action) {
        if (!card) return;
        var draftId = card.dataset.draftId;
        if (!draftId) return;

        if (action === 'edit') {
            openEditor(card);
            return;
        }
        if (action === 'skip') {
            var reason = window.prompt('Why are you skipping this?', '');
            if (reason === null) return;
            queueAction(card, function () {
                return postOutgoing(draftId, 'skip', {reason: reason});
            }, 'Skipped ' + truncate(card, 40));
            return;
        }
        if (action === 'approve') {
            var editor = card.querySelector('.ap-approval__editor');
            var editing = editor && !editor.hidden;
            var body = null;
            if (editing) {
                var textarea = editor.querySelector('.ap-approval__editor-input');
                if (textarea) body = textarea.value;
            }
            var verb = editing ? 'Edited and sent' : 'Approved';
            var label = verb + ' ' + truncate(card, 40);
            queueAction(card, function () {
                return postOutgoing(draftId, 'approve', {edited_body: body});
            }, label);
            return;
        }
    }

    function openEditor(card) {
        var editor = card.querySelector('.ap-approval__editor');
        if (!editor) return;
        editor.hidden = !editor.hidden;
        if (!editor.hidden) {
            var ta = editor.querySelector('.ap-approval__editor-input');
            if (ta) { ta.focus(); ta.selectionStart = ta.value.length; }
        }
    }

    function queueAction(card, runPromise, toastText) {
        card.classList.add('ap-approval--pending');
        if (!window.apToast) { runPromise(); return; }

        window.apToast.push({
            kind: 'undo',
            text: toastText,
            delayMs: 10000,
            onCommit: function () {
                runPromise().then(function () {
                    card.classList.add('ap-approval--done');
                    card.style.transition = 'opacity 260ms ease';
                    card.style.opacity = '0';
                    setTimeout(function () { card.remove(); drafts = drafts.filter(function (c) { return c !== card; }); fixFocus(); }, 280);
                }).catch(function (err) {
                    card.classList.remove('ap-approval--pending');
                    if (window.apToast) {
                        window.apToast.flash({kind: 'err', text: 'Could not save: ' + (err.message || err)});
                    }
                });
            },
            onUndo: function () {
                card.classList.remove('ap-approval--pending');
            },
        });
    }

    function postOutgoing(draftId, verb, body) {
        return fetch('/api/outgoing/' + encodeURIComponent(draftId) + '/' + verb, {
            method: 'POST',
            credentials: 'same-origin',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify(body || {}),
        }).then(function (r) {
            if (!r.ok) {
                return r.json().then(function (d) { throw new Error(d.error || d.detail || ('HTTP ' + r.status)); });
            }
            return r.json();
        });
    }

    function truncate(card, n) {
        var subj = card.querySelector('.ap-approval__subject');
        if (subj) {
            var t = (subj.textContent || '').trim();
            return t.length > n ? t.slice(0, n) + '...' : t;
        }
        var pipe = card.querySelector('.ap-approval__pipeline');
        return pipe ? pipe.textContent : 'draft';
    }

    function fixFocus() {
        if (!drafts.length) return;
        focusIdx = Math.min(focusIdx, drafts.length - 1);
        highlight(focusIdx);
    }
})();
