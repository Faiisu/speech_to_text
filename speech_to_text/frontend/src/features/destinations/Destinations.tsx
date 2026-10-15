import { useCallback, useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { api, ApiRequestError } from '../../shared/api'
import type { Destination, DestinationDefinition, DestinationSummary } from '../../shared/types'
import { ApiError, Loading } from '../../shared/States'
import './destinations.css'

const defaults = (): DestinationDefinition => ({
  name: '', endpoint_url: '', method: 'POST', headers: {}, auth: { type: 'none' },
  timeout_seconds: 5, max_attempts: 3, backoff_seconds: .25, max_backoff_seconds: 2,
  max_retry_after_seconds: 30, follow_redirects: false, idempotency_header: 'Idempotency-Key',
  payload_format: 'match_results_v1', tls_ca_bundle_ref: null,
  tls_client_certificate_ref: null, tls_client_key_ref: null,
  protocol: 'http', mqtt_topic: null, mqtt_qos: 1, mqtt_retain: false,
})

const sample = {
  event_type: 'transcription.match_results', event_id: '<source_id>', source_id: '<source_id>',
  language: 'th', transcript: 'สวัสดีครับ', matches: [{ keyword: 'สวัสดี', count: 1 }],
  status: 'completed', completed_at: '2026-10-14T09:30:00Z',
}

export default function Destinations() {
  const [items, setItems] = useState<DestinationSummary[]>([])
  const [selected, setSelected] = useState<Destination | null>(null)
  const [draft, setDraft] = useState<DestinationDefinition>(defaults)
  const [headersText, setHeadersText] = useState('{}')
  const [savedHeadersText, setSavedHeadersText] = useState('')
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState('')
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [cleanupWarning, setCleanupWarning] = useState('')
  const [saving, setSaving] = useState(false)
  const [testing, setTesting] = useState(false)
  const [deleting, setDeleting] = useState(false)
  const [testResult, setTestResult] = useState<Awaited<ReturnType<typeof api.testDestination>> | null>(null)
  const [savedDraft, setSavedDraft] = useState('')
  const isDirty = selected
    ? savedDraft !== JSON.stringify(draft) || headersText !== savedHeadersText
    : JSON.stringify(draft) !== JSON.stringify(defaults())
  const busy = saving || testing || deleting || loading
  const mqtt = draft.protocol === 'mqtt'

  const loadList = useCallback(async (keepSelection = true) => {
    setLoadError('')
    try {
      const destinations = await api.destinations()
      setItems(destinations)
      if (keepSelection && selected && destinations.some(item => item.destination_id === selected.destination_id)) {
        const fresh = await api.destination(selected.destination_id)
        setSelected(fresh)
        const next = { name: fresh.name, ...fresh.config } as DestinationDefinition
        setDraft(next); setHeadersText(JSON.stringify(next.headers, null, 2)); setSavedHeadersText(JSON.stringify(next.headers, null, 2)); setSavedDraft(JSON.stringify(next))
      } else if (!keepSelection) {
        setSelected(null); setDraft(defaults()); setHeadersText('{}'); setSavedHeadersText(''); setSavedDraft('')
      }
    } catch (reason) {
      setLoadError(reason instanceof Error ? reason.message : 'Could not load destinations.')
    } finally { setLoading(false) }
  }, [selected])

  useEffect(() => { void loadList(false) }, [])

  const selectDestination = async (id: string) => {
    setError(''); setNotice(''); setCleanupWarning(''); setTestResult(null); setLoading(true)
    try {
      const detail = await api.destination(id)
      const next = { name: detail.name, ...detail.config } as DestinationDefinition
      setSelected(detail); setDraft(next); setHeadersText(JSON.stringify(next.headers, null, 2)); setSavedHeadersText(JSON.stringify(next.headers, null, 2)); setSavedDraft(JSON.stringify(next))
    } catch (reason) { setError(errorMessage(reason, 'Could not load destination settings.')) }
    finally { setLoading(false) }
  }

  const update = <K extends keyof DestinationDefinition>(key: K, value: DestinationDefinition[K]) => {
    setDraft(current => ({ ...current, [key]: value }))
    setError(''); setNotice(''); setCleanupWarning('')
  }
  const updateAuth = (key: string, value: string) => {
    const auth = { ...draft.auth, [key]: value || null }
    if (auth.type === 'none') { delete auth.secret_ref; delete auth.header_name; delete auth.username_secret_ref; delete auth.password_secret_ref }
    if (auth.type === 'bearer') { delete auth.username_secret_ref; delete auth.password_secret_ref; delete auth.header_name }
    if (auth.type === 'basic' || auth.type === 'username_password') { delete auth.secret_ref; delete auth.header_name }
    if (auth.type === 'api_key_header') { delete auth.username_secret_ref; delete auth.password_secret_ref }
    update('auth', auth)
  }
  const payload = useMemo(() => JSON.stringify(mqtt
    ? { ...sample, event_id: '<source_id>:chunk:<ordinal>', rtf: 0.1 }
    : sample, null, 2), [mqtt])

  const save = async (event: React.FormEvent) => {
    event.preventDefault(); setError(''); setNotice(''); setCleanupWarning(''); setTestResult(null)
    let headers: Record<string, string>
    try {
      const parsed: unknown = JSON.parse(headersText)
      if (!parsed || Array.isArray(parsed) || typeof parsed !== 'object' || Object.values(parsed).some(value => typeof value !== 'string')) throw new Error()
      headers = parsed as Record<string, string>
    } catch { setError('Custom headers must be a JSON object with string values.'); return }
    const url = safeUrl(draft.endpoint_url, mqtt)
    if (!draft.name.trim()) { setError('Enter a destination name.'); return }
    if (!url) { setError(mqtt ? 'Enter an mqtt:// or mqtts:// broker URL with a host and optional port.' : 'Enter a valid absolute HTTP or HTTPS URL without credentials or a fragment.'); return }
    if (mqtt && (!draft.mqtt_topic || /[\0+#]/.test(draft.mqtt_topic))) { setError('Enter a publish topic without NUL, +, or # wildcards.'); return }
    if (draft.auth.type !== 'none' && url.protocol !== (mqtt ? 'mqtts:' : 'https:')) { setError('Credentials require an encrypted HTTPS or mqtts:// connection.'); return }
    const numeric: [string, number, number, boolean][] = [
      ['Timeout', draft.timeout_seconds, 0, true], ['Maximum attempts', draft.max_attempts, 1, false],
      ['Backoff', draft.backoff_seconds, 0, false], ['Maximum backoff', draft.max_backoff_seconds, 0, false],
      ['Maximum Retry-After', draft.max_retry_after_seconds, 0, true],
    ]
    for (const [label, value, min, strict] of numeric) {
      if (!Number.isFinite(value) || (strict ? value <= min : value < min) || (label === 'Maximum attempts' && !Number.isInteger(value))) {
        setError(`${label} must be ${label === 'Timeout' || label === 'Maximum Retry-After' ? 'a finite number greater than 0' : label === 'Maximum attempts' ? 'a positive whole number' : 'a finite number at least 0'}.`); return
      }
    }
    const cert = draft.tls_client_certificate_ref?.trim() || null
    const key = draft.tls_client_key_ref?.trim() || null
    if (Boolean(cert) !== Boolean(key)) { setError('Mutual TLS requires both a client certificate reference and a client key reference.'); return }
    const body = { ...draft, name: draft.name.trim(), endpoint_url: url.href, headers, tls_ca_bundle_ref: draft.tls_ca_bundle_ref?.trim() || null, tls_client_certificate_ref: cert, tls_client_key_ref: key }
    setSaving(true)
    try {
      const result = selected
        ? await api.updateDestination(selected.destination_id, body)
        : await api.createDestination(body)
      const next = { name: result.name, ...result.config } as DestinationDefinition
      setSelected(result); setDraft(next); setHeadersText(JSON.stringify(next.headers, null, 2)); setSavedHeadersText(JSON.stringify(next.headers, null, 2)); setSavedDraft(JSON.stringify(next))
      await loadList(true)
      setNotice(`Saved “${result.name}”.`)
      if (result.retained_cleanup?.status === 'failed') {
        setCleanupWarning(`Settings were saved, but clearing the old retained message failed (${result.retained_cleanup.error_code || 'delivery_failed'}). Save again to retry.`)
      }
    } catch (reason) { setError(errorMessage(reason, 'Could not save destination settings.')) }
    finally { setSaving(false) }
  }

  const sendTest = async () => {
    if (!selected || isDirty) return
    setTesting(true); setError(''); setNotice(''); setTestResult(null)
    try {
      const result = await api.testDestination(selected.destination_id)
      setTestResult(result)
    } catch (reason) { setError(errorMessage(reason, 'Could not send the test JSON.')) }
    finally { setTesting(false) }
  }

  const remove = async () => {
    if (!selected) return
    setDeleting(true); setError(''); setNotice('')
    try {
      await api.deleteDestination(selected.destination_id)
      const removedName = selected.name
      await loadList(false)
      setNotice(`Deleted “${removedName}”.`)
    } catch (reason) {
      setError(reason instanceof ApiRequestError && reason.status === 409
        ? 'This destination is selected by a profile. Choose another destination in that profile before deleting it.'
        : errorMessage(reason, 'Could not delete destination.'))
    } finally { setDeleting(false) }
  }

  if (loading && items.length === 0 && !loadError) return <div className="page-content"><Loading label="Loading destinations…" /></div>
  return <div className="page-content destinations-page">
    {loadError && <ApiError message={loadError} retry={() => { setLoading(true); void loadList(false) }} />}
    {error && <div className="destination-alert" role="alert">{error}{error.includes('selected by a profile') && <> <Link to="/profiles">Open profiles</Link></>}</div>}
    {cleanupWarning && <div className="destination-alert" role="alert">{cleanupWarning}</div>}
    {notice && <div className="destination-notice" role="status">{notice}</div>}
    <div className="destination-heading"><div><div className="eyebrow">OUTBOUND JSON</div><h1>Destinations</h1><p>Choose where transcript chunks and their keyword matches are sent.</p></div><button className="button destination-new" disabled={busy} onClick={() => { setSelected(null); setDraft(defaults()); setHeadersText('{}'); setSavedHeadersText(''); setSavedDraft(''); setError(''); setNotice(''); setCleanupWarning(''); setTestResult(null) }}>＋ New destination</button></div>
    <div className="destination-layout">
      <aside className="destination-list" aria-label="Saved destinations">
        <div className="destination-list-head"><span>SAVED RECEIVERS</span><b>{items.length.toString().padStart(2, '0')}</b></div>
        {items.map((item, index) => <button key={item.destination_id} className={`destination-list-item ${selected?.destination_id === item.destination_id ? 'is-selected' : ''}`} disabled={busy} onClick={() => void selectDestination(item.destination_id)}>
          <span className="destination-list-index">{String(index + 1).padStart(2, '0')}</span><span className="destination-list-copy"><strong>{item.name}</strong><small>{item.protocol === 'mqtt' ? `MQTT · ${item.mqtt_topic}` : item.method} · {item.endpoint_origin}</small></span><span className="destination-list-arrow">↗</span>
        </button>)}
        {!items.length && <div className="destination-list-empty"><span>⌁</span><b>No receivers yet</b><small>Add an endpoint to configure where match results go.</small></div>}
        <div className="destination-list-foot">One destination can be assigned to each profile.</div>
      </aside>
      <main className="destination-main">
        <form className="destination-form" onSubmit={save}>
          <div className="destination-form-head"><div><span className="eyebrow">{selected ? 'RECEIVER SETTINGS' : 'NEW RECEIVER'}</span><h2>{selected ? draft.name || 'Edit destination' : 'Configure a receiver'}</h2></div>{selected && <span className="destination-id">ID · {selected.destination_id.slice(0, 8)}</span>}</div>
          <section className="destination-section"><div className="destination-section-title"><span>01</span><div><h3>Endpoint</h3><p>{mqtt ? 'Publish each microphone transcript chunk separately; clips publish once when complete.' : 'Send one JSON record when a source completes.'}</p></div></div>
            <label htmlFor="destination-name">Name</label><input id="destination-name" value={draft.name} maxLength={80} onChange={event => update('name', event.target.value)} placeholder="e.g. Operations inbox" />
            <label htmlFor="destination-protocol">Transport</label><select id="destination-protocol" value={draft.protocol || 'http'} disabled={busy} onChange={event => { const protocol = event.target.value as 'http' | 'mqtt'; setDraft({ ...defaults(), name: draft.name, protocol, endpoint_url: protocol === 'mqtt' ? 'mqtt://127.0.0.1:1883' : '', mqtt_topic: protocol === 'mqtt' ? 'speech/transcriptions' : null }); setHeadersText('{}'); setError(''); setTestResult(null) }}><option value="http">HTTP / HTTPS</option><option value="mqtt">MQTT</option></select>
            <small>Changing transport resets its connection settings.</small>
            <label htmlFor="destination-url">{mqtt ? 'Broker URL' : 'Endpoint URL'}</label><input id="destination-url" type="url" value={draft.endpoint_url} onChange={event => update('endpoint_url', event.target.value)} placeholder={mqtt ? 'mqtt://127.0.0.1:1883' : 'https://receiver.example.com/transcripts'} />
            {mqtt ? <><label htmlFor="mqtt-topic">Topic</label><input id="mqtt-topic" value={draft.mqtt_topic || ''} onChange={event => update('mqtt_topic', event.target.value)} placeholder="speech/transcriptions" /><label htmlFor="mqtt-qos">QoS</label><select id="mqtt-qos" value={draft.mqtt_qos ?? 1} onChange={event => update('mqtt_qos', Number(event.target.value))}><option value="0">0 · Send without broker acknowledgement</option><option value="1">1 · At least once</option><option value="2">2 · Exactly once within a connection</option></select><label className="destination-check"><input type="checkbox" checked={draft.mqtt_retain ?? false} onChange={event => update('mqtt_retain', event.target.checked)} /><span>Retain the latest result on this topic</span></label></> : <div className="destination-inline-fields"><div><label htmlFor="destination-method">Method</label><select id="destination-method" value={draft.method} onChange={event => update('method', event.target.value as DestinationDefinition['method'])}><option>POST</option><option>PUT</option><option>PATCH</option></select></div><div><label htmlFor="destination-payload">Payload format</label><select id="destination-payload" value={draft.payload_format} disabled><option value="match_results_v1">Match results v1</option></select></div></div>}
            <small>{mqtt ? 'Each microphone record contains only that chunk’s text and match counts. Stopping publishes its flushed final chunk without an aggregate; run status remains cumulative. Retries can produce duplicate events, so deduplicate by event_id. Saving with Retain off clears an old retained packet on this topic; subscribers may receive an empty payload. Credentials require mqtts://.' : 'Use a literal URL. HTTP is allowed for approved credential-free receivers; credentials require HTTPS.'} Private receivers also need backend network approval.</small>
          </section>
          <section className="destination-section"><div className="destination-section-title"><span>02</span><div><h3>Authentication & headers</h3><p>Store reference names only. Secret values stay in the backend environment.</p></div></div>
            <label htmlFor="destination-auth">Authentication</label><select id="destination-auth" value={draft.auth.type} onChange={event => updateAuth('type', event.target.value)}><option value="none">None</option>{mqtt ? <option value="username_password">MQTT username / password</option> : <><option value="bearer">Bearer token</option><option value="basic">Basic authentication</option><option value="api_key_header">API key header</option></>}</select>
            {draft.auth.type === 'bearer' && <><label htmlFor="secret-ref">Approved secret reference</label><input id="secret-ref" value={draft.auth.secret_ref || ''} onChange={event => updateAuth('secret_ref', event.target.value)} placeholder="e.g. TRANSCRIPT_RECEIVER_TOKEN" /></>}
            {(draft.auth.type === 'basic' || draft.auth.type === 'username_password') && <div className="destination-inline-fields"><div><label htmlFor="username-ref">Username secret reference</label><input id="username-ref" value={draft.auth.username_secret_ref || ''} onChange={event => updateAuth('username_secret_ref', event.target.value)} placeholder="RECEIVER_USERNAME" /></div><div><label htmlFor="password-ref">Password secret reference</label><input id="password-ref" value={draft.auth.password_secret_ref || ''} onChange={event => updateAuth('password_secret_ref', event.target.value)} placeholder="RECEIVER_PASSWORD" /></div></div>}
            {draft.auth.type === 'api_key_header' && <div className="destination-inline-fields"><div><label htmlFor="api-header-name">Header name</label><input id="api-header-name" value={draft.auth.header_name || ''} onChange={event => updateAuth('header_name', event.target.value)} placeholder="X-API-Key" /></div><div><label htmlFor="api-secret-ref">Approved secret reference</label><input id="api-secret-ref" value={draft.auth.secret_ref || ''} onChange={event => updateAuth('secret_ref', event.target.value)} placeholder="RECEIVER_API_KEY" /></div></div>}
            {!mqtt && <><label htmlFor="custom-headers">Custom headers (JSON)</label><textarea id="custom-headers" rows={3} value={headersText} onChange={event => { setHeadersText(event.target.value); setError('') }} spellCheck={false} />
            <small>Headers may contain non-secret values. Do not put credentials here. References must be approved in backend deployment settings.</small></>}
          </section>
          <section className="destination-section"><div className="destination-section-title"><span>03</span><div><h3>Delivery behavior</h3><p>Bound each request and retry so receiver outages stay predictable.</p></div></div>
            <div className="destination-number-grid"><NumberField id="timeout" label="Timeout (seconds)" value={draft.timeout_seconds} onChange={value => update('timeout_seconds', value)} step="0.1" min="0.1"/><NumberField id="attempts" label="Maximum attempts" value={draft.max_attempts} onChange={value => update('max_attempts', value)} step="1" min="1"/><NumberField id="backoff" label="Backoff (seconds)" value={draft.backoff_seconds} onChange={value => update('backoff_seconds', value)} step="0.05" min="0"/><NumberField id="max-backoff" label="Maximum backoff" value={draft.max_backoff_seconds} onChange={value => update('max_backoff_seconds', value)} step="0.1" min="0"/>{!mqtt && <NumberField id="retry-after" label="Maximum Retry-After" value={draft.max_retry_after_seconds} onChange={value => update('max_retry_after_seconds', value)} step="any" min="0"/>}</div>
            {!mqtt && <><label htmlFor="idempotency-header">Idempotency header</label><input id="idempotency-header" value={draft.idempotency_header || ''} onChange={event => update('idempotency_header', event.target.value || null)} placeholder="Idempotency-Key (leave blank to disable)" />
            <div className="destination-redirect-note"><b>Redirects are disabled.</b> Configure the receiver’s final URL when possible.</div></>}
          </section>
          <section className="destination-section"><div className="destination-section-title"><span>04</span><div><h3>Private TLS resources</h3><p>Optional backend-approved environment references.</p></div></div>
            <label htmlFor="ca-ref">CA bundle reference</label><input id="ca-ref" value={draft.tls_ca_bundle_ref || ''} onChange={event => update('tls_ca_bundle_ref', event.target.value || null)} placeholder="e.g. RECEIVER_CA_BUNDLE_PATH" />
            <div className="destination-inline-fields"><div><label htmlFor="cert-ref">Client certificate reference</label><input id="cert-ref" value={draft.tls_client_certificate_ref || ''} onChange={event => update('tls_client_certificate_ref', event.target.value || null)} placeholder="RECEIVER_CLIENT_CERT_PATH" /></div><div><label htmlFor="key-ref">Client key reference</label><input id="key-ref" value={draft.tls_client_key_ref || ''} onChange={event => update('tls_client_key_ref', event.target.value || null)} placeholder="RECEIVER_CLIENT_KEY_PATH" /></div></div><small>Certificate and key references must be provided together. No file paths or certificate contents are stored here.</small>
          </section>
          <div className="destination-actions"><div className="destination-save-state" aria-live="polite">{isDirty ? 'Unsaved changes' : selected ? 'Saved configuration' : 'New configuration'}</div>{selected && <button type="button" className="button button-outline danger-button" onClick={() => void remove()} disabled={busy}>{deleting ? 'Deleting…' : 'Delete'}</button>}<button className="button button-dark" disabled={busy}>{saving ? 'Saving…' : selected ? 'Save changes' : 'Save destination'} <span>↗</span></button></div>
        </form>
        <aside className="destination-aside"><div className="destination-preview"><div className="destination-preview-head"><div><span className="eyebrow">OUTGOING BODY</span><h3>JSON preview</h3></div><span className="preview-tag">V1</span></div><p>{mqtt ? 'Preview of one MQTT chunk record. Transcript and matches contain only this chunk; UI totals remain cumulative.' : 'One record per completed source. Thai text stays UTF-8.'}</p><pre aria-label="Outgoing JSON preview">{payload}</pre></div>
          <div className="destination-test"><div className="destination-test-mark">↗</div><div className="eyebrow">LIVE RECEIVER CHECK</div><h3>Send test JSON</h3><p>This performs a real request with synthetic text and match data. It uses saved settings and does not alter your form.</p>{isDirty && <div className="test-save-hint">Save your changes before testing. The request will use the saved configuration.</div>}
            <button type="button" className="button destination-test-button" disabled={!selected || isDirty || busy} onClick={() => void sendTest()}>{testing ? 'Sending test…' : 'Send test JSON'} <span>↗</span></button>
            {testResult && <div className={`test-result ${testResult.status}`} role="status"><b>{testResult.status === 'delivered' ? mqtt ? 'Published to MQTT broker' : 'Receiver accepted the test' : 'Test request failed'}</b><span>{mqtt ? `MQTT · QoS ${testResult.mqtt_qos ?? draft.mqtt_qos} · ${draft.mqtt_topic} · ` : testResult.http_status ? `HTTP ${testResult.http_status} · ` : ''}{testResult.attempts} attempt{testResult.attempts === 1 ? '' : 's'}{testResult.error_code ? ` · ${testResult.error_code}` : ''}</span><small>Test event · {testResult.event_id}</small></div>}
            {!selected && <small>Save this destination to enable a live test.</small>}
          </div>
        </aside>
      </main>
    </div>
  </div>
}

function NumberField({ id, label, value, onChange, min, step }: { id: string; label: string; value: number; onChange: (value: number) => void; min: string; step: string }) {
  return <div><label htmlFor={`destination-${id}`}>{label}</label><input id={`destination-${id}`} type="number" min={min} step={step} value={Number.isFinite(value) ? value : ''} onChange={event => onChange(event.target.value === '' ? Number.NaN : Number(event.target.value))} /></div>
}

function safeUrl(value: string, mqtt = false) {
  try {
    const url = new URL(value.trim())
    if (!(mqtt ? ['mqtt:', 'mqtts:'] : ['http:', 'https:']).includes(url.protocol) || !url.hostname || url.username || url.password || url.hash) return null
    if (mqtt && ((!['', '/'].includes(url.pathname)) || url.search)) return null
    return url
  } catch { return null }
}

function errorMessage(reason: unknown, fallback: string) {
  if (!(reason instanceof Error)) return fallback
  if (reason instanceof ApiRequestError && reason.status === 503 && reason.message.toLowerCase().includes('secret')) return `${reason.message}. Add the reference name to SPEECH_TO_TEXT_DESTINATION_SECRET_REFS in the backend environment.`
  if (reason instanceof ApiRequestError && reason.status === 503 && reason.message.toLowerCase().includes('resource')) return `${reason.message}. Add the reference name to SPEECH_TO_TEXT_DESTINATION_RESOURCE_REFS in the backend environment.`
  return reason.message || fallback
}
