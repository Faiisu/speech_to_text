import { FormEvent, useCallback, useEffect, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { api } from '../../shared/api'
import type { Microphone, ModelChoice, Profile, ProfileDefinition, RuntimeChoice } from '../../shared/types'
import { ApiError, Loading } from '../../shared/States'

export default function ProfileEditor() {
  const { profileId } = useParams()
  const navigate = useNavigate()
  const editing = Boolean(profileId)
  const [profile, setProfile] = useState<Profile | null>(null)
  const [devices, setDevices] = useState<Microphone[]>([])
  const [models, setModels] = useState<ModelChoice[]>([])
  const [name, setName] = useState('')
  const [device, setDevice] = useState('')
  const [mode, setMode] = useState<ProfileDefinition['execution_mode']>('shared')
  const [model, setModel] = useState('turbo')
  const [runtime, setRuntime] = useState<RuntimeChoice['key']>('openvino-gpu')
  const [silenceThreshold, setSilenceThreshold] = useState('0.00')
  const [keywordText, setKeywordText] = useState('')
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const load = useCallback(async () => {
    const outcomes = await Promise.allSettled([api.microphones(), editing ? api.profile(profileId!) : Promise.resolve(null), api.models()])
    const deviceResult = outcomes[0]
    if (deviceResult.status === 'fulfilled') setDevices(deviceResult.value)
    else setError(deviceResult.reason instanceof Error ? deviceResult.reason.message : 'Could not load audio input devices.')
    const profileResult = outcomes[1]
    const modelResult = outcomes[2]
    if (modelResult.status === 'fulfilled') {
      setModels(modelResult.value.models)
      if (!editing) {
        setModel(modelResult.value.default_model || 'turbo')
        setRuntime((modelResult.value.default_runtime || 'openvino-gpu') as RuntimeChoice['key'])
      }
    } else setError(modelResult.reason instanceof Error ? modelResult.reason.message : 'Could not load models.')
    if (profileResult.status === 'fulfilled' && profileResult.value) {
      const p = profileResult.value; setProfile(p); setName(p.name); setDevice(p.device || ''); setMode(p.execution_mode); setModel(p.model); setRuntime(p.runtime); setKeywordText(p.keywords.join('\n')); setSilenceThreshold(String(p.silence_threshold))
    } else if (profileResult.status === 'rejected') setError(profileResult.reason instanceof Error ? profileResult.reason.message : 'Could not load the profile.')
    setLoading(false)
  }, [editing, profileId])
  useEffect(() => { void load() }, [load])
  const submit = async (event: FormEvent) => {
    event.preventDefault(); setError('')
    const keywords = keywordText.split(/[\n,]/).map(k => k.trim()).filter(Boolean)
    if (!name.trim()) { setError('Enter a profile name before saving.'); return }
    if (!keywords.length) { setError('Add at least one keyword.'); return }
    if (new Set(keywords).size !== keywords.length) { setError('Keywords must be unique.'); return }
    const threshold = Number(silenceThreshold)
    if (!Number.isFinite(threshold) || threshold < 0 || threshold >= 1) { setError('Noise floor must be at least 0 and less than 1.'); return }
    const body: ProfileDefinition = { name: name.trim(), device: device || null, execution_mode: mode, keywords, silence_threshold: threshold, model, runtime }
    setSaving(true)
    try { const saved = editing ? await api.updateProfile(profileId!, body) : await api.createProfile(body); navigate('/profiles', { state: { notice: `Saved profile “${saved.name}”.` } }) }
    catch (e) { setError(e instanceof Error ? e.message : 'Could not save the profile.') }
    finally { setSaving(false) }
  }
  if (loading) return <div className="page-content"><Loading /></div>
  const selectedModel = models.find(item => item.key === model)
  const runtimeChoices = selectedModel?.runtimes.filter(item => item.compatible) ?? []
  const selectedRuntime = selectedModel?.runtimes.find(item => item.key === runtime)
  return <div className="page-content editor-page">
    {error && <ApiError message={error} retry={() => { setLoading(true); void load() }} />}
    <Link className="back-link" to="/profiles">← Back to profiles</Link>
    <div className="page-heading"><div><div className="eyebrow">MICROPHONE CONFIGURATION</div><h1>{editing ? 'Edit profile' : 'Create profile'}<span>Microphone</span></h1><p>Name this setup, choose an audio input, and add the words you want to track.</p></div><span className="editor-stamp">PROFILE<br /><b>{editing ? profile?.profile_id.slice(0, 7).toUpperCase() : 'NEW'}</b></span></div>
    <form className="editor-layout" onSubmit={submit}>
      <div className="editor-form">
        <section className="form-section"><div className="form-section-title"><span className="section-symbol">01</span><div><h2>Profile name</h2><p>Choose a name that's easy to recognize when starting a workflow.</p></div></div><label className="field-label" htmlFor="profile-name">Name</label><input id="profile-name" autoFocus maxLength={80} value={name} onChange={e => setName(e.target.value)} placeholder="e.g. Meeting room A" /></section>
        <section className="form-section"><div className="form-section-title"><span className="section-symbol">02</span><div><h2>Audio input</h2><p>Select a microphone connected to this computer.</p></div></div><label className="field-label" htmlFor="profile-device">Microphone</label><select id="profile-device" value={device} onChange={e => setDevice(e.target.value)}><option value="">System default microphone</option>{devices.map(d => <option key={d.name} value={d.name} disabled={!d.selectable}>{d.name}{d.is_default ? ' · Default' : ''}{!d.selectable ? ' · Duplicate name; unavailable' : ''}</option>)}</select><small className="field-hint">Devices are discovered by the backend host.</small></section>
        <section className="form-section"><div className="form-section-title"><span className="section-symbol">03</span><div><h2>Keywords</h2><p>Add Thai words or phrases you want to track.</p></div></div><label className="field-label" htmlFor="keywords">One keyword per line</label><textarea id="keywords" rows={5} value={keywordText} onChange={e => setKeywordText(e.target.value)} placeholder={'สวัสดี\nประชุมเริ่มแล้ว\nสรุปงาน'} /><small className="field-hint">The workflow counts each matched occurrence in the transcript.</small></section>
        <section className="form-section"><div className="form-section-title"><span className="section-symbol">04</span><div><h2>Noise floor</h2><p>Audio below this RMS level is treated as silence.</p></div></div><label className="field-label" htmlFor="silence-threshold">Noise floor (RMS silence threshold)</label><input id="silence-threshold" type="number" min="0" step="any" value={silenceThreshold} onChange={e => setSilenceThreshold(e.target.value)} /><small className="field-hint">Enter a value from 0 up to, but not including, 1. Default: 0.00. A value of 0 disables silence filtering.</small></section>
        <section className="form-section"><div className="form-section-title"><span className="section-symbol">05</span><div><h2>Transcription model</h2><p>Choose the model this profile loads for each workflow.</p></div></div><label className="field-label" htmlFor="profile-model">Model</label><select id="profile-model" value={model} onChange={e => { const nextModel = models.find(item => item.key === e.target.value); setModel(e.target.value); if (nextModel && !nextModel.runtimes.some(item => item.key === runtime && item.compatible)) { const preferred = nextModel.runtimes.find(item => item.key === runtime && item.compatible) || nextModel.runtimes.find(item => item.key === 'openvino-gpu' && item.compatible) || nextModel.runtimes.find(item => item.compatible); if (preferred) setRuntime(preferred.key) } }}>{models.map(m => <option key={m.key} value={m.key}>{m.display_name} ({m.key}){m.ready ? '' : m.installed ? ' · installed, runtime unavailable' : ' · not installed'}</option>)}{!models.some(m => m.key === model) && <option value={model}>{model}</option>}</select><small className="field-hint">Model downloads are not managed here.</small></section>
        <section className="form-section"><div className="form-section-title"><span className="section-symbol">06</span><div><h2>Model runtime</h2><p>Choose a runtime supported by the selected model.</p></div></div><label className="field-label" htmlFor="profile-runtime">Runtime</label><select id="profile-runtime" value={runtime} onChange={e => setRuntime(e.target.value as RuntimeChoice['key'])}>{runtimeChoices.map(option => <option key={option.key} value={option.key}>{option.key}{option.ready ? ' · ready' : ' · unavailable'}</option>)}{!runtimeChoices.some(option => option.key === runtime) && <option value={runtime}>{runtime} · unsupported for this model</option>}</select><small className="field-hint">{selectedRuntime?.ready ? 'This runtime and model are ready on the backend host.' : selectedRuntime?.reason || 'Runtime readiness is checked on the backend host.'}</small></section>
        <section className="form-section"><div className="form-section-title"><span className="section-symbol">07</span><div><h2>Execution mode</h2><p>Choose how the transcription model is managed.</p></div></div><div className="mode-options"><label className={`mode-option ${mode === 'shared' ? 'selected' : ''}`}><input type="radio" name="mode" value="shared" checked={mode === 'shared'} onChange={() => setMode('shared')} /><span className="mode-radio" /><span><b>Shared model</b><small>Starts faster; workflows share one model instance.</small></span><span className="mode-badge">RECOMMENDED</span></label><label className={`mode-option ${mode === 'per_workflow_process' ? 'selected' : ''}`}><input type="radio" name="mode" value="per_workflow_process" checked={mode === 'per_workflow_process'} onChange={() => setMode('per_workflow_process')} /><span className="mode-radio" /><span><b>Separate process</b><small>Each workflow loads its own model instance.</small></span></label></div></section>
        <div className="form-actions"><Link to="/profiles" className="button button-outline">Cancel</Link><button className="button button-dark" disabled={saving || !!error}>{saving ? 'Saving…' : 'Save profile'} <span>↗</span></button></div>
      </div>
      <aside className="editor-aside"><div className="aside-wave"><div className="eyebrow">AUDIO NOTE</div><div className="mini-wave">{Array.from({ length: 24 }, (_, i) => <i key={i} style={{ height: `${14 + Math.abs(Math.sin(i * 1.7)) * 54}px` }} />)}</div></div><h3>Configure once.<br />Reuse anytime.</h3><p>Profiles are stored by the backend. Changes apply to workflows started after saving.</p><div className="aside-divider" /><small>Running workflows keep the keyword set they received at startup. Track them from the overview.</small></aside>
    </form>
  </div>
}
