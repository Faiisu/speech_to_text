import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { ApiRequestError, api } from '../../shared/api'
import { LANGUAGE_LABELS, type DecodingOptions, type Language, type Microphone, type ModelChoice, type Recording, type RuntimeChoice, type Transcription } from '../../shared/types'

type Settings = {
  model: string
  runtime: RuntimeChoice['key']
  language: Language
  silence_threshold: number
  keywords: string[]
  decoding_options: DecodingOptions
}

type Trial = {
  workflowId: string
  recordingId: string
  recordingLabel: string
  settings: Settings
  status: string
  transcript: Transcription | null
  measurements: ChunkMeasurement[]
  eventCursor: number
  error: string
}

type ChunkMeasurement = {
  sequence: number
  audio_seconds: number
  queue_wait_seconds: number
  inference_seconds: number
  rtf: number | null
  status: string
  error?: string | null
}

const isActive = (status: string) => ['queued', 'running', 'recording', 'stopping'].includes(status)
const hasAudio = (recording: Recording | null) => Boolean(recording && ['completed', 'failed'].includes(recording.status))
const canTranscribe = (recording: Recording | null) => Boolean(hasAudio(recording) && recording && recording.duration_seconds > 0)
const formatDuration = (seconds: number) => `${Math.floor(seconds / 60).toString().padStart(2, '0')}:${Math.floor(seconds % 60).toString().padStart(2, '0')}`
const MAX_UPLOAD_BYTES = 20 * 1024 * 1024

