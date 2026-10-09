let FEATURE = '';
let emitEvent = () => {};
let refreshSystem = async () => {};
const $ = (id) => document.getElementById(id);
const BRIDGE_DEFAULT = 'http://127.0.0.1:18767/api';
const state = {handleId: null, source: null, activeSource: null, groupId: null, eventIndex: 0, models: [], currentTab: 'clip', hostBridge: false, bridgeUrl: BRIDGE_DEFAULT};

function logEvent(type, message, source = 'control-center', tone = '') {
  emitEvent(type, message, source, tone);
}

async function request(path, options = {}) {
  const response = await fetch(`${FEATURE}${path}`, options);
  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = body.detail;
    throw new Error(typeof detail === 'string' ? detail : detail?.message || `Request failed (${response.status})`);
  }
  return body;
}
async function bridgeRequest(path, options = {}) {
  const response = await fetch(`${state.bridgeUrl}${path}`, options);
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(body.detail || `Mac microphone bridge request failed (${response.status})`);
  return body;
}

function selectedModel() { return state.models.find((model) => model.key === $('model-select').value); }
function selectedRuntime() { return selectedModel()?.runtimes.find((runtime) => runtime.key === $('runtime-select').value); }
function refreshActionState() {
  const canRun = Boolean(state.handleId);
  const canStartProcesses = !state.hostBridge && Boolean(selectedModel() && selectedRuntime()?.compatible && $('precision-select').value);
  $('transcribe-clip').disabled = !canRun;
  $('start-mic').disabled = !canRun;
  $('start-process').disabled = !canStartProcesses;
  $('run-capacity').disabled = !canStartProcesses;
  $('close-model').disabled = !canRun;
  $('load-model').disabled = canRun;
}

function updateSpine(input = 'Waiting for audio', transcript = 'No output yet') {
  $('input-state').textContent = input; $('transcript-state').textContent = transcript;
  $('input-node').classList.toggle('active', input === 'Listening');
  $('input-node').classList.toggle('ready', input === 'WAV selected');
  $('model-node').classList.toggle('ready', Boolean(state.handleId));
  $('spine-model-state').textContent = state.handleId ? 'Runtime ready' : 'Not loaded';
  $('transcript-node').classList.toggle('ready', transcript === 'Transcript ready');
  $('loaded-chip').textContent = state.handleId ? `${$('runtime-select').value.toUpperCase()} · READY` : 'NO MODEL';
  $('loaded-chip').classList.toggle('ready', Boolean(state.handleId));
}

async function loadCatalog() {
  try {
    const {models} = await request('/catalog'); state.models = models;
    const modelSelect = $('model-select'); modelSelect.replaceChildren();
    for (const model of models) { const option = new Option(`${model.display_name} · ${model.installed ? 'installed' : 'not installed'}`, model.key); modelSelect.add(option); }
    if (models.some((model) => model.key === 'turbo')) modelSelect.value = 'turbo';
    if (!models.length) modelSelect.add(new Option('No catalog entries', ''));
    updateRuntimeOptions();
    const {host_bridge: hostBridge = false, host_bridge_url: bridgeUrl = BRIDGE_DEFAULT} = await request('/capture-capabilities');
    state.hostBridge = hostBridge; state.bridgeUrl = bridgeUrl;
    if (hostBridge) {
      $('device-select').replaceChildren(new Option('OS selected input', ''));
      $('mic-capture-note').textContent = 'Capture runs on the Mac through the host bridge. Start the bridge in a Mac terminal, then allow microphone access when macOS prompts.';
      $('process-note').textContent = 'Process groups and capacity checks use direct container microphone capture and are unavailable in the Mac Docker profile.';
      try {
        const {devices} = await bridgeRequest('/devices');
        for (const device of devices) $('device-select').add(new Option(`${device.name}${device.default ? ' · default' : ''}`, device.name));
      } catch (error) { logEvent('Mac microphone bridge unavailable', error.message, 'capture boundary', 'warning'); }
    } else {
      $('process-note').textContent = 'Process groups and capacity runs load a separate process-owned model from the selection above. They do not use the single model handle from the WAV and microphone tabs.';
      const {devices, unavailable_reason} = await request('/devices');
      for (const device of devices) $('device-select').add(new Option(`${device.name}${device.default ? ' · default' : ''}`, device.name));
      if (unavailable_reason) logEvent('Input devices unavailable', unavailable_reason, 'capture boundary', 'warning');
      $('mic-capture-note').textContent = 'Capture runs on this computer through the selected host input. Browser microphone permission is not used.';
    }
    logEvent('Catalog refreshed', `${models.length} model option${models.length === 1 ? '' : 's'} discovered`, 'Feature 01');
  } catch (error) { logEvent('Catalog failed', error.message, 'Feature 01', 'error'); }
}

