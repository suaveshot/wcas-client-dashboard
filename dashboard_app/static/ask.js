// Per-role Ask form. POSTs {role_slug, question} to /api/ask and renders
// the answer. Uses DOM methods + textContent so user and model output is
// never interpreted as HTML; there is no innerHTML path with dynamic data.

(function () {
    function clear(el) {
        while (el.firstChild) el.removeChild(el.firstChild);
    }

    function makeDiv(className, text) {
        var el = document.createElement('div');
        if (className) el.className = className;
        if (text !== undefined && text !== null) el.textContent = text;
        return el;
    }

    function makeP(className, text) {
        var el = document.createElement('p');
        if (className) el.className = className;
        if (text !== undefined && text !== null) el.textContent = text;
        return el;
    }

    function renderLoading(container) {
        clear(container);
        container.appendChild(makeDiv('ap-ask-result__loading', 'Thinking...'));
    }

    function renderEmpty(container) {
        clear(container);
        container.appendChild(makeDiv('ap-ask-result__empty', 'Ask me something specific.'));
    }

    function renderError(container, message) {
        clear(container);
        var wrap = makeDiv('ap-ask-result__error');
        wrap.textContent = 'Could not reach the assistant (' + message + '). Try again in a moment.';
        container.appendChild(wrap);
    }

    function renderAnswer(container, data) {
        clear(container);
        var wrap = makeDiv('ap-ask-result__answer');
        wrap.appendChild(makeP('ap-ask-result__text', data.answer || '(no answer)'));

        if (data.sources && data.sources.length) {
            var src = data.sources[0];
            var sourceText = 'Based on ' + (src.source || 'telemetry');
            if (src.last_run) sourceText += ' from ' + src.last_run;
            wrap.appendChild(makeP('ap-ask-result__meta', sourceText));
        }

        if (typeof data.cost_usd === 'number') {
            var costText = 'Cost of this answer: $' + data.cost_usd.toFixed(4);
            if (data.model) costText += ' · ' + data.model;
            wrap.appendChild(makeP('ap-ask-result__meta ap-ask-result__meta--cost', costText));
        }
        container.appendChild(wrap);
    }

    document.querySelectorAll('.ap-role-detail__ask').forEach(function (wrapper) {
        var roleSlug = wrapper.dataset.roleSlug;
        var input = wrapper.querySelector('.ap-ask-input');
        var btn = wrapper.querySelector('.ap-ask-submit');
        var out = wrapper.querySelector('.ap-ask-result');

        function submit() {
            var question = (input.value || '').trim();
            if (question.length < 3) {
                renderEmpty(out);
                return;
            }
            btn.disabled = true;
            renderLoading(out);

            fetch('/api/ask', {
                method: 'POST',
                credentials: 'same-origin',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({role_slug: roleSlug, question: question}),
            })
            .then(function (r) {
                if (!r.ok) throw new Error('HTTP ' + r.status);
                return r.json();
            })
            .then(function (data) { renderAnswer(out, data); })
            .catch(function (err) { renderError(out, err.message); })
            .finally(function () { btn.disabled = false; });
        }

        btn.addEventListener('click', submit);
        input.addEventListener('keydown', function (e) {
            if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') {
                e.preventDefault();
                submit();
            }
        });
    });
})();