export default function ModelTuning() {
  const [microphones, setMicrophones] = useState<Microphone[]>([])
  const [microphoneError, setMicrophoneError] = useState('')
  const [models, setModels] = useState<ModelChoice[]>([])
  const [recordings, setRecordings] = useState<Recording[]>([])
  const [selectedRecordingId, setSelectedRecordingId] = useState('')
  const [device, setDevice] = useState('')
  const [sourceType, setSourceType] = useState<'microphone' | 'rtsp'>('microphone')
  const [rtspMode, setRtspMode] = useState<'url' | 'secret_ref'>('url')
  const [rtspUrl, setRtspUrl] = useState('')
  const [rtspUsername, setRtspUsername] = useState('')
  const [rtspPassword, setRtspPassword] = useState('')
  const [rtspSecretRef, setRtspSecretRef] = useState('')
  const [model, setModel] = useState('')
  const [runtime, setRuntime] = useState<RuntimeChoice['key'] | ''>('')
  const [language, setLanguage] = useState<Language>('th')
  const [silenceThreshold, setSilenceThreshold] = useState('0')
  const [beamSize, setBeamSize] = useState('1')
  const [temperature, setTemperature] = useState('0')
  const [conditionOnPreviousText, setConditionOnPreviousText] = useState(false)
  const [noRepeatNgramSize, setNoRepeatNgramSize] = useState('3')
  const [repetitionPenalty, setRepetitionPenalty] = useState('1')
  const [keywords, setKeywords] = useState('สวัสดี')
  const [trials, setTrials] = useState<Trial[]>([])
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(true)
  const [recordingBusy, setRecordingBusy] = useState(false)
  const [uploadFile, setUploadFile] = useState<File | null>(null)
  const [uploading, setUploading] = useState(false)
  const [tuningBusy, setTuningBusy] = useState(false)
  const [audioPlaying, setAudioPlaying] = useState(false)
  const [downloading, setDownloading] = useState(false)
  const inFlight = useRef(false)

  const selectedRecording = recordings.find(item => item.recording_id === selectedRecordingId) ?? null
  const activeRecording = recordings.find(item => item.status === 'recording') ?? null
  const selectedModel = models.find(item => item.key === model)
  const runtimes = selectedModel?.runtimes ?? []
  const selectedRuntime = runtimes.find(item => item.key === runtime)
  const ready = Boolean(selectedModel?.ready && selectedRuntime?.compatible && selectedRuntime.ready)
  const enabledMicrophones = useMemo(() => microphones.filter(item => item.selectable), [microphones])
  const trialRunning = trials.some(item => isActive(item.status))

  const load = useCallback(async () => {
    const results = await Promise.allSettled([api.microphones(), api.models(), api.recordings()])
    const [deviceResult, modelResult, recordingResult] = results
    if (deviceResult.status === 'fulfilled') {
      setMicrophones(deviceResult.value)
      setMicrophoneError('')
    } else {
      setMicrophoneError('Microphone discovery is unavailable. IPCAM and audio uploads remain available.')
    }
    if (modelResult.status === 'fulfilled') {
      setModels(modelResult.value.models)
      setModel(current => current || modelResult.value.default_model)
      setRuntime(current => current || modelResult.value.default_runtime as RuntimeChoice['key'])
    }
    if (recordingResult.status === 'fulfilled') {
      setRecordings(recordingResult.value)
      setSelectedRecordingId(current => current || recordingResult.value.find(item => item.status === 'completed')?.recording_id || recordingResult.value[0]?.recording_id || '')
    }
    const failures = results.slice(1).filter((result): result is PromiseRejectedResult => result.status === 'rejected')
    if (failures.length) setError(failures[0].reason instanceof Error ? failures[0].reason.message : 'Could not load model tuning data.')
    else setError('')
    setLoading(false)
  }, [])

  const refreshRecordings = useCallback(async () => {
    const updated = await api.recordings()
    setRecordings(updated)
    setSelectedRecordingId(current => current || updated.find(item => item.status === 'completed')?.recording_id || updated[0]?.recording_id || '')
  }, [])

  useEffect(() => { void load() }, [load])
  useEffect(() => {
    if (!recordings.some(item => item.status === 'recording')) return
    let pending = false
    const timer = window.setInterval(() => {
      if (pending) return
      pending = true
      void refreshRecordings().catch(reason => setError(reason instanceof Error ? reason.message : 'Could not refresh recording status.')).finally(() => { pending = false })
    }, 900)
    return () => window.clearInterval(timer)
  }, [recordings, refreshRecordings])

  useEffect(() => {
    if (!trials.some(item => isActive(item.status))) return
    const timer = window.setInterval(() => {
      if (inFlight.current) return
      inFlight.current = true
      void Promise.all(trials.filter(item => isActive(item.status)).map(async trial => {
        try {
          const snapshot = await api.transcription(trial.workflowId)
          const page = await api.events(trial.workflowId, trial.eventCursor)
          const measurements = page.events.filter(event => event.type === 'measurement') as unknown as ChunkMeasurement[]
          setTrials(current => current.map(item => item.workflowId === trial.workflowId ? {
            ...item,
            status: snapshot.status,
            transcript: snapshot,
            measurements: [...item.measurements, ...measurements],
            eventCursor: page.next_cursor,
            error: snapshot.error || '',
          } : item))
        } catch (reason) {
          const missing = reason instanceof ApiRequestError && reason.status === 404
          setTrials(current => current.map(item => item.workflowId === trial.workflowId ? { ...item, status: missing ? 'missing' : item.status, error: missing ? 'This trial is no longer available from the backend.' : reason instanceof Error ? reason.message : 'Could not refresh this trial.' } : item))
        }
      })).finally(() => { inFlight.current = false })
    }, 1200)
    return () => window.clearInterval(timer)
  }, [trials])

  const startRecording = async () => {
    setRecordingBusy(true); setError('')
    try {
      const source = sourceType === 'rtsp'
        ? rtspMode === 'url'
          ? { source_type: 'rtsp' as const, rtsp_url: rtspUrl.trim(), ...(rtspUsername.trim() ? { rtsp_username: rtspUsername.trim() } : {}), ...(rtspPassword ? { rtsp_password: rtspPassword } : {}) }
          : { source_type: 'rtsp' as const, rtsp_url_secret_ref: rtspSecretRef.trim() }
        : undefined
      const started = await api.startRecording(sourceType === 'rtsp' ? null : device || null, source)
      setRecordings(current => [started, ...current.filter(item => item.recording_id !== started.recording_id)])
      setSelectedRecordingId(started.recording_id)
    } catch (reason) { setError(reason instanceof Error ? reason.message : 'Could not start recording.') }
    finally { setRecordingBusy(false) }
  }

  const stopRecording = async () => {
    if (!activeRecording) return
    setRecordingBusy(true); setError('')
    try {
      const stopped = await api.stopRecording(activeRecording.recording_id)
      setRecordings(current => current.map(item => item.recording_id === stopped.recording_id ? stopped : item))
      setSelectedRecordingId(stopped.recording_id)
    } catch (reason) { setError(reason instanceof Error ? reason.message : 'Could not stop recording.') }
    finally { setRecordingBusy(false) }
  }

  const uploadRecording = async () => {
    if (!uploadFile || uploading) return
    setUploading(true); setError('')
    try {
      const imported = await api.uploadRecording(uploadFile)
      setRecordings(current => [imported, ...current.filter(item => item.recording_id !== imported.recording_id)])
      setSelectedRecordingId(imported.recording_id)
      setUploadFile(null)
      const input = document.getElementById('tuning-audio-file') as HTMLInputElement | null
      if (input) input.value = ''
    } catch (reason) { setError(reason instanceof Error ? reason.message : 'Could not upload audio file.') }
    finally { setUploading(false) }
  }

  const deleteRecording = async () => {
    if (!selectedRecording || selectedRecording.status === 'recording' || audioPlaying || downloading || trials.some(item => item.recordingId === selectedRecording.recording_id && isActive(item.status))) return
    setRecordingBusy(true); setError('')
    try {
      await api.deleteRecording(selectedRecording.recording_id)
      const remaining = recordings.filter(item => item.recording_id !== selectedRecording.recording_id)
      setRecordings(remaining)
      setSelectedRecordingId(remaining.find(item => item.status === 'completed')?.recording_id || '')
    } catch (reason) { setError(reason instanceof Error ? reason.message : 'Could not delete recording.') }
    finally { setRecordingBusy(false) }
  }

  const downloadRecording = async () => {
    if (!selectedRecording || downloading) return
    setDownloading(true)
    try {
      const blob = await api.recordingAudio(selectedRecording.recording_id)
      const url = URL.createObjectURL(blob)
      const anchor = document.createElement('a')
      anchor.href = url
      anchor.download = `recording-${selectedRecording.recording_id}.wav`
      anchor.click()
      window.setTimeout(() => URL.revokeObjectURL(url), 1000)
    } catch (reason) { setError(reason instanceof Error ? reason.message : 'Could not download WAV audio.') }
    finally { setDownloading(false) }
  }

  const runTrial = async () => {
    if (!selectedRecording || !hasAudio(selectedRecording)) return
    const words = keywords.split(/[\n,]/).map(word => word.trim()).filter(Boolean)
    const threshold = Number(silenceThreshold)
    const beam = Number(beamSize), temp = Number(temperature), noRepeat = Number(noRepeatNgramSize), penalty = Number(repetitionPenalty)
    if (!words.length || new Set(words).size !== words.length) { setError('Enter at least one unique keyword.'); return }
    if (!Number.isFinite(threshold) || threshold < 0 || threshold >= 1) { setError('Noise floor must be at least 0 and less than 1.'); return }
    if (!Number.isInteger(beam) || beam < 1 || beam > 10 || !Number.isFinite(temp) || temp < 0 || temp > 1 || !Number.isInteger(noRepeat) || noRepeat < 0 || noRepeat > 20 || !Number.isFinite(penalty) || penalty < 1 || penalty > 2) { setError('Check the decoding settings and their allowed ranges.'); return }
    if (!selectedModel || !selectedRuntime?.compatible) { setError('Choose a model and compatible runtime.'); return }
    const settings: Settings = { model, runtime: runtime as RuntimeChoice['key'], language, silence_threshold: threshold, keywords: words, decoding_options: { beam_size: beam, temperature: temp, condition_on_previous_text: conditionOnPreviousText, no_repeat_ngram_size: noRepeat, repetition_penalty: penalty } }
    setTuningBusy(true); setError('')
    try {
      const blob = await api.recordingAudio(selectedRecording.recording_id)
      if (blob.size <= 44 || selectedRecording.duration_seconds <= 0) throw new Error('The WAV recording contains no audio. Record a new clip before tuning.')
      const form = new FormData()
      form.append('file', new File([blob], `recording-${selectedRecording.recording_id}.wav`, { type: 'audio/wav' }))
      words.forEach(word => form.append('keywords', word))
      form.append('model', settings.model)
      form.append('runtime', settings.runtime)
      form.append('language', settings.language)
      form.append('silence_threshold', String(settings.silence_threshold))
      form.append('decoding_options', JSON.stringify(settings.decoding_options))
      const started = await api.transcribeClip(form)
      const sourceLabel = selectedRecording.source === 'rtsp' ? 'IPCAM (RTSP) · ' : ''
      const label = selectedRecording.filename || `${sourceLabel}${new Date(selectedRecording.created_at).toLocaleString()} · ${formatDuration(selectedRecording.duration_seconds)}`
      setTrials(current => [{ workflowId: started.workflow_id, recordingId: selectedRecording.recording_id, recordingLabel: label, settings, status: started.status, transcript: null, measurements: [], eventCursor: 0, error: '' }, ...current])
    } catch (reason) { setError(reason instanceof Error ? reason.message : 'Could not start a tuning trial.') }
    finally { setTuningBusy(false) }
  }

  if (loading) return <div className="page-content"><p>Loading model tuning…</p></div>
  return <div className="page-content tuning-page">
    <header className="tuning-heading">
      <div><div className="eyebrow">MODEL LAB <span className="tuning-heading-rule" /></div><h1>Model tuning</h1><p>Record or upload audio, then replay the same clip with different model settings and compare the transcript.</p></div>
      <div className="tuning-host-note"><span>◉</span><div><b>BACKEND AUDIO INPUT</b><small>Capture runs on this host · maximum 120 seconds</small></div></div>
    </header>
    {error && <div className="tuning-error" role="alert"><b>Action needs attention</b><span>{error}</span><button className="button button-outline" onClick={() => { setError(''); void load() }}>Refresh</button></div>}
    <section className="tuning-recording" aria-label="Audio recording">
      <div className="tuning-recording-main">
        <div className="tuning-section-label"><span className="tuning-step">01</span><div><h2>Record a reusable clip</h2><p>Audio is saved by the backend and reused for every trial.</p></div></div>
        <label htmlFor="tuning-source">Input source</label>
        <select id="tuning-source" value={sourceType} disabled={Boolean(activeRecording) || recordingBusy} onChange={event => { const next = event.target.value as 'microphone' | 'rtsp'; setSourceType(next); if (next === 'microphone') { setRtspUsername(''); setRtspPassword('') } }}><option value="microphone">Microphone</option><option value="rtsp">IPCAM (RTSP)</option></select>
        {sourceType === 'microphone' ? <>
          <label htmlFor="tuning-microphone">Microphone</label>
          <select id="tuning-microphone" value={device} disabled={Boolean(activeRecording) || recordingBusy} onChange={event => setDevice(event.target.value)}><option value="">System default microphone</option>{device && !enabledMicrophones.some(item => item.name === device) && <option value={device}>{device} · unavailable</option>}{enabledMicrophones.map(item => <option key={item.name} value={item.name}>{item.name}{item.is_default ? ' · Default' : ''}</option>)}</select>
          <small>Input devices are discovered by the backend host.</small>
          {microphoneError && <small className="tuning-mic-notice" role="status">{microphoneError}</small>}
        </> : <>
          <label htmlFor="tuning-rtsp-mode">RTSP address source</label>
          <select id="tuning-rtsp-mode" value={rtspMode} disabled={Boolean(activeRecording) || recordingBusy} onChange={event => { const next = event.target.value as 'url' | 'secret_ref'; setRtspMode(next); if (next === 'secret_ref') { setRtspUsername(''); setRtspPassword('') } }}><option value="url">Direct URL</option><option value="secret_ref">Environment variable reference</option></select>
          {rtspMode === 'url' ? <><label htmlFor="tuning-rtsp-url">RTSP URL<input id="tuning-rtsp-url" type="text" value={rtspUrl} disabled={Boolean(activeRecording) || recordingBusy} onChange={event => { const value = event.target.value; setRtspUrl(value); if (/^rtsps?:\/\/[^/]*@/i.test(value)) { setRtspUsername(''); setRtspPassword('') } }} placeholder="rtsp://camera.local:554/audio" /></label><label htmlFor="tuning-rtsp-username">Username<input id="tuning-rtsp-username" value={rtspUsername} disabled={Boolean(activeRecording) || recordingBusy} onChange={event => setRtspUsername(event.target.value)} autoComplete="username" /></label><label htmlFor="tuning-rtsp-password">Password<input id="tuning-rtsp-password" type="password" value={rtspPassword} disabled={Boolean(activeRecording) || recordingBusy} onChange={event => setRtspPassword(event.target.value)} autoComplete="new-password" /></label><small>Paste a complete authenticated URL, or enter separate credentials. Password stays in this page until you leave.</small></> : <label htmlFor="tuning-rtsp-secret-ref">Environment variable name<input id="tuning-rtsp-secret-ref" value={rtspSecretRef} disabled={Boolean(activeRecording) || recordingBusy} onChange={event => setRtspSecretRef(event.target.value)} placeholder="CAMERA_AUDIO_URL" /></label>}
          {rtspMode === 'secret_ref' && <small>Use an approved backend environment variable for the complete RTSP URL.</small>}
        </>}
        <div className="tuning-rec-actions">{activeRecording ? <button className="button button-dark" disabled={recordingBusy} onClick={() => void stopRecording()}><i className="recording-square" />{recordingBusy ? 'Stopping…' : 'Stop recording'}</button> : <button className="button button-lime" disabled={recordingBusy || (sourceType === 'rtsp' && (rtspMode === 'url' ? !rtspUrl.trim() : !rtspSecretRef.trim()))} onClick={() => void startRecording()}><i className="recording-dot" />{recordingBusy ? 'Starting…' : 'Start recording'}</button>}<span className={activeRecording ? 'recording-state active' : 'recording-state'}><i />{activeRecording ? `Recording · ${formatDuration(activeRecording.duration_seconds)}` : selectedRecording?.status === 'completed' ? `Saved · ${formatDuration(selectedRecording.duration_seconds)}` : 'Ready to record'}</span></div>
        {activeRecording && <p className="tuning-live-hint" role="status">The backend will end capture at 120 seconds. You can stop it here at any time.</p>}
        <div className="tuning-upload">
          <label htmlFor="tuning-audio-file">Audio file</label>
          <input id="tuning-audio-file" type="file" accept=".wav,.mp3,.m4a,audio/wav,audio/mpeg,audio/mp4" disabled={uploading} onChange={event => {
            const file = event.currentTarget.files?.[0] ?? null
            if (file && file.size > MAX_UPLOAD_BYTES) { setUploadFile(null); setError('Audio files must be 20 MiB or smaller.'); event.currentTarget.value = ''; return }
            setError(''); setUploadFile(file)
          }} />
          <small>Upload PCM16 WAV, MP3, or M4A audio, up to 20 MiB and 120 seconds. Files are saved as WAV.</small>
          <button className="button button-outline" disabled={!uploadFile || uploading} onClick={() => void uploadRecording()}>{uploading ? 'Uploading audio…' : 'Upload audio'}</button>
        </div>
      </div>
      <div className="tuning-recording-saved">
        <div className="saved-header"><span className="eyebrow">SAVED RECORDINGS</span><button className="text-button" onClick={() => { void load() }}>Refresh ↻</button></div>
        <label htmlFor="saved-recording">Choose a clip</label>
        <select id="saved-recording" value={selectedRecordingId} onChange={event => setSelectedRecordingId(event.target.value)}><option value="">Select a saved recording</option>{recordings.map(item => <option key={item.recording_id} value={item.recording_id}>{item.source === 'rtsp' ? 'IPCAM (RTSP) · ' : ''}{item.filename ? `${item.filename} · ` : `${new Date(item.created_at).toLocaleString()} · `}{item.status} · {formatDuration(item.duration_seconds)}</option>)}</select>
        {selectedRecording ? <div className="recording-details"><div><small>STATUS</small><b className={`recording-status ${selectedRecording.status}`}>{selectedRecording.status}</b></div><div><small>DURATION</small><b>{formatDuration(selectedRecording.duration_seconds)}</b></div><div><small>{selectedRecording.filename ? 'FILE' : 'INPUT'}</small><b title={selectedRecording.filename || selectedRecording.device || (selectedRecording.source === 'rtsp' ? 'IPCAM (RTSP)' : 'System default')}>{selectedRecording.source === 'rtsp' ? 'IPCAM (RTSP)' : selectedRecording.filename || selectedRecording.device || 'System default'}</b></div></div> : <p className="tuning-empty">No saved recordings yet. Record a clip or upload an audio file.</p>}
        {selectedRecording?.warnings?.map(warning => <p className="tuning-warning" key={warning}>{warning}</p>)}
        {selectedRecording?.status === 'failed' && <p className="tuning-warning">{selectedRecording.error || 'This recording failed. Start a new recording.'}</p>}
        {hasAudio(selectedRecording) && <><audio className="tuning-audio" controls preload="none" src={`/api/v1/recordings/${encodeURIComponent(selectedRecording!.recording_id)}/audio`} onPlay={() => setAudioPlaying(true)} onPause={() => setAudioPlaying(false)} onEnded={() => setAudioPlaying(false)} /><div className="recording-file-actions"><button className="tuning-download" disabled={downloading} onClick={() => void downloadRecording()}>{downloading ? 'Preparing WAV…' : 'Download WAV'} <span>↓</span></button><button className="text-button danger" disabled={recordingBusy || audioPlaying || downloading || trials.some(item => item.recordingId === selectedRecording!.recording_id && isActive(item.status))} onClick={() => void deleteRecording()}>Delete</button></div></>}
      </div>
    </section>

    <section className="tuning-config" aria-label="Model tuning configuration">
      <div className="tuning-config-head"><div className="tuning-section-label"><span className="tuning-step">02</span><div><h2>Choose model settings</h2><p>Each trial records a snapshot of these values for comparison.</p></div></div><span className={`tuning-readiness ${ready ? 'ready' : ''}`}><i />{ready ? 'MODEL READY' : selectedRuntime?.reason || 'CHECK MODEL READINESS'}</span></div>
      <div className="tuning-config-grid">
        <label>Keywords<textarea rows={3} value={keywords} onChange={event => setKeywords(event.target.value)} placeholder="One keyword per line" /><small>One phrase per line or comma separated.</small></label>
        <label>Language<select value={language} onChange={event => setLanguage(event.target.value as Language)}>{(['th', 'en', 'auto'] as const).map(value => <option key={value} value={value}>{LANGUAGE_LABELS[value]}</option>)}</select><small>Auto detect allows runtime language detection but does not guarantee recognition of code switching. Language support depends on the model.</small></label>
        <label>Model<select value={model} onChange={event => { const next = models.find(item => item.key === event.target.value); setModel(event.target.value); if (!next?.runtimes.some(item => item.key === runtime && item.compatible)) setRuntime((next?.runtimes.find(item => item.compatible)?.key ?? 'openvino-gpu') as RuntimeChoice['key']) }}>{models.map(item => <option key={item.key} value={item.key}>{item.display_name} ({item.key}){!item.installed ? ' · not installed' : !item.ready ? ' · unavailable' : ''}</option>)}</select><small>{selectedModel?.ready ? 'Installed and available on this host.' : selectedModel?.installed ? 'Model is installed; a compatible runtime may be unavailable.' : 'Model is not installed on this host.'}</small></label>
        <label>Runtime<select value={runtime} onChange={event => setRuntime(event.target.value as RuntimeChoice['key'])}>{runtimes.map(item => <option key={item.key} value={item.key} disabled={!item.compatible}>{item.key}{!item.compatible ? ' · incompatible' : !item.ready ? ' · unavailable' : ' · ready'}</option>)}</select><small>{selectedRuntime?.reason || 'Choose a runtime supported by the selected model.'}</small></label>
        <label>Noise floor (RMS silence threshold)<input type="number" min="0" max="0.999999" step="any" value={silenceThreshold} onChange={event => setSilenceThreshold(event.target.value)} /><small>0 disables silence filtering. Range: 0 to less than 1.</small></label>
        <label>Beam size<input type="number" min="1" max="10" step="1" value={beamSize} onChange={event => setBeamSize(event.target.value)} /><small>Search width, 1 to 10. Default: 1.</small></label>
        <label>Temperature<input type="number" min="0" max="1" step="any" value={temperature} onChange={event => setTemperature(event.target.value)} /><small>Variation from 0 to 1. Default: 0.</small></label>
        <label>No-repeat n-gram size<input type="number" min="0" max="20" step="1" value={noRepeatNgramSize} onChange={event => setNoRepeatNgramSize(event.target.value)} /><small>0 disables this constraint. Default: 3.</small></label>
        <label>Repetition penalty<input type="number" min="1" max="2" step="any" value={repetitionPenalty} onChange={event => setRepetitionPenalty(event.target.value)} /><small>Values above 1 discourage repeats. Default: 1.</small></label>
      </div>
      <label className="tuning-check"><input type="checkbox" checked={conditionOnPreviousText} onChange={event => setConditionOnPreviousText(event.target.checked)} />Condition on previous text <small>Default: off.</small></label>
      <div className="tuning-config-footer"><span>{trialRunning ? 'Wait for the current trial to finish before starting another.' : hasAudio(selectedRecording) ? `Using saved WAV · ${selectedRecording!.recording_id.slice(0, 8)}` : 'Record and save a clip before starting a trial.'}</span><button className="button button-dark" onClick={() => void runTrial()} disabled={tuningBusy || trialRunning || !canTranscribe(selectedRecording) || !ready || Boolean(activeRecording)}>{trialRunning ? 'Trial in progress' : tuningBusy ? 'Preparing audio…' : 'Run tuning trial'} <span>↗</span></button></div>
    </section>

    <section className="tuning-trials" aria-labelledby="tuning-trials-title">
      <div className="tuning-trials-heading"><div><div className="eyebrow">CURRENT PAGE SESSION / {trials.length.toString().padStart(2, '0')} TRIALS</div><h2 id="tuning-trials-title">Trial comparison</h2></div></div>
      {trials.length === 0 ? <div className="tuning-trials-empty"><span>⌁</span><b>No trials yet</b><p>Run a trial to see its transcript, keyword counts, speed, and configuration snapshot.</p></div> : <div className="tuning-trial-list">{trials.map((trial, index) => <TrialCard key={trial.workflowId} trial={trial} index={trials.length - index} />)}</div>}
    </section>
  </div>
}