async function adoptLoadedModel() {
  try {
    const {models = []} = await request('');
    const readyModels = models.filter((model) => model.state === 'ready');
    if (readyModels.length !== 1) return;
    const model = readyModels[0];
    const catalogModel = state.models.find((item) => item.key === model.model);
    if (!catalogModel) return;

    $('model-select').value = model.model;
    updateRuntimeOptions();
    if (![...$('runtime-select').options].some((option) => option.value === model.runtime)) return;
    $('runtime-select').value = model.runtime;
    updatePrecisionOptions();
    if (![...$('precision-select').options].some((option) => option.value === model.precision)) return;
    $('precision-select').value = model.precision;

    state.handleId = model.id;
    $('model-state').textContent = `${model.model} · ${model.runtime} · ${model.state}`;
    refreshActionState(); updateSpine();
    logEvent('Existing model adopted', `${model.model} · ${model.runtime} · ${model.precision}`, model.id);
  } catch (error) { logEvent('Model status unavailable', error.message, 'Feature 01', 'warning'); }
}

function updateRuntimeOptions() {
  const model = selectedModel(); const select = $('runtime-select'); select.replaceChildren();
  for (const runtime of model?.runtimes || []) {
    const option = new Option(`${runtime.key} · ${runtime.ready ? 'ready' : 'unavailable'}`, runtime.key);
    option.disabled = !runtime.compatible; select.add(option);
  }
  if (!select.options.length) select.add(new Option('No runtimes', ''));
  updatePrecisionOptions();
}
function updatePrecisionOptions() {
  const runtime = selectedRuntime(); const select = $('precision-select'); select.replaceChildren();
  for (const precision of runtime?.precision_options || []) select.add(new Option(precision === 'source' ? 'Source checkpoint' : precision, precision));
  $('model-reason').textContent = runtime?.ready ? 'Runtime dependencies, device, and model weights are ready.' : runtime?.reason || 'Select a compatible model and runtime.';
  $('model-reason').classList.toggle('error-copy', Boolean(runtime && !runtime.ready));
}

async function refreshOverview() {
  await refreshSystem();
}

function flowSettings(input = 'clip') {
  const mic = input === 'mic';
  return {language: $('language').value.trim() || 'th', chunk_seconds: Number($(mic ? 'mic-chunk' : 'chunk-seconds').value),
    silence_threshold: Number($(mic ? 'mic-threshold' : 'threshold').value), decoding_options: {beam_size: Number($('beam-size').value),
      temperature: Number($('temperature').value), condition_on_previous_text: $('previous-text').checked}};
}

async function loadModel() {
  const runtime = selectedRuntime();
  try {
    if (!selectedModel()) throw new Error('Choose a model from the catalog first.');
    if (!runtime?.compatible) throw new Error('The selected runtime is incompatible with this model.');
    const model = await request('/models', {method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({model: selectedModel().key, runtime: runtime.key, precision: $('precision-select').value})});
    state.handleId = model.handle_id; $('model-state').textContent = `${model.model} · ${model.runtime} · ${model.state}`;
    $('model-state').classList.remove('error-copy'); refreshActionState(); updateSpine();
    logEvent('Model ready', `${model.model} · ${model.runtime} · ${model.precision}`, model.handle_id);
    await refreshOverview();
  } catch (error) { $('model-state').textContent = error.message; $('model-state').classList.add('error-copy'); logEvent('Model load failed', error.message, 'Feature 01', 'error'); }
}
async function closeModel() {
  try { await request(`/models/${state.handleId}`, {method: 'DELETE'}); logEvent('Model closed', $('model-state').textContent, state.handleId); state.handleId = null; $('model-state').textContent = 'No model loaded'; refreshActionState(); updateSpine(); await refreshOverview(); }
  catch (error) { logEvent('Model close failed', error.message, 'Feature 01', 'error'); }
}

