const $ = (id) => document.getElementById(id);
const pageRoot = $('feature-page');
const state = {features: [], activeFeature: null, unmount: null, stylesheet: null, seenErrors: new Set()};

function localAsset(path) {
  if (typeof path !== 'string' || !path.startsWith('/assets/features/')) throw new Error('Feature page assets must be registered local feature files.');
  return new URL(path, window.location.origin).href;
}

function logEvent(type, message, source = 'control-center', tone = '') {
  const list = $('event-list'); list.querySelector('.event-empty')?.remove();
  const row = document.createElement('li'); row.className = `event-item ${tone}`;
  const time = document.createElement('time'); time.className = 'event-time'; time.textContent = new Date().toLocaleTimeString([], {hour12: false});
  const copy = document.createElement('div'); copy.className = 'event-copy';
  const label = document.createElement('div'); label.className = 'event-type'; label.textContent = type;
  const detail = document.createElement('div'); detail.className = 'event-message'; detail.textContent = message;
  const tag = document.createElement('div'); tag.className = 'event-source'; tag.textContent = source;
  copy.append(label, detail, tag); row.append(time, copy); list.prepend(row);
}

async function request(path, options = {}) {
  const response = await fetch(path, options);
  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = body.detail;
    throw new Error(typeof detail === 'string' ? detail : detail?.message || `Request failed (${response.status})`);
  }
  return body;
}

function setConnection(ready, message = ready ? 'Connected' : 'Service unavailable') {
  $('connection-light').className = `status-light ${ready ? 'ready' : 'error'}`;
  $('service-light').className = `status-light ${ready ? 'ready' : 'error'}`;
  $('connection-state').textContent = message; $('system-state').textContent = ready ? 'Service ready' : 'Connection lost';
  $('overview-service').textContent = ready ? 'Ready' : 'Offline';
}

async function refreshSystem() {
  try {
    const system = await request('/api/system');
    $('overview-inputs').textContent = `${system.active_sessions.length} active`;
    $('overview-model').textContent = system.loaded_models ? `${system.loaded_models} loaded` : 'None loaded';
    for (const error of system.recent_errors || []) {
      const key = `${error.at}:${error.message}`;
      if (!state.seenErrors.has(key)) { state.seenErrors.add(key); logEvent('Service error', error.message, 'system overview', 'error'); }
    }
    setConnection(true);
  } catch (error) {
    setConnection(false); $('overview-model').textContent = 'Unavailable'; $('overview-inputs').textContent = 'Unavailable';
  }
}

async function showFeature(feature) {
  if (state.unmount) { try { state.unmount(); } catch (error) { logEvent('Feature cleanup failed', error.message, state.activeFeature?.id, 'error'); } }
  state.unmount = null; state.activeFeature = feature;
  for (const button of $('feature-nav').querySelectorAll('.nav-link')) button.classList.toggle('active', button.dataset.featureId === feature.id);
  $('page-title').textContent = feature.name; $('page-summary').textContent = feature.summary;
  $('page-eyebrow').textContent = `LOCAL SPEECH SYSTEM / ${feature.id.toUpperCase()}`;
  $('contract-link').href = feature.contract_url;
  pageRoot.replaceChildren(Object.assign(document.createElement('p'), {className: 'muted', textContent: 'Loading feature page…'}));
  state.stylesheet?.remove(); state.stylesheet = null;
  try {
    const templateResponse = await fetch(localAsset(feature.page_template));
    if (!templateResponse.ok) throw new Error(`Feature page template failed (${templateResponse.status})`);
    pageRoot.innerHTML = await templateResponse.text();
    const stylesheet = document.createElement('link'); stylesheet.rel = 'stylesheet'; stylesheet.href = localAsset(feature.page_stylesheet);
    document.head.append(stylesheet); state.stylesheet = stylesheet;
    const controller = await import(localAsset(feature.page_module));
    if (typeof controller.mount !== 'function') throw new Error('Feature page module must export mount(root, context).');
    state.unmount = await controller.mount(pageRoot, {feature, request: (path, options) => request(`/api/features/${encodeURIComponent(feature.id)}${path}`, options),
      logEvent, refreshSystem});
    if (typeof state.unmount !== 'function') state.unmount = null;
  } catch (error) {
    pageRoot.replaceChildren(Object.assign(document.createElement('p'), {className: 'error-copy', textContent: `Feature page unavailable: ${error.message}`}));
    logEvent('Feature page unavailable', error.message, feature.id, 'error');
  }
  await refreshSystem();
}

function renderNavigation() {
  const nav = $('feature-nav'); nav.replaceChildren();
  for (const [index, feature] of state.features.entries()) {
    const button = document.createElement('button'); button.type = 'button'; button.className = 'nav-link'; button.dataset.featureId = feature.id;
    const marker = document.createElement('span'); marker.className = 'nav-marker'; marker.textContent = String(index + 1).padStart(2, '0');
    const name = document.createElement('span'); name.textContent = feature.name;
    const verification = document.createElement('small'); verification.className = 'nav-verification';
    verification.textContent = feature.verification_status.replaceAll('-', ' ');
    button.append(marker, name, verification); button.addEventListener('click', () => showFeature(feature)); nav.append(button);
  }
}

async function loadRegistry() {
  try {
    state.features = await request('/api/features'); renderNavigation();
    if (!state.features.length) { pageRoot.textContent = 'No documented features are registered.'; $('page-title').textContent = 'Control center'; $('page-summary').textContent = 'Register a feature page to begin.'; return; }
    await showFeature(state.features[0]);
  } catch (error) {
    setConnection(false); $('feature-nav').replaceChildren(Object.assign(document.createElement('p'), {className: 'error-copy', textContent: 'Feature registry unavailable'}));
    pageRoot.replaceChildren(Object.assign(document.createElement('p'), {className: 'error-copy', textContent: `Cannot load registered feature pages: ${error.message}`}));
    logEvent('Registry unavailable', error.message, 'control-center', 'error');
  }
}

document.addEventListener('DOMContentLoaded', () => {
  $('clear-events').addEventListener('click', () => { $('event-list').replaceChildren(Object.assign(document.createElement('li'), {className: 'event-empty', textContent: 'Event log cleared for this browser session.'})); });
  refreshSystem(); loadRegistry(); setInterval(refreshSystem, 2500);
});
