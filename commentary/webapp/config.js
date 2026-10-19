/**
 * The one place the web apps (glasses app + review page) get their endpoints.
 * No secrets here -- these are public, unauthenticated Modal URLs.
 */
window.APP_CONFIG = {
  // glasses-guide FastAPI app (commentary/guide/modal_app.py)
  guideApi: 'https://nihaarshah--glasses-guide-api.us-east.modal.run',
  // live Moondream commentary (commentary/modal_app.py), unchanged
  commentaryLatestUrl: 'https://nihaarshah--glasses-commentary-commentary-latest.modal.run',
  displayPollMs: 1000,
  commentaryPollMs: 1000,
  passAdvanceMs: 1500,
};

// Dev only: ?api=http://localhost:8787 points the app at a local backend.
(function() {
  var m = /[?&]api=([^&]+)/.exec(location.search);
  if (m) window.APP_CONFIG.guideApi = decodeURIComponent(m[1]).replace(/\/$/, '');
})();
