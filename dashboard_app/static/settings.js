// /settings page - auto-save per toggle with undo via apToast.

(function () {
    'use strict';
    var root = document.querySelector('.ap-settings');
    if (!root) return;

    root.querySelectorAll('input[type="checkbox"][data-pref]').forEach(function (input) {
        input.addEventListener('change', function () {
            var pref = input.dataset.pref;
            if (pref === 'require_approval') {
                var pid = input.dataset.pipelineId;
                if (!pid) return;
                savePipelineApproval(pid, input.checked);
            } else {
                savePref(pref, input.checked);
            }
        });
    });

    var pauseBtn = document.getElementById('ap-pause-all');
    if (pauseBtn) {
        pauseBtn.addEventListener('click', function () {
            if (!confirm('Pause every role? Your pipelines will stop sending anything until you turn them back on.')) return;
            fetch('/api/tenant/pause', {method: 'POST', credentials: 'same-origin'})
                .then(function (r) { if (!r.ok) throw new Error('HTTP ' + r.status); return r.json(); })
                .then(function () {
                    if (window.apToast) window.apToast.flash({kind: 'ok', text: 'All roles paused. Reload to see the resume button.'});
                    setTimeout(function () { window.location.reload(); }, 1200);
                })
                .catch(function (err) {
                    if (window.apToast) window.apToast.flash({kind: 'err', text: 'Could not pause: ' + err.message});
                });
        });
    }

    // W3 (settings F5): Resume button only renders server-side when paused.
    var resumeBtn = document.getElementById('ap-resume-all');
    if (resumeBtn) {
        resumeBtn.addEventListener('click', function () {
            fetch('/api/tenant/resume', {method: 'POST', credentials: 'same-origin'})
                .then(function (r) { if (!r.ok) throw new Error('HTTP ' + r.status); return r.json(); })
                .then(function () {
                    if (window.apToast) window.apToast.flash({kind: 'ok', text: 'All roles resumed.'});
                    setTimeout(function () { window.location.reload(); }, 800);
                })
                .catch(function (err) {
                    if (window.apToast) window.apToast.flash({kind: 'err', text: 'Could not resume: ' + err.message});
                });
        });
    }

    function savePref(key, value) {
        var body = {};
        body[key] = value;
        fetch('/api/settings', {
            method: 'POST',
            credentials: 'same-origin',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify(body),
        }).then(function (r) { if (!r.ok) throw new Error('HTTP ' + r.status); return r.json(); })
          .then(function () {
              if (window.apToast) window.apToast.flash({kind: 'ok', text: 'Saved.', durationMs: 2000});
          })
          .catch(function (err) {
              if (window.apToast) window.apToast.flash({kind: 'err', text: 'Could not save: ' + err.message});
          });
    }

    function savePipelineApproval(pid, on) {
        fetch('/api/settings/pipeline/' + encodeURIComponent(pid) + '/require_approval', {
            method: 'POST',
            credentials: 'same-origin',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({require_approval: on}),
        }).then(function (r) { if (!r.ok) throw new Error('HTTP ' + r.status); return r.json(); })
          .then(function () {
              if (window.apToast) window.apToast.flash({kind: 'ok', text: (on ? 'Will queue' : 'Will auto-send') + ' ' + pid, durationMs: 2500});
          })
          .catch(function (err) {
              if (window.apToast) window.apToast.flash({kind: 'err', text: 'Could not save: ' + err.message});
          });
    }
})();
