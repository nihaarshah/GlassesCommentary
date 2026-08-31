/**
 * Polls the Modal /latest endpoint for whatever the capture client (laptop
 * webcam today, glasses DAT app later) most recently sent to /caption, and
 * displays it. This webapp has no camera access of its own -- Meta Display
 * Glasses webapps only get REST/WebSocket/sensor APIs, not getUserMedia --
 * so it is a viewer, not a capture client.
 */
(function() {
  'use strict';

  var CONFIG = {
    latestUrl: 'https://nihaarshah--glasses-commentary-commentary-latest.modal.run',
    refreshIntervalMs: 4000,
  };

  var refreshTimer = null;

  function setLoading(isLoading) {
    var el = document.getElementById('loading');
    if (el) el.classList.toggle('hidden', !isLoading);
  }

  function setError(hasError) {
    var el = document.getElementById('error');
    if (el) el.classList.toggle('hidden', !hasError);
  }

  function timeAgo(t) {
    if (!t) return '';
    var s = Math.round(Date.now() / 1000 - t);
    if (s < 2) return 'just now';
    return s + 's ago';
  }

  function loadCommentary() {
    setLoading(true);
    fetch(CONFIG.latestUrl, { cache: 'no-store' })
      .then(function(res) {
        if (!res.ok) throw new Error('HTTP ' + res.status);
        return res.json();
      })
      .then(function(data) {
        setLoading(false);
        setError(false);
        var text = data.commentary || 'Waiting for the first frame…';
        document.getElementById('commentary-text').textContent = text;
        document.getElementById('commentary-meta').textContent = timeAgo(data.t);
        document.getElementById('status-indicator').textContent = 'Live';
      })
      .catch(function() {
        setLoading(false);
        setError(true);
        document.getElementById('status-indicator').textContent = 'Offline';
      });
  }

  function startAutoRefresh() {
    stopAutoRefresh();
    refreshTimer = setInterval(loadCommentary, CONFIG.refreshIntervalMs);
  }

  function stopAutoRefresh() {
    if (refreshTimer) {
      clearInterval(refreshTimer);
      refreshTimer = null;
    }
  }

  function setupEvents() {
    document.addEventListener('click', function(e) {
      var actionEl = e.target.closest('[data-action]');
      if (actionEl && actionEl.dataset.action === 'refresh') loadCommentary();
    });

    document.addEventListener('keydown', function(e) {
      if (e.key === 'Enter' &&
          document.activeElement &&
          document.activeElement.classList.contains('focusable')) {
        document.activeElement.click();
        e.preventDefault();
      }
    });
  }

  function init() {
    setupEvents();
    var btn = document.querySelector('.focusable');
    if (btn) btn.focus();
    loadCommentary();
    startAutoRefresh();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
