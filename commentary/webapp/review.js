/**
 * Review page (phone or laptop): edit, reorder, merge or delete steps, answer
 * the follow-up questions, then publish so the procedure shows on the glasses.
 * Static vanilla JS; talks to the glasses-guide API from config.js.
 */
(function() {
  'use strict';

  var API = window.APP_CONFIG.guideApi;
  var proc = null;
  var dirty = false;

  function $(id) { return document.getElementById(id); }

  function api(method, path, body) {
    return fetch(API + path, {
      method: method,
      headers: body ? { 'Content-Type': 'application/json' } : {},
      body: body ? JSON.stringify(body) : undefined,
    }).then(function(res) {
      return res.json().then(function(data) {
        if (!res.ok) throw new Error(data.detail || ('HTTP ' + res.status));
        return data;
      });
    });
  }

  function status(text, isError) {
    var s = $('status');
    s.textContent = text;
    s.style.color = isError ? 'var(--bad)' : '';
  }

  function words(s) { return (s || '').trim().split(/\s+/).filter(Boolean).length; }
  function list(s) { return (s || '').split(',').map(function(x) { return x.trim(); }).filter(Boolean); }
  function lines(s) { return (s || '').split('\n').map(function(x) { return x.trim(); }).filter(Boolean); }
  function media(p) { return p ? API + '/media/' + p : ''; }
  function fmt(t) { return t == null ? '' : t.toFixed(1) + 's'; }

  // ------------------------------------------------------------------ load

  function loadPicker(selectId) {
    return api('GET', '/procedures').then(function(procs) {
      var picker = $('picker');
      picker.innerHTML = '<option value="">Choose a procedure…</option>' +
        procs.map(function(p) {
          return '<option value="' + p.id + '">' + p.title.replace(/</g, '&lt;') + ' (' + p.status + ', ' + p.steps + ' steps)</option>';
        }).join('');
      var want = selectId || new URLSearchParams(location.search).get('id');
      if (want) picker.value = want;
      if (picker.value) loadProcedure(picker.value);
      $('capture').classList.toggle('hidden', !!picker.value);
    });
  }

  function loadProcedure(id) {
    status('Loading…');
    return api('GET', '/procedures/' + id).then(function(p) {
      proc = p;
      dirty = false;
      var q = new URLSearchParams(location.search);
      q.set('id', id);
      history.replaceState(null, '', '?' + q.toString());
      render();
      status(p.status === 'published' ? 'Published' : 'Draft');
    }).catch(function(e) { status(e.message, true); });
  }

  // ------------------------------------------------------------------ render

  function render() {
    $('editor').classList.remove('hidden');
    $('capture').classList.add('hidden');
    $('proc-title').value = proc.title;
    $('proc-meta').textContent = proc.steps.length + ' steps' + (proc.source_capture ? ' · from capture ' + proc.source_capture : '');
    $('publish').classList.toggle('hidden', proc.status === 'published');
    $('unpublish').classList.toggle('hidden', proc.status !== 'published');

    var fq = proc.followups || [];
    $('followups').classList.toggle('hidden', !fq.length);
    $('followup-list').innerHTML = '';
    fq.forEach(function(q) {
      var div = document.createElement('div');
      div.className = 'followup';
      var step = proc.steps.findIndex(function(s) { return s.id === q.step_id; });
      div.innerHTML = '<p></p><textarea rows="2" placeholder="Your answer"></textarea>';
      div.querySelector('p').textContent = q.question + (step >= 0 ? ' (step ' + (step + 1) + ')' : '');
      var ta = div.querySelector('textarea');
      ta.value = q.answer || '';
      ta.dataset.fq = q.id;
      ta.addEventListener('input', function() { dirty = true; status('Unsaved changes'); });
      $('followup-list').appendChild(div);
    });

    var ol = $('steps');
    ol.innerHTML = '';
    proc.steps.forEach(function(s, i) {
      var li = $('step-tpl').content.firstElementChild.cloneNode(true);
      li.dataset.index = i;
      li.querySelector('.step-num').textContent = 'Step ' + (i + 1);
      li.querySelector('.step-time').textContent = s.t_start != null ? fmt(s.t_start) + '–' + fmt(s.t_end) : '';
      var img = li.querySelector('.ref');
      if (s.reference_image) img.src = media(s.reference_image); else img.classList.add('hidden');
      if (s.clip) {
        var v = li.querySelector('.clip');
        v.src = media(s.clip);
        v.classList.remove('hidden');
      }
      li.querySelector('[data-f=title]').value = s.title;
      li.querySelector('[data-f=instruction]').value = s.instruction;
      li.querySelector('[data-f=check]').value = s.check;
      li.querySelector('[data-f=tips]').value = (s.tips || []).join('\n');
      li.querySelector('[data-f=tools]').value = (s.tools || []).join(', ');
      li.querySelector('[data-f=parts]').value = (s.parts || []).join(', ');
      li.querySelector('[data-op=up]').disabled = i === 0;
      li.querySelector('[data-op=down]').disabled = i === proc.steps.length - 1;
      li.querySelector('[data-op=merge]').disabled = i === proc.steps.length - 1;
      li.querySelector('[data-op=delete]').disabled = proc.steps.length === 1;
      ol.appendChild(li);
      updateCounts(li);
    });
  }

  function updateCounts(li) {
    li.querySelectorAll('.count').forEach(function(c) {
      var n = words(li.querySelector('[data-f=' + c.dataset.for + ']').value);
      var max = +c.dataset.max;
      c.textContent = n + '/' + max + ' words';
      c.classList.toggle('over', n > max);
    });
  }

  // ------------------------------------------------------------------ edit

  /** Pull the form back into `proc` before any structural change or save. */
  function collect() {
    proc.title = $('proc-title').value.trim();
    document.querySelectorAll('#steps > li').forEach(function(li) {
      var s = proc.steps[+li.dataset.index];
      s.title = li.querySelector('[data-f=title]').value.trim();
      s.instruction = li.querySelector('[data-f=instruction]').value.trim();
      s.check = li.querySelector('[data-f=check]').value.trim();
      s.tips = lines(li.querySelector('[data-f=tips]').value);
      s.tools = list(li.querySelector('[data-f=tools]').value);
      s.parts = list(li.querySelector('[data-f=parts]').value);
    });
  }

  function uniq(a) { return a.filter(function(x, i) { return a.indexOf(x) === i; }); }

  /** Merge step i into i+1's end state: both checks must hold afterwards. */
  function merge(i) {
    var a = proc.steps[i], b = proc.steps[i + 1];
    var merged = Object.assign({}, a, {
      t_end: b.t_end,
      reference_image: b.reference_image || a.reference_image,
      clip: b.clip || a.clip,
      keyframes: (a.keyframes || []).concat(b.keyframes || []),
      tips: uniq((a.tips || []).concat(b.tips || [])),
      tools: uniq((a.tools || []).concat(b.tools || [])),
      parts: uniq((a.parts || []).concat(b.parts || [])),
      check: a.check.replace(/\.\s*$/, '') + '; and ' + b.check,
    });
    proc.steps.splice(i, 2, merged);
  }

  function onStepOp(op, i) {
    collect();
    var steps = proc.steps;
    if (op === 'up') steps.splice(i - 1, 0, steps.splice(i, 1)[0]);
    if (op === 'down') steps.splice(i + 1, 0, steps.splice(i, 1)[0]);
    if (op === 'merge') merge(i);
    if (op === 'delete' && confirm('Delete step ' + (i + 1) + '?')) steps.splice(i, 1);
    dirty = true;
    render();
    status('Unsaved changes');
  }

  function save() {
    collect();
    var over = proc.steps.findIndex(function(s) { return words(s.title) > 5 || words(s.instruction) > 12; });
    if (over >= 0) {
      status('Step ' + (over + 1) + ' is over the word limit', true);
      return Promise.reject(new Error('limits'));
    }
    status('Saving…');
    return api('PUT', '/procedures/' + proc.id, proc).then(function(p) {
      var answers = {};
      document.querySelectorAll('#followup-list textarea').forEach(function(ta) {
        if (ta.value.trim()) answers[ta.dataset.fq] = ta.value.trim();
      });
      return Object.keys(answers).length ? api('POST', '/procedures/' + p.id + '/answers', { answers: answers }) : p;
    }).then(function(p) {
      proc = p;
      dirty = false;
      render();
      status('Saved');
      return p;
    }).catch(function(e) {
      status('Save failed: ' + e.message, true);
      throw e;
    });
  }

  function publish(on) {
    (dirty ? save() : Promise.resolve()).then(function() {
      return api('POST', '/procedures/' + proc.id + (on ? '/publish' : '/unpublish'));
    }).then(function() {
      return loadPicker(proc.id);
    }).then(function() {
      status(on ? 'Published: it shows on the glasses now' : 'Unpublished');
    }).catch(function() {});
  }

  function checkCapture() {
    var id = $('capture-id').value.trim();
    if (!id) return;
    api('GET', '/captures/' + id).then(function(c) {
      var txt = c.state + (c.stage ? ' · ' + c.stage : '') + ' · ' + Math.round(c.progress * 100) + '%';
      if (c.error) txt += ' · ' + c.error;
      $('capture-status').textContent = txt;
      if (c.state === 'done' && c.procedure_id) loadPicker(c.procedure_id);
      else if (c.state === 'processing') setTimeout(checkCapture, 4000);
    }).catch(function(e) { $('capture-status').textContent = e.message; });
  }

  // ------------------------------------------------------------------ wire up

  function init() {
    $('picker').addEventListener('change', function(e) {
      if (dirty && !confirm('Discard unsaved changes?')) return;
      if (e.target.value) loadProcedure(e.target.value);
    });
    $('steps').addEventListener('click', function(e) {
      var b = e.target.closest('[data-op]');
      if (b) onStepOp(b.dataset.op, +b.closest('li').dataset.index);
    });
    $('steps').addEventListener('input', function(e) {
      dirty = true;
      status('Unsaved changes');
      updateCounts(e.target.closest('li'));
    });
    $('proc-title').addEventListener('input', function() { dirty = true; status('Unsaved changes'); });
    $('save').addEventListener('click', function() { save().catch(function() {}); });
    $('publish').addEventListener('click', function() { publish(true); });
    $('unpublish').addEventListener('click', function() { publish(false); });
    $('capture-check').addEventListener('click', checkCapture);
    window.addEventListener('beforeunload', function(e) { if (dirty) e.preventDefault(); });
    var cap = new URLSearchParams(location.search).get('capture');
    if (cap) { $('capture-id').value = cap; checkCapture(); }
    loadPicker();
  }

  init();
})();
