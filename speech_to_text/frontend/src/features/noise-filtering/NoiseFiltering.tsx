import { useEffect, useMemo, useRef, useState } from 'react'
import { api } from '../../shared/api'
import type { FilteredRecordingResult, Recording, RecordingSpectrogram } from '../../shared/types'
import './noise-filtering.css'

const playable = (recording: Recording) => ['completed', 'failed'].includes(recording.status) && recording.duration_seconds > 0
const formatDuration = (seconds: number) => `${Math.floor(seconds / 60).toString().padStart(2, '0')}:${Math.floor(seconds % 60).toString().padStart(2, '0')}`
const MAX_UPLOAD_BYTES = 20 * 1024 * 1024

export default function NoiseFiltering() {
  const [recordings, setRecordings] = useState<Recording[]>([])
  const [selectedId, setSelectedId] = useState('')
  const [uploadFile, setUploadFile] = useState<File | null>(null)
  const [lowHz, setLowHz] = useState('100')
  const [highHz, setHighHz] = useState('7000')
  const [result, setResult] = useState<FilteredRecordingResult | null>(null)
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState<'uploading' | 'filtering' | ''>('')
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const mounted = useRef(false)

  const selected = recordings.find(item => item.recording_id === selectedId) ?? null
  const nyquist = useMemo(() => selected?.sample_rate ? selected.sample_rate / 2 : 0, [selected])
  const validBounds = Number.isFinite(Number(lowHz)) && Number.isFinite(Number(highHz)) && Number(lowHz) > 0 && Number(highHz) > Number(lowHz) && Number(highHz) < nyquist
  const eligibleRecordings = recordings.filter(playable)

  const refreshRecordings = async () => {
    setError('')
    try {
      const items = await api.recordings()
      if (!mounted.current) return
      setRecordings(items)
      setSelectedId(current => current || items.find(playable)?.recording_id || '')
    } catch (reason) {
      if (mounted.current) setError(reason instanceof Error ? reason.message : 'Could not load saved recordings.')
      throw reason
    }
  }

  useEffect(() => {
    mounted.current = true
    void refreshRecordings().catch(() => {}).finally(() => { if (mounted.current) setLoading(false) })
    return () => { mounted.current = false }
  }, [])

  useEffect(() => {
    if (selected?.sample_rate) setHighHz(current => Number(current) >= nyquist ? String(Math.floor(nyquist * 0.875)) : current)
  }, [selected?.recording_id, nyquist])

  const upload = async () => {
    if (!uploadFile || busy) return
    if (!/\.(wav|mp3|m4a)$/i.test(uploadFile.name)) { setError('Choose a WAV, MP3, or M4A audio file.'); return }
    if (uploadFile.size > MAX_UPLOAD_BYTES) { setError('Audio files must be 20 MiB or smaller.'); return }
    setBusy('uploading'); setError(''); setNotice('')
    try {
      const imported = await api.uploadRecording(uploadFile)
      if (!mounted.current) return
      setRecordings(current => [imported, ...current.filter(item => item.recording_id !== imported.recording_id)])
      setSelectedId(imported.recording_id)
      setUploadFile(null)
      const input = document.getElementById('noise-filter-file') as HTMLInputElement | null
      if (input) input.value = ''
      setNotice(`${imported.filename || 'Audio'} is ready to filter.`)
    } catch (reason) { if (mounted.current) setError(reason instanceof Error ? reason.message : 'Could not upload audio.') }
    finally { if (mounted.current) setBusy('') }
  }

  const filter = async () => {
    if (!selected || !playable(selected) || !validBounds || busy) return
    const snapshot = selected
    const bounds = { low_hz: Number(lowHz), high_hz: Number(highHz) }
    setBusy('filtering'); setError(''); setNotice('')
    try {
      const filtered = await api.filterRecording({ recording_id: snapshot.recording_id, ...bounds })
      if (!mounted.current) return
      setRecordings(current => [filtered.recording, ...current.filter(item => item.recording_id !== filtered.recording.recording_id)])
      setResult(filtered)
      setNotice('Filtered audio saved as a new recording. The source clip is unchanged.')
    } catch (reason) { if (mounted.current) setError(reason instanceof Error ? reason.message : 'Could not filter this recording.') }
    finally { if (mounted.current) setBusy('') }
  }

  if (loading) return <div className="page-content"><div className="loading"><span className="spinner" />Loading saved audio…</div></div>
  return <div className="page-content noise-filter-page">
    <header className="page-heading noise-filter-heading">
      <div><div className="eyebrow">AUDIO WORKSPACE / FREQUENCY FILTER</div><h1>Noise filtering</h1><p>Choose a saved clip, set its pass band, then listen to what changed.</p></div>
      <div className="noise-filter-signal" aria-hidden="true"><i /><i /><i /><i /><i /><i /><i /><i /><i /><i /><i /></div>
    </header>

    {error && <div className="api-error" role="alert"><span className="error-mark">!</span><div><strong>Could not continue</strong><p>{error}</p></div><button className="text-button" onClick={() => setError('')}>Dismiss</button></div>}
    {notice && <div className="notice" role="status"><span>{notice}</span><button aria-label="Dismiss" onClick={() => setNotice('')}>×</button></div>}

    <section className="noise-filter-setup" aria-label="Choose audio and filter frequencies">
      <div className="noise-filter-source">
        <div className="noise-filter-section-title"><span className="eyebrow">SOURCE AUDIO</span><h2>Choose a recording</h2><p>Use a saved clip or add a WAV, MP3, or M4A file.</p><button className="text-button noise-filter-refresh" onClick={() => void refreshRecordings().catch(() => {})}>Refresh saved audio</button></div>
        <label className="noise-filter-select">Saved audio<select aria-label="Saved audio" value={selectedId} disabled={Boolean(busy)} onChange={event => { setSelectedId(event.target.value); setError('') }}><option value="">Select a saved recording</option>{eligibleRecordings.map(item => <option key={item.recording_id} value={item.recording_id}>{item.filename || `${item.source === 'rtsp' ? 'IPCAM · ' : ''}${new Date(item.created_at).toLocaleString()}`} · {formatDuration(item.duration_seconds)}</option>)}</select></label>
        {selected ? <div className="noise-filter-recording-meta"><span><small>CLIP</small><b title={selected.filename || ''}>{selected.filename || (selected.source === 'rtsp' ? 'IPCAM (RTSP)' : 'Saved recording')}</b></span><span><small>DURATION</small><b>{formatDuration(selected.duration_seconds)}</b></span><span aria-label="Sample rate"><small>SAMPLE RATE</small><b>{selected.sample_rate ? `${selected.sample_rate.toLocaleString()} Hz` : '—'}</b></span></div> : <div className="noise-filter-empty">{eligibleRecordings.length ? 'Select a saved clip to begin.' : 'No saved clips yet. Upload audio to start filtering.'}</div>}
        <div className="noise-filter-upload"><label htmlFor="noise-filter-file">Add an audio file <span>WAV · MP3 · M4A, up to 20 MiB</span></label><input id="noise-filter-file" type="file" accept=".wav,.mp3,.m4a,audio/wav,audio/mpeg,audio/mp4" disabled={Boolean(busy)} onChange={event => { setUploadFile(event.target.files?.[0] ?? null); setError('') }} /><button className="button button-outline" disabled={!uploadFile || Boolean(busy)} onClick={() => void upload()}>{busy === 'uploading' ? 'Uploading…' : 'Upload audio'} <span>↑</span></button></div>
      </div>
      <div className="noise-filter-controls">
        <div className="noise-filter-section-title"><span className="eyebrow">BAND-PASS FILTER</span><h2>Keep a frequency range</h2><p>Frequencies outside these bounds are reduced.</p></div>
        <div className="noise-filter-bounds"><label>Low cutoff <span>Hz</span><input aria-label="Low cutoff frequency" type="number" min="1" max={nyquist - 1} step="any" value={lowHz} disabled={Boolean(busy)} onChange={event => setLowHz(event.target.value)} /></label><div className="noise-filter-range-mark" aria-hidden="true">to</div><label>High cutoff <span>Hz</span><input aria-label="High cutoff frequency" type="number" min="1" max={nyquist - 1} step="any" value={highHz} disabled={Boolean(busy)} onChange={event => setHighHz(event.target.value)} /></label></div>
        <div className="noise-filter-bound-note">{nyquist ? <>Valid range: 0 &lt; low &lt; high &lt; {nyquist.toLocaleString()} Hz Nyquist</> : selected ? 'Sample rate unavailable. Refresh saved audio to read the WAV header.' : 'Choose a recording to see its valid frequency range.'}</div>
        <button className="button button-dark noise-filter-action" disabled={!selected || !selected.sample_rate || !playable(selected) || !validBounds || Boolean(busy)} onClick={() => void filter()}>{busy === 'filtering' ? 'Filtering audio…' : 'Filter and compare'} <span>↗</span></button>
      </div>
    </section>

    {result ? <Comparison result={result} /> : <section className="noise-filter-preview-empty"><span className="noise-filter-preview-glyph" aria-hidden="true">⌁</span><div><h2>Your comparison will appear here</h2><p>Filtering creates a new saved WAV, then plots both clips on the same time, frequency, and dBFS scales.</p></div></section>}
  </div>
}