function TrialCard({ trial, index }: { trial: Trial; index: number }) {
  const snapshot = trial.transcript
  const config = {
    ...trial.settings,
    model: snapshot?.model ?? trial.settings.model,
    runtime: snapshot?.runtime ?? trial.settings.runtime,
    language: snapshot?.language ?? trial.settings.language,
    silence_threshold: snapshot?.silence_threshold ?? trial.settings.silence_threshold,
    keywords: snapshot?.keywords ?? trial.settings.keywords,
    decoding_options: snapshot?.decoding_options ?? trial.settings.decoding_options,
  }
  const counts = snapshot?.matches ?? []
  return <article className="tuning-trial-card">
    <header><div className="trial-number">{String(index).padStart(2, '0')}</div><div className="trial-card-title"><b>Trial {index}</b><small>{trial.recordingLabel} · WAV {trial.recordingId.slice(0, 8)}</small><small>{config.model} · {config.runtime} · {LANGUAGE_LABELS[config.language]} · silence {config.silence_threshold}</small></div><span className={`trial-status ${trial.status}`}><i />{trial.status}</span></header>
    {trial.error && <p className="trial-error">{trial.error}</p>}
    {snapshot?.error && snapshot.error !== trial.error && <p className="trial-error">{snapshot.error}</p>}
    {snapshot?.transcript && <div className="trial-transcript"><small>TRANSCRIPT · {LANGUAGE_LABELS[config.language].toUpperCase()}</small><p>{snapshot.transcript}</p></div>}
    {snapshot?.status === 'completed' && <div className="trial-result-grid"><div><small>KEYWORD COUNTS</small>{counts.length ? counts.map(match => <span key={match.keyword}>{match.keyword}<b>{match.count}</b></span>) : <span>No matches</span>}</div><div><small>LATEST CHUNK RTF</small><b>{snapshot.latest_rtf == null ? '—' : snapshot.latest_rtf.toFixed(3)}</b></div></div>}
    <section className="trial-measurements" aria-label={`Chunk measurements for trial ${index}`}>
      <h3>Chunk RTF</h3>
      {trial.measurements.length === 0 ? <p>{trial.status === 'completed' ? 'No chunks reached inference.' : trial.status === 'failed' ? 'No chunk measurements were reported.' : 'Waiting for the first chunk measurement.'}</p> : <div className="trial-measurements-scroll"><table><thead><tr><th scope="col">Chunk</th><th scope="col">Audio</th><th scope="col">Queue wait</th><th scope="col">Inference</th><th scope="col">RTF</th><th scope="col">Status</th></tr></thead><tbody>{trial.measurements.map(measurement => <tr key={measurement.sequence}><th scope="row">{measurement.sequence + 1}</th><td>{measurement.audio_seconds.toFixed(2)} s</td><td>{measurement.queue_wait_seconds.toFixed(3)} s</td><td>{measurement.inference_seconds.toFixed(3)} s</td><td>{typeof measurement.rtf === 'number' && Number.isFinite(measurement.rtf) ? measurement.rtf.toFixed(3) : '—'}</td><td>{measurement.status}{measurement.error ? ` · ${measurement.error}` : ''}</td></tr>)}</tbody></table></div>}
    </section>
    <details className="trial-settings"><summary>Settings snapshot</summary><div className="trial-setting-values"><span>Model <b>{config.model}</b></span><span>Runtime <b>{config.runtime}</b></span><span>Language <b>{LANGUAGE_LABELS[config.language]}</b></span><span>Silence threshold <b>{config.silence_threshold}</b></span><span>Keywords <b>{config.keywords.join(', ')}</b></span>{Object.entries(config.decoding_options).map(([key, value]) => <span key={key}>{key.replaceAll('_', ' ')} <b>{String(value)}</b></span>)}</div></details>
  </article>
}
