// /goals page - add form + remove buttons.

(function () {
    'use strict';

    var form = document.getElementById('ap-goal-form');
    if (form) {
        form.addEventListener('submit', function (e) {
            e.preventDefault();
            var data = new FormData(form);
            var body = {
                title: data.get('title'),
                metric: data.get('metric'),
                target: Number(data.get('target')),
                timeframe: data.get('timeframe'),
            };
            fetch('/api/goals', {
                method: 'POST',
                credentials: 'same-origin',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify(body),
            }).then(function (r) {
                if (!r.ok) return r.json().then(function (d) { throw new Error(d.detail || d.error || ('HTTP ' + r.status)); });
                return r.json();
            }).then(function () {
                if (window.apToast) window.apToast.flash({kind: 'ok', text: 'Goal pinned.'});
                setTimeout(function () { window.location.reload(); }, 500);
            }).catch(function (err) {
                if (window.apToast) window.apToast.flash({kind: 'err', text: err.message});
            });
        });
    }

    document.querySelectorAll('.ap-goal-card__remove').forEach(function (btn) {
        btn.addEventListener('click', function () {
            var gid = btn.dataset.goalId;
            if (!gid) return;
            if (!window.apToast) {
                postRemove(gid);
                return;
            }
            window.apToast.push({
                kind: 'undo',
                text: 'Removing goal...',
                delayMs: 10000,
                onCommit: function () {
                    postRemove(gid).then(function () {
                        var card = document.querySelector('[data-goal-id="' + gid + '"]');
                        if (card) card.remove();
                    }).catch(function (err) {
                        window.apToast.flash({kind: 'err', text: err.message});
                    });
                },
            });
        });
    });

    function postRemove(gid) {
        return fetch('/api/goals/' + encodeURIComponent(gid), {
            method: 'DELETE',
            credentials: 'same-origin',
        }).then(function (r) {
            if (!r.ok) return r.json().then(function (d) { throw new Error(d.detail || d.error || ('HTTP ' + r.status)); });
            return r.json();
        });
    }
})();
