import { FormEvent, useCallback, useEffect, useState } from 'react'
import { Link, useNavigate, useParams, useSearchParams } from 'react-router-dom'
import { api } from '../../shared/api'
import { LANGUAGE_LABELS, type DestinationSummary, type Language, type Microphone, type ModelChoice, type NoiseFilterConfig, type Profile, type ProfileDefinition, type Recording, type RuntimeChoice } from '../../shared/types'
import { ApiError, Loading } from '../../shared/States'

const DEFAULT_DECODING_OPTIONS = { beam_size: 1, temperature: 0, condition_on_previous_text: false, no_repeat_ngram_size: 3, repetition_penalty: 1 }
const DEFAULT_OPENVINO_SPEECH_FILTER = { enabled: false, no_speech_threshold: 0.6, logprob_threshold: -1.0 }

export default function ProfileEditor() {
  const { profileId } = useParams()
  const [searchParams] = useSearchParams()
  const duplicateSourceId = searchParams.get('duplicate')
  const navigate = useNavigate()
  const editing = Boolean(profileId)
  const duplicating = !editing && Boolean(duplicateSourceId)
  const [profile, setProfile] = useState<Profile | null>(null)
  const [devices, setDevices] = useState<Microphone[]>([])
  const [models, setModels] = useState<ModelChoice[]>([])
  const [destinations, setDestinations] = useState<DestinationSummary[]>([])
  const [recordings, setRecordings] = useState<Recording[]>([])
  const [recordingLoadError, setRecordingLoadError] = useState('')
  const [destinationId, setDestinationId] = useState('')
  const [destinationError, setDestinationError] = useState('')
  const [name, setName] = useState('')
  const [sourceType, setSourceType] = useState<'microphone' | 'rtsp'>('microphone')
  const [rtspEntryType, setRtspEntryType] = useState<'url' | 'secret'>('url')
  const [rtspUrl, setRtspUrl] = useState('')
  const [rtspUsername, setRtspUsername] = useState('')
  const [rtspPassword, setRtspPassword] = useState('')
  const [hasSavedPassword, setHasSavedPassword] = useState(false)
  const [clearSavedPassword, setClearSavedPassword] = useState(false)
  const [rtspSecretRef, setRtspSecretRef] = useState('')
  const [device, setDevice] = useState('')
  const [deviceLoadError, setDeviceLoadError] = useState('')
  const [mode, setMode] = useState<ProfileDefinition['execution_mode']>('shared')
  const [model, setModel] = useState('turbo')
  const [runtime, setRuntime] = useState<RuntimeChoice['key']>('openvino-gpu')
  const [language, setLanguage] = useState<Language>('th')
  const [silenceThreshold, setSilenceThreshold] = useState('0.00')
  const [vadEnabled, setVadEnabled] = useState(false)
  const [vadThreshold, setVadThreshold] = useState('0.5')
  const [minSpeechDuration, setMinSpeechDuration] = useState('250')
  const [beamSize, setBeamSize] = useState('1')
  const [temperature, setTemperature] = useState('0')
  const [initialPrompt, setInitialPrompt] = useState('')
  const [conditionOnPreviousText, setConditionOnPreviousText] = useState(false)
  const [noRepeatNgramSize, setNoRepeatNgramSize] = useState('3')
  const [repetitionPenalty, setRepetitionPenalty] = useState('1')
  const [openvinoSpeechFilterEnabled, setOpenvinoSpeechFilterEnabled] = useState(false)
  const [openvinoNoSpeechThreshold, setOpenvinoNoSpeechThreshold] = useState('0.6')
  const [openvinoLogprobThreshold, setOpenvinoLogprobThreshold] = useState('-1')
  const [bandpassEnabled, setBandpassEnabled] = useState(false)
  const [lowHz, setLowHz] = useState('100')
  const [highHz, setHighHz] = useState('7000')
  const [filterOrder, setFilterOrder] = useState('4')
  const [denoiseEnabled, setDenoiseEnabled] = useState(false)
  const [noiseRecordingIds, setNoiseRecordingIds] = useState<string[]>([])
  const [keywordText, setKeywordText] = useState('')
  const [error, setError] = useState('')
  const [loadError, setLoadError] = useState('')
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const load = useCallback(async () => {
    setLoadError('')
    setDeviceLoadError('')
    const sourceProfile = editing ? api.profile(profileId!) : duplicating ? api.profile(duplicateSourceId!) : Promise.resolve(null)
    const outcomes = await Promise.allSettled([api.microphones(), sourceProfile, api.models(), api.destinations(), api.recordings()])
    const deviceResult = outcomes[0]
    if (deviceResult.status === 'fulfilled') setDevices(deviceResult.value)
    else setDeviceLoadError(deviceResult.reason instanceof Error ? deviceResult.reason.message : 'Could not load audio input devices.')
    const profileResult = outcomes[1]
    const modelResult = outcomes[2]
    const destinationsResult = outcomes[3]
    const recordingsResult = outcomes[4]
    if (destinationsResult.status === 'fulfilled') setDestinations(destinationsResult.value)
    else setDestinationError('Saved receivers could not be loaded. You can still save without selecting one, or retry after checking the API connection.')
    if (recordingsResult.status === 'fulfilled') setRecordings(recordingsResult.value)
    else setRecordingLoadError('Saved recordings could not be loaded. Noise samples are unavailable until the API connection is restored.')
    if (modelResult.status === 'fulfilled') {
      setModels(modelResult.value.models)
      if (!editing && !duplicating) {
        setModel(modelResult.value.default_model || 'turbo')
        setRuntime((modelResult.value.default_runtime || 'openvino-gpu') as RuntimeChoice['key'])
      }
    } else setLoadError(modelResult.reason instanceof Error ? modelResult.reason.message : 'Could not load models.')
    if (profileResult.status === 'fulfilled' && profileResult.value) {
      const p = profileResult.value
      if (editing) setProfile(p)
      setName(duplicating ? '' : p.name)
      setSourceType(p.source_type || 'microphone')
      setRtspUrl(p.rtsp_url || '')
      setRtspUsername(p.rtsp_username || '')
      setRtspPassword('')
      setHasSavedPassword(Boolean(p.rtsp_has_password))
      setClearSavedPassword(false)
      setRtspSecretRef(p.rtsp_url_secret_ref || '')
      setRtspEntryType(p.rtsp_url_secret_ref ? 'secret' : 'url')
      setDevice(p.device || '')
      setMode(p.execution_mode)
      setModel(p.model)
      setRuntime(p.runtime)
      setLanguage(p.language || 'th')
      setKeywordText(p.keywords.join('\n'))
      setSilenceThreshold(String(p.silence_threshold))
      setVadEnabled(p.vad?.enabled ?? false)
      setVadThreshold(String(p.vad?.threshold ?? 0.5))
      setMinSpeechDuration(String(p.vad?.min_speech_duration_ms ?? 250))
      setDestinationId(p.destination_id || '')
      const options = p.decoding_options || DEFAULT_DECODING_OPTIONS
      setBeamSize(String(options.beam_size))
      setTemperature(String(options.temperature))
      setInitialPrompt(options.initial_prompt ?? '')
      setConditionOnPreviousText(options.condition_on_previous_text)
      setNoRepeatNgramSize(String(options.no_repeat_ngram_size))
      setRepetitionPenalty(String(options.repetition_penalty))
      const openvinoFilter = options.openvino_speech_filter || DEFAULT_OPENVINO_SPEECH_FILTER
      setOpenvinoSpeechFilterEnabled(openvinoFilter.enabled)
      setOpenvinoNoSpeechThreshold(String(openvinoFilter.no_speech_threshold))
      setOpenvinoLogprobThreshold(String(openvinoFilter.logprob_threshold))
      const noise = p.noise_filter
      setBandpassEnabled(noise?.bandpass_enabled || false)
      setLowHz(String(noise?.low_hz ?? 100))
      setHighHz(String(noise?.high_hz ?? 7000))
      setFilterOrder(String(noise?.order ?? 4))
      setDenoiseEnabled(noise?.denoise_enabled || false)
      setNoiseRecordingIds(noise?.noise_recording_ids || [])
    } else if (profileResult.status === 'rejected') {
      setLoadError(duplicating
        ? `Could not load the profile to duplicate. Check that it still exists, then return to profiles and try again. ${profileResult.reason instanceof Error ? profileResult.reason.message : ''}`
        : profileResult.reason instanceof Error ? profileResult.reason.message : 'Could not load the profile.')
    }
    setLoading(false)
  }, [editing, profileId, duplicating, duplicateSourceId])
  useEffect(() => { void load() }, [load])
  const submit = async (event: FormEvent) => {
    event.preventDefault(); setError('')
    if (loadError || (sourceType === 'microphone' && deviceLoadError) || (editing && !profile) || (duplicating && !duplicateSourceId)) return
    const keywords = keywordText.split(/[\n,]/).map(k => k.trim()).filter(Boolean)
    if (!name.trim()) { setError('Enter a profile name before saving.'); return }
    if (!keywords.length) { setError('Add at least one keyword.'); return }
    if (new Set(keywords).size !== keywords.length) { setError('Keywords must be unique.'); return }
    const threshold = Number(silenceThreshold)
    if (!Number.isFinite(threshold) || threshold < 0 || threshold >= 1) { setError('Noise floor must be at least 0 and less than 1.'); return }
    const speechThreshold = Number(vadThreshold)
    const minimumSpeech = Number(minSpeechDuration)
    if (!Number.isFinite(speechThreshold) || speechThreshold <= 0 || speechThreshold >= 1) { setError('Speech probability threshold must be greater than 0 and less than 1.'); return }
    if (!Number.isInteger(minimumSpeech) || minimumSpeech < 0 || minimumSpeech > 30000) { setError('Minimum speech duration must be an integer from 0 to 30000 ms.'); return }
    const beam = Number(beamSize)
    const temperatureValue = Number(temperature)
    const noRepeat = Number(noRepeatNgramSize)
    const penalty = Number(repetitionPenalty)
    if (!beamSize.trim() || !Number.isInteger(beam) || beam < 1 || beam > 10) { setError('Beam size must be an integer from 1 to 10.'); return }
    if (!temperature.trim() || !Number.isFinite(temperatureValue) || temperatureValue < 0 || temperatureValue > 1) { setError('Temperature must be from 0 to 1.'); return }
    if (!Number.isInteger(noRepeat) || !noRepeatNgramSize.trim() || noRepeat < 0 || noRepeat > 20) { setError('No-repeat n-gram size must be an integer from 0 to 20.'); return }
    if (!repetitionPenalty.trim() || !Number.isFinite(penalty) || penalty < 1 || penalty > 2) { setError('Repetition penalty must be from 1 to 2.'); return }
    const noSpeechThreshold = Number(openvinoNoSpeechThreshold)
    const logprobThreshold = Number(openvinoLogprobThreshold)
    if (!openvinoNoSpeechThreshold.trim() || !Number.isFinite(noSpeechThreshold) || noSpeechThreshold < 0 || noSpeechThreshold > 1) { setError('No speech threshold must be from 0 to 1.'); return }
    if (!openvinoLogprobThreshold.trim() || !Number.isFinite(logprobThreshold) || logprobThreshold > 0) { setError('Log probability threshold must be a finite number less than or equal to 0.'); return }
    const low = Number(lowHz); const high = Number(highHz); const order = Number(filterOrder)
    if (!Number.isFinite(low) || low <= 0 || !Number.isFinite(high) || high <= low || high >= 8000 || !Number.isInteger(order) || order < 1 || order > 8) { setError('Band-pass settings need 0 < low cutoff < high cutoff < 8000 Hz and an integer filter order from 1 to 8.'); return }
    if (noiseRecordingIds.length > 8) { setError('Choose no more than 8 noise recordings.'); return }
    if (denoiseEnabled && !noiseRecordingIds.length) { setError('Choose at least one noise-only WAV recording to enable spectral denoising.'); return }
    if (sourceType === 'rtsp' && !(rtspEntryType === 'url' ? rtspUrl.trim() : rtspSecretRef.trim())) { setError(rtspEntryType === 'url' ? 'Enter an RTSP URL.' : 'Enter an environment variable name for the RTSP URL.'); return }
    if (duplicating && hasSavedPassword && sourceType === 'rtsp' && !rtspPassword && rtspEntryType === 'url' && !/^rtsps?:\/\/[^/]*@/i.test(rtspUrl)) { setError('Re-enter the saved camera password for the duplicate profile.'); return }
    const noise_filter: NoiseFilterConfig = { bandpass_enabled: bandpassEnabled, low_hz: low, high_hz: high, order, denoise_enabled: denoiseEnabled, noise_recording_ids: noiseRecordingIds }
    const body: ProfileDefinition = { name: name.trim(), source_type: sourceType, device: sourceType === 'microphone' ? device || null : null, rtsp_url: sourceType === 'rtsp' && rtspEntryType === 'url' ? rtspUrl.trim() : null, rtsp_url_secret_ref: sourceType === 'rtsp' && rtspEntryType === 'secret' ? rtspSecretRef.trim() : null, ...(sourceType === 'rtsp' && rtspEntryType === 'url' && rtspUsername.trim() ? { rtsp_username: rtspUsername.trim() } : {}), ...(sourceType === 'rtsp' && rtspEntryType === 'url' && (rtspPassword || clearSavedPassword) ? { rtsp_password: clearSavedPassword ? '' : rtspPassword } : {}), execution_mode: mode, keywords, silence_threshold: threshold, vad: { enabled: vadEnabled, threshold: speechThreshold, min_speech_duration_ms: minimumSpeech }, model, runtime, language, destination_id: destinationId || null, decoding_options: { beam_size: beam, temperature: temperatureValue, condition_on_previous_text: conditionOnPreviousText, no_repeat_ngram_size: noRepeat, repetition_penalty: penalty, initial_prompt: initialPrompt.trim(), openvino_speech_filter: { enabled: openvinoSpeechFilterEnabled, no_speech_threshold: noSpeechThreshold, logprob_threshold: logprobThreshold } }, noise_filter }
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
    {loadError && <ApiError message={loadError} retry={() => { setLoading(true); void load() }} />}
    {error && <div className="notice" role="alert">{error}</div>}
    <Link className="back-link" to="/profiles">← Back to profiles</Link>
    <div className="page-heading"><div><div className="eyebrow">AUDIO INPUT CONFIGURATION</div><h1>{editing ? 'Edit profile' : duplicating ? 'Duplicate profile' : 'Create profile'}<span>Audio input</span></h1><p>{duplicating ? 'Review the copied settings, choose a new profile name, then save.' : 'Name this setup, choose an audio input, and add the words you want to track.'}</p></div><span className="editor-stamp">PROFILE<br /><b>{editing ? profile?.profile_id.slice(0, 7).toUpperCase() : 'NEW'}</b></span></div>
    <form className="editor-layout" onSubmit={submit}>
      <div className="editor-form">
        <section className="form-section"><div className="form-section-title"><span className="section-symbol">01</span><div><h2>Profile name</h2><p>Choose a name that's easy to recognize when starting a workflow.</p></div></div><label className="field-label" htmlFor="profile-name">Name</label><input id="profile-name" autoFocus maxLength={80} value={name} onChange={e => { setName(e.target.value); setError('') }} placeholder={duplicating ? 'Enter a new profile name' : 'e.g. Meeting room A'} />{duplicating && <small className="field-hint">This is a new profile. Enter a name that does not match another profile.</small>}</section>
        <section className="form-section"><div className="form-section-title"><span className="section-symbol">02</span><div><h2>Audio input</h2><p>Choose a microphone or an IP camera audio stream.</p></div></div><label className="field-label" htmlFor="profile-source-type">Input source</label><select id="profile-source-type" value={sourceType} onChange={event => { const next = event.target.value as 'microphone' | 'rtsp'; setSourceType(next); if (next === 'microphone') { setRtspUsername(''); setRtspPassword(''); setClearSavedPassword(false) } setError('') }}><option value="microphone">Microphone</option><option value="rtsp">IPCAM (RTSP)</option></select>{sourceType === 'microphone' ? <><label className="field-label" htmlFor="profile-device">Microphone</label><select id="profile-device" value={device} onChange={e => setDevice(e.target.value)}><option value="">System default microphone</option>{device && !devices.some(d => d.name === device) && <option value={device}>{device} · saved, unavailable</option>}{devices.map(d => <option key={d.name} value={d.name} disabled={!d.selectable}>{d.name}{d.is_default ? ' · Default' : ''}{!d.selectable ? ' · Duplicate name; unavailable' : ''}</option>)}</select><small className="field-hint">Devices are discovered by the backend host.</small>{deviceLoadError && <div className="notice" role="alert">{deviceLoadError} <button type="button" onClick={() => { setLoading(true); void load() }}>Retry</button></div>}</> : <><label className="field-label" htmlFor="rtsp-entry-type">RTSP address source</label><select id="rtsp-entry-type" value={rtspEntryType} onChange={event => { const next = event.target.value as 'url' | 'secret'; setRtspEntryType(next); if (next === 'secret') { setRtspUsername(''); setRtspPassword(''); setClearSavedPassword(false) } }}><option value="url">Direct URL</option><option value="secret">Environment variable reference</option></select>{rtspEntryType === 'url' ? <><label className="field-label" htmlFor="rtsp-url">RTSP URL</label><input id="rtsp-url" value={rtspUrl} onChange={event => { const value = event.target.value; setRtspUrl(value); if (/^rtsps?:\/\/[^/]*@/i.test(value)) { setRtspUsername(''); setRtspPassword('') } }} placeholder="rtsp://camera.local/live" autoComplete="off" /><small className="field-hint">Paste a complete authenticated URL, or enter separate credentials below.</small><label className="field-label" htmlFor="rtsp-username">Username</label><input id="rtsp-username" value={rtspUsername} onChange={event => setRtspUsername(event.target.value)} autoComplete="username" /><label className="field-label" htmlFor="rtsp-password">Password</label><input id="rtsp-password" type="password" value={rtspPassword} onChange={event => { setRtspPassword(event.target.value); setClearSavedPassword(false) }} autoComplete="new-password" />{hasSavedPassword && editing && <><small className="field-hint">A saved password is stored encrypted. Leave blank to keep it when the URL and username are unchanged.</small><label className="field-hint"><input type="checkbox" checked={clearSavedPassword} onChange={event => setClearSavedPassword(event.target.checked)} /> Clear saved password</label></>}{hasSavedPassword && duplicating && <small className="field-hint">The saved password is not copied. Re-enter it for this profile.</small>}</> : <><label className="field-label" htmlFor="rtsp-secret-ref">Environment variable name</label><input id="rtsp-secret-ref" value={rtspSecretRef} onChange={event => setRtspSecretRef(event.target.value)} placeholder="CAMERA_AUDIO_URL" autoComplete="off" /><small className="field-hint">Store the complete authenticated RTSP URL in an environment variable and allow its name in SPEECH_TO_TEXT_RTSP_SECRET_REFS.</small></>}</>}</section>
        <section className="form-section"><div className="form-section-title"><span className="section-symbol">03</span><div><h2>Keywords</h2><p>Add words or phrases you want to track.</p></div></div><label className="field-label" htmlFor="keywords">One keyword per line</label><textarea id="keywords" rows={5} value={keywordText} onChange={e => setKeywordText(e.target.value)} placeholder={'สวัสดี\nประชุมเริ่มแล้ว\nสรุปงาน'} /><small className="field-hint">The workflow counts each matched occurrence in the transcript.</small></section>
        <section className="form-section"><div className="form-section-title"><span className="section-symbol">04</span><div><h2>Speech detection</h2><p>Gate audio with RMS energy and optional speech probability.</p></div></div><label className="field-label" htmlFor="silence-threshold">Noise floor (RMS silence threshold)</label><input id="silence-threshold" type="number" min="0" step="any" value={silenceThreshold} onChange={e => setSilenceThreshold(e.target.value)} /><small className="field-hint">RMS measures signal energy, not whether the sound is speech. Default: 0.00; 0 disables this gate.</small><label className="profile-decoding-check"><input type="checkbox" checked={vadEnabled} onChange={event => setVadEnabled(event.target.checked)} /><span>Enable voice activity detection (VAD)</span></label><label className="field-label" htmlFor="vad-threshold">Speech probability threshold</label><input id="vad-threshold" type="number" min="0.000001" max="0.999999" step="any" value={vadThreshold} onChange={event => setVadThreshold(event.target.value)} /><label className="field-label" htmlFor="vad-min-speech-duration">Minimum speech duration (ms)</label><input id="vad-min-speech-duration" type="number" min="0" max="30000" step="1" value={minSpeechDuration} onChange={event => setMinSpeechDuration(event.target.value)} /><small className="field-hint">VAD estimates speech probability after the RMS gate. Default: off, threshold 0.5, minimum 250 ms. Changes apply on the next start.</small></section>
        <section className="form-section"><div className="form-section-title"><span className="section-symbol">05</span><div><h2>Audio preprocessing</h2><p>Filter each complete transcription chunk before silence detection and recognition.</p></div></div>
          <label className="profile-decoding-check"><input type="checkbox" checked={bandpassEnabled} onChange={event => setBandpassEnabled(event.target.checked)} /><span>Enable band-pass filter</span></label>
          <label className="field-label" htmlFor="filter-low-hz">Low cutoff (Hz)</label><input id="filter-low-hz" type="number" min="0.1" step="any" value={lowHz} onChange={event => setLowHz(event.target.value)} />
          <label className="field-label" htmlFor="filter-high-hz">High cutoff (Hz)</label><input id="filter-high-hz" type="number" min="0.2" max="7999.9" step="any" value={highHz} onChange={event => setHighHz(event.target.value)} />
          <label className="field-label" htmlFor="filter-order">Filter order</label><input id="filter-order" type="number" min="1" max="8" step="1" value={filterOrder} onChange={event => setFilterOrder(event.target.value)} />
          <small className="field-hint">Defaults: 100–7000 Hz, order 4. Cutoffs must satisfy 0 &lt; low &lt; high &lt; 8000 Hz. Both preprocessing options are off by default.</small>
          <label className="profile-decoding-check"><input type="checkbox" checked={denoiseEnabled} onChange={event => setDenoiseEnabled(event.target.checked)} /><span>Enable stationary-noise denoising</span></label>
          <small className="field-hint">Select up to 8 finalized WAVs that contain noise only. Noise is copied and prepared at the workflow start, so the run keeps working if a recording is later deleted. <Link to="/noise-filtering">Open Noise filtering</Link> · <Link to="/model-tuning">Open Model tuning</Link></small>
          {recordingLoadError && <small className="field-hint" role="alert">{recordingLoadError}</small>}
          {recordings.filter(item => ['completed', 'failed'].includes(item.status) && item.filename?.toLowerCase().endsWith('.wav')).length ? <div className="profile-noise-recordings" aria-label="Noise-only WAV recordings">{recordings.filter(item => ['completed', 'failed'].includes(item.status) && item.filename?.toLowerCase().endsWith('.wav')).map(item => <label key={item.recording_id} className="profile-decoding-check"><input type="checkbox" checked={noiseRecordingIds.includes(item.recording_id)} disabled={!noiseRecordingIds.includes(item.recording_id) && noiseRecordingIds.length >= 8} onChange={event => setNoiseRecordingIds(current => event.target.checked ? [...current, item.recording_id] : current.filter(id => id !== item.recording_id))} /><span>{item.filename} · {item.duration_seconds.toFixed(1)} s</span></label>)}</div> : <small className="field-hint">No finalized WAV recordings are available. Import or record a noise-only WAV in Model tuning.</small>}
          {noiseRecordingIds.some(id => !recordings.some(item => item.recording_id === id)) && <div className="profile-noise-missing"><small className="field-hint">Some saved noise sources were deleted.</small><button type="button" className="button button-outline" onClick={() => setNoiseRecordingIds(current => current.filter(id => recordings.some(item => item.recording_id === id)))}>Remove unavailable sources</button></div>}
        </section>
        <section className="form-section"><div className="form-section-title"><span className="section-symbol">05</span><div><h2>Language</h2><p>Choose the language used for recognition.</p></div></div><label className="field-label" htmlFor="profile-language">Language</label><select id="profile-language" value={language} onChange={event => setLanguage(event.target.value as Language)}>{(['th', 'en', 'auto'] as const).map(value => <option key={value} value={value}>{LANGUAGE_LABELS[value]}</option>)}</select><small className="field-hint">Auto detect allows runtime language detection but does not guarantee recognition of code switching. Language support depends on the model. Changes apply on the next start.</small></section>
        <section className="form-section"><div className="form-section-title"><span className="section-symbol">05</span><div><h2>Transcription model</h2><p>Choose the model this profile loads for each workflow.</p></div></div><label className="field-label" htmlFor="profile-model">Model</label><select id="profile-model" value={model} onChange={e => { const nextModel = models.find(item => item.key === e.target.value); setModel(e.target.value); if (nextModel && !nextModel.runtimes.some(item => item.key === runtime && item.compatible)) { const preferred = nextModel.runtimes.find(item => item.key === runtime && item.compatible) || nextModel.runtimes.find(item => item.key === 'openvino-gpu' && item.compatible) || nextModel.runtimes.find(item => item.compatible); if (preferred) setRuntime(preferred.key) } }}>{models.map(m => <option key={m.key} value={m.key}>{m.display_name} ({m.key}){m.ready ? '' : m.installed ? ' · installed, runtime unavailable' : ' · not installed'}</option>)}{!models.some(m => m.key === model) && <option value={model}>{model}</option>}</select><small className="field-hint">Model downloads are not managed here.</small></section>
        <section className="form-section"><div className="form-section-title"><span className="section-symbol">06</span><div><h2>Model runtime</h2><p>Choose a runtime supported by the selected model.</p></div></div><label className="field-label" htmlFor="profile-runtime">Runtime</label><select id="profile-runtime" value={runtime} onChange={e => setRuntime(e.target.value as RuntimeChoice['key'])}>{runtimeChoices.map(option => <option key={option.key} value={option.key}>{option.key}{option.ready ? ' · ready' : ' · unavailable'}</option>)}{!runtimeChoices.some(option => option.key === runtime) && <option value={runtime}>{runtime} · unsupported for this model</option>}</select><small className="field-hint">{selectedRuntime?.ready ? 'This runtime and model are ready on the backend host.' : selectedRuntime?.reason || 'Runtime readiness is checked on the backend host.'}</small></section>
        <section className="form-section"><div className="form-section-title"><span className="section-symbol">07</span><div><h2>Execution mode</h2><p>Choose how the transcription model is managed.</p></div></div><div className="mode-options"><label className={`mode-option ${mode === 'shared' ? 'selected' : ''}`}><input type="radio" name="mode" value="shared" checked={mode === 'shared'} onChange={() => setMode('shared')} /><span className="mode-radio" /><span><b>Shared model</b><small>Starts faster; workflows share one model instance.</small></span><span className="mode-badge">RECOMMENDED</span></label><label className={`mode-option ${mode === 'per_workflow_process' ? 'selected' : ''}`}><input type="radio" name="mode" value="per_workflow_process" checked={mode === 'per_workflow_process'} onChange={() => setMode('per_workflow_process')} /><span className="mode-radio" /><span><b>Separate process</b><small>Each workflow loads its own model instance.</small></span></label></div></section>
        <section className="form-section profile-decoding-settings"><div className="form-section-title"><span className="section-symbol">08</span><div><h2>Decoding settings</h2><p>Adjust how the model generates transcript text.</p></div></div><label className="field-label" htmlFor="beam-size">Beam size</label><input id="beam-size" aria-label="Beam size" type="number" min="1" max="10" step="1" required value={beamSize} onChange={event => setBeamSize(event.target.value)} /><small className="field-hint">Search width, from 1 to 10. Default: 1.</small><label className="field-label" htmlFor="temperature">Temperature</label><input id="temperature" aria-label="Temperature" type="number" min="0" max="1" step="any" required value={temperature} onChange={event => setTemperature(event.target.value)} /><small className="field-hint">Higher values add variation. Range: 0 to 1. Default: 0.</small><label className="field-label" htmlFor="initial-prompt">Initial prompt</label><textarea id="initial-prompt" rows={3} value={initialPrompt} onChange={event => setInitialPrompt(event.target.value)} /><small className="field-hint">Add vocabulary or context to guide transcription. Leave blank to disable. This is not an instruction-following field.</small><label className="profile-decoding-check"><input id="condition-on-previous-text" type="checkbox" aria-describedby="condition-on-previous-text-hint" checked={conditionOnPreviousText} disabled={runtime === 'transformers'} onChange={event => setConditionOnPreviousText(event.target.checked)} /><span>Condition on previous text</span></label><small className="field-hint" id="condition-on-previous-text-hint">Enabled for runtimes that support previous-text conditioning; Transformers keeps it inactive. Default: off.</small><label className="field-label" htmlFor="no-repeat-ngram-size">No-repeat n-gram size</label><input id="no-repeat-ngram-size" aria-label="No-repeat n-gram size" type="number" min="0" max="20" step="1" required value={noRepeatNgramSize} onChange={event => setNoRepeatNgramSize(event.target.value)} /><small className="field-hint">Limit repeated token n-grams; 0 disables this constraint. Range: 0 to 20. Default: 3.</small><label className="field-label" htmlFor="repetition-penalty">Repetition penalty</label><input id="repetition-penalty" aria-label="Repetition penalty" type="number" min="1" max="2" step="any" required value={repetitionPenalty} onChange={event => setRepetitionPenalty(event.target.value)} /><small className="field-hint">Values above 1 discourage repeated tokens. Range: 1 to 2. Default: 1.</small><div className="openvino-speech-filter"><h3>OpenVINO speech filter</h3><label className="profile-decoding-check"><input aria-label="Enable OpenVINO speech filter" type="checkbox" checked={openvinoSpeechFilterEnabled} disabled={!runtime.startsWith('openvino')} onChange={event => setOpenvinoSpeechFilterEnabled(event.target.checked)} /><span>Enable OpenVINO speech filter</span></label><label className="field-label" htmlFor="openvino-no-speech-threshold">No speech threshold</label><input id="openvino-no-speech-threshold" aria-label="No speech threshold" type="number" min="0" max="1" step="any" required value={openvinoNoSpeechThreshold} disabled={!runtime.startsWith('openvino')} onChange={event => setOpenvinoNoSpeechThreshold(event.target.value)} /><label className="field-label" htmlFor="openvino-logprob-threshold">Log probability threshold</label><input id="openvino-logprob-threshold" aria-label="Log probability threshold" type="number" max="0" step="any" required value={openvinoLogprobThreshold} disabled={!runtime.startsWith('openvino')} onChange={event => setOpenvinoLogprobThreshold(event.target.value)} /><small className="field-hint">Defaults: disabled, 0.6, and −1. OpenVINO CPU/GPU only. Settings stay dormant with other runtimes.</small></div><small className="field-hint">Changes take effect the next time you start this profile.</small></section>
        <section className="form-section profile-output-section"><div className="form-section-title"><span className="section-symbol">09</span><div><h2>Output destination</h2><p>Optionally send completed transcript matches to one saved receiver.</p></div></div><label className="field-label" htmlFor="profile-destination">Destination</label><select id="profile-destination" value={destinationId} onChange={event => setDestinationId(event.target.value)}><option value="">No output destination</option>{destinationId && !destinations.some(destination => destination.destination_id === destinationId) && <option value={destinationId}>Saved destination unavailable · {destinationId.slice(0, 8)}</option>}{destinations.map(destination => <option key={destination.destination_id} value={destination.destination_id}>{destination.name} · {destination.protocol === 'mqtt' ? `MQTT · ${destination.mqtt_topic}` : destination.method} · {destination.endpoint_origin}</option>)}</select>{destinationError ? <small className="field-hint">{destinationError} <Link to="/destinations">Manage destinations</Link></small> : <small className="field-hint">Forwarding happens once when the source completes. <Link to="/destinations">Manage destinations</Link></small>}{destinationId && !destinations.some(destination => destination.destination_id === destinationId) && <small className="field-hint profile-destination-warning">This profile points to a destination that is unavailable. Choose another destination or clear the selection before saving.</small>}</section>
        <div className="form-actions"><Link to="/profiles" className="button button-outline">Cancel</Link><button className="button button-dark" disabled={saving || Boolean(loadError) || (sourceType === 'microphone' && Boolean(deviceLoadError)) || (editing && !profile)}>{saving ? 'Saving…' : 'Save profile'} <span>↗</span></button></div>
      </div>
      <aside className="editor-aside"><div className="aside-wave"><div className="eyebrow">AUDIO NOTE</div><div className="mini-wave">{Array.from({ length: 24 }, (_, i) => <i key={i} style={{ height: `${14 + Math.abs(Math.sin(i * 1.7)) * 54}px` }} />)}</div></div><h3>Configure once.<br />Reuse anytime.</h3><p>Profiles are stored by the backend. Changes apply to workflows started after saving.</p><div className="aside-divider" /><small>Running workflows keep the keyword set they received at startup. Track them from the overview.</small></aside>
    </form>
  </div>
}
