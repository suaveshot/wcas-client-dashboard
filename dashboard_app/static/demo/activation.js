/* =========================================================
   Activation Demo - four scripted moments
   Vanilla JS so timing for typewriters / spring choreography
   stays crisp and predictable.
   ========================================================= */

(() => {
  const $ = (sel, root=document) => root.querySelector(sel);
  const $$ = (sel, root=document) => [...root.querySelectorAll(sel)];

  /* ---------- timing knobs ---------- */
  let SPEED = 1; // 1 = normal, 0.4 = slow-mo
  const T = {
    char: 30,           // typewriter ms/char (iMessage-fast)
    charSlow: 75,       // for the "Generic" preface (slower, intentionally bland)
    toolGap: 700,       // between tool rows starting
    toolDone: 600,      // pending → done
    receiptStagger: 90,
  };
  const ms = (n) => n / SPEED;

  /* ---------- shared chat helpers ---------- */
  const msgs = $('#msgs');

  function clearChat(){ msgs.innerHTML = ''; }
  function clearRings(){ $('#ring-grid').innerHTML = ''; renderRings(initialRingState()); }
  function setEyebrow(s, sub){
    $('#chat-eyebrow').textContent = s;
    if (sub != null) $('#hint-l').innerHTML = sub;
  }
  function setTitle(html){ $('#chat-title').innerHTML = html; }

  function agentMsg(){
    const wrap = document.createElement('div');
    wrap.className = 'ap-msg';
    wrap.innerHTML = '<div class="ap-msg__glyph">✦</div><div class="ap-msg__body"></div>';
    msgs.appendChild(wrap);
    return wrap.querySelector('.ap-msg__body');
  }
  function userMsg(text){
    const wrap = document.createElement('div');
    wrap.className = 'ap-msg ap-msg--user';
    wrap.innerHTML = `<div class="ap-bubble">${text}</div>`;
    msgs.appendChild(wrap);
    scrollChat();
    return wrap;
  }
  function scrollChat(){ msgs.scrollTop = msgs.scrollHeight; }

  function sleep(t){ return new Promise(r => setTimeout(r, ms(t))); }

  /* ============================================================
     SOUND DESIGN - WebAudio, no assets
     ============================================================ */
  let SOUND_ON = false;
  let audioCtx = null;
  let lastTickAt = 0;
  let SOUND_PROFILE = 'typewriter'; // 'typewriter' | 'soft' | 'mechanical'
  let SOUND_VOL = 0.8; // 0..1 multiplier
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
    const ctx = ensureAudio();
    if (!ctx) return;
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
    osc.start(t);
    osc.stop(t + dur + release + 0.02);
  }
  // typewriter key strike - layered click + thunk + brief noise burst
  function keyStrike(){
    if (!SOUND_ON) return;
    const ctx = ensureAudio(); if (!ctx) return;
    const t = ctx.currentTime;

    if (SOUND_PROFILE === 'soft'){
      // original soft tick
      tone({ freq: 1400 + Math.random()*200, dur:0.012, type:'square', vol:0.02, attack:0.001, release:0.008 });
      return;
    }

    // Layer 1 - short noise burst (the "click")
    const buf = getNoiseBuffer();
    if (buf){
      const src = ctx.createBufferSource();
      src.buffer = buf;
      const hp = ctx.createBiquadFilter(); hp.type = 'highpass'; hp.frequency.value = 2400;
      const ng = ctx.createGain();
      ng.gain.setValueAtTime(0, t);
      ng.gain.linearRampToValueAtTime(0.10 * SOUND_VOL, t + 0.001);
      ng.gain.exponentialRampToValueAtTime(0.0001, t + 0.025);
      src.connect(hp).connect(ng).connect(ctx.destination);
      src.start(t);
      src.stop(t + 0.04);
    }

    // Layer 2 - low thunk (the key body hitting the platen)
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

    // Layer 3 - mid-body click pitch (varies slightly per "key")
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
  function returnDing(){
    if (!SOUND_ON) return;
    // carriage return ding - a small bell at end of typing
    tone({ freq: 1760, dur:0.18, type:'sine', vol:0.06, attack:0.002, release:0.18 });
    setTimeout(() => tone({ freq: 2637, dur:0.22, type:'sine', vol:0.04, attack:0.002, release:0.22 }), 60);
  }
  function chime(){ tone({ freq: 880, freqEnd: 1320, dur:0.18, type:'sine', vol:0.06, attack:0.005, release:0.18 }); }
  function chordFinale(){
    [523.25, 659.25, 783.99, 1046.5].forEach((f, i) => {
      setTimeout(() => tone({ freq:f, dur:0.6, type:'sine', vol:0.05, attack:0.01, release:0.6 }), i * 80);
    });
  }
  function whoosh(){ tone({ freq:200, freqEnd:60, dur:0.35, type:'sawtooth', vol:0.04, attack:0.01, release:0.3 }); }

  /* ============================================================
     FAUX CURSOR
     ============================================================ */
  const cursor = $('#faux-cursor');
  let cursorShown = false;
  function showCursor(){ cursor.classList.add('is-shown'); cursorShown = true; }
  function hideCursor(){ cursor.classList.remove('is-shown'); cursorShown = false; }
  async function moveCursorTo(target, {dwell=400} = {}){
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
    tone({ freq:600, dur:0.05, type:'sine', vol:0.04, attack:0.002, release:0.04 });
    await sleep(220);
    el.click();
    await sleep(180);
    cursor.classList.remove('is-clicking');
  }
  async function hoverCursorOver(target, {dwell=900} = {}){
    const el = typeof target === 'string' ? $(target) : target;
    if (!el) return;
    await moveCursorTo(el, {dwell:0});
    el.dispatchEvent(new MouseEvent('mouseover', {bubbles:true}));
    await sleep(dwell);
    el.dispatchEvent(new MouseEvent('mouseout', {bubbles:true}));
  }

  /* ============================================================
     RING STATE / RENDER
     ============================================================ */

  // stages: 0 = idle, 1 = ready, 2 = running, 3 = connected (closed)
  function initialRingState(){
    return [
      { id:'gbp',    stage:3, cred:'Last sync 2 min ago' },
      { id:'seo',    stage:3, cred:'3 sites tracked' },
      { id:'reviews',stage:3, cred:'Voice accepted' },
      { id:'email',  stage:1, cred:'Needs GHL' },
      { id:'chat',   stage:0, cred:'1-line snippet' },
      { id:'blog',   stage:0, cred:'WordPress detected' },
      { id:'social', stage:0, cred:'Optional' }
    ];
  }
  let RING_STATE = initialRingState();

  const STAGE_LABELS = ['Not started','Ready','Running','Connected'];
  const STAGE_CLASSES = ['ap-ring--idle','ap-ring--ready','ap-ring--running','ap-ring--connected'];

  function renderRings(state){
    RING_STATE = state;
    const grid = $('#ring-grid');
    grid.innerHTML = '';
    const meta = window.RINGS;
    state.forEach((r, idx) => {
      const m = meta.find(x => x.id === r.id);
      const stageClass = STAGE_CLASSES[r.stage];
      const arcLen = 2 * Math.PI * 40;
      const arcOffset = arcLen * (1 - r.stage/3);
      const stagesHtml = [0,1,2,3].map(i => {
        if (r.stage > i) return '<span class="on"></span>';
        if (r.stage === i+1 && r.stage < 3) return '<span class="now"></span>';
        if (r.stage === 3) return '<span class="on"></span>';
        return '<span></span>';
      }).join('');
      const div = document.createElement('div');
      div.className = `ap-ring ${stageClass}`;
      div.dataset.ringId = r.id;
      div.innerHTML = `
        <div class="ap-ring__bezel">
          <svg class="ap-ring__arc" viewBox="0 0 84 84">
            <circle class="arc-track" cx="42" cy="42" r="40"></circle>
            <circle class="arc-fill" cx="42" cy="42" r="40"
              stroke-dasharray="${arcLen}" stroke-dashoffset="${arcOffset}"></circle>
          </svg>
          ${window.VENDOR_SVG[m.svg]}
        </div>
        <div class="ap-ring__label">${m.label}</div>
        <span class="ap-ring__pill">${r.stage===3?'Connected':STAGE_LABELS[r.stage]}</span>
        <div class="ap-stages">${stagesHtml}</div>
        <div class="ap-cred">${r.cred || ''}</div>
      `;
      grid.appendChild(div);
    });
    // progress bar
    const closed = state.filter(r => r.stage === 3).length;
    $('#prog-count').textContent = `${closed} of 7`;
    $('#prog-fill').style.width = `${(closed/7)*100}%`;
  }

  function updateRing(id, patch){
    const idx = RING_STATE.findIndex(r => r.id === id);
    if (idx < 0) return;
    RING_STATE[idx] = { ...RING_STATE[idx], ...patch };
    renderRings(RING_STATE);
  }

  /* ============================================================
     SCENE 1 - Tool-call thinking surface
     ============================================================ */

  const TOOLS = [
    { tool:'fetch_url',         text:'Reading your About page on garciafolklorico.com',  done:'Reading your About page', detail:'2.1 KB · 4 sections' },
    { tool:'extract_facts',     text:'Pulling specific phrases and signals',              done:'Found 8 facts',           detail:'tone, signature, locations' },
    { tool:'list_reviews',      text:'Loading the last 487 Google reviews',               done:'Read 487 reviews',        detail:'avg 4.8 ★ · 2 locations' },
    { tool:'cluster_phrases',   text:'Clustering recurring phrases by sentiment',         done:'14 phrase clusters',      detail:'3 owner-voice patterns' },
    { tool:'draft_voice',       text:'Drafting voice options',                            done:'Drafted 3 voice options', detail:'1 recommended' }
  ];

  async function sceneOne({stopAt = 'end'} = {}){
    clearChat();
    setEyebrow('Activation · 0 of 7 connected', 'Reading your About page right now.');
    setTitle("Hey Itzel. Give me 30 seconds.<br>I'll learn your business first.");
    renderRings([
      { id:'gbp', stage:1, cred:'Ready to connect' },
      { id:'seo', stage:1, cred:'Ready to connect' },
      { id:'reviews', stage:1, cred:'Ready to connect' },
      { id:'email', stage:0, cred:'Needs CRM' },
      { id:'chat', stage:0, cred:'Not started' },
      { id:'blog', stage:0, cred:'WordPress detected' },
      { id:'social', stage:0, cred:'Optional' }
    ]);

    await sleep(280);
    userMsg('Help me set this thing up.');
    await sleep(360);

    // Agent message with embedded thinking surface
    const body = agentMsg();
    body.innerHTML = `
      <p><strong>On it.</strong> Let me look at what you already have so I'm not asking obvious questions.</p>
      <div class="think" id="think">
        <div class="think__header">Agent · live tool calls</div>
        <ul class="think__list" id="think-list"></ul>
      </div>
    `;
    scrollChat();
    const list = $('#think-list', body);

    // build all rows up front (hidden)
    TOOLS.forEach((t,i) => {
      const li = document.createElement('li');
      li.className = 'think__row is-pending';
      li.innerHTML = `
        <span class="think__icon"></span>
        <span class="think__tool">${t.tool}()</span>
        <span class="think__text">${t.text}<span class="think__caret"></span></span>
        <span class="think__elapsed"></span>
      `;
      list.appendChild(li);
    });
    const rows = [...list.children];

    // animate them sequentially
    for (let i = 0; i < TOOLS.length; i++){
      const row = rows[i];
      row.classList.remove('is-pending');
      row.classList.add('is-shown', 'is-active');
      // remove caret/active state on previous row
      if (i > 0){
        const prev = rows[i-1];
        prev.classList.remove('is-active');
        prev.classList.add('is-done');
        prev.querySelector('.think__icon').innerHTML =
          '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><path d="M20 6 9 17l-5-5"/></svg>';
        prev.querySelector('.think__text').innerHTML =
          `${TOOLS[i-1].done} <span style="color:var(--ink-faint);font-size:11px;margin-left:4px">· ${TOOLS[i-1].detail}</span>`;
        prev.querySelector('.think__elapsed').textContent = `${(0.3 + Math.random()*0.7).toFixed(1)}s`;
      }
      scrollChat();
      await sleep(T.toolGap);
    }
    // close last row
    const last = rows[rows.length-1];
    last.classList.remove('is-active');
    last.classList.add('is-done');
    last.querySelector('.think__icon').innerHTML =
      '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><path d="M20 6 9 17l-5-5"/></svg>';
    last.querySelector('.think__text').innerHTML =
      `${TOOLS[TOOLS.length-1].done} <span style="color:var(--ink-faint);font-size:11px;margin-left:4px">· ${TOOLS[TOOLS.length-1].detail}</span>`;
    last.querySelector('.think__elapsed').textContent = '0.6s';
    $('#think').classList.add('is-done');
    $('#think .think__header').firstChild && ($('#think .think__header').innerHTML = 'Agent · 5 tools, 3.1s');
    scrollChat();

    if (stopAt === 'end') return;
  }

  /* ============================================================
     SCENE 2 - Voice card side-by-side reveal
     ============================================================ */

  const GENERIC = [
    { lbl:'Tone',         text:'Friendly, professional, and helpful' },
    { lbl:'Signature',    text:'Best regards, The Garcia Folklorico Team' },
    { lbl:'Sample reply', text:'Thank you so much for your kind review! We truly appreciate your business and look forward to welcoming you back again soon.' }
  ];

  // YOUR VOICE - phrases inside [[ ]] are lifted from the owner's site / reviews
  const VOICE = [
    { lbl:'Tone',         tokens:[
        { t:'Like a ', src:null },
        { t:'tía who gives you the abuela treatment in class', src:{ kind:'about', q:'I run this studio like my abuela ran her zapateado classes. With love, and a clipboard.', label:'About page · paragraph 3' } },
        { t:'. ', src:null },
        { t:'Specific over polished', src:{ kind:'reviews', q:'Itzel told us exactly which dance we were watching and why it mattered. No fluff.', label:'5★ review · L. Ortiz · Mar 2026' } },
        { t:'.', src:null }
      ]},
    { lbl:'Signature',    tokens:[
        { t:'“abrazos, ', src:null },
        { t:'Itzel & the Folklorico crew', src:{ kind:'about', q:"I'm Itzel, and this is the Folklorico crew I've built over 14 years.", label:'About page · paragraph 1' } },
        { t:'” ', src:null },
        { t:'(she signs every email this way)', src:null }
      ]},
    { lbl:'Sample reply', tokens:[
        { t:'“Lucia, ', src:null },
        { t:'tell your hermana we said hi', src:{ kind:'reviews', q:'Itzel still asks about my hermana by name. Three years in and that kind of memory floors me.', label:'Yelp review · May 2026' } },
        { t:'. ', src:null },
        { t:"Saturday at 11, jarabe class. Door's open at 10:45 if you want to warm up", src:{ kind:'reviews', q:"17 of her replies confirm a class time with a personal detail like 'come early to warm up.'", label:'Reviews · phrase cluster #2' } },
        { t:'.”', src:null }
      ]}
  ];

  async function sceneTwo({skipPreface = false} = {}){
    clearChat();
    setEyebrow('Activation · 3 of 7 connected', 'Drafting your voice from the About page.');
    setTitle('Here\'s the voice I\'d use.<br>Lifted from your own words.');
    renderRings([
      { id:'gbp', stage:3, cred:'Connected' },
      { id:'seo', stage:3, cred:'3 sites tracked' },
      { id:'reviews', stage:3, cred:'Connecting…' },
      { id:'email', stage:1, cred:'Needs GHL' },
      { id:'chat', stage:0, cred:'Not started' },
      { id:'blog', stage:0, cred:'WordPress detected' },
      { id:'social', stage:0, cred:'Optional' }
    ]);

    await sleep(200);
    userMsg('What would you sound like replying to my reviews?');
    await sleep(360);

    const body = agentMsg();
    body.style.width = '100%';
    body.innerHTML = `
      <p>Most chatbots default to <strong>generic</strong>. I read 487 of your reviews and your About page first. Compare:</p>
      <div class="voice-card is-step-0" id="vcard">
        <div class="voice-card__head">
          <span>Voice profile</span>
          <span class="voice-card__head-r">
            <span class="v-tag-l">Generic</span>
            <span class="v-tag-r">Your voice</span>
          </span>
        </div>
        <div class="voice-grid">
          <div class="voice-col voice-col--generic" id="vcol-l">
            <div class="voice-col__label">What most AI gives you</div>
            <div id="vlines-l"></div>
          </div>
          <div class="voice-col voice-col--yours" id="vcol-r">
            <div class="voice-col__label">What I drafted from your site + reviews</div>
            <div id="vlines-r"></div>
          </div>
        </div>
        <div class="voice-card__actions">
          <button class="ap-btn ap-btn--primary"><svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="M20 6 9 17l-5-5"/></svg>Accept this voice</button>
          <button class="ap-btn ap-btn--ghost">Tweak it</button>
          <button class="ap-btn ap-btn--ghost" style="margin-left:auto">See sources (2)</button>
        </div>
      </div>
    `;
    const card = $('#vcard');
    const colL = $('#vlines-l');
    const colR = $('#vlines-r');
    scrollChat();

    // pre-build R lines empty (so layout doesn't jump when we type into them)
    VOICE.forEach((v, i) => {
      const lineEl = document.createElement('div');
      lineEl.className = 'voice-line';
      lineEl.innerHTML = `<span class="voice-line__lbl">${v.lbl}</span><span class="voice-line__txt" data-i="${i}"></span>`;
      colR.appendChild(lineEl);
    });
    // caret on the first one
    const firstTxt = colR.querySelector('[data-i="0"]');
    firstTxt.innerHTML = '<span class="voice-typing-caret"></span>';

    // Step 1 - type GENERIC into left column (slow, intentionally bland)
    card.classList.remove('is-step-0');
    card.classList.add('is-step-1');
    if (!skipPreface){
      for (const g of GENERIC){
        const lineEl = document.createElement('div');
        lineEl.className = 'voice-line';
        lineEl.innerHTML = `<span class="voice-line__lbl">${g.lbl}</span><span class="voice-line__txt"></span>`;
        colL.appendChild(lineEl);
        await typeInto(lineEl.querySelector('.voice-line__txt'), g.text, T.charSlow * 0.5);
      }
    } else {
      // skip preface → show all instantly
      colL.innerHTML = GENERIC.map(g =>
        `<div class="voice-line"><span class="voice-line__lbl">${g.lbl}</span><span class="voice-line__txt">${g.text}</span></div>`
      ).join('');
    }

    await sleep(350);

    // Step 2 - fade left to .55, type RIGHT character by character with src highlights
    card.classList.remove('is-step-1');
    card.classList.add('is-step-2');
    await sleep(180);

    for (let i = 0; i < VOICE.length; i++){
      const v = VOICE[i];
      const txtEl = colR.querySelector(`[data-i="${i}"]`);
      txtEl.innerHTML = '<span class="voice-typing-caret"></span>';
      // type each token
      for (const tok of v.tokens){
        if (tok.src){
          // create a span with highlight, type into it
          const sp = document.createElement('span');
          sp.className = 'src';
          sp.dataset.srcKind = tok.src.kind;
          sp.dataset.srcQ = tok.src.q;
          sp.dataset.srcLabel = tok.src.label;
          // insert before caret
          const caret = txtEl.querySelector('.voice-typing-caret');
          txtEl.insertBefore(sp, caret);
          await typeIntoNode(sp, tok.t, T.char);
        } else {
          // plain text - append before caret
          const caret = txtEl.querySelector('.voice-typing-caret');
          await typeBeforeCaret(txtEl, caret, tok.t, T.char);
        }
      }
      // remove caret from this line, add to next
      const caret = txtEl.querySelector('.voice-typing-caret');
      if (caret) caret.remove();
      const next = colR.querySelector(`[data-i="${i+1}"]`);
      if (next){ next.innerHTML = '<span class="voice-typing-caret"></span>'; }
      await sleep(220);
    }

    card.classList.add('typing-done');
    if (isPlaying) await choreographySceneTwo();
  }

  // typewriter into element (text only)
  function typeInto(el, text, speed = T.char){
    return new Promise(resolve => {
      let i = 0;
      el.innerHTML = '';
      const caret = document.createElement('span');
      caret.className = 'voice-typing-caret';
      el.appendChild(caret);
      const tick_ = () => {
        if (i >= text.length){
          caret.remove();
          return resolve();
        }
        const tn = document.createTextNode(text[i++]);
        el.insertBefore(tn, caret);
        tick();
        setTimeout(tick_, ms(speed));
      };
      tick_();
    });
  }
  function typeIntoNode(node, text, speed = T.char){
    return new Promise(resolve => {
      let i = 0;
      const tick_ = () => {
        if (i >= text.length) return resolve();
        node.appendChild(document.createTextNode(text[i++]));
        tick();
        setTimeout(tick_, ms(speed));
      };
      tick_();
    });
  }
  function typeBeforeCaret(parent, caret, text, speed = T.char){
    return new Promise(resolve => {
      let i = 0;
      const tick_ = () => {
        if (i >= text.length) return resolve();
        parent.insertBefore(document.createTextNode(text[i++]), caret);
        tick();
        setTimeout(tick_, ms(speed));
      };
      tick_();
    });
  }

  /* ---------- voice tooltip ---------- */
  const tip = $('#src-tip');
  document.addEventListener('mouseover', (e) => {
    const src = e.target.closest('.src');
    if (!src || !src.dataset.srcQ) return;
    tip.innerHTML = `<span class="src-tip__lbl">${src.dataset.srcLabel}</span>"${src.dataset.srcQ}"`;
    const r = src.getBoundingClientRect();
    tip.style.left = (r.left + r.width/2 - 120) + 'px';
    tip.style.top  = (r.top - 8 - tip.offsetHeight) + 'px';
    requestAnimationFrame(() => {
      const h = tip.offsetHeight;
      tip.style.top = (r.top - 12 - h) + 'px';
      tip.classList.add('is-shown');
    });
  });
  document.addEventListener('mouseout', (e) => {
    if (e.target.closest('.src')) tip.classList.remove('is-shown');
  });

  /* ============================================================
     SCENE 3 - Live email simulation + receipts inspector
     ============================================================ */

  // Email body - phrases in [[ ]] are sourced from voice/data/playbook
  const EMAIL_TOKENS = [
    { t:'Hi Maria,\n\n', src:null },
    { t:'Thank you for the kind words about Saturday\'s show. ', src:'voice' },
    { t:'I\'m so glad your daughter loved the cuadro from Veracruz.\n\n', src:null },
    { t:'I noticed you booked the spring class on April 4', src:'data' },
    { t:' ' + String.fromCharCode(0x2014) + ' that puts you in the next intake (3 spots left). ', src:'data' },
    { t:'I\'ve flagged your account so Ana picks up your call first.\n\n', src:'playbook' },
    { t:'See you Saturday.\n\n', src:'voice' },
    { t:'' + String.fromCharCode(0x2014) + ' Itzel & the Folklorico crew', src:'voice' }
  ];

  const RECEIPTS = {
    voice: {
      icon:'<svg class="receipt__icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 1v22M5 8v8M19 8v8M2 11v2M22 11v2M8.5 5v14M15.5 5v14"/></svg>',
      label:'Voice card',
      count:'3 phrases',
      render: () => `
        <div class="insp-voice">
          <div class="insp-quote">"I'm Itzel, and this is the Folklorico crew I've built over 14 years. <span style="background:rgba(233,123,46,.22);padding:0 2px;border-radius:2px">Every detail of every dance matters</span>. The costumes, the steps, the music."</div>
          <div class="insp-meta">
            <span><strong>Source:</strong> About page · paragraph 1</span>
            <span><strong>Confidence:</strong> 0.94</span>
            <span><strong>Used in:</strong> 12 drafts</span>
          </div>
          <div class="insp-quote" style="border-left-color:var(--teal)">"<span style="background:rgba(46,143,168,.24);padding:0 2px;border-radius:2px">See you Saturday</span>" " + String.fromCharCode(0x2014) + " recurring closer in 17 of 487 reviews from Itzel herself.</div>
          <div class="insp-meta">
            <span><strong>Source:</strong> Reviews · phrase cluster #2</span>
            <span><strong>Frequency:</strong> 17 / 487</span>
          </div>
        </div>
      `
    },
    data: {
      icon:'<svg class="receipt__icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><ellipse cx="12" cy="5" rx="9" ry="3"/><path d="M3 5v14a9 3 0 0 0 18 0V5"/><path d="M3 12a9 3 0 0 0 18 0"/></svg>',
      label:'CRM row',
      count:'GHL · contact #4127',
      render: () => `
        <div class="insp-crm">
          <div class="insp-crm__row"><span>contact_id</span><span>4127</span></div>
          <div class="insp-crm__row"><span>name</span><span>Maria Sanchez</span></div>
          <div class="insp-crm__row"><span>email</span><span>m.sanchez@gmail.com</span></div>
          <div class="insp-crm__row"><span>phone</span><span>+1 (510) 555-0142</span></div>
          <div class="insp-crm__row"><span>tags</span><span>parent, returning, vip</span></div>
          <div class="insp-crm__row"><span>last_booking</span><span><span class="insp-crm__highlight">2026-04-04</span> · spring_intake</span></div>
          <div class="insp-crm__row"><span>capacity_left</span><span><span class="insp-crm__highlight">3 spots</span></span></div>
          <div class="insp-crm__row"><span>assigned_to</span><span>ana@garciafolklorico.com</span></div>
          <div class="insp-crm__row"><span>last_touch</span><span>2026-04-22 · review_thank_you</span></div>
        </div>
      `
    },
    playbook: {
      icon:'<svg class="receipt__icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 4h16v16H4z"/><path d="M9 8h6M9 12h6M9 16h4"/></svg>',
      label:'Playbook',
      count:'5★ → reply',
      render: () => `
        <div class="insp-play">
          <div class="insp-step is-active">
            <span class="insp-step__n">1</span>
            <span class="insp-step__label">Acknowledge the specific show, by date</span>
            <span class="insp-step__tag">voice.signature</span>
          </div>
          <div class="insp-step is-active">
            <span class="insp-step__n">2</span>
            <span class="insp-step__label">Reference the dance / class they mentioned</span>
            <span class="insp-step__tag">extract.entity</span>
          </div>
          <div class="insp-step is-active">
            <span class="insp-step__n">3</span>
            <span class="insp-step__label">If returning customer, surface next-class capacity</span>
            <span class="insp-step__tag">crm.spots_left</span>
          </div>
          <div class="insp-step is-active">
            <span class="insp-step__n">4</span>
            <span class="insp-step__label">Route to assigned rep, name them</span>
            <span class="insp-step__tag">crm.assigned_to</span>
          </div>
          <div class="insp-step">
            <span class="insp-step__n">5</span>
            <span class="insp-step__label">Close with Itzel's signature phrase</span>
            <span class="insp-step__tag">voice.closer</span>
          </div>
          <div class="insp-step">
            <span class="insp-step__n">6</span>
            <span class="insp-step__label">Queue for Itzel's approval if &lt; 0.85 confidence</span>
            <span class="insp-step__tag">guard.threshold</span>
          </div>
        </div>
      `
    }
  };

  async function sceneThree(){
    clearChat();
    setEyebrow('Activation · 4 of 7 connected', 'Drafting your reply to Maria Sanchez, live.');
    setTitle('A real reply, drafted live.<br>Every claim has a receipt.');
    renderRings([
      { id:'gbp', stage:3, cred:'Connected' },
      { id:'seo', stage:3, cred:'3 sites tracked' },
      { id:'reviews', stage:3, cred:'Voice accepted' },
      { id:'email', stage:2, cred:'Drafting reply…' },
      { id:'chat', stage:3, cred:'Snippet pasted' },
      { id:'blog', stage:1, cred:'Awaiting topic' },
      { id:'social', stage:0, cred:'Optional' }
    ]);
    await sleep(220);

    const body = agentMsg();
    body.style.width = '100%';
    body.innerHTML = `
      <p>Maria Sanchez left a 5★ review 90 seconds ago. Drafting a reply right now. Watch the receipts below light up as I use them.</p>
      <div class="sim-email" id="sim-email">
        <div class="sim-email__head">
          <span class="pill-live">Live</span>
          <span style="margin-left:auto;font-variant-numeric:tabular-nums">Drafting · 0.0s</span>
        </div>
        <div class="sim-email__meta">
          <div class="sim-email__meta-row"><span>To</span><span>Maria Sanchez · m.sanchez@gmail.com</span></div>
          <div class="sim-email__meta-row"><span>From</span><span>Itzel García · hello@garciafolklorico.com</span></div>
          <div class="sim-email__meta-row"><span>Re</span><span>5★ review · "Best Saturday show this season"</span></div>
        </div>
        <div class="sim-email__subj">Re: thank you, and your spring class spot</div>
        <div class="sim-email__body" id="sim-body"></div>
        <div class="receipts" id="receipts">
          <span class="receipts__lbl">Receipts</span>
          <button class="receipt" data-receipt="voice">
            ${RECEIPTS.voice.icon}<span>${RECEIPTS.voice.label}</span><span class="receipt__count">${RECEIPTS.voice.count}</span>
          </button>
          <button class="receipt" data-receipt="data">
            ${RECEIPTS.data.icon}<span>${RECEIPTS.data.label}</span><span class="receipt__count">${RECEIPTS.data.count}</span>
          </button>
          <button class="receipt" data-receipt="playbook">
            ${RECEIPTS.playbook.icon}<span>${RECEIPTS.playbook.label}</span><span class="receipt__count">${RECEIPTS.playbook.count}</span>
          </button>
        </div>
        <div class="inspector" id="inspector">
          <div class="inspector__inner">
            <div class="inspector__head">
              <span class="inspector__title" id="insp-title">Source</span>
              <button class="inspector__close" id="insp-close" aria-label="Close">×</button>
            </div>
            <div class="inspector__body" id="insp-body"></div>
          </div>
        </div>
      </div>
    `;
    scrollChat();

    const bodyEl = $('#sim-body');
    const caret = document.createElement('span');
    caret.className = 'sim-email__caret';
    bodyEl.appendChild(caret);

    const t0 = performance.now();
    const elapsedEl = body.querySelector('.sim-email__head span:last-child');

    // Type the email body, marking sources inline
    for (const seg of EMAIL_TOKENS){
      let target;
      if (seg.src){
        target = document.createElement('span');
        target.className = 'src';
        target.dataset.src = seg.src;
        bodyEl.insertBefore(target, caret);
      } else {
        target = bodyEl;
      }
      for (let i = 0; i < seg.t.length; i++){
        if (target === bodyEl){
          bodyEl.insertBefore(document.createTextNode(seg.t[i]), caret);
        } else {
          target.appendChild(document.createTextNode(seg.t[i]));
        }
        tick();
        const t = ((performance.now() - t0) / 1000).toFixed(1);
        elapsedEl.textContent = `Drafting · ${t}s`;
        await sleep(T.char);
      }
    }
    $('#sim-email').classList.add('is-done');
    elapsedEl.textContent = `Drafted in ${((performance.now() - t0)/1000).toFixed(1)}s · awaiting your approval`;

    // reveal receipts
    $('#receipts').classList.add('is-shown');
    if (isPlaying) await choreographySceneThree();
  }

  // receipt clicks
  document.addEventListener('click', (e) => {
    const r = e.target.closest('.receipt');
    if (r){
      const kind = r.dataset.receipt;
      const all = $$('#receipts .receipt');
      const wasActive = r.classList.contains('is-active');
      all.forEach(x => x.classList.remove('is-active'));
      const insp = $('#inspector');
      if (wasActive){
        insp.classList.remove('is-open');
        return;
      }
      r.classList.add('is-active');
      $('#insp-title').textContent = `Source · ${RECEIPTS[kind].label}`;
      $('#insp-body').innerHTML = RECEIPTS[kind].render();
      insp.classList.add('is-open');
      return;
    }
    if (e.target.id === 'insp-close'){
      $('#inspector').classList.remove('is-open');
      $$('#receipts .receipt').forEach(x => x.classList.remove('is-active'));
    }
  });

  /* ============================================================
     SCENE 4 - All rings closed (completion choreography)
     ============================================================ */

  async function sceneFour(){
    clearChat();
    setEyebrow('Activation · 6 of 7 connected', 'One more to go.');
    setTitle("Last role: Social.<br>Watch what happens.");

    // Start: 6 connected, 1 running
    renderRings([
      { id:'gbp', stage:3, cred:'Connected' },
      { id:'seo', stage:3, cred:'3 sites tracked' },
      { id:'reviews', stage:3, cred:'Voice accepted' },
      { id:'email', stage:3, cred:'GHL connected' },
      { id:'chat', stage:3, cred:'Snippet pasted' },
      { id:'blog', stage:3, cred:'WordPress published' },
      { id:'social', stage:2, cred:'Posting first job…' }
    ]);

    await sleep(280);
    userMsg('Connect Social too. Last one.');
    await sleep(420);

    const body = agentMsg();
    body.innerHTML = `
      <p>Posting your first carousel to Instagram now…</p>
      <div class="think is-done">
        <div class="think__header">Agent · 3 tools, 1.4s</div>
        <ul class="think__list">
          <li class="think__row is-shown is-done">
            <span class="think__icon"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><path d="M20 6 9 17l-5-5"/></svg></span>
            <span class="think__tool">meta.post()</span>
            <span class="think__text">Carousel published <span style="color:var(--ink-faint);font-size:11px;margin-left:4px">· 4 slides · "Spring intake open"</span></span>
            <span class="think__elapsed">0.8s</span>
          </li>
          <li class="think__row is-shown is-done">
            <span class="think__icon"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><path d="M20 6 9 17l-5-5"/></svg></span>
            <span class="think__tool">verify_run()</span>
            <span class="think__text">First-run heartbeat received <span style="color:var(--ink-faint);font-size:11px;margin-left:4px">· post_id 8821</span></span>
            <span class="think__elapsed">0.4s</span>
          </li>
        </ul>
      </div>
    `;
    scrollChat();

    await sleep(900);

    // The money shot - close the 7th ring with the choreography
    const social = $$('#ring-grid .ap-ring').find(el => el.dataset.ringId === 'social');

    // 1. Update the social ring to connected, marking it just-closed for the pop
    const newState = RING_STATE.map(r =>
      r.id === 'social' ? { ...r, stage: 3, cred: 'Posted · 4-slide carousel' } : r
    );
    renderRings(newState);
    chime();
    const socialAfter = $$('#ring-grid .ap-ring').find(el => el.dataset.ringId === 'social');
    socialAfter.classList.add('ap-ring--just-closed');

    await sleep(500);

    // 2. Trigger the cluster celebration - inflate 8% (1.08 scale) + gold halo
    $('#grid-wrap').classList.add('is-celebrating');
    $('#rings-pane').classList.add('is-celebrating');
    chordFinale();

    // 3. Confetti
    spawnConfetti();

    // 4. Update the chat
    setEyebrow('Activation · 7 of 7 connected · Activated', 'All seven roles activated.');
    setTitle('Activated.<br>Your dashboard is live.');

    await sleep(1200);

    // hold the celebration; let user replay or move on
  }

  function spawnConfetti(){
    const layer = $('#confetti-layer');
    layer.innerHTML = '';
    const colors = ['#D4A437', '#F4C53D', '#E97B2E', '#FBBC05'];
    const w = layer.offsetWidth;
    for (let i = 0; i < 28; i++){
      const c = document.createElement('span');
      c.className = 'confetti';
      c.style.left = (10 + Math.random() * 80) + '%';
      c.style.top  = (28 + Math.random() * 30) + '%';
      c.style.background = colors[i % colors.length];
      c.style.animationDelay = (Math.random() * 0.4) + 's';
      c.style.transform = `rotate(${Math.random()*360}deg)`;
      c.style.width = (3 + Math.random()*5) + 'px';
      c.style.height = (3 + Math.random()*5) + 'px';
      layer.appendChild(c);
    }
  }

  /* ============================================================
     DASHBOARD HANDOFF
     ============================================================ */
  function renderHandoffGrid(){
    const grid = $('#dash-roles');
    if (!grid || grid.dataset.rendered) return;
    const stats = {
      gbp:'42 reviews · 4.9★', seo:'+31% clicks', reviews:'12 replies sent',
      email:'3 drafts ready', chat:'8 chats today', blog:'2 posts queued', social:'1 carousel live'
    };
    const foots = {
      gbp:'GBP · last sync 4 min ago', seo:'Search Console · today',
      reviews:'Yelp + Google · all responded', email:'awaiting your approval',
      chat:'avg response 12s', blog:'WordPress · queued', social:'Instagram · live'
    };
    grid.innerHTML = window.RINGS.map(r => `
      <div class="dash__role">
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
  function showHandoff(){
    renderHandoffGrid();
    whoosh();
    $('#handoff').classList.add('is-shown');
  }
  function hideHandoff(){
    $('#handoff').classList.remove('is-shown');
  }
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') hideHandoff();
  });

  /* ============================================================
     AUTOPLAY STORY MODE
     ============================================================ */
  let isPlaying = false;
  let playToken = 0;
  const SCENE_TITLES = ['Tool calls','Voice card','Live email','All rings closed'];

  function setAutoplayCaption(n){
    $('#autoplay-caption').textContent = `Scene ${n} of 4 · ${SCENE_TITLES[n-1]}`;
  }
  function setAutoplayProgress(pct){
    $('#autoplay-fill').style.width = pct + '%';
  }

  async function playAll(){
    const myToken = ++playToken;
    isPlaying = true;
    $('#play-btn').classList.add('is-playing');
    $('#play-btn').lastChild && ($('#play-btn').lastChild.textContent = ' Stop');
    $('#autoplay-bar').classList.add('is-on');
    setAutoplayProgress(0);
    hideHandoff();

    const totalDur = 4; // weights per scene
    const weights = [1, 1.1, 1.2, 1];
    const sum = weights.reduce((a,b)=>a+b,0);
    let elapsedW = 0;

    for (let i = 1; i <= 4; i++){
      if (myToken !== playToken) return;
      setAutoplayCaption(i);
      const start = elapsedW / sum * 100;
      const end = (elapsedW + weights[i-1]) / sum * 100;
      // animate progress while scene runs
      animateProgress(start, end, weights[i-1] * 8000);
      await runScene(i, { fromAutoplay:true });
      if (myToken !== playToken) return;
      elapsedW += weights[i-1];
      // brief beat between scenes (except after last)
      if (i < 4) await sleep(1100);
    }
    if (myToken !== playToken) return;
    setAutoplayProgress(100);

    // After scene 4 completes, show the handoff
    await sleep(900);
    if (myToken !== playToken) return;
    showHandoff();

    await sleep(2400);
    stopPlay();
  }

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

  function stopPlay(){
    playToken++;
    isPlaying = false;
    cancelAnimationFrame(progressRAF);
    $('#play-btn').classList.remove('is-playing');
    $('#autoplay-bar').classList.remove('is-on');
    setAutoplayProgress(0);
    hideCursor();
  }

  /* ============================================================
     CURSOR CHOREOGRAPHY (per-scene scripts during autoplay)
     ============================================================ */
  // Hooked into specific scenes - call from sceneTwo / sceneThree once their
  // primary content finishes, only when autoplay is active.
  async function choreographySceneTwo(){
    if (!isPlaying) return;
    showCursor();
    // Hover a highlighted phrase to reveal tooltip
    await sleep(400);
    const phrase = $('#vlines-r .src');
    if (phrase) await hoverCursorOver(phrase, {dwell:1100});
    await sleep(200);
    // Click "Accept this voice"
    const accept = $('#vcard .ap-btn--primary');
    if (accept){
      await moveCursorTo(accept, {dwell:300});
      await clickCursor(accept);
    }
    hideCursor();
  }
  async function choreographySceneThree(){
    if (!isPlaying) return;
    showCursor();
    await sleep(300);
    // Click each receipt in turn
    const receipts = $$('#receipts .receipt');
    for (let i = 0; i < receipts.length; i++){
      await moveCursorTo(receipts[i], {dwell:280});
      await clickCursor(receipts[i]);
      await sleep(900);
    }
    // close inspector
    const close = $('#insp-close');
    if (close){
      await moveCursorTo(close, {dwell:240});
      await clickCursor(close);
    }
    hideCursor();
  }

  /* ============================================================
     SCENE 5 - Full client dashboard
     ============================================================ */
  async function sceneFive(){
    renderHandoffGrid();
    $('#handoff').classList.add('is-shown');
    whoosh();
  }

  const SCENES = { 1: sceneOne, 2: sceneTwo, 3: sceneThree, 4: sceneFour, 5: sceneFive };
  let currentScene = 1;
  let runToken = 0;

  async function runScene(n, opts = {}){
    currentScene = n;
    $$('.scene-btn').forEach(b => b.classList.toggle('is-active', +b.dataset.scene === n));

    // Reset celebration / inspector between scenes
    $('#grid-wrap').classList.remove('is-celebrating');
    $('#rings-pane').classList.remove('is-celebrating');
    $('#confetti-layer').innerHTML = '';
    const insp = $('#inspector');
    if (insp) insp.classList.remove('is-open');
    if (n !== 5) hideHandoff();

    const myToken = ++runToken;
    try { await SCENES[n](); } catch (err){ console.error(err); }
  }

  $$('.scene-btn').forEach(b => {
    b.addEventListener('click', () => {
      if (isPlaying) stopPlay();
      runScene(+b.dataset.scene);
    });
  });
  $('#play-btn').addEventListener('click', () => {
    if (isPlaying) { stopPlay(); return; }
    ensureAudio(); // unlock audio on user gesture
    playAll();
  });
  $('#replay-btn').addEventListener('click', () => {
    if (isPlaying) stopPlay();
    runScene(currentScene);
  });

  // sound toggle
  $('#sound-btn').addEventListener('click', (e) => {
    SOUND_ON = !SOUND_ON;
    e.currentTarget.classList.toggle('is-on', SOUND_ON);
    e.currentTarget.setAttribute('aria-pressed', SOUND_ON);
    if (SOUND_ON){ ensureAudio(); chime(); }
  });

  // slow-mo toggle
  $('#reduced-btn').addEventListener('click', (e) => {
    const btn = e.currentTarget;
    btn.classList.toggle('is-on');
    SPEED = btn.classList.contains('is-on') ? 0.4 : 1;
  });

  // keyboard nav
  document.addEventListener('keydown', (e) => {
    if (e.target.tagName === 'INPUT') return;
    if (e.key === 'ArrowRight' || e.key === ' '){
      e.preventDefault();
      const next = Math.min(5, currentScene + 1);
      if (next !== currentScene) runScene(next);
    } else if (e.key === 'ArrowLeft'){
      e.preventDefault();
      const prev = Math.max(1, currentScene - 1);
      if (prev !== currentScene) runScene(prev);
    } else if (e.key === 'r'){
      runScene(currentScene);
    } else if (['1','2','3','4','5'].includes(e.key)){
      runScene(+e.key);
    }
  });

  /* ============================================================
     TWEAKS PANEL - host edit-mode protocol + UI wiring
     ============================================================ */
  const TWEAK_DEFAULTS = /*EDITMODE-BEGIN*/{
    "soundProfile": "typewriter",
    "soundVolume": 80,
    "typeSpeedMs": 30
  }/*EDITMODE-END*/;

  function applyTweaks(t){
    SOUND_PROFILE = t.soundProfile;
    SOUND_VOL = t.soundVolume / 100;
    T.char = t.typeSpeedMs;
    // reflect in panel UI
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
    try {
      window.parent.postMessage({type:'__edit_mode_set_keys', edits:{[key]: val}}, '*');
    } catch(e){}
  }

  // host protocol - listener FIRST, then announce
  window.addEventListener('message', (e) => {
    const d = e.data || {};
    if (d.type === '__activate_edit_mode'){
      $('#tweaks').classList.add('is-open');
    } else if (d.type === '__deactivate_edit_mode'){
      $('#tweaks').classList.remove('is-open');
    }
  });
  try { window.parent.postMessage({type:'__edit_mode_available'}, '*'); } catch(e){}

  // close button
  $('#tw-close')?.addEventListener('click', () => {
    $('#tweaks').classList.remove('is-open');
    try { window.parent.postMessage({type:'__edit_mode_dismissed'}, '*'); } catch(e){}
  });

  // segmented profile
  $$('#tw-profile .tweaks__seg-btn').forEach(b => {
    b.addEventListener('click', () => {
      setTweak('soundProfile', b.dataset.v);
      // play preview if sound on
      ensureAudio();
      if (!SOUND_ON) { SOUND_ON = true; }
      keyStrike(); setTimeout(keyStrike, 90); setTimeout(keyStrike, 180);
    });
  });
  $('#tw-vol')?.addEventListener('input', (e) => {
    setTweak('soundVolume', +e.target.value);
  });
  $('#tw-vol')?.addEventListener('change', () => {
    ensureAudio(); SOUND_ON = true; keyStrike();
  });
  $('#tw-speed')?.addEventListener('input', (e) => {
    setTweak('typeSpeedMs', +e.target.value);
  });
  $('#tw-test')?.addEventListener('click', () => {
    ensureAudio(); SOUND_ON = true;
    // play a 6-char demo
    const word = 'Hello.';
    let i = 0;
    const playNext = () => {
      if (i >= word.length) { setTimeout(returnDing, 120); return; }
      i++; keyStrike();
      setTimeout(playNext, T.char + 5);
    };
    playNext();
  });

  // ring tick during scene 1 tool calls
  // (added subtly; lives in sceneOne)

  // boot
  renderRings(initialRingState());
  runScene(1);
})();
