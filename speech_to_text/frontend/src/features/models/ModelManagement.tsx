import { useCallback, useEffect, useRef, useState, type FormEvent } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../../shared/api'
import type { ModelChoice, ModelDeletionPlan, ModelJob, ModelInspection, ModelRuntime } from '../../shared/types'
import './models.css'

const active = (status: ModelJob['status']) => status === 'queued' || status === 'running'
const runtimeLabel = (runtime: string) => runtime === 'ctranslate2' ? 'CTranslate2' : runtime === 'transformers' ? 'Transformers (Gemma 4)' : runtime.replace(/^openvino-/, 'OpenVINO ').toUpperCase().replace('OPENVINO ', 'OpenVINO ')
const formatBytes = (bytes: number) => bytes < 1024 ? `${bytes} B` : bytes < 1024 ** 2 ? `${(bytes / 1024).toFixed(1)} KB` : `${(bytes / 1024 ** 2).toFixed(1)} MB`

export default function ModelManagement() {
  const [repository, setRepository] = useState('openai/whisper-tiny')
  const [revision, setRevision] = useState('main')
  const [modelKey, setModelKey] = useState('whisper-tiny')
  const [token, setToken] = useState('')
  const [inspection, setInspection] = useState<ModelInspection | null>(null)
  const [runtime, setRuntime] = useState('ctranslate2')
  const [precision, setPrecision] = useState('')
  const [models, setModels] = useState<ModelChoice[]>([])
  const [catalogSelection, setCatalogSelection] = useState<ModelChoice | null>(null)
  const [deletion, setDeletion] = useState<{ modelKey: string; plan: ModelDeletionPlan | null; loading: boolean; error: string; errorPhase: 'preview' | 'delete' | null } | null>(null)
  const [deletingModelKey, setDeletingModelKey] = useState<string | null>(null)
  const [jobs, setJobs] = useState<ModelJob[]>([])
  const [loading, setLoading] = useState(true)
  const [inspecting, setInspecting] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState('')
  const polling = useRef(false)
  const inspectionVersion = useRef(0)
  const repositoryInput = useRef<HTMLInputElement>(null)
  const preparationPanel = useRef<HTMLElement>(null)

  const selectedRuntime = inspection?.runtimes.find(item => item.key === runtime)
  const selectedModelRuntime = models.find(item => item.key === modelKey.trim())?.runtimes.find(item => item.key === runtime)
  const defaultPrecision = selectedModelRuntime?.default_precision
    ?? models.flatMap(item => item.runtimes).find(item => item.key === runtime)?.default_precision
  const installedPrecisions = selectedModelRuntime?.installed_precisions ?? []
  const hasGenericArtifact = selectedModelRuntime?.has_generic_artifact ?? false
  const hasActiveJob = jobs.some(job => active(job.status))
  const canConvert = Boolean(inspection?.compatible && selectedRuntime?.available && !submitting && !hasActiveJob)
  const validModelKey = modelKey.trim().length <= 64 && /^[A-Za-z0-9][A-Za-z0-9._-]*$/.test(modelKey.trim()) && !modelKey.includes('..')

  const refreshCatalog = useCallback(async () => {
    const catalog = await api.models()
    setModels(catalog.models)
  }, [])

  const refreshJobs = useCallback(async () => {
    const response = await api.modelJobs()
    setJobs(response.jobs)
    return response.jobs
  }, [])

  const load = useCallback(async () => {
    setLoading(true)
    setError('')
    const results = await Promise.allSettled([api.models(), api.modelJobs()])
    const [catalog, jobList] = results
    if (catalog.status === 'fulfilled') setModels(catalog.value.models)
    if (jobList.status === 'fulfilled') setJobs(jobList.value.jobs)
    const failure = results.find(result => result.status === 'rejected')
    if (failure?.status === 'rejected') setError(failure.reason instanceof Error ? failure.reason.message : 'Could not load model management data.')
    setLoading(false)
  }, [])

  useEffect(() => { void load() }, [load])

  useEffect(() => {
    if (!jobs.some(job => active(job.status))) return
    const timer = window.setInterval(() => {
      if (polling.current) return
      polling.current = true
      void refreshJobs().then(updated => {
        if (updated.some(job => job.status === 'completed')) return refreshCatalog()
      }).catch(reason => setError(reason instanceof Error ? reason.message : 'Could not refresh preparation jobs.')).finally(() => { polling.current = false })
    }, 1200)
    return () => window.clearInterval(timer)
  }, [jobs, refreshCatalog, refreshJobs])

  const inspectRepository = async (requestedRepository: string, requestedRevision: string, requestToken = '', selectedModel: ModelChoice | null = catalogSelection) => {
    const requestVersion = ++inspectionVersion.current
    setInspecting(true)
    setError('')
    setInspection(null)
    setToken('')
    try {
      const result = await api.inspectModel({ repository: requestedRepository, revision: requestedRevision, ...(requestToken ? { token: requestToken } : {}) })
      if (requestVersion !== inspectionVersion.current) return
      setInspection(result)
      const preferredRuntime = selectedModel?.runtimes.find(item => item.key === runtime)?.key ?? runtime
      const suggested = result.runtimes.find(item => item.key === preferredRuntime && item.available) ?? result.runtimes.find(item => item.available) ?? result.runtimes[0]
      if (suggested) {
        setRuntime(suggested.key)
        const preferredPrecision = selectedModel?.runtimes.find(item => item.key === suggested.key)?.default_precision
        setPrecision(preferredPrecision && suggested.precision_options.includes(preferredPrecision) ? preferredPrecision : suggested.precision_options[0] ?? '')
      }
    } catch (reason) {
      if (requestVersion === inspectionVersion.current) setError(reason instanceof Error ? reason.message : 'Could not inspect this repository.')
    } finally {
      if (requestVersion === inspectionVersion.current) {
        setInspecting(false)
      }
    }
  }

  const inspect = async (event: FormEvent) => {
    event.preventDefault()
    if (inspecting) return
    await inspectRepository(repository.trim(), revision.trim() || 'main', token)
  }

  const editIdentity = (field: 'repository' | 'revision' | 'model_key', value: string) => {
    setCatalogSelection(null)
    if (field === 'repository') {
      inspectionVersion.current++
      setInspecting(false)
      setInspection(null)
      setRepository(value)
    } else if (field === 'revision') {
      inspectionVersion.current++
      setInspecting(false)
      setInspection(null)
      setRevision(value)
    } else setModelKey(value)
  }

  const selectCatalogModel = async (model: ModelChoice) => {
    if (!model.repository) return
    const pinnedRevision = model.revision || 'main'
    setCatalogSelection(model)
    setModelKey(model.key)
    setRepository(model.repository)
    setRevision(pinnedRevision)
    setToken('')
    setError('')
    setInspection(null)
    window.requestAnimationFrame(() => {
      preparationPanel.current?.scrollIntoView({ behavior: 'auto', block: 'start' })
      repositoryInput.current?.focus({ preventScroll: true })
    })
    await inspectRepository(model.repository, pinnedRevision, '', model)
  }

  const loadDeletionPlan = async (modelKey: string) => {
    setDeletion({ modelKey, plan: null, loading: true, error: '', errorPhase: null })
    try {
      const plan = await api.modelDeletionPlan(modelKey)
      setDeletion(current => current?.modelKey === modelKey ? { modelKey, plan, loading: false, error: '', errorPhase: null } : current)
    } catch (reason) {
      setDeletion(current => current?.modelKey === modelKey ? { ...current, loading: false, error: reason instanceof Error ? reason.message : 'Could not preview local model deletion.', errorPhase: 'preview' } : current)
    }
  }

  const confirmDelete = async () => {
    if (!deletion?.plan?.deletable || deletingModelKey) return
    const { modelKey } = deletion
    setDeletingModelKey(modelKey)
    setDeletion(current => current?.modelKey === modelKey ? { ...current, error: '', errorPhase: null } : current)
    try {
      const result = await api.deleteLocalModel(modelKey)
      if (!result.deleted) throw new Error('The server did not confirm model deletion.')
      if (catalogSelection?.key === modelKey) {
        inspectionVersion.current++
        setInspecting(false)
        setCatalogSelection(null)
        setInspection(null)
        setToken('')
        setModelKey('')
        setRepository('')
        setRevision('main')
      }
      setDeletion(null)
      try {
        await refreshCatalog()
      } catch (reason) {
        setError(reason instanceof Error ? `Model deleted, but the catalog could not refresh: ${reason.message}` : 'Model deleted, but the catalog could not refresh.')
      }
    } catch (reason) {
      setDeletion(current => current?.modelKey === modelKey ? { ...current, error: reason instanceof Error ? reason.message : 'Could not delete the local model.', errorPhase: 'delete' } : current)
    } finally {
      setDeletingModelKey(null)
    }
  }

  const startJob = async (operation: 'download' | 'convert') => {
    if (submitting || hasActiveJob || !validModelKey || !inspection?.compatible || (operation === 'convert' && !canConvert)) return
    const requestToken = token
    setSubmitting(true)
    setError('')
    setToken('')
    try {
      const job = await api.startModelJob({
        repository: repository.trim(), model_key: modelKey.trim(), revision: revision.trim() || 'main',
        ...(requestToken ? { token: requestToken } : {}), operation,
        ...(operation === 'convert' ? { runtime, precision } : {}),
      })
      setJobs(current => [job, ...current.filter(item => item.job_id !== job.job_id)])
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Could not start model preparation.')
    } finally {
      setToken('')
      setSubmitting(false)
    }
  }

  const runtimeChanged = (value: string) => {
    setRuntime(value)
    setPrecision(inspection?.runtimes.find(item => item.key === value)?.precision_options[0] ?? '')
  }

  return <div className="page-content models-page">
    <header className="models-heading">
      <div><div className="eyebrow">MODEL LIBRARY <span className="models-heading-rule" /></div><h1>Models</h1><p>Bring a supported speech model into this host, then prepare it for a transcription runtime.</p></div>
      <div className="models-heading-links"><Link to="/profiles">Audio profiles ↗</Link><Link to="/model-tuning">Model tuning ↗</Link></div>
    </header>

    {error && <div className="models-error" role="alert"><span>{error}</span><button className="button button-outline" onClick={() => { setError(''); void load() }}>Refresh</button></div>}

    <div className="models-workspace">
      <section ref={preparationPanel} className="models-preparation" aria-labelledby="model-source-heading">
        <div className="models-form-head"><div><div className="eyebrow">HUGGING FACE SOURCE</div><h2 id="model-source-heading">Prepare a model</h2><p>Inspection reads repository metadata and configuration only. Model weights are fetched when you start a job.</p></div><span className="models-whisper-mark" aria-hidden="true">W</span></div>
        <form onSubmit={event => void inspect(event)} className="models-form">
          <label htmlFor="model-repository">Repository ID<input ref={repositoryInput} id="model-repository" value={repository} onChange={event => editIdentity('repository', event.target.value)} placeholder="organization/whisper-checkpoint" required /></label>
          <div className="models-fields-two">
            <label htmlFor="model-revision">Revision<input id="model-revision" value={revision} onChange={event => editIdentity('revision', event.target.value)} placeholder="main" /></label>
          <label htmlFor="model-key">Local model key<input id="model-key" value={modelKey} onChange={event => editIdentity('model_key', event.target.value)} placeholder="whisper-tiny" required maxLength={64} pattern="[A-Za-z0-9][A-Za-z0-9._-]*" title="Use letters, numbers, dots, underscores, or hyphens; avoid '..'." /></label>
          </div>
          <label htmlFor="model-token">Access token <span className="models-optional">OPTIONAL</span><input id="model-token" type="password" autoComplete="off" value={token} onChange={event => setToken(event.target.value)} placeholder="hf_…" disabled={inspecting} /></label>
          <small className="models-token-note">Used for this request only. It clears after inspection or job submission; re-enter it for the next request. A server configured HF_TOKEN can be used without entering one here.</small>
          <div className="models-inspect-row"><button className="button button-dark" type="submit" disabled={inspecting || !repository.trim()}>{inspecting ? 'Inspecting…' : 'Inspect repository'}<span>↗</span></button><span>Whisper Transformers checkpoints</span></div>
        </form>

        {inspection && <section className={`models-inspection ${inspection.compatible ? 'is-compatible' : 'is-incompatible'}`} aria-label="Inspection result" aria-live="polite">
          <div className="models-inspection-title"><div><span className="eyebrow">SOURCE INSPECTION</span><h3>{inspection.compatible ? 'Whisper source is compatible' : 'This source cannot be prepared'}</h3></div><span className="models-inspection-badge">{inspection.compatible ? 'SUPPORTED' : 'UNSUPPORTED'}</span></div>
          <dl className="models-source-meta"><div><dt>Repository</dt><dd>{inspection.repository}</dd></div><div><dt>Resolved revision</dt><dd>{inspection.revision}</dd></div><div><dt>Model type</dt><dd>{inspection.model_type || 'Unknown'}</dd></div></dl>
          {inspection.reasons.length > 0 && <ul className="models-reasons">{inspection.reasons.map(reason => <li key={reason}>{reason}</li>)}</ul>}
          <div className="models-runtime-list">{inspection.runtimes.map(item => <RuntimeStatus key={item.key} runtime={item} selected={runtime === item.key} onSelect={() => runtimeChanged(item.key)} />)}</div>
          <p className="models-verification-note">Format compatibility checks the source and installed runtime. Only a completed conversion with a passing local inference check is marked runtime-verified; neither check measures recognition accuracy.</p>
        </section>}
      </section>

      <aside className="models-actions-panel" aria-label="Preparation options">
        <div className="eyebrow">PREPARATION</div><h2>Choose what to do</h2>
        <p>Downloading stores source weights. Runtime preparation verifies a local inference; Gemma 4 uses its Transformers source files without conversion.</p>
        <label htmlFor="model-runtime">Target runtime<select id="model-runtime" value={runtime} onChange={event => runtimeChanged(event.target.value)} disabled={!inspection}>{inspection?.runtimes.map(item => <option key={item.key} value={item.key}>{runtimeLabel(item.key)}{item.available ? '' : ' · unavailable'}</option>) ?? <option value="ctranslate2">CTranslate2</option>}</select></label>
        <label htmlFor="model-precision">Precision<select id="model-precision" value={precision} onChange={event => setPrecision(event.target.value)} disabled={!selectedRuntime?.available || !selectedRuntime.precision_options.length}>{selectedRuntime?.precision_options.length ? selectedRuntime.precision_options.map(option => <option key={option} value={option}>{option}</option>) : <option value="">{inspection ? 'No precision available' : 'Inspect to see precision options'}</option>}</select></label>
        {selectedRuntime && <p className={selectedRuntime.available ? 'models-runtime-note' : 'models-runtime-note unavailable'}>{selectedRuntime.available ? `Available on this host · ${selectedRuntime.precision_options.join(', ')}` : selectedRuntime.reason || 'This runtime is unavailable on this host.'}</p>}
        {selectedRuntime?.available && defaultPrecision && precision !== defaultPrecision && <p className={installedPrecisions.includes(defaultPrecision) ? 'models-precision-note' : 'models-precision-note warning'}>Workflow default: <b>{defaultPrecision}</b>. This job prepares <b>{precision}</b>{installedPrecisions.includes(defaultPrecision) ? '; the default precision is already installed.' : `; available installed precisions: ${hasGenericArtifact ? 'not recorded (legacy artifact)' : installedPrecisions.join(', ') || 'none'}.`}</p>}
        <div className="models-job-actions">{!catalogSelection && <button className="button button-outline" disabled={!inspection?.compatible || !validModelKey || submitting || hasActiveJob} onClick={() => void startJob('download')}>{submitting ? 'Starting…' : 'Download source'}</button>}<button className="button button-lime" disabled={!canConvert || !validModelKey} onClick={() => void startJob('convert')}>{submitting ? 'Starting…' : catalogSelection ? 'Prepare runtime' : runtime === 'transformers' ? 'Download and verify' : 'Download and convert'}</button></div>
        {!validModelKey && <small className="models-key-error" role="status">Use a local key of at most 64 letters, numbers, dots, underscores, or hyphens, without '..'.</small>}
        {hasActiveJob && <small className="models-action-hint" role="status">Wait for the active preparation job to finish before starting another.</small>}
        {!inspection && <small className="models-action-hint">Inspect a repository to enable preparation.</small>}
      </aside>
    </div>

    <section className="models-jobs-section" aria-labelledby="models-jobs-heading">
      <div className="models-section-heading"><div><div className="eyebrow">HOST ACTIVITY</div><h2 id="models-jobs-heading">Preparation jobs</h2></div><button className="button button-outline" onClick={() => { void refreshJobs().catch(reason => setError(reason instanceof Error ? reason.message : 'Could not refresh jobs.')) }}>Refresh jobs</button></div>
      {loading ? <p className="models-empty">Loading model jobs…</p> : jobs.length === 0 ? <p className="models-empty">No preparation jobs yet. Inspect a Whisper repository to begin.</p> : <div className="models-job-list">{jobs.map(job => <JobCard key={job.job_id} job={job} />)}</div>}
    </section>

    <section className="models-catalog-section" aria-labelledby="models-catalog-heading">
      <div className="models-section-heading"><div><div className="eyebrow">LOCAL CATALOG</div><h2 id="models-catalog-heading">Installed models</h2></div><button className="button button-outline" onClick={() => { void refreshCatalog().catch(reason => setError(reason instanceof Error ? reason.message : 'Could not refresh model catalog.')) }}>Refresh catalog</button></div>
      {loading ? <p className="models-empty">Loading installed models…</p> : <div className="models-catalog-list">{models.map(model => <article className="models-catalog-item" key={model.key}>
        <div className="models-catalog-name"><strong>{model.display_name}</strong><code>{model.key}</code></div>
        <span className={`models-readiness ${model.ready ? 'ready' : 'not-ready'}`}><i />{model.ready ? 'READY' : model.installed ? 'NOT READY' : 'NOT PREPARED'}</span>
        <div className="models-catalog-runtimes">{model.runtimes.map(item => { const installed = item.installed_precisions ?? []; const genericArtifact = item.has_generic_artifact ?? false; const defaultMissing = !genericArtifact && item.default_precision && !installed.includes(item.default_precision); return <div key={item.key} className="models-catalog-runtime-row"><span className={item.ready ? 'ready' : ''}>{runtimeLabel(item.key)} · {item.ready ? 'ready' : item.reason || 'not prepared'}</span><small>Installed precisions: {genericArtifact ? 'not recorded (legacy artifact)' : installed.join(', ') || 'none'}</small>{defaultMissing && <small className="models-default-warning">Workflow default: {item.default_precision}; available: {installed.join(', ') || 'none'}</small>}</div> })}</div>
        <div className="models-catalog-action">
          {model.repository ? <button className="button button-outline" type="button" onClick={() => void selectCatalogModel(model)}>{model.installed ? 'Prepare another runtime' : 'Convert model'}</button> : <button className="button button-outline" type="button" disabled title="This model has no saved source repository.">Preparation unavailable · source unknown</button>}
          <button className="button button-danger-outline" type="button" disabled={Boolean(deletingModelKey) || deletion?.loading} onClick={() => void loadDeletionPlan(model.key)}>{deletingModelKey === model.key ? 'Deleting…' : deletion?.modelKey === model.key && deletion.loading ? 'Checking…' : 'Delete local model'}</button>
        </div>
        {deletion?.modelKey === model.key && <DeletionConfirmation
          modelKey={model.key}
          plan={deletion.plan}
          loading={deletion.loading}
          error={deletion.error}
          errorPhase={deletion.errorPhase}
          deleting={deletingModelKey === model.key}
          onCancel={() => setDeletion(null)}
          onRetryPreview={() => void loadDeletionPlan(model.key)}
          onConfirm={() => void confirmDelete()}
        />}
      </article>)}</div>}
      <p className="models-catalog-note">Ready models can be selected in <Link to="/profiles">Audio profiles</Link> and <Link to="/model-tuning">Model tuning</Link>. Active transcription workflows keep their current model settings.</p>
    </section>
  </div>
}