function Comparison({ result }: { result: FilteredRecordingResult }) {
  const { settings } = result
  const sourceName = result.source.filename || (result.source.source === 'rtsp' ? 'IPCAM recording' : `Recording ${result.source_recording_id.slice(0, 8)}`)
  const filteredName = result.recording.filename || `Filtered recording ${result.recording.recording_id.slice(0, 8)}`
  return <section className="noise-filter-comparison" aria-label="Before and after comparison">
    <div className="noise-filter-result-heading"><div><span className="eyebrow">FILTERED COPY / {settings.low_hz.toLocaleString()}–{settings.high_hz.toLocaleString()} HZ</span><h2>Before and after</h2><p>{sourceName} · {settings.sample_rate.toLocaleString()} Hz · {settings.channels === 1 ? 'Mono' : 'Stereo'} · {formatDuration(settings.duration_seconds)}</p></div><span className="noise-filter-snapshot">Saved WAV <b>{filteredName}</b></span></div>
    <div className="noise-filter-players"><AudioColumn label="Before" name={sourceName} recordingId={result.source_recording_id} /><AudioColumn label="After" name={filteredName} recordingId={result.recording.recording_id} /></div>
    <div className="noise-filter-plots">
      <Spectrogram title="Before filtering" data={result.before} duration={settings.duration_seconds} nyquist={settings.nyquist_hz} />
      <Spectrogram title="After filtering" data={result.after} duration={settings.duration_seconds} nyquist={settings.nyquist_hz} />
    </div>
    <div className="noise-filter-legend"><span>POWER / dBFS</span><div className="noise-filter-colorbar" aria-hidden="true" /><div className="noise-filter-color-ticks"><span>−100</span><span>−75</span><span>−50</span><span>−25</span><span>0 dBFS</span></div></div>
    <p className="noise-filter-method">Both plots use one absolute scale and average channel power before plotting. Band-pass filtering reduces frequencies outside the selected range; it does not identify noise that overlaps speech.</p>
  </section>
}

