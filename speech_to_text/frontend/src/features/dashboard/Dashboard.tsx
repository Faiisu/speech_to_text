import { useCallback, useEffect, useRef, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { api } from '../../shared/api'
import type { Microphone, Profile, Transcription } from '../../shared/types'
import { ApiError, EmptyState, Loading } from '../../shared/States'

export default function Dashboard() {
  const navigate = useNavigate()
  const [profiles, setProfiles] = useState<Profile[]>([])
  const [runs, setRuns] = useState<Transcription[]>([])
  const [devices, setDevices] = useState<Microphone[]>([])
  const [error, setError] = useState('')
  const [deviceError, setDeviceError] = useState('')
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState<string | null>(null)
  const [stopping, setStopping] = useState<string | null>(null)
  const [notice, setNotice] = useState('')
  const profileCarousel = useRef<HTMLDivElement>(null)
  const [canScrollProfilesBack, setCanScrollProfilesBack] = useState(false)
  const [canScrollProfilesForward, setCanScrollProfilesForward] = useState(false)
  const refresh = useCallback(async () => {
    const [profileResult, runResult, deviceResult] = await Promise.allSettled([api.profiles(), api.transcriptions(), api.microphones()])
    if (profileResult.status === 'fulfilled') setProfiles(profileResult.value)
    if (runResult.status === 'fulfilled') setRuns(runResult.value)
    if (deviceResult.status === 'fulfilled') { setDevices(deviceResult.value); setDeviceError('') }
    else { setDevices([]); setDeviceError(deviceResult.reason instanceof Error ? deviceResult.reason.message : 'Microphone discovery is unavailable.') }
    const requiredError = profileResult.status === 'rejected' ? profileResult.reason : runResult.status === 'rejected' ? runResult.reason : null
    setError(requiredError instanceof Error ? requiredError.message : requiredError ? 'Could not load dashboard data.' : '')
    setLoading(false)
  }, [])
  useEffect(() => { void refresh(); const timer = window.setInterval(() => void refresh(), 5000); return () => window.clearInterval(timer) }, [refresh])
  const start = async (profile: Profile) => {
    setBusy(profile.profile_id); setNotice('')
    try { const run = await api.startProfile(profile.profile_id); setNotice(`Started a workflow from “${profile.name}”.`); await refresh(); navigate(`/runs/${run.workflow_id}`) }
    catch (e) { setNotice(e instanceof Error ? e.message : 'Could not start the workflow.') }
    finally { setBusy(null) }
  }
  const stop = async (run: Transcription) => {
    setStopping(run.workflow_id); setNotice('')
    try { await api.stop(run.workflow_id); await refresh() }
    catch (e) { setNotice(e instanceof Error ? e.message : 'Could not stop this workflow.') }
    finally { setStopping(null) }
  }
  const updateProfileCarouselControls = useCallback(() => {
    const carousel = profileCarousel.current
    if (!carousel) return
    setCanScrollProfilesBack(carousel.scrollLeft > 1)
    setCanScrollProfilesForward(carousel.scrollLeft + carousel.clientWidth < carousel.scrollWidth - 1)
  }, [])
  const scrollProfiles = (direction: -1 | 1) => {
    const carousel = profileCarousel.current
    if (!carousel) return
    const cards = carousel.querySelectorAll<HTMLElement>('.profile-card')
    if (!cards.length) return
    const currentLeft = carousel.getBoundingClientRect().left
    const currentIndex = [...cards].reduce((nearest, card, index) => {
      const distance = Math.abs(card.getBoundingClientRect().left - currentLeft)
      const nearestDistance = Math.abs(cards[nearest].getBoundingClientRect().left - currentLeft)
      return distance < nearestDistance ? index : nearest
    }, 0)
    const nextCard = cards[Math.max(0, Math.min(cards.length - 1, currentIndex + direction))]
    const targetLeft = carousel.scrollLeft + nextCard.getBoundingClientRect().left - currentLeft
    carousel.scrollTo({ left: targetLeft, behavior: 'smooth' })
  }
  useEffect(() => {
    updateProfileCarouselControls()
    const carousel = profileCarousel.current
    if (!carousel) return
    const observer = new ResizeObserver(updateProfileCarouselControls)
    observer.observe(carousel)
    return () => observer.disconnect()
  }, [profiles.length, updateProfileCarouselControls])
  if (loading) return <div className="page-content"><Loading /></div>
  return <div className="page-content dashboard-page">
    {error && <ApiError message={error} retry={() => { setLoading(true); void refresh() }} />}
    {notice && <div className="notice" role="status">{notice}<button onClick={() => setNotice('')} aria-label="Dismiss">×</button></div>}
    <div className="metric-row"><Metric label="Audio input profiles" value={profiles.length} detail="Saved on this backend" icon="◉" /><Metric label="Active profiles" value={profiles.filter(profile => runs.some(run => run.kind === 'microphone' && run.profile_id === profile.profile_id && active(run.status))).length} detail="Refreshes every 5 seconds" icon="◌" /><Metric label="Audio input devices" value={deviceError ? '—' : devices.length} detail={deviceError ? 'Microphone discovery unavailable' : `${devices.filter(d => d.selectable).length} selectable`} icon="⌁" /></div>
    <section className="content-section"><div className="section-heading"><div><div className="eyebrow">READY TO START</div><h2>Start from a profile</h2><p>Choose a saved audio input and keyword set.</p>{deviceError && <small role="status">{deviceError} IPCAM profiles remain available.</small>}</div><div className="profile-carousel-actions">{profiles.length > 1 && <div className="profile-carousel-controls" aria-label="Profile carousel controls"><button className="carousel-control" type="button" aria-label="Previous profiles" aria-controls="dashboard-profile-carousel" disabled={!canScrollProfilesBack} onClick={() => scrollProfiles(-1)}>←</button><button className="carousel-control" type="button" aria-label="Next profiles" aria-controls="dashboard-profile-carousel" disabled={!canScrollProfilesForward} onClick={() => scrollProfiles(1)}>→</button></div>}<Link to="/profiles" className="text-link">All profiles <span>→</span></Link></div></div>
      {profiles.length ? <div className="profile-grid" id="dashboard-profile-carousel" ref={profileCarousel} onScroll={updateProfileCarouselControls} role="region" aria-roledescription="carousel" aria-label="Audio input profiles" tabIndex={0}>{profiles.map((p, i) => { const run = runs.find(item => item.kind === 'microphone' && item.profile_id === p.profile_id && active(item.status)); const isStopping = stopping === run?.workflow_id || run?.status === 'stopping'; const inputLabel = p.source_type === 'rtsp' ? 'IPCAM (RTSP)' : p.device || 'System default microphone'; return <article className="profile-card" key={p.profile_id}><div className="card-top"><span className={`mic-glyph mic-${i % 3}`}>⌁</span><span className="card-tag">{run ? 'ACTIVE' : p.execution_mode === 'shared' ? 'SHARED MODEL' : 'PROCESS ISOLATED'}</span></div><h3>{p.name}</h3><div className="profile-device">◉ {inputLabel} · {p.model}</div><div className="keyword-list">{p.keywords.slice(0, 3).map(k => <span key={k}>{k}</span>)}{p.keywords.length > 3 && <small>+{p.keywords.length - 3}</small>}</div><button className={`button ${run ? 'button-outline' : 'button-dark'} card-action`} disabled={busy === p.profile_id || isStopping || !!error} aria-label={run ? `Stop workflow ${p.name}` : `Start workflow ${p.name}`} onClick={() => run ? void stop(run) : void start(p)}>{busy === p.profile_id ? 'Starting…' : isStopping ? 'Stopping…' : run ? 'Stop listening' : 'Start listening'} <span>{run ? 'Ⅱ' : '↗'}</span></button></article> })}</div> : <EmptyState title="No input profiles yet" action={<Link className="button button-dark" to="/profiles/new">Create a profile <span>↗</span></Link>}>Create your first profile to choose an audio input and keywords, then start a workflow.</EmptyState>}</section>
    <section className="content-section runs-section"><div className="section-heading"><div><div className="eyebrow">LIVE MONITOR</div><h2>Active workflows</h2></div><span className="refresh-note"><i /> Auto-refreshing</span></div><RunList runs={runs.filter(run => active(run.status))} stopping={stopping} onStop={stop} emptyTitle="No active workflows" emptyDescription="Start a workflow from a microphone profile to see it here while it is running." /></section>
  </div>
}

function active(status: string) { return ['queued', 'running', 'recording', 'stopping'].includes(status) }
function Metric({ label, value, detail, icon }: { label: string; value: number | string; detail: string; icon: string }) { return <div className="metric"><div className="metric-icon">{icon}</div><div><span>{label}</span><strong>{typeof value === 'number' ? value.toString().padStart(2, '0') : value}</strong><small>{detail}</small></div></div> }
export function RunList({ runs, stopping, onStop, emptyTitle = 'No transcription workflows yet', emptyDescription = 'Start a workflow from a microphone profile to see its status and results here.' }: { runs: Transcription[]; stopping: string | null; onStop: (run: Transcription) => void; emptyTitle?: string; emptyDescription?: string }) {
  if (!runs.length) return <EmptyState title={emptyTitle}>{emptyDescription}</EmptyState>
  return <div className="run-list">{runs.map(run => {
    const name = run.profile_name || (run.source_type === 'rtsp' ? 'IPCAM (RTSP)' : run.kind === 'microphone' ? 'Live microphone' : 'Audio clip')
    const isStopping = stopping === run.workflow_id || run.status === 'stopping'
    const latestRtf = run.latest_rtf
    const hasRtf = latestRtf !== null && Number.isFinite(latestRtf)
    const rtfDescription = !hasRtf ? 'Waiting for first measurement' : latestRtf <= 1 ? 'Within real time' : 'Slower than real time'
    const rtfValue = hasRtf ? latestRtf.toFixed(2) : '—'
    const rtfState = !hasRtf ? 'is-waiting' : latestRtf <= 1 ? 'is-realtime' : 'is-slower'
    const rtfSummary = run.rtf_count
      ? `min ${run.rtf_min?.toFixed(2)} · avg ${run.rtf_avg?.toFixed(2)} · max ${run.rtf_max?.toFixed(2)}`
      : hasRtf ? (latestRtf <= 1 ? '≤ real time' : 'slower') : 'Waiting'
    return <div className="run-row" key={run.workflow_id}>
      <Link to={`/runs/${run.workflow_id}`} className="run-row-link" aria-label={`Open workflow ${name}; realtime factor ${hasRtf ? `${rtfValue}, ${rtfDescription}` : rtfDescription}; ${rtfSummary}`}>
        <span className={`run-state ${active(run.status) ? 'is-active' : ''}`} />
        <div className="run-main"><strong>{name}</strong><span>{run.source_type === 'rtsp' ? 'IPCAM (RTSP)' : run.device || 'Default input'} · {run.keywords.join(' / ')}</span></div>
        <span className={`status-pill status-${run.status}`}>{statusLabel(run.status)}</span>
        <span className={`run-row-rtf ${rtfState}`} aria-label={`Realtime factor: ${hasRtf ? `${rtfValue}, ${rtfDescription}` : rtfDescription}; ${rtfSummary}`} title={`Realtime factor: ${hasRtf ? `${rtfValue}, ${rtfDescription}` : rtfDescription}; ${rtfSummary}`}>
          <small>RTF</small><strong>{rtfValue}</strong><em>{rtfSummary}</em>
        </span>
        <time>{new Date(run.created_at).toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit' })}</time>
        <span className="row-arrow">↗</span>
      </Link>
      <span className="run-stop-slot">{run.kind === 'microphone' && active(run.status) && <button className="button button-outline danger-button run-stop" type="button" aria-label={`Stop workflow ${name}`} disabled={isStopping} onClick={() => onStop(run)}>{isStopping ? 'Stopping…' : 'Stop'}</button>}</span>
    </div>
  })}</div>
}
export function statusLabel(status: string) { const labels: Record<string, string> = { queued: 'Queued', running: 'Running', recording: 'Listening', stopping: 'Stopping', stopped: 'Stopped', completed: 'Completed', failed: 'Failed' }; return labels[status] || status }
