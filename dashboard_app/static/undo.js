// Toast + Undo primitive for the WCAS Client Dashboard.
//
// Exposes a single global: `window.apToast`.
// Every surface that mutates state (Apply a rec, Dismiss attention, pause a
// role, auto-send a message) routes through this so undo is free and the
// visual treatment is consistent.
//
// Public API:
//
//   apToast.push({kind, text, onCommit, onUndo, delayMs})
//     - Queues a delayed commit. If the user clicks "Undo" within delayMs,
//       onUndo() fires (if provided) and onCommit is NOT called. Otherwise
//       onCommit() fires. Returns the toast id.
//
//   apToast.flash({kind, text, durationMs})
//     - Fire-and-forget toast; auto-dismisses.
//
//   apToast.dismiss(id)
//     - Remove a toast immediately.
//
// Kinds: 'undo' (default when onCommit present), 'ok', 'err', 'info'.
// Styling is pure CSS; see styles.css "TOAST / UNDO CHIP" section.

(function () {
    var DEFAULT_DELAY_MS = 10_000;
    var DEFAULT_FLASH_MS = 3_500;
    var MAX_VISIBLE = 4;
    var TICK_MS = 100;

    var toasts = new Map();  // id -> {rootEl, timerId, tickerId}
    var nextId = 1;

    function stackRoot() {
        var root = document.querySelector('.ap-toast-stack');
        if (root) return root;
        root = document.createElement('div');
        root.className = 'ap-toast-stack';
        root.setAttribute('aria-live', 'polite');
        root.setAttribute('aria-atomic', 'true');
        document.body.appendChild(root);
        return root;
    }

    function evictOldest() {
        var root = stackRoot();
        while (root.children.length >= MAX_VISIBLE) {
            // Stack uses column-reverse so DOM order is oldest-first visually
            // bottom. Evict the DOM-first child (visually oldest toast).
            var oldest = root.firstElementChild;
            if (!oldest) break;
            var id = Number(oldest.dataset.toastId);
            commit(id, /*skipCommit=*/true);
        }
    }

    function buildToast(opts, id, fires) {
        var el = document.createElement('div');
        el.className = 'ap-toast ap-toast--' + (opts.kind || (fires ? 'undo' : 'info'));
        el.dataset.toastId = String(id);
        el.setAttribute('role', opts.kind === 'err' ? 'alert' : 'status');

        var row = document.createElement('div');
        row.className = 'ap-toast__row';

        var text = document.createElement('span');
        text.className = 'ap-toast__text';
        text.textContent = opts.text || '';
        row.appendChild(text);

        if (fires) {
            var undoBtn = document.createElement('button');
            undoBtn.className = 'ap-toast__undo';
            undoBtn.type = 'button';
            undoBtn.textContent = 'Undo';
            undoBtn.addEventListener('click', function () {
                undo(id);
            });
            row.appendChild(undoBtn);

            var count = document.createElement('span');
            count.className = 'ap-toast__count';
            count.textContent = Math.ceil((opts.delayMs || DEFAULT_DELAY_MS) / 1000) + 's';
            row.appendChild(count);
        }

        el.appendChild(row);

        if (fires) {
            var dots = document.createElement('div');
            dots.className = 'ap-toast__dots';
            for (var i = 0; i < 10; i++) {
                var dot = document.createElement('span');
                dot.className = 'ap-toast__dot';
                dots.appendChild(dot);
            }
            el.appendChild(dots);
        }

        return el;
    }

    function tickProgress(id, startedAt, durationMs) {
        var entry = toasts.get(id);
        if (!entry) return;
        var elapsed = Date.now() - startedAt;
        var pct = Math.min(1, elapsed / durationMs);
        var count = entry.rootEl.querySelector('.ap-toast__count');
        if (count) {
            var remaining = Math.max(0, Math.ceil((durationMs - elapsed) / 1000));
            count.textContent = remaining + 's';
        }
        var dots = entry.rootEl.querySelectorAll('.ap-toast__dot');
        var lit = Math.floor(pct * dots.length);
        dots.forEach(function (d, idx) {
            d.classList.toggle('ap-toast__dot--lit', idx < lit);
        });
    }

    function fadeOut(el) {
        el.style.transition = 'opacity 180ms ease, transform 180ms ease';
        el.style.opacity = '0';
        el.style.transform = 'translateY(8px)';
        setTimeout(function () {
            if (el.parentNode) el.parentNode.removeChild(el);
        }, 200);
    }

    function commit(id, skipCommit) {
        var entry = toasts.get(id);
        if (!entry) return;
        toasts.delete(id);
        if (entry.timerId) clearTimeout(entry.timerId);
        if (entry.tickerId) clearInterval(entry.tickerId);
        if (!skipCommit && typeof entry.onCommit === 'function') {
            try { entry.onCommit(); } catch (err) { console.error('apToast onCommit error', err); }
        }
        fadeOut(entry.rootEl);
    }

    function undo(id) {
        var entry = toasts.get(id);
        if (!entry) return;
        toasts.delete(id);
        if (entry.timerId) clearTimeout(entry.timerId);
        if (entry.tickerId) clearInterval(entry.tickerId);
        if (typeof entry.onUndo === 'function') {
            try { entry.onUndo(); } catch (err) { console.error('apToast onUndo error', err); }
        }
        fadeOut(entry.rootEl);
    }

    function push(opts) {
        opts = opts || {};
        evictOldest();

        var id = nextId++;
        var fires = typeof opts.onCommit === 'function';
        var delay = (opts.delayMs || DEFAULT_DELAY_MS) | 0;

        var el = buildToast(opts, id, fires);
        stackRoot().appendChild(el);

        var entry = {
            rootEl: el,
            onCommit: opts.onCommit,
            onUndo: opts.onUndo,
            timerId: null,
            tickerId: null,
        };
        toasts.set(id, entry);

        if (fires) {
            var startedAt = Date.now();
            entry.tickerId = setInterval(function () {
                tickProgress(id, startedAt, delay);
            }, TICK_MS);
            entry.timerId = setTimeout(function () {
                commit(id, false);
            }, delay);
        } else {
            var duration = (opts.durationMs || DEFAULT_FLASH_MS) | 0;
            entry.timerId = setTimeout(function () {
                commit(id, true);  // no onCommit on flash toasts
            }, duration);
        }

        return id;
    }

    function flash(opts) {
        opts = opts || {};
        return push({
            kind: opts.kind || 'info',
            text: opts.text || '',
            durationMs: opts.durationMs,
        });
    }

    function dismiss(id) {
        commit(id, true);
    }

    window.apToast = {
        push: push,
        flash: flash,
        dismiss: dismiss,
    };
})();
