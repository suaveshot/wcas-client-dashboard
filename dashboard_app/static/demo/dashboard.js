/* =========================================================
   Dashboard Demo - six scripted scenes (wow ladder)
   1: Morning brief    2: Approve Maria
   3: Reviews drilldown 4: Apply rec → calendar slots
   5: Ask: Tuesday slow  6: End of day recap
   ========================================================= */

(() => {
  const $ = (sel, root=document) => root.querySelector(sel);
  const $$ = (sel, root=document) => [...root.querySelectorAll(sel)];

  /* ---------- timing knobs ---------- */
  let SPEED = 1;
  const T = {
    char: 28,
    counterDur: 1100,
    feedStagger: 220,
    statStagger: 100,
  };
  const ms = (n) => n / SPEED;
  const sleep = (t) => new Promise(r => setTimeout(r, ms(t)));

  /* ============================================================
     SOUND - same primitives as activate demo
     ============================================================ */
  let SOUND_ON = false;
  let audioCtx = null;
  let lastTickAt = 0;
  let SOUND_PROFILE = 'typewriter';
  let SOUND_VOL = 0.8;
  let noiseBuffer = null;

  function ensureAudio(){
    if (audioCtx) return audioCtx;
    try { audioCtx = new (window.AudioContext || window.webkitAudioContext)(); }
    catch (e) { return null; }
    return audioCtx;
  }
  function getNoiseBuffer(){
    if (noiseBuffer) return noiseBuffer;
    const ctx = ensureAudio(); if (!ctx) return null;
    const len = ctx.sampleRate * 0.2;
    noiseBuffer = ctx.createBuffer(1, len, ctx.sampleRate);
    const d = noiseBuffer.getChannelData(0);
    for (let i = 0; i < len; i++) d[i] = Math.random()*2 - 1;
    return noiseBuffer;
  }
  function tone({freq=800, dur=0.05, type='sine', vol=0.05, attack=0.005, release=0.04, freqEnd=null}){
    if (!SOUND_ON) return;
    const ctx = ensureAudio(); if (!ctx) return;
    const t = ctx.currentTime;
    const osc = ctx.createOscillator();
    const g = ctx.createGain();
    osc.type = type;
    osc.frequency.setValueAtTime(freq, t);
    if (freqEnd) osc.frequency.exponentialRampToValueAtTime(freqEnd, t + dur);
    g.gain.setValueAtTime(0, t);
    g.gain.linearRampToValueAtTime(vol * SOUND_VOL, t + attack);
    g.gain.exponentialRampToValueAtTime(0.0001, t + dur + release);
    osc.connect(g).connect(ctx.destination);
    osc.start(t); osc.stop(t + dur + release + 0.02);
  }
  function keyStrike(){
    if (!SOUND_ON) return;
    const ctx = ensureAudio(); if (!ctx) return;
    const t = ctx.currentTime;
    if (SOUND_PROFILE === 'soft'){
      tone({ freq: 1400 + Math.random()*200, dur:0.012, type:'square', vol:0.02, attack:0.001, release:0.008 });
      return;
    }
    const buf = getNoiseBuffer();
    if (buf){
      const src = ctx.createBufferSource(); src.buffer = buf;
      const hp = ctx.createBiquadFilter(); hp.type='highpass'; hp.frequency.value=2400;
      const ng = ctx.createGain();
      ng.gain.setValueAtTime(0, t);
      ng.gain.linearRampToValueAtTime(0.10 * SOUND_VOL, t + 0.001);
      ng.gain.exponentialRampToValueAtTime(0.0001, t + 0.025);
      src.connect(hp).connect(ng).connect(ctx.destination);
      src.start(t); src.stop(t + 0.04);
    }
    const thunkFreq = SOUND_PROFILE === 'mechanical' ? 95 + Math.random()*30 : 140 + Math.random()*40;
    const o1 = ctx.createOscillator(); o1.type='triangle';
    o1.frequency.setValueAtTime(thunkFreq * 2.4, t);
    o1.frequency.exponentialRampToValueAtTime(thunkFreq, t + 0.05);
    const g1 = ctx.createGain();
    g1.gain.setValueAtTime(0, t);
    g1.gain.linearRampToValueAtTime(0.18 * SOUND_VOL, t + 0.002);
    g1.gain.exponentialRampToValueAtTime(0.0001, t + 0.08);
    o1.connect(g1).connect(ctx.destination);
    o1.start(t); o1.stop(t + 0.1);
    const midF = 480 + Math.random()*220;
    const o2 = ctx.createOscillator(); o2.type='square';
    o2.frequency.setValueAtTime(midF, t);
    const g2 = ctx.createGain();
    g2.gain.setValueAtTime(0, t);
    g2.gain.linearRampToValueAtTime(0.05 * SOUND_VOL, t + 0.001);
    g2.gain.exponentialRampToValueAtTime(0.0001, t + 0.02);
    o2.connect(g2).connect(ctx.destination);
    o2.start(t); o2.stop(t + 0.04);
  }
  function tick(){
    const now = performance.now();
    if (now - lastTickAt < 25) return;
    lastTickAt = now;
    keyStrike();
  }
  function chime(){ tone({ freq: 880, freqEnd: 1320, dur:0.18, type:'sine', vol:0.06, attack:0.005, release:0.18 }); }
  function whoosh(){ tone({ freq:200, freqEnd:60, dur:0.35, type:'sawtooth', vol:0.04, attack:0.01, release:0.3 }); }
  function pop(){ tone({ freq:600, freqEnd:900, dur:0.08, type:'sine', vol:0.05, attack:0.002, release:0.07 }); }
  function softClick(){ tone({ freq:520, dur:0.04, type:'sine', vol:0.04, attack:0.002, release:0.04 }); }

  /* ============================================================
     FAUX CURSOR
     ============================================================ */
  const cursor = $('#faux-cursor');
  let cursorShown = false;
  function showCursor(){ cursor.classList.add('is-shown'); cursorShown = true; }
  function hideCursor(){ cursor.classList.remove('is-shown'); cursorShown = false; }
  async function moveCursorTo(target, {dwell=420} = {}){
    const el = typeof target === 'string' ? $(target) : target;
    if (!el) return;
    const r = el.getBoundingClientRect();
    const x = r.left + r.width/2 - 8;
    const y = r.top + r.height/2 - 6;
    cursor.style.left = x + 'px';
    cursor.style.top  = y + 'px';
    if (!cursorShown) showCursor();
    await sleep(dwell);
  }
  async function clickCursor(target){
    const el = typeof target === 'string' ? $(target) : target;
    if (!el) return;
    cursor.classList.add('is-clicking');
    softClick();
    await sleep(220);
    el.click();
    await sleep(180);
    cursor.classList.remove('is-clicking');
  }

  /* ============================================================
     Helpers
     ============================================================ */
  function countUp(el, target, dur = T.counterDur, start = 0){
    const t0 = performance.now();
    const step = () => {
      const k = Math.min(1, (performance.now() - t0) / (dur / SPEED));
      const ease = 1 - Math.pow(1 - k, 3);
      const v = Math.round(start + (target - start) * ease);
      el.textContent = v;
      if (k < 1) requestAnimationFrame(step);
      else el.textContent = target;
    };
    requestAnimationFrame(step);
  }
  function startCountUps(scope = document){
    $$('.count-up', scope).forEach(el => {
      const target = +el.dataset.target;
      const start = +(el.dataset.start || 0);
      countUp(el, target, T.counterDur, start);
    });
  }

  /* roles row content (mirrors activation demo) */
  function renderRoles(){
    const grid = $('#dash-roles');
    if (!grid || grid.dataset.rendered) return;
    const stats = {
      gbp:'42 reviews · 4.9★', seo:'+31% clicks', reviews:'12 replies sent',
      email:'3 drafts ready', chat:'8 chats today', blog:'2 posts queued', social:'1 carousel live'
    };
    const foots = {
      gbp:'last sync 4 min ago', seo:'today',
      reviews:'all responded', email:'awaiting your approval',
      chat:'avg response 12s', blog:'queued', social:'live'
    };
    grid.innerHTML = window.RINGS.map(r => `
      <div class="dash__role" data-role="${r.id}">
        <div class="dash__role-h">
          <div class="dash__role-icon">${window.VENDOR_SVG[r.svg]}</div>
          <div class="dash__role-lbl">${r.label}</div>
          <div class="dash__role-status" title="Live"></div>
        </div>
        <div class="dash__role-stat">${stats[r.id] || ''}</div>
        <div class="dash__role-foot">${foots[r.id] || ''}</div>
      </div>
    `).join('');
    grid.dataset.rendered = '1';
  }

  /* feed events (most recent first) */
  const FEED = [
    { dot:'ok', t:'6 min ago',  body:'Replied to Yelp review from Tomás L. <span style="color:var(--ink-faint)">"Saturday show, brought my mom."</span>' },
    { dot:'',   t:'14 min ago', body:'Booked May 3 · Maria Sanchez · party of 8' },
    { dot:'ok', t:'42 min ago', body:'Updated Google Business hours for the May 3 event' },
    { dot:'',   t:'1 hr ago',   body:'Drafted blog post: <em>"Spring tasting menu " + String.fromCharCode(0x2014) + " 6 dishes from 3 abuelas"</em>' },
    { dot:'ok', t:'2 hr ago',   body:'Activated 7/7 roles · agent fully online' },
  ];
  function renderFeed(){
    const feed = $('#feed');
    feed.innerHTML = FEED.map((f) => `
      <div class="dash__feed-item">
        <div class="dash__feed-dot ${f.dot==='ok'?'dash__feed-dot--ok':''}"></div>
        <div>${f.body}<span class="dash__feed-time">${f.t}</span></div>
      </div>
    `).join('');
  }

  /* ============================================================
     SCENE 1 - Morning brief
     ============================================================ */
  async function sceneOne(){
    resetHome();
    renderRoles();
    renderFeed();
    pop();
    await sleep(160);
    $('#hero').classList.add('is-in');
    await sleep(280);
    // Stagger stats
    const stats = $$('#stats .dash__stat');
    for (let i = 0; i < stats.length; i++){
      stats[i].classList.add('is-in');
      await sleep(T.statStagger);
    }
    // Counters fire as stats land - start them with the hero
    startCountUps($('#stats'));
    await sleep(700);
    // Stream feed - newest first, one at a time
    const items = $$('#feed .dash__feed-item');
    for (let i = 0; i < items.length; i++){
      items[i].classList.add('is-in');
      tick();
      await sleep(T.feedStagger);
    }
  }

  /* ============================================================
     SCENE 2 - Approve Maria
     ============================================================ */
  async function sceneTwo(){
    resetHome({ keepHero:true });
    renderRoles(); renderFeed();
    // ensure visible immediately (skip the long count-ups on replay)
    showHomeInstant();
    await sleep(300);
    // Expand Maria's approval card
    const card = $('#appr-maria');
    if (isPlaying){
      showCursor();
      card.scrollIntoView?.({ block:'nearest' });
      await moveCursorTo(card.querySelector('.dash__appr-from'), {dwell:300});
    }
    card.classList.add('is-expanded');
    pop();
    await sleep(900); // let side-by-side land
    // Move to Send button
    const sendBtn = $('#appr-send-btn');
    if (isPlaying){
      await moveCursorTo(sendBtn, {dwell:380});
      await clickCursor(sendBtn);
    }
    // Mark as sent
    sendBtn.disabled = true;
    sendBtn.style.opacity = .6;
    const actions = card.querySelector('.dash__appr-actions');
    actions.innerHTML = '<span class="dash__sent-flag">Sent · 0.4s · queued in your Sent folder</span>';
    chime();
    await sleep(700);
    card.classList.remove('is-expanded');
    card.classList.add('is-sent');
    // Update count
    $('#appr-count').textContent = '2';
    // Add to feed
    const newRow = document.createElement('div');
    newRow.className = 'dash__feed-item is-new is-in';
    newRow.innerHTML = `<div class="dash__feed-dot dash__feed-dot--ok"></div><div>Sent reply to Maria Sanchez · party of 8 confirmed<span class="dash__feed-time">just now</span></div>`;
    $('#feed').prepend(newRow);
    if (isPlaying) hideCursor();
  }

  /* ============================================================
     SCENE 3 - Reviews drilldown
     ============================================================ */
  const REVIEWS = [
    { name:'Lucia Ortiz', stars:5, when:'Mar 2026', src:'Yelp',
      quote:'Itzel asked about my hermana by name. Who does that? The food was perfect and the show ' + String.fromCharCode(0x2014) + ' actually moving.',
      draft:'"Lucia " + String.fromCharCode(0x2014) + " tell tu hermana we said hi. Saturday at 7, table by the window. " + String.fromCharCode(0x2014) + " Itzel & the Folklorico crew"',
      conf:0.94, label:'High match' },
    { name:'Tomás León', stars:5, when:'Apr 2026', src:'Yelp',
      quote:'Brought my mom for her birthday ' + String.fromCharCode(0x2014) + ' they pulled out a chair facing the dancers. Best Saturday show this season.',
      draft:'"Tomás " + String.fromCharCode(0x2014) + " happy birthday to mom. Bring her back any Saturday, we\'ll save the same chair. " + String.fromCharCode(0x2014) + " Itzel"',
      conf:0.91, label:'High match' },
    { name:'Diego Romero', stars:4, when:'Apr 2026', src:'Google',
      quote:'Great food, but had to wait 25 min for our table. Patio was packed.',
      draft:'"Diego " + String.fromCharCode(0x2014) + " thanks for telling me. 25 min is too long. I\'m adding two more Saturday slots starting next week. " + String.fromCharCode(0x2014) + " Itzel"',
      conf:0.78, label:'Held for review' },
    { name:'Carla M.', stars:5, when:'Apr 2026', src:'Yelp',
      quote:'The empanadas. The empanadas!! Itzel came over and explained which one had the family recipe.',
      draft:'"Carla " + String.fromCharCode(0x2014) + " it\'s the green one. My abuela made me promise never to write it down. " + String.fromCharCode(0x2014) + " Itzel"',
      conf:0.96, label:'High match' },
  ];

  async function sceneThree(){
    resetHome({ keepHero:true, instant:true });
    showHomeInstant();
    renderRoles();
    await sleep(200);

    // Click the Reviews role tile
    const tile = $('#dash-roles [data-role="reviews"]');
    if (isPlaying){
      showCursor();
      await moveCursorTo(tile, {dwell:400});
      await clickCursor(tile);
    }
    tile.classList.add('is-focused');
    await sleep(220);

    // Slide the role detail in
    $('#roledetail').classList.add('is-shown');
    $('#dash-body').style.opacity = '0';
    pop();

    // Render review rows
    const list = $('#rd-list');
    list.innerHTML = REVIEWS.map((r, i) => `
      <div class="review-row" data-i="${i}">
        <div class="review-row__col">
          <div class="review-row__lbl">${r.src} · ${r.when}</div>
          <div class="review-row__src">
            <div style="display:flex;align-items:center;gap:8px">
              <span class="review-row__name">${r.name}</span>
              <span class="stars">${'★'.repeat(r.stars)}${'☆'.repeat(5-r.stars)}</span>
            </div>
            <div class="review-row__quote">${r.quote}</div>
          </div>
        </div>
        <div class="review-row__col">
          <div class="review-row__lbl">Drafted · ${r.conf >= 0.85 ? 'auto-sent' : 'held for you'}</div>
          <div class="review-row__draft">
            <div style="color:var(--ink)">${r.draft}</div>
            <div class="conf">
              <span>Voice match</span>
              <div class="conf__bar"><div class="conf__fill ${r.conf < 0.85 ? 'conf__fill--mid' : ''}" data-conf="${r.conf}"></div></div>
              <span class="conf__num">${r.conf.toFixed(2)}</span>
            </div>
          </div>
        </div>
      </div>
    `).join('');

    await sleep(220);
    const rows = $$('#rd-list .review-row');
    for (let i = 0; i < rows.length; i++){
      rows[i].classList.add('is-in');
      tick();
      await sleep(160);
    }
    // Animate the confidence bars
    await sleep(200);
    $$('#rd-list .conf__fill').forEach(b => {
      const c = +b.dataset.conf;
      b.style.width = (c * 100).toFixed(1) + '%';
    });
    await sleep(900);
    if (isPlaying) hideCursor();
  }

  /* ============================================================
     SCENE 4 - Apply Saturday recommendation
     ============================================================ */
  async function sceneFour(){
    resetHome({ keepHero:true, instant:true });
    showHomeInstant();
    renderRoles(); renderFeed();
    await sleep(280);

    // Highlight the recommendation
    const rec = $('#rec-saturday');
    rec.scrollIntoView?.({ block:'center' });
    if (isPlaying){
      showCursor();
      await moveCursorTo(rec, {dwell:400});
    }
    rec.style.boxShadow = '0 0 0 4px rgba(233,123,46,.18)';
    await sleep(500);

    // Click Apply
    const applyBtn = $('#rec-apply');
    if (isPlaying){
      await moveCursorTo(applyBtn, {dwell:300});
      await clickCursor(applyBtn);
    }

    // Mark as applied
    rec.classList.add('is-applied');
    rec.style.boxShadow = '';
    rec.querySelector('.dash__rec-eyebrow').innerHTML = '<span>✓ Applied</span>';
    rec.querySelector('.dash__rec-title').textContent = 'Saturday slots opened ' + String.fromCharCode(0x2014) + ' 1pm & 4pm';
    rec.querySelector('.dash__rec-body').innerHTML = 'Pushed to Square scheduling and Google Calendar. <strong>Coverage now 9am–9pm.</strong>';
    rec.querySelector('.dash__btn--p').remove();
    chime();
    await sleep(550);

    // Slide calendar in
    $('#cal').classList.add('is-shown');
    $('#dash-body').style.opacity = '0';
    pop();

    // Build calendar grid
    buildCalendar();
    await sleep(420);

    // Bloom the new cells (1pm and 4pm Saturday) one at a time
    const newCells = $$('#cal-grid .cal__cell--new');
    for (let i = 0; i < newCells.length; i++){
      newCells[i].classList.add('is-bloomed');
      pop();
      await sleep(260);
    }

    // Push a new feed entry (visible after we go back, but write it now)
    const newRow = document.createElement('div');
    newRow.className = 'dash__feed-item is-new is-in';
    newRow.innerHTML = `<div class="dash__feed-dot dash__feed-dot--accent"></div><div>Opened 2 Saturday slots: 1pm &amp; 4pm " + String.fromCharCode(0x2014) + " synced to Square + Google<span class="dash__feed-time">just now</span></div>`;
    $('#feed').prepend(newRow);

    await sleep(900);
    if (isPlaying) hideCursor();
  }

  function buildCalendar(){
    const grid = $('#cal-grid');
    const HOURS = ['9am','10am','11am','12pm','1pm','2pm','3pm','4pm','5pm','6pm','7pm','8pm','9pm'];
    const DAYS = ['Sun','Mon','Tue','Wed','Thu','Fri','Sat'];
    // Pre-existing booked schedule (illustrative). Saturday: booked at 11am, 12pm, 2pm, 3pm, 5pm-7pm. 1pm & 4pm = NEW.
    // Days: 0..6 = Sun..Sat
    const sched = {
      // sat (i=6): we'll handle separately
      0: ['blocked','blocked','blocked','blocked','blocked','blocked','blocked','blocked','blocked','blocked','blocked','blocked','blocked'],
      1: ['blocked','blocked','blocked','blocked','blocked','blocked','blocked','blocked','blocked','blocked','blocked','blocked','blocked'],
      2: ['','','booked','booked','','booked','booked','','booked','booked','','',''],
      3: ['','booked','booked','','','booked','booked','','booked','booked','booked','',''],
      4: ['','','booked','booked','booked','','booked','','booked','booked','','',''],
      5: ['','','booked','booked','','booked','booked','booked','booked','booked','booked','booked',''],
      6: ['','','booked','booked','new','booked','booked','new','booked','booked','booked','booked',''],
    };
    let html = '<div></div>';
    DAYS.forEach((d,i) => {
      html += `<div class="cal__col-h ${i===6?'is-sat':''}">${d}</div>`;
    });
    HOURS.forEach((h, hi) => {
      html += `<div class="cal__hour">${h}</div>`;
      for (let di = 0; di < 7; di++){
        const v = sched[di][hi];
        let cls = 'cal__cell';
        let lbl = '';
        if (v === 'booked'){ cls += ' cal__cell--booked'; lbl = '·'; }
        else if (v === 'blocked'){ cls += ' cal__cell--blocked'; lbl = ''; }
        else if (v === 'new'){ cls += ' cal__cell--new'; lbl = 'NEW'; }
        html += `<div class="${cls}">${lbl}</div>`;
      }
    });
    grid.innerHTML = html;
  }

  /* ============================================================
     SCENE 5 - Ask: why was last Tuesday slow?
     ============================================================ */
  const ASK_Q = 'why was last Tuesday slow?';

  // The answer with citation chips
  const ANSWER_HTML = `
    <p>Search clicks dipped <span class="num">22%</span> (84 → 65). Two things showed up in the data: <span class="ask__cite" data-src="district">spring break started in your district Tuesday</span> (about <span class="num">40%</span> of your families), and <span class="ask__cite" data-src="festival">the Mariachi Festival downtown ran 4–8pm</span>, so weeknight class searches were down across Ventura County.</p>
    <p>Wed bounced back to baseline. <strong>Not a trend, just a one-day blip.</strong></p>
  `;

  async function sceneFive(){
    resetHome({ keepHero:true, instant:true });
    showHomeInstant();
    renderRoles(); renderFeed();
    await sleep(200);

    // Click the Ask pill
    const pill = $('#ask-pill');
    if (isPlaying){
      showCursor();
      await moveCursorTo(pill, {dwell:380});
      await clickCursor(pill);
    }

    // Show ask overlay
    $('#ask').classList.add('is-shown');
    pop();
    await sleep(280);
    if (isPlaying) hideCursor();

    // Type the question
    const qEl = $('#ask-q');
    qEl.innerHTML = '<span class="ask__caret"></span>';
    const caret = qEl.querySelector('.ask__caret');
    for (let i = 0; i < ASK_Q.length; i++){
      qEl.insertBefore(document.createTextNode(ASK_Q[i]), caret);
      tick();
      await sleep(T.char + Math.random()*15);
    }
    await sleep(300);
    caret.remove();
    qEl.innerHTML = ASK_Q;

    // Stream answer (typewriter on the rendered HTML - render fully then reveal char by char into a target)
    const body = $('#ask-body');
    body.innerHTML = '<div class="ask__answer" id="ask-answer"></div>';
    await sleep(220);
    await typewriteHTMLInto($('#ask-answer'), ANSWER_HTML, T.char + 4);

    // Source receipts
    await sleep(300);
    const sources = document.createElement('div');
    sources.className = 'ask__sources';
    sources.innerHTML = `
      <div class="ask__sources-h">Sources · 4 receipts</div>
      <div class="ask__source-row"><span class="ask__source-tag">Search Console</span><span class="ask__source-detail">Tuesday clicks 65 vs. 4-week avg 84</span><span class="ask__source-val">−22%</span></div>
      <div class="ask__source-row"><span class="ask__source-tag">School calendar</span><span class="ask__source-detail">Ventura USD spring break · Apr 21–25, 2026</span><span class="ask__source-val">~40% of families</span></div>
      <div class="ask__source-row"><span class="ask__source-tag">Local events</span><span class="ask__source-detail">Mariachi Festival, Plaza Park · 4–8pm Tue</span><span class="ask__source-val">est. 2,400 attended</span></div>
      <div class="ask__source-row"><span class="ask__source-tag">GBP search</span><span class="ask__source-detail">"folklorico class ventura" Wed/Thu · back to baseline</span><span class="ask__source-val">+0–3%</span></div>
    `;
    body.appendChild(sources);
    requestAnimationFrame(() => sources.classList.add('is-in'));
    chime();
    await sleep(900);
  }

  // Stream HTML by walking nodes - preserves the citation chips
  async function typewriteHTMLInto(target, html, charMs){
    const tmp = document.createElement('div');
    tmp.innerHTML = html;
    // Helper to clone shell of an element (no children)
    const cloneShell = (el) => {
      const c = document.createElement(el.tagName);
      for (const a of el.attributes) c.setAttribute(a.name, a.value);
      return c;
    };
    async function walk(srcParent, dstParent){
      for (const node of [...srcParent.childNodes]){
        if (node.nodeType === Node.TEXT_NODE){
          const txt = node.textContent;
          for (let i = 0; i < txt.length; i++){
            dstParent.appendChild(document.createTextNode(txt[i]));
            tick();
            await sleep(charMs);
          }
        } else if (node.nodeType === Node.ELEMENT_NODE){
          const shell = cloneShell(node);
          dstParent.appendChild(shell);
          await walk(node, shell);
        }
      }
    }
    await walk(tmp, target);
  }

  /* ============================================================
     SCENE 6 - End of day recap
     ============================================================ */
  async function sceneSix(){
    resetHome({ keepHero:true, instant:true });
    showHomeInstant();

    // Update greeting to reflect time of day
    $('#dash-greet-time').textContent = 'Good evening';

    // Show recap overlay
    $('#recap').classList.add('is-shown');
    whoosh();
    await sleep(360);

    // Stagger stats with count-ups
    const stats = $$('#recap .recap__stat');
    for (let i = 0; i < stats.length; i++){
      stats[i].classList.add('is-in');
      const cu = stats[i].querySelector('.count-up');
      countUp(cu, +cu.dataset.target, 900, 0);
      pop();
      await sleep(160);
    }

    await sleep(620);
    $('#recap-closer').classList.add('is-in');
    chime();
  }

  /* ============================================================
     RESET / ROUTING helpers
     ============================================================ */
  function resetHome({ keepHero=false, instant=false } = {}){
    // Hide overlays
    $('#roledetail').classList.remove('is-shown');
    $('#cal').classList.remove('is-shown');
    $('#ask').classList.remove('is-shown');
    $('#recap').classList.remove('is-shown');
    $('#dash-body').style.opacity = '1';

    // Reset Ask state
    $('#ask-q').innerHTML = '';
    $('#ask-body').innerHTML = '';

    // Reset Maria card
    const card = $('#appr-maria');
    if (card){
      card.classList.remove('is-expanded','is-sent');
      const actions = card.querySelector('.dash__appr-actions');
      if (actions && !actions.querySelector('.dash__btn')){
        actions.innerHTML = `
          <button class="dash__btn dash__btn--p" id="appr-send-btn">Send</button>
          <button class="dash__btn">Edit</button>
          <button class="dash__btn">Skip</button>`;
      }
    }
    $('#appr-count').textContent = '3';

    // Reset role tile focus
    $$('#dash-roles .dash__role').forEach(t => t.classList.remove('is-focused'));

    // Reset recommendation
    const rec = $('#rec-saturday');
    if (rec){
      rec.classList.remove('is-applied');
      rec.style.boxShadow = '';
      rec.querySelector('.dash__rec-eyebrow').innerHTML = '<span>✦ Suggested</span>';
      rec.querySelector('.dash__rec-title').textContent = 'Add 2 more time slots for Saturday';
      rec.querySelector('.dash__rec-body').innerHTML = 'You\'ve turned away 3 Saturday inquiries this week. Open <strong>1pm</strong> and <strong>4pm</strong>?';
      const acts = rec.querySelectorAll('button');
      if (acts.length < 2){
        // re-add Apply
        const dismiss = rec.querySelector('button');
        const applyBtn = document.createElement('button');
        applyBtn.className = 'dash__btn dash__btn--p';
        applyBtn.id = 'rec-apply';
        applyBtn.style.fontSize = '11px';
        applyBtn.textContent = 'Apply';
        rec.insertBefore(applyBtn, dismiss);
      }
    }

    // Reset calendar
    $$('#cal-grid .cal__cell--new').forEach(c => c.classList.remove('is-bloomed'));

    // Reset hero/stats animation states unless keeping
    if (!keepHero){
      $('#hero').classList.remove('is-in');
      $$('#stats .dash__stat').forEach(s => s.classList.remove('is-in'));
      $$('#stats .count-up').forEach(el => { el.textContent = el.dataset.start || '0'; });
      $$('#feed .dash__feed-item').forEach(f => f.classList.remove('is-in'));
      $$('#feed .is-new').forEach(f => f.remove());
    }

    if (instant){ /* handled by showHomeInstant() */ }

    // Reset recap
    $$('#recap .recap__stat, #recap-closer').forEach(el => el.classList.remove('is-in'));
    $$('#recap .count-up').forEach(el => el.textContent = '0');

    // Reset evening greeting
    $('#dash-greet-time').textContent = 'Good morning';
  }

  function showHomeInstant(){
    $('#hero').classList.add('is-in');
    $$('#stats .dash__stat').forEach(s => s.classList.add('is-in'));
    $$('#stats .count-up').forEach(el => el.textContent = el.dataset.target);
    $$('#feed .dash__feed-item').forEach(f => f.classList.add('is-in'));
  }

  /* ============================================================
     AUTOPLAY
     ============================================================ */
  let isPlaying = false;
  let playToken = 0;
  const SCENE_TITLES = ['Morning brief','Approve reply','Reviews drilldown','Apply rec','Ask · Tuesday slow','End of day'];
  function setAutoplayCaption(n){
    $('#autoplay-caption').textContent = `Scene ${n} of 6 · ${SCENE_TITLES[n-1]}`;
  }
  function setAutoplayProgress(pct){ $('#autoplay-fill').style.width = pct + '%'; }
  let progressRAF = null;
  function animateProgress(from, to, durMs){
    cancelAnimationFrame(progressRAF);
    const t0 = performance.now();
    const step = () => {
      const t = (performance.now() - t0) / (durMs / SPEED);
      const k = Math.min(1, t);
      setAutoplayProgress(from + (to - from) * k);
      if (k < 1 && isPlaying) progressRAF = requestAnimationFrame(step);
    };
    step();
  }
  async function playAll(){
    const myToken = ++playToken;
    isPlaying = true;
    $('#play-btn').classList.add('is-playing');
    if ($('#play-btn').lastChild) $('#play-btn').lastChild.textContent = ' Stop';
    $('#autoplay-bar').classList.add('is-on');
    setAutoplayProgress(0);

    const weights = [1, 0.9, 1.4, 1.2, 1.6, 1.0];
    const sum = weights.reduce((a,b) => a+b, 0);
    let elapsedW = 0;
    for (let i = 1; i <= 6; i++){
      if (myToken !== playToken) return;
      setAutoplayCaption(i);
      const start = elapsedW / sum * 100;
      const end = (elapsedW + weights[i-1]) / sum * 100;
      animateProgress(start, end, weights[i-1] * 7000);
      await runScene(i, { fromAutoplay:true });
      if (myToken !== playToken) return;
      elapsedW += weights[i-1];
      if (i < 6) await sleep(900);
    }
    if (myToken !== playToken) return;
    setAutoplayProgress(100);
    await sleep(2400);
    stopPlay();
  }
  function stopPlay(){
    playToken++;
    isPlaying = false;
    cancelAnimationFrame(progressRAF);
    $('#play-btn').classList.remove('is-playing');
    if ($('#play-btn').lastChild) $('#play-btn').lastChild.textContent = ' Play demo';
    $('#autoplay-bar').classList.remove('is-on');
    setAutoplayProgress(0);
    hideCursor();
  }

  /* ============================================================
     Scene controller
     ============================================================ */
  const SCENES = { 1: sceneOne, 2: sceneTwo, 3: sceneThree, 4: sceneFour, 5: sceneFive, 6: sceneSix };
  let currentScene = 1;

  async function runScene(n, opts = {}){
    currentScene = n;
    $$('.scene-btn').forEach(b => b.classList.toggle('is-active', +b.dataset.scene === n));
    // Notify host (speaker notes sync)
    try { window.parent.postMessage({slideIndexChanged: n - 1}, '*'); } catch(e){}
    try { await SCENES[n](); } catch (err){ console.error(err); }
  }

  $$('.scene-btn').forEach(b => {
    if (!b.dataset.scene) return;
    b.addEventListener('click', () => {
      if (isPlaying) stopPlay();
      runScene(+b.dataset.scene);
    });
  });
  $('#play-btn').addEventListener('click', () => {
    if (isPlaying){ stopPlay(); return; }
    ensureAudio();
    playAll();
  });
  $('#replay-btn').addEventListener('click', () => {
    if (isPlaying) stopPlay();
    runScene(currentScene);
  });
  $('#sound-btn').addEventListener('click', (e) => {
    SOUND_ON = !SOUND_ON;
    e.currentTarget.classList.toggle('is-on', SOUND_ON);
    e.currentTarget.setAttribute('aria-pressed', SOUND_ON);
    if (SOUND_ON){ ensureAudio(); chime(); }
  });
  $('#ask-pill').addEventListener('click', () => {
    if (isPlaying) stopPlay();
    runScene(5);
  });
  $('#rd-back')?.addEventListener('click', () => {
    if (isPlaying) stopPlay();
    runScene(1);
  });
  $('#cal-back')?.addEventListener('click', () => {
    if (isPlaying) stopPlay();
    runScene(1);
  });

  // Esc closes overlays
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape'){
      if ($('#ask').classList.contains('is-shown')) { $('#ask').classList.remove('is-shown'); return; }
      if ($('#recap').classList.contains('is-shown')) { $('#recap').classList.remove('is-shown'); return; }
      if ($('#cal').classList.contains('is-shown')) { runScene(1); return; }
      if ($('#roledetail').classList.contains('is-shown')) { runScene(1); return; }
    }
    if (e.key === 'ArrowRight'){
      e.preventDefault();
      const next = Math.min(6, currentScene + 1);
      if (next !== currentScene) runScene(next);
    } else if (e.key === 'ArrowLeft'){
      e.preventDefault();
      const prev = Math.max(1, currentScene - 1);
      if (prev !== currentScene) runScene(prev);
    } else if (['1','2','3','4','5','6'].includes(e.key)){
      runScene(+e.key);
    }
  });

  /* ============================================================
     TWEAKS
     ============================================================ */
  const TWEAK_DEFAULTS = /*EDITMODE-BEGIN*/{
    "soundProfile": "typewriter",
    "soundVolume": 80,
    "typeSpeedMs": 28
  }/*EDITMODE-END*/;
  function applyTweaks(t){
    SOUND_PROFILE = t.soundProfile;
    SOUND_VOL = t.soundVolume / 100;
    T.char = t.typeSpeedMs;
    $$('#tw-profile .tweaks__seg-btn').forEach(b => {
      b.classList.toggle('is-on', b.dataset.v === t.soundProfile);
    });
    const vol = $('#tw-vol'); if (vol) vol.value = t.soundVolume;
    const volV = $('#tw-vol-v'); if (volV) volV.textContent = t.soundVolume + '%';
    const sp = $('#tw-speed'); if (sp) sp.value = t.typeSpeedMs;
    const spV = $('#tw-speed-v'); if (spV) spV.textContent = t.typeSpeedMs + ' ms/char';
  }
  let TW = { ...TWEAK_DEFAULTS };
  applyTweaks(TW);
  function setTweak(key, val){
    TW = { ...TW, [key]: val };
    applyTweaks(TW);
    try { window.parent.postMessage({type:'__edit_mode_set_keys', edits:{[key]: val}}, '*'); } catch(e){}
  }
  window.addEventListener('message', (e) => {
    const d = e.data || {};
    if (d.type === '__activate_edit_mode'){ $('#tweaks').classList.add('is-open'); }
    else if (d.type === '__deactivate_edit_mode'){ $('#tweaks').classList.remove('is-open'); }
  });
  try { window.parent.postMessage({type:'__edit_mode_available'}, '*'); } catch(e){}
  $('#tw-close')?.addEventListener('click', () => {
    $('#tweaks').classList.remove('is-open');
    try { window.parent.postMessage({type:'__edit_mode_dismissed'}, '*'); } catch(e){}
  });
  $$('#tw-profile .tweaks__seg-btn').forEach(b => {
    b.addEventListener('click', () => {
      setTweak('soundProfile', b.dataset.v);
      ensureAudio();
      if (!SOUND_ON) SOUND_ON = true;
      keyStrike(); setTimeout(keyStrike, 90); setTimeout(keyStrike, 180);
    });
  });
  $('#tw-vol')?.addEventListener('input', (e) => setTweak('soundVolume', +e.target.value));
  $('#tw-speed')?.addEventListener('input', (e) => setTweak('typeSpeedMs', +e.target.value));

  /* ============================================================
     boot
     ============================================================ */
  renderRoles();
  renderFeed();
  runScene(1);
})();
