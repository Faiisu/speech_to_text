import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { api, ApiRequestError } from '../../shared/api'
import type { ModelChoice, StressEvent, StressTest, StressTrial, Transcription } from '../../shared/types'
import TrialEvidence from './TrialEvidence'
import './stress-tests.css'

const STORAGE_KEY = 'echodesk.lastStressTestId'
const counts = [1, 2, 4]
const topologies = [
  { key: 'shared-model', title: 'Shared model', caption: 'One model-owning process' },
  { key: 'per-input-model', title: 'Per workflow process', caption: 'A model copy for each workflow' },
]
const isActive = (status: string) => status === 'queued' || status === 'running'
type CellState = { state: string; completed?: number; elapsed?: number; reason?: string }

function formatSeconds(value: unknown) {
  return typeof value === 'number' && Number.isFinite(value) ? `${value.toFixed(2)} s` : '—'
}
function cellKey(topology: string, count: number) { return `${topology}:${count}` }
function statusForTrial(trial: StressTrial) {
  if (trial.status === 'unavailable' || trial.capacity_verdict === 'unavailable') return 'unavailable'
  if (trial.capacity_verdict === 'pass') return 'capacity pass'
  if (trial.capacity_verdict === 'fail') return 'capacity failed'
  return trial.status
}

