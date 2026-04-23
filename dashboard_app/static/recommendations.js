// /recommendations page - simple tabs Live / Drafts.

(function () {
    var tabs = document.querySelectorAll('.ap-recs-tab');
    if (!tabs.length) return;
    var lists = document.querySelectorAll('.ap-recs-list');
    tabs.forEach(function (t) {
        t.addEventListener('click', function () {
            var which = t.dataset.tab;
            tabs.forEach(function (x) { x.classList.toggle('ap-recs-tab--active', x === t); });
            lists.forEach(function (l) { l.hidden = l.dataset.tab !== which; });
        });
    });
})();