function normalizeForCer(value) { return [...value.normalize('NFC').toLocaleLowerCase()].filter((char) => !/[\s\p{P}\p{S}]/u.test(char)); }
function characterErrorRate(reference, hypothesis) {
  const a = normalizeForCer(reference); const b = normalizeForCer(hypothesis); const row = Array.from({length: b.length + 1}, (_, i) => i);
  for (let i = 1; i <= a.length; i++) { let diagonal = row[0]; row[0] = i;
    for (let j = 1; j <= b.length; j++) { const old = row[j]; row[j] = Math.min(row[j] + 1, row[j - 1] + 1, diagonal + (a[i - 1] === b[j - 1] ? 0 : 1)); diagonal = old; } }
  return a.length ? row[b.length] / a.length : (b.length ? 1 : 0);
}

function measurementSummary(measurement) {
  return `#${measurement.sequence} RTF ${measurement.rtf.toFixed(3)} · ${measurement.audio_seconds.toFixed(2)}s audio · ${measurement.inference_seconds.toFixed(2)}s inference`;
}

async function transcribeClip(event) {
  event.preventDefault(); const file = $('clip-file').files[0];
  if (!file) return;
  const form = new FormData(); form.append('file', file); form.append('handle_id', state.handleId);
  const flow = flowSettings(); for (const [key, value] of Object.entries(flow)) if (key !== 'decoding_options') form.append(key, String(value));
  for (const [key, value] of Object.entries(flow.decoding_options)) form.append(key, String(value));
  const reference = $('reference-text').value.trim();
  $('transcript-output').textContent = 'Transcribing audio…'; $('run-time').textContent = 'RUNNING';
  updateSpine('WAV selected', 'Waiting for model'); logEvent('Clip submitted', `${file.name} · ${Math.round(file.size / 1024)} KB`, 'finite audio');
  try {
    const result = await request('/clips', {method: 'POST', body: form});
    $('transcript-output').textContent = result.transcript || 'No speech detected in eligible audio.';
    $('run-time').textContent = `${result.elapsed_seconds.toFixed(2)} s`;
    let accuracy = 'Accuracy: not measured';
    if (reference && $('reference-verified').checked) accuracy = `User-verified reference CER: ${(characterErrorRate(reference, result.transcript) * 100).toFixed(1)}%`;
    const measurements = result.measurements || [];
    const measurementText = measurements.length ? `RTF: ${measurements.map(measurementSummary).join(' · ')}` : 'RTF: no eligible audio chunks';
    $('result-meta').replaceChildren(document.createTextNode(`Configuration: ${result.configuration.model} · ${result.configuration.runtime} · ${result.configuration.language}`), document.createTextNode(measurementText), document.createTextNode(accuracy));
    updateSpine('WAV selected', 'Transcript ready'); logEvent('Transcription complete', `${result.transcript.length} characters · ${result.elapsed_seconds.toFixed(2)} seconds · ${measurements.map((item) => `RTF ${item.rtf.toFixed(3)}`).join(', ') || 'no eligible chunks'}`, 'finite audio');
  } catch (error) { $('transcript-output').textContent = `Transcription failed: ${error.message}`; $('run-time').textContent = 'FAILED'; updateSpine('WAV selected', 'No transcript'); logEvent('Transcription failed', error.message, 'finite audio', 'error'); }
}