export default function StressTests() {
  const [catalog, setCatalog] = useState<{ models: ModelChoice[]; default_model: string; default_runtime: string } | null>(null)
  const [selectedModel, setSelectedModel] = useState('')
  const [selectedRuntime, setSelectedRuntime] = useState('')
  const [selectedPrecision, setSelectedPrecision] = useState('')
  const [runs, setRuns] = useState<Transcription[]>([])
  const [run, setRun] = useState<StressTest | null>(null)
  const [stressId, setStressId] = useState('')
  const [cells, setCells] = useState<Record<string, CellState>>({})
  const [loading, setLoading] = useState(true)
  const [prerequisitesReady, setPrerequisitesReady] = useState(false)
  const [starting, setStarting] = useState(false)
  const [missing, setMissing] = useState(false)
  const [gapNotice, setGapNotice] = useState(false)
  const [error, setError] = useState('')
  const [announcement, setAnnouncement] = useState('')
  const cursor = useRef(0)
  const currentRunId = useRef('')
  const inFlight = useRef<{ id: string; promise: Promise<void> } | null>(null)
  const activeMic = useMemo(() => runs.find(item => item.kind === 'microphone' && ['starting', 'queued', 'running', 'recording', 'stopping'].includes(item.status)), [runs])
  const model = catalog?.models.find(item => item.key === selectedModel)
  const runtimes = model?.runtimes ?? []
  const runtime = runtimes.find(item => item.key === selectedRuntime)
  const precisionOptions = runtime?.precision_options ?? []
  const matrixBusy = !!stressId && (!run || isActive(run.status))
  const canStart = prerequisitesReady && !loading && !starting && !!catalog && !!selectedModel && !!runtime?.compatible && !activeMic && !matrixBusy && (!selectedPrecision || precisionOptions.includes(selectedPrecision))

  const refreshPrerequisites = useCallback(async () => {
    try {
      const [modelData, transcriptionRuns] = await Promise.all([api.models(), api.transcriptions()])
      setCatalog(modelData)
      setRuns(transcriptionRuns)
      setSelectedModel(current => current || modelData.default_model)
      setSelectedRuntime(current => current || modelData.default_runtime)
      setPrerequisitesReady(true)
    } catch (e) {
      setPrerequisitesReady(false)
      throw e
    }
  }, [])

  const loadRun = useCallback(async (id: string) => {
    if (currentRunId.current !== id) return
    if (inFlight.current?.id === id) return inFlight.current.promise
    const isCurrent = () => currentRunId.current === id
    const request = (async () => {
    try {
      const status = await api.stressTest(id)
      if (!isCurrent()) return
      setRun(status)
      setMissing(false)
      setError('')
      const page = await api.stressEvents(id, cursor.current)
      if (!isCurrent()) return
      cursor.current = Math.max(cursor.current, page.next_cursor)
      if (page.events.length) {
        setCells(current => {
          const next = { ...current }
          for (const event of page.events) {
            if (!event.topology || !event.workflow_count) continue
            const key = cellKey(event.topology, event.workflow_count)
            if (event.type === 'trial_started') next[key] = { state: 'preparing' }
            if (event.type === 'trial_progress') next[key] = { state: 'replaying', completed: event.completed_workflows, elapsed: event.elapsed_seconds }
            if (event.type === 'trial_unavailable') next[key] = { state: 'unavailable', reason: event.reason }
            if (event.type === 'trial_completed') next[key] = { state: event.capacity_verdict === 'pass' ? 'capacity pass' : 'capacity failed' }
          }
          return next
        })
      }
      if (status.report) setCells(trialCells(status.report))
      if (!isActive(status.status)) setAnnouncement(`Capacity matrix ${status.status}.`)
    } catch (e) {
      if (!isCurrent()) return
      if (e instanceof ApiRequestError && e.status === 410) {
        const detail = e.detail as { oldest_cursor?: unknown } | null
        const oldest = Number(detail && typeof detail === 'object' ? detail.oldest_cursor : NaN)
        setGapNotice(true)
        try {
          const status = await api.stressTest(id)
          if (!isCurrent()) return
          setRun(status)
          if (Number.isFinite(oldest) && oldest > 0) cursor.current = oldest - 1
          else cursor.current = 0
          const page = await api.stressEvents(id, cursor.current)
          if (!isCurrent()) return
          cursor.current = Math.max(cursor.current, page.next_cursor)
          setCells(status.report ? trialCells(status.report) : current => applyEvents(current, page.events))
        } catch (recoveryError) {
          if (!isCurrent()) return
          if (recoveryError instanceof ApiRequestError && recoveryError.status === 404) {
            setMissing(true)
            setRun(null)
            setStressId('')
            currentRunId.current = ''
            localStorage.removeItem(STORAGE_KEY)
            setLoading(false)
          }
          setError(recoveryError instanceof Error ? recoveryError.message : 'Could not recover the stress-test event stream.')
        }
      } else if (e instanceof ApiRequestError && e.status === 404) {
        setMissing(true)
        setRun(null)
        setStressId('')
        currentRunId.current = ''
        localStorage.removeItem(STORAGE_KEY)
        setLoading(false)
      } else setError(e instanceof Error ? e.message : 'Could not load this capacity matrix.')
    } finally {
      if (isCurrent()) setLoading(false)
    }
    })()
    inFlight.current = { id, promise: request }
    try { await request }
    finally { if (inFlight.current?.promise === request) inFlight.current = null }
  }, [])

  useEffect(() => {
    void refreshPrerequisites().catch(e => setError(e instanceof Error ? e.message : 'Could not load model settings.')).finally(() => setLoading(false))
    const saved = localStorage.getItem(STORAGE_KEY)
    if (saved) {
      currentRunId.current = saved
      setStressId(saved)
      setLoading(true)
      void loadRun(saved)
    }
  }, [loadRun, refreshPrerequisites])

  useEffect(() => {
    if (!stressId || (run && !isActive(run.status))) return
    const timer = window.setInterval(() => void loadRun(stressId), 1400)
    return () => window.clearInterval(timer)
  }, [stressId, run?.status, loadRun])

  useEffect(() => {
    const timer = window.setInterval(() => {
      void refreshPrerequisites().catch(e => setError(e instanceof Error ? e.message : 'Could not refresh microphone status.'))
    }, 5000)
    return () => window.clearInterval(timer)
  }, [refreshPrerequisites])

  const start = async () => {
    setStarting(true)
    setError('')
    try {
      const settings: { model?: string; runtime?: string; precision?: string } = {}
      if (selectedModel && selectedModel !== catalog?.default_model) settings.model = selectedModel
      if (selectedRuntime && selectedRuntime !== catalog?.default_runtime) settings.runtime = selectedRuntime
      if (selectedPrecision) settings.precision = selectedPrecision
      const started = await api.startStressTest(settings)
      localStorage.setItem(STORAGE_KEY, started.stress_test_id)
      currentRunId.current = started.stress_test_id
      setStressId(started.stress_test_id)
      cursor.current = 0
      setCells(emptyCells('queued'))
      setMissing(false)
      setGapNotice(false)
      setRun(null)
      setAnnouncement('Capacity matrix queued.')
      await loadRun(started.stress_test_id)
    } catch (e) {
      if (e instanceof ApiRequestError && e.status === 409) {
        await refreshPrerequisites().catch(() => undefined)
        setError(`${e.message}. Refresh the workflow list and clear the active microphone or stress run before starting.`)
      } else setError(e instanceof Error ? e.message : 'Could not start the capacity matrix.')
    } finally { setStarting(false) }
  }

  return <div className="page-content stress-page">
    {error && <div className="stress-error" role="alert"><b>Capacity matrix unavailable</b><span>{error}</span><button className="button button-outline" onClick={() => { void refreshPrerequisites().catch(e => setError(e instanceof Error ? e.message : 'Could not refresh microphone status.')); if (stressId) void loadRun(stressId) }}>Refresh</button></div>}
    {gapNotice && <p className="stress-gap" role="status">Some earlier progress events expired. The current report and retained events are shown.</p>}
    {missing && <div className="stress-missing" role="status"><b>This run is no longer available.</b><span>The backend restarted or evicted its bounded history. Start a new matrix to continue.</span></div>}
    <header className="stress-heading">
      <div><div className="eyebrow">CAPACITY LAB <span className="stress-heading-rule" /></div><h1>Capacity lab</h1><p>Replay a fixed speech clip at real time to see how each process topology handles concurrent workflows.</p></div>
      <div className="stress-retention"><span className="retention-mark">⌁</span><span><b>LOCAL RUN HISTORY</b><small>Retained by this backend process</small></span></div>
    </header>

    <section className="stress-setup" aria-label="Stress test setup">
      <div className="workload-facts"><div className="facts-head"><span className="eyebrow">FIXED WORKLOAD</span><span className="workload-wav">WAV / PCM16</span></div><div className="wav-filename"><span aria-hidden="true">♫</span><strong>test-audio.wav</strong></div><div className="facts-grid"><div><small>DURATION</small><b>29.33 <i>s</i></b></div><div><small>PLAYBACK</small><b>2 <i>loops</i></b></div><div><small>CHUNK</small><b>30 <i>s</i></b></div></div></div>
      <div className="stress-settings">
        <div className="settings-heading"><div><span className="eyebrow">CONFIGURATION</span><h2>Model settings</h2></div><span className="backend-default">BACKEND DEFAULTS</span></div>
        <div className="settings-controls">
          <label>Model<select disabled={!!run && isActive(run.status)} value={selectedModel} onChange={event => { setSelectedModel(event.target.value); const next = catalog?.models.find(item => item.key === event.target.value); if (!next?.runtimes.some(entry => entry.key === selectedRuntime && entry.compatible)) setSelectedRuntime(next?.runtimes.find(entry => entry.compatible)?.key ?? '') ; setSelectedPrecision('') }}>{catalog?.models.map(item => <option key={item.key} value={item.key}>{item.display_name}{!item.installed ? ' · not installed' : ''}</option>)}</select></label>
          <label>Runtime<select disabled={!!run && isActive(run.status)} value={selectedRuntime} onChange={event => { setSelectedRuntime(event.target.value); setSelectedPrecision('') }}>{runtimes.map(item => <option key={item.key} value={item.key} disabled={!item.compatible}>{item.key}{!item.compatible ? ' · incompatible' : !item.ready ? ' · unavailable' : ''}</option>)}</select><small>{runtime && (!runtime.compatible ? runtime.reason || 'This runtime is incompatible.' : !runtime.ready ? `${runtime.reason || 'Runtime is not ready.'} Affected trials may be unavailable.` : 'Compatible and ready on this host.')}</small></label>
          <label>Precision<select disabled={!!run && isActive(run.status)} value={selectedPrecision} onChange={event => setSelectedPrecision(event.target.value)}><option value="">Backend default</option>{precisionOptions.map(option => <option key={option} value={option}>{option}</option>)}</select><small>Overrides are sent only when selected.</small></label>
        </div>
        <div className="settings-footer"><span>One matrix at a time · no cancel action</span><button className="button button-lime stress-start" onClick={() => void start()} disabled={!canStart} aria-label="Run capacity matrix">{starting ? 'Queueing…' : matrixBusy ? 'Checking matrix' : run && isActive(run.status) ? 'Matrix running' : 'Run capacity matrix'} <span>↗</span></button></div>
      </div>
    </section>

    {activeMic && <div className="stress-blocker" role="status"><span className="blocker-icon">Ⅱ</span><div><b>Microphone workflow is active</b><p>Stop it before measuring capacity. This run is using {activeMic.device || 'the default input'}.</p></div><Link className="button button-outline" to={`/runs/${activeMic.workflow_id}`}>Open workflow ↗</Link></div>}

    <section className="matrix-section" aria-labelledby="matrix-title">
      <div className="matrix-heading"><div><span className="eyebrow">TWO TOPOLOGIES / SIX COLD START TRIALS</span><h2 id="matrix-title">The workload matrix</h2></div><div className="matrix-legend"><span><i className="legend-ready" />Ready</span><span><i className="legend-active" />In progress</span><span><i className="legend-failed" />Failed</span></div></div>
      <div className="matrix-board">{topologies.map((topology, column) => <section className={`topology-column topology-${column}`} key={topology.key} aria-label={topology.title}>
        <header><span className="topology-glyph" aria-hidden="true">{column === 0 ? '◉' : '◌'}</span><div><h3>{topology.title}</h3><p>{topology.caption}</p></div><small>{column === 0 ? '01 MODEL' : 'N MODELS'}</small></header>
        <div className="trial-list">{counts.map(count => {
          const trial = run?.report?.[topology.key]?.trials.find(item => item.workflow_count === count)
          const progress = cells[cellKey(topology.key, count)] ?? { state: matrixBusy ? 'queued' : 'not started' }
          const state = trial ? statusForTrial(trial) : progress.state
          const reason = trial?.unavailable_reason || progress.reason
          return <article className={`trial-cell cell-${state.replaceAll(' ', '-')}`} key={count}>
            <div className="trial-count"><strong>{count}</strong><span>{count === 1 ? 'workflow' : 'workflows'}</span></div>
            <div className="trial-content"><div className="trial-state"><span className="state-mark" aria-hidden="true" />{state}</div>{trial ? <p>{trial.status === 'completed' ? `${trial.dropped_or_failed_chunks ?? 0} dropped or failed chunks` : reason || 'This trial could not run on the host.'}</p> : state === 'replaying' ? <p>Per-trial replay · {progress.completed ?? 0} of {count} complete · {formatSeconds(progress.elapsed)} elapsed</p> : state === 'preparing' ? <p>Preparing model and input workflows</p> : state === 'unavailable' ? <p>{reason || 'This trial could not run on the host.'}</p> : state === 'capacity pass' || state === 'capacity failed' ? <p>Trial completed · waiting for final report</p> : state === 'queued' && matrixBusy ? <p>Waiting for this trial to start</p> : <p>{state === 'not started' ? 'Waiting for a capacity matrix' : 'Waiting for this trial to start'}</p>}</div>
            {trial && <div className="trial-verdict">{trial.capacity_verdict === 'pass' ? 'PASS' : trial.capacity_verdict === 'fail' ? 'FAIL' : trial.capacity_verdict === 'unavailable' ? 'N/A' : '—'}</div>}
          </article>
        })}</div>
      </section>)}</div>
      <div className="stress-live" aria-live="polite" aria-atomic="true">{announcement}</div>
      {stressId && <div className="matrix-run-meta"><span>Run {stressId}</span>{run ? <><span className={`status-pill status-${run.status}`}>{run.status}</span><span>{run.model} · {run.runtime} · {run.precision}</span></> : <span className="status-pill status-running">checking status</span>}{(!run || !isActive(run.status)) && <button className="button button-outline" onClick={() => void start()} disabled={!canStart}>Run again ↻</button>}</div>}
    </section>

    {run?.status === 'failed' && run.error && <div className="stress-error" role="alert"><b>Stress workflow failed</b><span>{run.error}</span></div>}

    {run?.report && <TrialEvidence report={run.report} />}
  </div>
}