function RuntimeStatus({ runtime, selected, onSelect }: { runtime: ModelRuntime; selected: boolean; onSelect: () => void }) {
  return <button type="button" className={`models-runtime-choice ${selected ? 'selected' : ''} ${runtime.available ? '' : 'unavailable'}`} onClick={onSelect} aria-pressed={selected}>
    <span className="models-runtime-dot" /><span className="models-runtime-copy"><strong>{runtimeLabel(runtime.key)}</strong><small>{runtime.available ? `Available · ${runtime.precision_options.join(', ')}` : runtime.reason || 'Unavailable'}</small></span><span className="models-runtime-select">{selected ? 'SELECTED' : runtime.available ? 'SELECT' : 'UNAVAILABLE'}</span>
  </button>
}

function DeletionConfirmation({ modelKey, plan, loading, error, errorPhase, deleting, onCancel, onRetryPreview, onConfirm }: {
  modelKey: string
  plan: ModelDeletionPlan | null
  loading: boolean
  error: string
  errorPhase: 'preview' | 'delete' | null
  deleting: boolean
  onCancel: () => void
  onRetryPreview: () => void
  onConfirm: () => void
}) {
  return <section className="models-delete-confirm" aria-label={`Delete confirmation for ${modelKey}`}>
    <div className="models-delete-title"><strong>Delete local model</strong><code>{modelKey}</code></div>
    {loading && <p className="models-delete-message" role="status">Checking local files and deletion blockers…</p>}
    {error && <p className="models-delete-error" role="alert">{error}</p>}
    {error && errorPhase === 'preview' && <div className="models-delete-actions"><button className="button button-outline" type="button" disabled={loading || deleting} onClick={onRetryPreview}>Retry preview</button><button className="button button-outline" type="button" disabled={deleting} onClick={onCancel}>Cancel</button></div>}
    {plan && <>
      <p className="models-delete-message">This removes the following local directories and files ({formatBytes(plan.total_bytes)} total):</p>
      {plan.files.length > 0 ? <ul className="models-delete-files">{plan.files.map(file => <li key={`${file.kind}:${file.path}`}><code>{file.path}</code><span>{file.kind === 'source' ? 'Source' : 'Runtime'} · {formatBytes(file.bytes)}</span></li>)}</ul> : <p className="models-delete-message">No model directories are listed.</p>}
      {plan.registered && <p className="models-delete-message">The local catalog alias for <code>{modelKey}</code> will also be removed.</p>}
      <p className="models-delete-cache-note">Built-in aliases and the shared Hugging Face cache are kept; they may still appear as not prepared.</p>
      {!plan.deletable && <p className="models-delete-blocker" role="status">{plan.reason || 'This model cannot be deleted right now.'}</p>}
      {error && errorPhase === 'delete' && <p className="models-delete-error-note" role="status">The local catalog has not been refreshed; you can retry or cancel.</p>}
      <div className="models-delete-actions">
        <button className="button button-outline" type="button" disabled={deleting} onClick={onCancel}>Cancel</button>
        <button className="button button-danger" type="button" disabled={!plan.deletable || deleting || loading} onClick={onConfirm}>{deleting ? 'Deleting…' : errorPhase === 'delete' ? 'Retry delete local model' : 'Delete local model'}</button>
      </div>
    </>}
  </section>
}

function JobCard({ job }: { job: ModelJob }) {
  return <article className={`models-job-card job-${job.status}`}>
    <div className="models-job-top"><div className="models-job-title"><strong>{job.model_key}</strong><span>{job.repository} · {job.revision}</span></div><span className={`models-job-status ${job.status}`}>{job.status}</span></div>
    <div className="models-job-detail"><span>{job.operation === 'download' ? 'Source download' : `${runtimeLabel(job.runtime || '')} conversion${job.precision ? ` · ${job.precision}` : ''}`}</span><strong>{job.phase}</strong></div>
    {job.status === 'completed' && job.result && <div className="models-job-result"><span>{job.result.downloaded ? 'Source saved' : 'Source reused'}</span>{job.result.converted && <span>Runtime artifact prepared</span>}<span className={job.result.verified ? 'verified' : 'unverified'}>{job.result.verified ? 'Inference verified' : 'Inference not verified'}</span></div>}
    {job.error && <p className="models-job-error" role="alert">{job.error}</p>}
    <small className="models-job-time">Updated {new Date(job.updated_at).toLocaleString()}</small>
  </article>
}