function AudioColumn({ label, name, recordingId }: { label: string; name: string; recordingId: string }) {
  return <div className="noise-filter-player"><div><span>{label}</span><b title={name}>{name}</b></div><audio controls preload="metadata" aria-label={`${label}: ${name}`} src={`/api/v1/recordings/${encodeURIComponent(recordingId)}/audio`} /></div>
}

function Spectrogram({ title, data, duration, nyquist }: { title: string; data: RecordingSpectrogram; duration: number; nyquist: number }) {
  const canvas = useRef<HTMLCanvasElement>(null)
  useEffect(() => {
    const element = canvas.current
    if (!element || !data.dbfs.length) return
    const context = element.getContext('2d')
    if (!context) return
    const width = 820, height = 260, left = 82, top = 18, plotWidth = 710, plotHeight = 190
    element.width = width; element.height = height
    context.fillStyle = '#f7f9f4'; context.fillRect(0, 0, width, height)
    const colors = [[0, 21, 41, 60], [0.25, 50, 116, 105], [0.5, 144, 175, 102], [0.75, 224, 188, 83], [1, 238, 118, 91]]
    const colorAt = (value: number) => {
      const t = Math.max(0, Math.min(1, (value + 100) / 100))
      const index = Math.min(colors.length - 2, Math.floor(t * (colors.length - 1)))
      const amount = t * (colors.length - 1) - index
      const start = colors[index], end = colors[index + 1]
      return `rgb(${Math.round(start[1] + amount * (end[1] - start[1]))},${Math.round(start[2] + amount * (end[2] - start[2]))},${Math.round(start[3] + amount * (end[3] - start[3]))})`
    }
    const edges = (centers: number[], minimum: number, maximum: number) => {
      if (centers.length === 1) return [minimum, maximum]
      return [minimum, ...centers.slice(1).map((center, index) => (centers[index] + center) / 2), maximum]
    }
    const frequencyEdges = edges(data.frequencies_hz, 0, nyquist)
    const timeEdges = edges(data.times_seconds, 0, duration)
    data.dbfs.forEach((row, rowIndex) => row.forEach((value, columnIndex) => {
      const x0 = left + timeEdges[columnIndex] / duration * plotWidth
      const x1 = left + timeEdges[columnIndex + 1] / duration * plotWidth
      const y0 = top + plotHeight - frequencyEdges[rowIndex + 1] / nyquist * plotHeight
      const y1 = top + plotHeight - frequencyEdges[rowIndex] / nyquist * plotHeight
      context.fillStyle = colorAt(value)
      context.fillRect(x0, y0, x1 - x0 + .5, y1 - y0 + .5)
    }))
    context.strokeStyle = '#94a49a'; context.lineWidth = 1; context.font = '10px IBM Plex Mono, monospace'; context.fillStyle = '#74857c'
    context.beginPath(); context.moveTo(left, top); context.lineTo(left, top + plotHeight); context.lineTo(left + plotWidth, top + plotHeight); context.stroke()
    context.font = '16px IBM Plex Mono, monospace'
    context.textAlign = 'right'; context.textBaseline = 'middle'
    for (let tick = 0; tick <= 4; tick++) {
      const y = top + plotHeight - plotHeight * tick / 4
      context.strokeStyle = '#ffffff55'; context.beginPath(); context.moveTo(left, y); context.lineTo(left + plotWidth, y); context.stroke()
      context.fillStyle = '#74857c'; context.fillText(`${Math.round(nyquist * tick / 4).toLocaleString()}`, left - 10, y)
    }
    context.textAlign = 'center'; context.textBaseline = 'top'
    for (let tick = 0; tick <= 4; tick++) {
      const x = left + plotWidth * tick / 4
      context.strokeStyle = '#ffffff55'; context.beginPath(); context.moveTo(x, top); context.lineTo(x, top + plotHeight); context.stroke()
      const timeLabel = Number((duration * tick / 4).toFixed(duration < 8 ? 2 : 0))
      context.textAlign = tick === 0 ? 'left' : tick === 4 ? 'right' : 'center'
      context.fillStyle = '#74857c'; context.fillText(`${timeLabel} s`, x, top + plotHeight + 12)
    }
  }, [data, duration, nyquist])
  return <article className="noise-filter-plot"><h3>{title}</h3><canvas ref={canvas} role="img" aria-label={`${title} spectrogram, frequency from 0 to ${nyquist} hertz and time from 0 to ${duration.toFixed(1)} seconds`} /><div className="noise-filter-axis-titles"><span>Frequency · Hz</span><span>Time · seconds</span></div></article>
}