function emptyCells(state = 'not started'): Record<string, CellState> {
  return Object.fromEntries(topologies.flatMap(topology => counts.map(count => [cellKey(topology.key, count), { state }])))
}
function trialCells(report: NonNullable<StressTest['report']>): Record<string, CellState> {
  const cells = emptyCells()
  for (const topology of topologies) for (const trial of report[topology.key]?.trials ?? []) {
    cells[cellKey(topology.key, trial.workflow_count)] = { state: statusForTrial(trial), reason: trial.unavailable_reason ?? undefined }
  }
  return cells
}
function applyEvents(current: Record<string, CellState>, events: StressEvent[]) {
  const next = { ...current }
  for (const event of events) {
    if (!event.topology || !event.workflow_count) continue
    const key = cellKey(event.topology, event.workflow_count)
    if (event.type === 'trial_started') next[key] = { state: 'preparing' }
    if (event.type === 'trial_progress') next[key] = { state: 'replaying', completed: event.completed_workflows, elapsed: event.elapsed_seconds }
    if (event.type === 'trial_unavailable') next[key] = { state: 'unavailable', reason: event.reason }
    if (event.type === 'trial_completed') next[key] = { state: event.capacity_verdict === 'pass' ? 'capacity pass' : 'capacity failed' }
  }
  return next
}