async function startMicrophone() {
  if (state.micStarting || state.activeSource) return;
  state.micStarting = true; $('start-mic').disabled = true;
  try {
    const payload = {handle_id: state.handleId, device: $('device-select').value || null,
      flow_config: {...flowSettings('mic'), source_id: $('mic-source-id').value.trim() || undefined}};
    const result = state.hostBridge
      ? await bridgeRequest('/sessions', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload)})
      : await request('/microphones', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload)});
    state.activeSource = result.source_id; $('start-mic').classList.add('hidden'); $('stop-mic').classList.remove('hidden');
    updateSpine('Listening', 'Waiting for speech'); $('transcript-output').textContent = 'Microphone is listening. Speech events will appear here.';
    logEvent('Microphone started', $('device-select').value || 'OS selected input', result.source_id); followEvents(result.source_id);
  } catch (error) { logEvent('Microphone failed', error.message, 'capture boundary', 'error'); }
  finally { state.micStarting = false; if (!state.activeSource) $('start-mic').disabled = !state.handleId; }
}
async function startProcessGroup() {
  const devices = $('process-devices').value.split('\n').map((line) => line.trim()).filter(Boolean);
  if (!devices.length) { logEvent('Process group rejected', 'Enter at least one input device name.', 'process topology', 'error'); return; }
  const flowConfigs = readSourceConfigs(devices);
  try {
    const result = await request('/process-groups', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({devices,
      topology: $('topology').value, model_config: {model: $('model-select').value, runtime: $('runtime-select').value, precision: $('precision-select').value}, flow_config: flowSettings(), flow_configs: flowConfigs})});
    state.groupId = result.group_id; $('start-process').classList.add('hidden'); $('stop-process').classList.remove('hidden');
    logEvent('Process group started', `${result.topology} · ${result.sessions.length} source(s)`, result.group_id);
    for (const session of result.sessions) followEvents(session.source_id);
  } catch (error) { logEvent('Process group failed', error.message, 'process topology', 'error'); }
}
function readSourceConfigs(devices) {
  return devices.map((device, index) => {
    const row = $('source-configs').querySelector(`[data-source-index="${index}"]`);
    return {language: row.querySelector('[data-setting="language"]').value.trim() || 'th',
      chunk_seconds: Number(row.querySelector('[data-setting="chunk_seconds"]').value),
      silence_threshold: Number(row.querySelector('[data-setting="silence_threshold"]').value)};
  });
}
function renderSourceConfigs() {
  const devices = $('process-devices').value.split('\n').map((line) => line.trim()).filter(Boolean);
  const previous = new Map([...$('source-configs').querySelectorAll('[data-source-index]')].map((row) => [row.dataset.device, {
    language: row.querySelector('[data-setting="language"]').value,
    chunk_seconds: row.querySelector('[data-setting="chunk_seconds"]').value,
    silence_threshold: row.querySelector('[data-setting="silence_threshold"]').value}]));
  const container = $('source-configs'); container.replaceChildren();
  if (!devices.length) { const note = document.createElement('p'); note.className = 'field-note'; note.textContent = 'Add device names to configure each source independently.'; container.append(note); return; }
  devices.forEach((device, index) => {
    const prior = previous.get(device) || {};
    const section = document.createElement('fieldset'); section.className = 'source-config'; section.dataset.sourceIndex = String(index); section.dataset.device = device;
    const legend = document.createElement('legend'); legend.textContent = `Source ${index + 1} · ${device}`; section.append(legend);
    const grid = document.createElement('div'); grid.className = 'field-grid settings-grid';
    for (const [key, label, value, type, min, max, step] of [
      ['language','Language',prior.language || 'th','text',null,null,null],
      ['chunk_seconds','Chunk · seconds',prior.chunk_seconds || '5','number','0.1','30','0.1'],
      ['silence_threshold','Silence threshold',prior.silence_threshold || '0.05','number','0','0.99','0.01']]) {
      const field = document.createElement('label'); field.textContent = label;
      const input = document.createElement('input'); input.type = type; input.value = value; input.dataset.setting = key;
      if (min) { input.min = min; input.max = max; input.step = step; }
      field.append(input); grid.append(field);
    }
    section.append(grid); container.append(section);
  });
}
async function runCapacity() {
  const devices = $('process-devices').value.split('\n').map((line) => line.trim()).filter(Boolean);
  if (!devices.length) { logEvent('Capacity check rejected', 'Enter at least one input device name.', 'capacity', 'error'); return; }
  const button = $('run-capacity'); button.disabled = true;
  $('capacity-state').lastElementChild.textContent = 'Capacity: running real microphone capture and measuring each source…';
  $('capacity-state').classList.remove('capacity-pass','capacity-fail');
  try {
    const result = await request('/capacity', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({
      devices, topology: $('topology').value, duration_seconds: Number($('capacity-duration').value),
      queue_capacity: Number($('capacity-queue').value), enqueue_timeout: Number($('capacity-timeout').value),
      model: $('model-select').value, runtime: $('runtime-select').value, precision: $('precision-select').value,
      ...flowSettings(), flow_configs: readSourceConfigs(devices)})});
    const verdict = result.status === 'unavailable' ? 'Unavailable' : (result.realtime_verdict || 'Inconclusive').replaceAll('-', ' ');
    $('capacity-state').lastElementChild.textContent = `Capacity: ${verdict}${result.prerequisite_error ? ` · ${result.prerequisite_error}` : ''}`;
    $('capacity-state').classList.toggle('capacity-pass', result.realtime_verdict === 'pass');
    $('capacity-state').classList.toggle('capacity-fail', result.realtime_verdict === 'fail');
    const queue = result.max_observed_queue_depth === null ? 'unavailable' : `${result.max_observed_queue_depth} chunks`;
    const memory = result.peak_total_process_rss_bytes === null ? 'unavailable' : `${(result.peak_total_process_rss_bytes / 1048576).toFixed(1)} MiB`;
    const perSource = Object.entries(result.eligible_audio_seconds_by_source || {}).map(([source, seconds]) => `${source}: ${seconds.toFixed(2)} s`).join(' · ') || 'unavailable';
    const utilization = Object.entries(result.inference_utilization_by_model || {}).map(([model, value]) => `${model}: ${value.toFixed(2)}`).join(' · ') || 'unavailable';
    $('capacity-measurements').textContent = result.status === 'unavailable' ? `Measurements unavailable · ${result.prerequisite_error || 'runtime or capture prerequisite unavailable'}` :
      [`Capture: ${Number(result.capture_window_seconds).toFixed(2)} s`, `Eligible audio: ${perSource}`,
       `Input queue peak: ${queue} (${result.queue_depth_measurement || 'unavailable'})`,
       `Inference utilization: ${utilization}`, `Peak process memory: ${memory}`].join('\n');
    logEvent('Capacity result', `${verdict} · queue ${queue} · memory ${memory} · eligible audio ${perSource}`, result.topology || 'capacity', result.realtime_verdict === 'fail' ? 'error' : result.status === 'unavailable' ? 'warning' : '');
  } catch (error) {
    $('capacity-state').lastElementChild.textContent = `Capacity: error · ${error.message}`;
    $('capacity-measurements').textContent = 'Measurements unavailable because the capacity request failed.';
    logEvent('Capacity check failed', error.message, 'capacity', 'error');
  } finally { button.disabled = !(selectedModel() && selectedRuntime()?.compatible && $('precision-select').value); }
}
async function followEvents(sourceId) {
  const events = new EventSource(`${FEATURE}/events/${encodeURIComponent(sourceId)}`); state.source = events;
  events.onmessage = (message) => {
    const event = JSON.parse(message.data); const detail = event.type === 'transcript' ? event.text : event.type === 'measurement' ? measurementSummary(event) : event.message || event.status || '';
    logEvent(event.type, `${event.sequence !== undefined ? `#${event.sequence} · ` : ''}${detail}`, `${event.source_id} · event ${event.event_sequence}`, event.type === 'error' ? 'error' : '');
    if (event.type === 'transcript') { $('transcript-output').textContent = `${$('transcript-output').textContent === 'Microphone is listening. Speech events will appear here.' ? '' : `${$('transcript-output').textContent}\n`}${event.text}`; updateSpine('Listening', 'Transcript ready'); }
    if (event.type === 'error') { updateSpine('Listening', 'Input error'); }
    if (event.type === 'completed') {
      events.close(); state.source = null;
      if (state.hostBridge) bridgeRequest(`/sessions/${encodeURIComponent(sourceId)}`, {method: 'DELETE'}).catch(() => {});
      if (state.activeSource === sourceId) { state.activeSource = null; $('start-mic').classList.remove('hidden'); $('start-mic').disabled = !state.handleId; $('stop-mic').classList.add('hidden'); updateSpine('Waiting for audio', $('transcript-state').textContent); }
    }
  };
  events.onerror = () => { if (events.readyState === EventSource.CLOSED) logEvent('Event stream closed', 'Reconnect or restart the input to receive events.', sourceId, 'warning'); };
}
async function stopMicrophone() {
  if (!state.activeSource) return;
  try {
    if (state.hostBridge) await bridgeRequest(`/sessions/${encodeURIComponent(state.activeSource)}`, {method: 'DELETE'});
    else await request(`/sessions/${encodeURIComponent(state.activeSource)}/stop`, {method: 'POST'});
    logEvent('Microphone stop requested', 'Flushing accepted audio before completion.', state.activeSource);
  }
  catch (error) { logEvent('Microphone stop failed', error.message, state.activeSource, 'error'); }
}
async function stopProcessGroup() {
  if (!state.groupId) return;
  try { await request(`/process-groups/${state.groupId}/stop`, {method: 'POST'}); logEvent('Process group stopped', 'All source flows reached shutdown.', state.groupId); state.groupId = null; $('start-process').classList.remove('hidden'); $('stop-process').classList.add('hidden'); }
  catch (error) { logEvent('Process group stop failed', error.message, 'process topology', 'error'); }
}

function selectTab(tab) {
  state.currentTab = tab;
  for (const [name, id] of [['clip','clip-form'],['mic','mic-form'],['process','process-form']]) $(id).classList.toggle('hidden', name !== tab);
  for (const [name, id] of [['clip','clip-tab'],['mic','mic-tab'],['process','process-tab']]) { $(id).classList.toggle('is-current', name === tab); $(id).setAttribute('aria-selected', String(name === tab)); }
  updateSpine(tab === 'mic' ? 'Waiting for microphone' : tab === 'process' ? 'Waiting for input devices' : 'Waiting for audio');
}

export async function mount(_root, context) {
  FEATURE = `/api/features/${encodeURIComponent(context.feature.id)}`;
  emitEvent = context.logEvent;
  refreshSystem = context.refreshSystem;
  $('model-select').addEventListener('change', () => { updateRuntimeOptions(); refreshActionState(); });
  $('runtime-select').addEventListener('change', () => { updatePrecisionOptions(); refreshActionState(); });
  $('load-model').addEventListener('click', loadModel); $('close-model').addEventListener('click', closeModel);
  $('clip-form').addEventListener('submit', transcribeClip); $('start-mic').addEventListener('click', startMicrophone);
  $('stop-mic').addEventListener('click', stopMicrophone); $('start-process').addEventListener('click', startProcessGroup);
  $('stop-process').addEventListener('click', stopProcessGroup); $('clip-tab').addEventListener('click', () => selectTab('clip'));
  $('run-capacity').addEventListener('click', runCapacity); $('process-devices').addEventListener('input', renderSourceConfigs);
  $('mic-tab').addEventListener('click', () => selectTab('mic')); $('process-tab').addEventListener('click', () => selectTab('process'));
  $('clip-file').addEventListener('change', () => { const file = $('clip-file').files[0]; $('file-name').textContent = file?.name || 'Choose a PCM16 WAV · mono/stereo · 8–48 kHz'; updateSpine(file ? 'WAV selected' : 'Waiting for audio'); });
  await loadCatalog(); await adoptLoadedModel(); await refreshOverview(); refreshActionState();
  return () => { state.source?.close(); state.source = null; };
}
