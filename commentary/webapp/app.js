/**
 * Ray-Ban Display web app: display + input for the guide, plus live commentary.
 *
 * The glasses web app has no camera (no getUserMedia), so the phone app is the
 * camera and Modal holds all state. This app polls GET /display (~1 Hz), which
 * says which screen to show, and POSTs button presses back. Every state change
 * is a full redraw of #app (black = transparent on the additive display).
 *
 * Screens: home, recording, step, checking, result, showme, done, commentary.
 */
(function() {
  'use strict';

  var CONFIG = window.APP_CONFIG;
  var API = CONFIG.guideApi;

  var state = {
    display: null,      // last GET /display payload
    local: null,        // client-only screen override: 'checking' | 'showme' | 'commentary'
    lastKey: '',        // signature of what's drawn, to skip identical redraws
    offline: false,
    advanceTimer: null,
    advancedFor: '',    // attempt timestamp we already auto-advanced for
    showmeTimer: null,
    commentary: { text: '', t: 0 },
    commentaryTimer: null,
  };

  // ------------------------------------------------------------------ utils

  function el(html) {
    var t = document.createElement('template');
    t.innerHTML = html.trim();
    return t.content;
  }

  function esc(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  function media(path) {
    return path ? API + '/media/' + path : '';
  }

  function mmss(seconds) {
    seconds = Math.max(0, Math.round(seconds));
    var m = Math.floor(seconds / 60), s = seconds % 60;
    return m + ':' + (s < 10 ? '0' : '') + s;
  }

  function api(method, path, body) {
    return fetch(API + path, {
      method: method,
      cache: 'no-store',
      headers: body ? { 'Content-Type': 'application/json' } : {},
      body: body ? JSON.stringify(body) : undefined,
    }).then(function(res) {
      if (!res.ok) throw new Error('HTTP ' + res.status);
      return res.json();
    });
  }

  function button(action, label, extra) {
    return '<button class="btn focusable" data-action="' + action + '"' + (extra || '') + '>' + label + '</button>';
  }

  // ------------------------------------------------------------------ screens

  function screenHome(d) {
    var procs = (d.procedures || []).slice(0, 3);
    var list = procs.length
      ? procs.map(function(p) {
          return '<button class="list-item focusable" data-action="start" data-id="' + esc(p.id) + '">' +
            '<span class="list-title">' + esc(p.title) + '</span>' +
            '<span class="list-meta">' + p.steps + ' steps</span></button>';
        }).join('')
      : '<p class="big muted">No procedures yet</p>';
    return '<div class="screen">' +
      '<header class="header"><h1>Guide</h1><span class="header-meta">' + (state.offline ? 'Offline' : 'Pick a task') + '</span></header>' +
      '<div class="list">' + list + '</div>' +
      '<nav class="nav-bar">' + button('commentary', 'Live view') + '</nav>' +
      '</div>';
  }

  function screenRecording(d) {
    var cap = d.capture || {};
    var elapsed = (d.server_t || Date.now() / 1000) - (cap.started_at || 0);
    return '<div class="screen center">' +
      '<div class="rec"><span class="rec-dot"></span><span class="rec-label">REC</span></div>' +
      '<div class="rec-time" id="rec-time" data-start="' + (cap.started_at || 0) + '" data-skew="' + ((d.server_t || 0) - Date.now() / 1000) + '">' + mmss(elapsed) + '</div>' +
      '<p class="big muted">' + esc(cap.title || 'Recording') + '</p>' +
      '<p class="hint">Talk through each step as you go</p>' +
      '</div>';
  }

  function stepHeader(run) {
    return '<header class="step-head"><span class="step-count">' + (run.current_step + 1) + ' of ' + run.total_steps + '</span>' +
      '<span class="step-title">' + esc(run.step.title) + '</span></header>';
  }

  function screenStep(run) {
    var s = run.step;
    return '<div class="screen">' + stepHeader(run) +
      '<p class="instruction">' + esc(s.instruction) + '</p>' +
      (s.reference_image ? '<div class="ref"><img src="' + media(s.reference_image) + '" alt=""></div>' : '<div class="ref"></div>') +
      '<nav class="nav-bar three">' +
        button('check', '&#10003; Check') +
        button('showme', '&#9654; Show me', (s.clip || (s.keyframes || []).length) ? '' : ' disabled') +
        // On step 1 there's nothing to go back to: leave the run instead.
        (run.current_step === 0 ? button('home', '&#10005; Exit') : button('back', '&#8592; Back')) +
      '</nav></div>';
  }

  function screenChecking(run) {
    return '<div class="screen center">' +
      '<div class="loading-spinner big-spinner"></div>' +
      '<p class="big">Checking&hellip;</p>' +
      '<p class="hint">Keep the work in view</p>' +
      '</div>';
  }

  function screenResult(run) {
    var a = run.last_attempt || { verdict: 'error', reason: '' };
    if (a.verdict === 'pass') {
      return '<div class="screen center pass">' +
        '<div class="verdict-icon pass-icon">&#10003;</div>' +
        '<p class="big">' + esc(a.reason || 'Looks right') + '</p>' +
        '</div>';
    }
    var title = { fail: 'Not yet', unsure: 'Look closer', hold_still: 'Hold still', error: 'Try again' }[a.verdict] || 'Try again';
    var tip = (run.step.tips || [])[0];
    return '<div class="screen">' +
      '<header class="warn-head"><span class="verdict-icon warn-icon">&#9888;</span><span class="warn-title">' + title + '</span></header>' +
      '<p class="reason">' + esc(a.reason) + '</p>' +
      '<div class="result-body">' +
        (run.step.reference_image ? '<div class="ref small"><img src="' + media(run.step.reference_image) + '" alt=""></div>' : '') +
        (tip ? '<p class="tip">&ldquo;' + esc(tip) + '&rdquo;</p>' : '') +
      '</div>' +
      '<nav class="nav-bar three">' +
        button('check', '&#8635; Re-check') +
        button('override', 'Override') +
        button('back', '&#8592; Back') +
      '</nav></div>';
  }

  function screenShowMe(run) {
    var s = run.step;
    var body = s.clip
      ? '<video id="clip" src="' + media(s.clip) + '" autoplay muted loop playsinline></video>'
      : '';
    var frames = (s.keyframes || []).map(function(k, i) {
      return '<img class="kf' + (i === 0 && !s.clip ? ' on' : '') + '" src="' + media(k) + '" alt="">';
    }).join('');
    return '<div class="screen">' + stepHeader(run) +
      '<div class="showme">' + body + '<div class="kfs' + (s.clip ? ' hidden' : '') + '">' + frames + '</div></div>' +
      '<nav class="nav-bar">' + button('closeshowme', '&#8592; Back') + '</nav></div>';
  }

  function screenDone(run) {
    var total = (run.ended_at || Date.now() / 1000) - run.started_at;
    return '<div class="screen center">' +
      '<div class="verdict-icon pass-icon">&#10003;</div>' +
      '<p class="done-title">' + esc(run.procedure_title || 'Done') + '</p>' +
      '<div class="stats">' +
        '<div><span class="stat">' + mmss(total) + '</span><span class="stat-label">time</span></div>' +
        '<div><span class="stat">' + run.total_steps + '</span><span class="stat-label">steps</span></div>' +
        '<div><span class="stat">' + run.issues_caught + '</span><span class="stat-label">issues caught</span></div>' +
      '</div>' +
      '<p class="hint">Record: ' + esc(API.replace(/^https:\/\//, '') + '/runs/' + run.id + '/record') + '</p>' +
      '<nav class="nav-bar">' + button('home', 'Home') + '</nav></div>';
  }

  function screenCommentary() {
    var c = state.commentary;
    var ago = c.t ? Math.round(Date.now() / 1000 - c.t) : null;
    return '<div class="screen">' +
      '<header class="header"><h1>Live view</h1><span class="header-meta">' + (ago == null ? '' : ago < 2 ? 'just now' : ago + 's ago') + '</span></header>' +
      '<div class="content"><p class="commentary-text">' + esc(c.text || 'Waiting for the first frame…') + '</p></div>' +
      '<nav class="nav-bar">' + button('closecommentary', '&#8592; Back') + '</nav></div>';
  }

  // ------------------------------------------------------------------ render

  function currentView() {
    var d = state.display || { mode: 'home', procedures: [] };
    if (state.local === 'commentary') return { name: 'commentary', html: screenCommentary(), key: 'c|' + state.commentary.text + '|' + Math.floor(Date.now() / 5000) };
    if (d.mode === 'recording') return { name: 'recording', html: screenRecording(d), key: 'rec|' + d.capture.id };
    if (d.mode === 'run' && d.run) {
      var run = d.run;
      if (run.screen === 'done') return { name: 'done', html: screenDone(run), key: 'done|' + run.id };
      if (state.local === 'checking') return { name: 'checking', html: screenChecking(run), key: 'checking|' + run.id };
      if (state.local === 'showme') return { name: 'showme', html: screenShowMe(run), key: 'showme|' + run.id + '|' + run.current_step };
      var a = run.last_attempt;
      if (run.screen === 'result') return { name: 'result', html: screenResult(run), key: 'result|' + run.id + '|' + run.current_step + '|' + (a ? a.at : '') };
      return { name: 'step', html: screenStep(run), key: 'step|' + run.id + '|' + run.current_step };
    }
    var procs = (d.procedures || []).map(function(p) { return p.id + p.title + p.steps; }).join(',');
    return { name: 'home', html: screenHome(d), key: 'home|' + procs + '|' + state.offline };
  }

  function render(force) {
    var v = currentView();
    if (!force && v.key === state.lastKey) return v;
    var focused = document.activeElement && document.activeElement.dataset ? document.activeElement.dataset.action : null;
    var app = document.getElementById('app');
    app.innerHTML = '';
    app.appendChild(el(v.html));
    app.firstElementChild.dataset.screen = v.name;
    state.lastKey = v.key;
    afterRender(v.name, focused);
    return v;
  }

  function afterRender(name, previouslyFocused) {
    var preferred = { result: 'check', step: 'check', showme: 'closeshowme', done: 'home' }[name];
    var target = (previouslyFocused && document.querySelector('[data-action="' + previouslyFocused + '"]:not([disabled])')) ||
      (preferred && document.querySelector('[data-action="' + preferred + '"]:not([disabled])')) ||
      document.querySelector('.focusable:not([disabled])');
    if (target) target.focus();

    clearInterval(state.showmeTimer);
    if (name === 'showme') startShowMe();

    clearTimeout(state.advanceTimer);
    var run = state.display && state.display.run;
    if (name === 'result' && run && run.last_attempt && run.last_attempt.verdict === 'pass' && state.advancedFor !== run.last_attempt.at) {
      var attemptAt = run.last_attempt.at;
      var fromStep = run.current_step;
      state.advanceTimer = setTimeout(function() {
        state.advancedFor = attemptAt;
        // from_step: a no-op if another screen (the panel page) already advanced.
        api('POST', '/runs/' + run.id + '/next?from_step=' + fromStep).then(function(r) { applyRun(r); render(); });
      }, CONFIG.passAdvanceMs);
    }
  }

  function startShowMe() {
    var video = document.getElementById('clip');
    var kfs = document.querySelector('.kfs');
    function cycleFrames() {
      if (!kfs) return;
      kfs.classList.remove('hidden');
      var imgs = kfs.querySelectorAll('img');
      if (!imgs.length) return;
      var i = 0;
      imgs[0].classList.add('on');
      state.showmeTimer = setInterval(function() {
        imgs[i].classList.remove('on');
        i = (i + 1) % imgs.length;
        imgs[i].classList.add('on');
      }, 900);
    }
    if (video) {
      // If video can't play on this device, fall back to cycling keyframes.
      video.addEventListener('error', function() { video.remove(); cycleFrames(); });
      var p = video.play && video.play();
      if (p && p.catch) p.catch(function() { video.remove(); cycleFrames(); });
    } else {
      cycleFrames();
    }
  }

  function tickClock() {
    var t = document.getElementById('rec-time');
    if (!t) return;
    var start = parseFloat(t.dataset.start), skew = parseFloat(t.dataset.skew);
    t.textContent = mmss(Date.now() / 1000 + skew - start);
  }

  // ------------------------------------------------------------------ actions

  function runId() {
    return state.display && state.display.run && state.display.run.id;
  }

  function applyRun(run) {
    if (!run) return;
    state.display = { mode: run.screen === 'done' && !runId() ? 'home' : 'run', run: run, server_t: Date.now() / 1000 };
  }

  function act(action, data) {
    var id = runId();
    switch (action) {
      case 'start':
        return api('POST', '/runs', { procedure_id: data.id }).then(applyRun).then(function() { render(); });
      case 'check':
        state.local = 'checking';
        render();
        return api('POST', '/runs/' + id + '/check').then(function(run) {
          state.local = null;
          applyRun(run);
          render();
        }).catch(function() {
          state.local = null;
          render(true);
        });
      case 'next':
      case 'back':
      case 'override':
        return api('POST', '/runs/' + id + '/' + action).then(function(run) { applyRun(run); render(); });
      case 'showme':
        state.local = 'showme';
        return render();
      case 'closeshowme':
        state.local = null;
        return render();
      case 'home':
        state.local = null;
        return api('POST', '/home').then(poll);
      case 'commentary':
        state.local = 'commentary';
        loadCommentary();
        clearInterval(state.commentaryTimer);
        state.commentaryTimer = setInterval(loadCommentary, CONFIG.commentaryPollMs);
        return render();
      case 'closecommentary':
        state.local = null;
        clearInterval(state.commentaryTimer);
        return render();
    }
  }

  function loadCommentary() {
    fetch(CONFIG.commentaryLatestUrl, { cache: 'no-store' })
      .then(function(res) { return res.json(); })
      .then(function(d) {
        state.commentary = { text: d.commentary || '', t: d.t || 0 };
        if (state.local === 'commentary') render();
      })
      .catch(function() {});
  }

  // ------------------------------------------------------------------ polling

  function poll() {
    if (state.local === 'checking') return Promise.resolve(); // the POST will answer
    return api('GET', '/display').then(function(d) {
      state.offline = false;
      state.display = d;
      if (d.mode !== 'run' && state.local !== 'commentary') state.local = null;
      render();
    }).catch(function() {
      state.offline = true;
      render();
    });
  }

  // ------------------------------------------------------------------ input

  function moveFocus(delta) {
    var items = Array.prototype.slice.call(document.querySelectorAll('.focusable:not([disabled])'));
    if (!items.length) return;
    var i = items.indexOf(document.activeElement);
    items[(i + delta + items.length) % items.length].focus();
  }

  function setupEvents() {
    document.addEventListener('click', function(e) {
      var a = e.target.closest('[data-action]');
      if (a && !a.disabled) act(a.dataset.action, a.dataset);
    });
    document.addEventListener('keydown', function(e) {
      if (e.key === 'Enter' && document.activeElement && document.activeElement.classList.contains('focusable')) {
        document.activeElement.click();
        e.preventDefault();
      } else if (e.key === 'ArrowRight' || e.key === 'ArrowDown') {
        moveFocus(1);
        e.preventDefault();
      } else if (e.key === 'ArrowLeft' || e.key === 'ArrowUp') {
        moveFocus(-1);
        e.preventDefault();
      }
    });
  }

  function init() {
    setupEvents();
    render(true);
    poll();
    setInterval(poll, CONFIG.displayPollMs);
    setInterval(tickClock, 1000);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
