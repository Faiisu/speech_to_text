import { useCallback, useEffect, useRef, useState } from 'react'
import { api } from '../../shared/api'
import { ApiError, EmptyState, Loading } from '../../shared/States'
import type { Microphone, MicrophoneTest } from '../../shared/types'
import './live-audio.css'

const ACTIVE = new Set(['starting', 'running', 'stopping'])

function formatLevel(value: number | null) {
  return value === null || !Number.isFinite(value) ? '—' : value.toFixed(4)
}

function meterWidth(value: number | null) {
  return `${Math.max(0, Math.min(1, value ?? 0)) * 100}%`
}

function statusLabel(status: string) {
  return status.replaceAll('_', ' ').replace(/^./, value => value.toUpperCase())
}

export default function LiveAudio() {
  const [devices, setDevices] = useState<Microphone[]>([])
  const [selectedDevice, setSelectedDevice] = useState('')
  const [test, setTest] = useState<MicrophoneTest | null>(null)
  const [loading, setLoading] = useState(true)
  const [deviceError, setDeviceError] = useState('')
  const [monitorError, setMonitorError] = useState('')
  const [actionError, setActionError] = useState('')
  const [busy, setBusy] = useState<'starting' | 'stopping' | ''>('')
  const [monitorRefresh, setMonitorRefresh] = useState(0)
  const mounted = useRef(false)
  const latestTest = useRef<MicrophoneTest | null>(null)
  latestTest.current = test

  const loadDevices = useCallback(async () => {
    setLoading(true)
    try {
      setDevices(await api.microphones())
      setDeviceError('')
    } catch (cause) {
      setDeviceError(cause instanceof Error ? cause.message : 'Could not load microphone devices.')
    } finally { setLoading(false) }
  }, [])

  useEffect(() => { void loadDevices() }, [loadDevices])

  useEffect(() => {
    mounted.current = true
    return () => {
      mounted.current = false
      const current = latestTest.current
      if (current && ACTIVE.has(current.status)) void api.stopMicrophoneTest(current.test_id).catch(() => {})
    }
  }, [])

  useEffect(() => {
    if (!test || !ACTIVE.has(test.status)) return
    let current = true
    let inFlight = false
    const poll = async () => {
      if (inFlight) return
      inFlight = true
      try {
        const latest = await api.microphoneTest(test.test_id)
        if (!current) return
        setTest(latest)
        setMonitorError('')
      } catch (cause) {
        if (current) setMonitorError(cause instanceof Error ? cause.message : 'Could not read microphone test results.')
      } finally { inFlight = false }
    }
    const timer = window.setInterval(() => { void poll() }, 250)
    return () => { current = false; window.clearInterval(timer) }
  }, [test?.test_id, test?.status, monitorRefresh])

  const start = async () => {
    if (busy || (test && ACTIVE.has(test.status))) return
    setBusy('starting')
    setActionError('')
    setMonitorError('')
    try {
      const next = await api.startMicrophoneTest(selectedDevice || null)
      if (!mounted.current) {
        if (ACTIVE.has(next.status)) void api.stopMicrophoneTest(next.test_id).catch(() => {})
        return
      }
      setTest(next)
    } catch (cause) {
      if (mounted.current) setActionError(cause instanceof Error ? cause.message : 'Could not start the microphone test. Try again.')
    } finally { if (mounted.current) setBusy('') }
  }

  const stop = async () => {
    if (!test || busy || !ACTIVE.has(test.status)) return
    setBusy('stopping')
    setActionError('')
    try {
      const stopped = await api.stopMicrophoneTest(test.test_id)
      if (mounted.current) setTest(stopped)
    }
    catch (cause) { if (mounted.current) setActionError(cause instanceof Error ? cause.message : 'Could not stop the microphone test. Try again.') }
    finally { if (mounted.current) setBusy('') }
  }

  if (loading) return <div className="page-content"><Loading label="Checking available microphone devices…" /></div>
  return <div className="page-content live-audio-page">
    <header className="page-heading live-audio-heading">
      <div><div className="eyebrow">AUDIO INPUT / DEVICE CHECK</div><h1>Microphone test <span>Check that an input device is working.</span></h1><p>Capture a short live sample and inspect its signal level.</p></div>
      <button className="button button-outline" type="button" onClick={() => void loadDevices()} disabled={loading}>Refresh devices <span aria-hidden="true">↻</span></button>
    </header>

    {deviceError && <ApiError message={deviceError} retry={() => void loadDevices()} />}
    {actionError && <div className="live-action-error" role="alert"><span>{actionError}</span><button type="button" aria-label="Dismiss error" onClick={() => setActionError('')}>×</button></div>}

    <section className="mic-test-layout" aria-label="Microphone device test">
      <section className="panel mic-test-controls" aria-labelledby="device-test-heading">
        <div className="section-heading compact"><div><div className="eyebrow">HOST INPUTS</div><h2 id="device-test-heading">Select a device</h2></div><span className="event-count">{devices.filter(device => device.selectable).length} available</span></div>
        {deviceError ? <p className="muted-copy">Device list unavailable. Retry to check connected inputs.</p> : <>
          {devices.length === 0 && <p className="muted-copy">No named input devices were discovered. You can still test the system default input.</p>}
          <label className="live-source-label" htmlFor="microphone-device">Microphone</label>
          <select id="microphone-device" value={selectedDevice} disabled={!!test && ACTIVE.has(test.status)} onChange={event => setSelectedDevice(event.target.value)}>
            <option value="">System default microphone</option>
            {devices.map(device => <option key={device.name} value={device.name} disabled={!device.selectable}>{device.name}{device.selectable ? '' : ' · unavailable'}</option>)}
          </select>
          <div className="mic-device-details" role="status">
            {selectedDevice ? devices.find(device => device.name === selectedDevice)?.selectable
              ? <span>Selected device is available for testing.</span>
              : <span>Selected device is unavailable.</span>
              : <span>The operating system will choose the default input.</span>}
          </div>
          <div className="mic-test-actions">
            {test && ACTIVE.has(test.status)
              ? <button className="button button-outline danger-button" type="button" onClick={() => void stop()} disabled={!!busy}>{busy === 'stopping' || test.status === 'stopping' ? 'Stopping…' : 'Stop test'} <span aria-hidden="true">Ⅱ</span></button>
              : <button className="button button-dark" type="button" onClick={() => void start()} disabled={!!busy || !!deviceError || (!!selectedDevice && !devices.find(device => device.name === selectedDevice)?.selectable)}>{busy === 'starting' ? 'Starting…' : test ? 'Test again' : 'Start microphone test'} <span aria-hidden="true">▶</span></button>}
            {test && !ACTIVE.has(test.status) && <button className="button button-outline" type="button" onClick={() => { setTest(null); setMonitorError(''); setActionError('') }}>Clear result</button>}
          </div>
        </>}
        <p className="mic-test-privacy">Audio is checked in memory and discarded. This page does not record or transcribe it.</p>
      </section>

      <section className="panel mic-test-results" aria-labelledby="test-results-heading">
        <div className="section-heading compact"><div><div className="eyebrow">SIGNAL CHECK</div><h2 id="test-results-heading">Microphone level</h2></div>{test && <span className={`status-pill status-${test.status}`} role="status">{statusLabel(test.status)}</span>}</div>
        {monitorError && test && ACTIVE.has(test.status) && <ApiError message={monitorError} retry={() => { setMonitorError(''); setMonitorRefresh(value => value + 1) }} />}
        {!test ? <EmptyState title="No microphone test yet">Choose a device and start a test to see live signal levels and captured sample details.</EmptyState> : <>
          {test.error && <div className="mic-test-failure" role="alert"><strong>Microphone test failed</strong><p>{test.error}</p></div>}
          <div className="mic-levels" aria-live="polite">
            <SignalMeter label="RMS level" value={test.rms} />
            <SignalMeter label="Peak level" value={test.peak} />
          </div>
          <div className="mic-sample-grid" aria-label="Captured sample details">
            <SampleValue label="Sample rate" value={test.sample_rate ? `${test.sample_rate.toLocaleString()} Hz` : '—'} />
            <SampleValue label="Channels" value={test.channels === null ? '—' : String(test.channels)} />
            <SampleValue label="Frames received" value={test.frames_received.toLocaleString()} />
            <SampleValue label="Elapsed" value={`${test.elapsed_seconds.toFixed(2)} s`} />
          </div>
          {ACTIVE.has(test.status) && <p className="mic-test-running" role="status">{test.frames_received === 0 ? 'Waiting for audio frames…' : test.rms === 0 && test.peak === 0 ? 'Audio frames are arriving, but the current signal is silent.' : 'Audio signal detected.'} The test stops automatically after 30 seconds.</p>}
          {test.status === 'completed' && <p className="mic-test-complete" role="status">{completionMessage(test)}</p>}
          {test.status === 'stopped' && <p className="mic-test-complete" role="status">Test stopped.</p>}
        </>}
      </section>
    </section>
  </div>
}

function completionMessage(test: MicrophoneTest) {
  if (test.frames_received === 0) return 'Test completed, but no audio frames were received.'
  if ((test.rms !== null && test.rms > 0) || (test.peak !== null && test.peak > 0)) return 'Signal detected. The input produced a measurable audio level.'
  if (test.rms === 0) return 'Audio frames were received, but the input is silent.'
  return 'Audio frames were received, but the signal level could not be measured.'
}

function SignalMeter({ label, value }: { label: string; value: number | null }) {
  const numeric = value !== null && Number.isFinite(value) ? value : 0
  return <div className="mic-signal-meter">
    <div className="mic-signal-label"><span>{label}</span><b>{formatLevel(value)}</b></div>
    <div className="mic-signal-track" role="meter" aria-label={label} aria-valuemin={0} aria-valuemax={1} aria-valuenow={numeric}>
      <span style={{ width: meterWidth(value) }} />
    </div>
    <small>Full scale · 0 to 1</small>
  </div>
}

function SampleValue({ label, value }: { label: string; value: string }) {
  return <div><small>{label}</small><b>{value}</b></div>
}
