import { useCallback, useEffect, useRef, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { api, ApiRequestError } from '../../shared/api'
import type { Transcription, WorkflowEvent } from '../../shared/types'
import { ApiError, Loading } from '../../shared/States'
import { statusLabel } from '../dashboard/Dashboard'

const terminal = (s: string) => ['completed', 'failed', 'stopped', 'not-found'].includes(s)
const active = (s: string) => ['queued', 'running', 'recording', 'stopping'].includes(s)
const fallbackTimeZones = [
  'Africa/Cairo', 'America/Chicago', 'America/Denver', 'America/Los_Angeles', 'America/New_York',
  'America/Sao_Paulo', 'Asia/Bangkok', 'Asia/Dubai', 'Asia/Hong_Kong', 'Asia/Kolkata',
  'Asia/Seoul', 'Asia/Shanghai', 'Asia/Singapore', 'Asia/Tokyo', 'Australia/Sydney',
  'Europe/Berlin', 'Europe/London', 'Europe/Paris', 'Pacific/Auckland',
]

function supportedTimeZones(localTimeZone: string) {
  const intlWithTimeZones = Intl as typeof Intl & { supportedValuesOf?: (key: 'timeZone') => string[] }
  const available = intlWithTimeZones.supportedValuesOf?.('timeZone') ?? fallbackTimeZones
  return [...new Set(['UTC', ...available, localTimeZone])].sort((left, right) => {
    if (left === 'UTC') return -1
    if (right === 'UTC') return 1
    return left.localeCompare(right)
  })
}
const localTimeZone = Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC'
const timeZones = supportedTimeZones(localTimeZone)

export default function RunDetail() {
  const { workflowId = '' } = useParams()
  const [run, setRun] = useState<Transcription | null>(null)
  const [events, setEvents] = useState<WorkflowEvent[]>([])
  const [error, setError] = useState('')
  const [workflowMissing, setWorkflowMissing] = useState(false)
  const [loading, setLoading] = useState(true)
  const [stopping, setStopping] = useState(false)
  const [timeZone, setTimeZone] = useState(localTimeZone)
  const cursorRef = useRef(0)
  const statusRef = useRef('')
  const workflowRef = useRef({ id: workflowId, generation: 0 })
  const inFlightRef = useRef<{ id: string; generation: number; promise: Promise<void> } | null>(null)
  if (workflowRef.current.id !== workflowId) {
    workflowRef.current = { id: workflowId, generation: workflowRef.current.generation + 1 }
    cursorRef.current = 0
    statusRef.current = ''
  }
  const load = useCallback(async () => {
    const { id, generation } = workflowRef.current
    const pending = inFlightRef.current
    if (pending?.id === id && pending.generation === generation) return pending.promise
    const isCurrent = () => workflowRef.current.id === id && workflowRef.current.generation === generation
    const request = (async () => {
      try {
        const status = await api.transcription(id)
        if (!isCurrent()) return
        setRun(status)
        statusRef.current = status.status
        setError('')
        setWorkflowMissing(false)

        const page = await api.events(id, cursorRef.current)
        if (!isCurrent()) return
        cursorRef.current = Math.max(cursorRef.current, page.next_cursor)
        if (page.events.length) {
          setEvents(current => {
            if (!isCurrent()) return current
            const byCursor = new Map(current.map(event => [event.cursor, event]))
            for (const event of page.events) {
              if (!byCursor.has(event.cursor)) byCursor.set(event.cursor, event)
            }
            return [...byCursor.values()].sort((left, right) => left.cursor - right.cursor)
          })
        }
      } catch (e) {
        if (!isCurrent()) return
        const missing = e instanceof ApiRequestError && e.status === 404
        setWorkflowMissing(missing)
        if (missing) {
          statusRef.current = 'not-found'
          setRun(null)
        }
        setError(e instanceof Error ? e.message : 'Could not load this workflow.')
      } finally {
        if (isCurrent()) setLoading(false)
      }
    })()
    inFlightRef.current = { id, generation, promise: request }
    try { await request }
    finally {
      if (inFlightRef.current?.promise === request) inFlightRef.current = null
    }
  }, [workflowId])
  useEffect(() => {
    setRun(null)
    setEvents([])
    setError('')
    setWorkflowMissing(false)
    setLoading(true)
    setStopping(false)
    cursorRef.current = 0
    statusRef.current = ''
    void load()
    const timer = window.setInterval(() => { if (!statusRef.current || !terminal(statusRef.current)) void load() }, 1800)
    return () => window.clearInterval(timer)
  }, [load])
  const stop = async () => { setStopping(true); try { await api.stop(workflowId); await load() } catch (e) { setError(e instanceof Error ? e.message : 'Could not stop this workflow.') } finally { setStopping(false) } }
  if (loading) return <div className="page-content"><Loading label="Loading workflow details…" /></div>
  return <div className="page-content run-detail-page">
    {error && !workflowMissing && <ApiError message={error} retry={() => { setLoading(true); void load() }} />}
    <Link className="back-link" to="/">← Back to overview</Link>
    {run ? <><div className="page-heading run-heading"><div><div className="eyebrow">WORKFLOW / {run.kind === 'microphone' ? 'MICROPHONE' : 'CLIP'}</div><h1>{run.profile_name || (run.kind === 'microphone' ? 'Live microphone' : 'Audio clip')}</h1><p>Started {new Date(run.created_at).toLocaleString('en-US')} · {run.device || 'Default input'}</p></div><div className="run-heading-actions"><span className={`status-pill status-${run.status}`}>{active(run.status) && <i />} {statusLabel(run.status)}</span>{run.kind === 'microphone' && active(run.status) && <button className="button button-outline danger-button" disabled={stopping || !!error} onClick={() => void stop()}>{stopping ? 'Stopping…' : 'Stop listening'}</button>}</div></div>
      <div className="run-kpis"><div><small>WORKFLOW ID</small><b>{run.workflow_id}</b></div><div><small>MODEL</small><b>{run.model || 'Default'}</b></div><div><small>RUNTIME</small><b>{run.runtime || '—'}</b></div><div><small>MODEL MODE</small><b>{run.execution_mode === 'per_workflow_process' ? 'Separate process' : run.execution_mode ? 'Shared model' : '—'}</b></div><div><small>KEYWORDS</small><b>{run.keywords.length} total</b></div></div>
      {active(run.status) && <section className="run-rtf" aria-labelledby="run-rtf-heading" aria-live="polite">
        <div>
          <h2 id="run-rtf-heading">Realtime factor (RTF)</h2>
          <p>{run.latest_rtf !== null && Number.isFinite(run.latest_rtf)
            ? <><strong>{run.latest_rtf.toFixed(2)}</strong> · {run.latest_rtf <= 1 ? 'Within real time' : 'Slower than real time'}</>
            : 'Waiting for first measurement'}</p>
        </div>
        <small>RTF ≤ 1.0 is within real time; RTF &gt; 1.0 is slower than real time.</small>
      </section>}
      {run.error && <div className="run-error"><b>This workflow encountered an error</b><p>{run.error}</p></div>}
      <div className="run-content-grid"><section className="panel transcript-panel"><div className="section-heading compact"><div><div className="eyebrow">RECOGNIZED TEXT</div><h2>Transcript</h2></div><span className="transcript-language">TRANSCRIPT · TH</span></div>{run.transcript ? <p className="transcript-text">{run.transcript}</p> : <div className="transcript-placeholder"><span className="placeholder-wave">〰</span><span>{active(run.status) ? 'Waiting for recognized speech…' : run.status === 'failed' ? 'This workflow failed before producing a transcript.' : 'No transcript is available for this workflow.'}</span></div>}<div className="match-heading"><div><div className="eyebrow">KEYWORD TRACKING</div><h3>Matched keywords</h3></div></div>{run.matches?.length ? <div className="match-list">{run.matches.map(m => <div key={m.keyword}><span>{m.keyword}</span><b>{m.count}<small> matches</small></b></div>)}</div> : <p className="muted-copy">Keyword results will appear here when available.</p>}</section><section className="panel event-panel"><div className="section-heading compact event-heading"><div><div className="eyebrow">EVENT STREAM</div><h2>Events</h2></div><div className="event-heading-controls"><label className="event-timezone-label" htmlFor="event-timezone">Time zone</label><select id="event-timezone" className="event-timezone-select" value={timeZone} onChange={event => setTimeZone(event.target.value)}>{timeZones.map(zone => <option key={zone} value={zone}>{zone}</option>)}</select><span className="event-count" aria-label={`${events.length} events`}>{events.length}</span></div></div><div className="timeline">{events.length ? events.map((event, i) => <div className="timeline-item" key={`${event.cursor}-${i}`}><span className={`timeline-dot ${event.type === 'workflow_error' ? 'dot-error' : ''}`} /><div><strong>{eventLabel(event.type)}</strong><small>#{event.cursor} · {eventTime(event, timeZone)}</small>{event.type === 'transcript' && typeof event.text === 'string' && <p>{event.text}</p>}{event.type === 'workflow_error' && typeof event.error === 'string' && <p>{event.error}</p>}</div></div>) : <div className="muted-copy">Waiting for the first event…</div>}</div></section></div>
    </> : <div className="empty-state"><div className="empty-icon">⌁</div><strong>Workflow not found</strong>{workflowMissing ? <p role="status">This workflow is not in the current backend history. Run status and events are kept in backend process memory, so a backend restart or another backend instance can make this run unavailable.</p> : <p>The backend may have removed this workflow from its temporary history.</p>}<Link className="button button-dark" to="/">Back to overview</Link></div>}
  </div>
}

function eventLabel(type: string) { const names: Record<string, string> = { queued: 'Queued', started: 'Started', recording: 'Listening', audio_queued: 'Audio queued', measurement: 'Measurement', transcript: 'Transcript received', match_results: 'Keywords matched', stopping: 'Stopping', stopped: 'Stopped', completed: 'Workflow completed', workflow_error: 'Workflow error' }; return names[type] || type }
function eventTime(event: WorkflowEvent, timeZone: string) {
  const meaningfulTime = event.type === 'audio_queued'
    ? event.first_queued_at
    : event.type === 'measurement'
      ? event.completed_at
      : undefined
  const value = meaningfulTime || event.timestamp || event.created_at
  if (typeof value !== 'string') return 'Tracking'
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? 'Tracking' : date.toLocaleString('en-US', { timeZone })
}
