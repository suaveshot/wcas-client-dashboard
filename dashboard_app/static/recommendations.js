// /recommendations page  -  tabs Live / Drafts + Refresh button.

(function () {
    var tabs = document.querySelectorAll('.ap-recs-tab');
    if (tabs.length) {
        var lists = document.querySelectorAll('.ap-recs-list');
        tabs.forEach(function (t) {
            t.addEventListener('click', function () {
                var which = t.dataset.tab;
                tabs.forEach(function (x) { x.classList.toggle('ap-recs-tab--active', x === t); });
                lists.forEach(function (l) { l.hidden = l.dataset.tab !== which; });
            });
        });
    }

    var btn = document.getElementById('ap-refresh-recs');
    if (!btn) return;

    function pushToast(kind, text) {
        if (window.apToast && typeof window.apToast.push === 'function') {
            window.apToast.push({ kind: kind, text: text });
        }
    }

    function formatUsd(n) {
        if (typeof n !== 'number' || isNaN(n)) return '';
        if (n < 0.01) return '$' + n.toFixed(4);
        return '$' + n.toFixed(2);
    }

    btn.addEventListener('click', function () {
        if (btn.disabled) return;
        btn.disabled = true;
        btn.classList.add('is-loading');
        var originalLabel = btn.querySelector('span');
        var originalText = originalLabel ? originalLabel.textContent : '';
        if (originalLabel) originalLabel.textContent = 'Refreshing';

        pushToast('info', 'Reading your full business context. This usually takes about 20 seconds.');

        fetch('/api/recommendations/refresh', {
            method: 'POST',
            credentials: 'same-origin',
            headers: { 'Content-Type': 'application/json' },
            body: '{}'
        }).then(function (resp) {
            return resp.json().then(function (body) { return { status: resp.status, body: body }; });
        }).then(function (res) {
            var status = res.status;
            var body = res.body || {};
            if (status === 200 && body.ok) {
                var cost = formatUsd(body.usd);
                var n = body.live_count != null ? body.live_count : (body.count || 0);
                var msg = 'Updated. ' + n + ' fresh ' + (n === 1 ? 'recommendation' : 'recommendations');
                if (cost) msg += '. ' + cost + ' spent';
                msg += '.';
                pushToast('ok', msg);
                setTimeout(function () { window.location.reload(); }, 900);
                return;
            }
            if (status === 429) {
                pushToast('err', body.error || body.detail || 'Daily limit reached. Try again tomorrow.');
            } else if (status === 503) {
                pushToast('err', 'Assistant offline. Try again in a minute.');
            } else if (status === 502) {
                pushToast('err', 'Could not read the response. Try again.');
            } else {
                pushToast('err', body.error || body.detail || 'Refresh failed. Try again.');
            }
            btn.disabled = false;
            btn.classList.remove('is-loading');
            if (originalLabel) originalLabel.textContent = originalText;
        }).catch(function () {
            pushToast('err', 'Network error. Check your connection and try again.');
            btn.disabled = false;
            btn.classList.remove('is-loading');
            if (originalLabel) originalLabel.textContent = originalText;
        });
    });
})();
