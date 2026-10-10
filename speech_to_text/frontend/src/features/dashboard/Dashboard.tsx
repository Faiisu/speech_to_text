import { useCallback, useEffect, useState } from 'react'
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
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState<string | null>(null)
  const [stopping, setStopping] = useState<string | null>(null)
  const [notice, setNotice] = useState('')
  const refresh = useCallback(async () => {
    try {
      const [p, t, d] = await Promise.all([api.profiles(), api.transcriptions(), api.microphones()])
      setProfiles(p); setRuns(t); setDevices(d); setError('')
    } catch (e) { setError(e instanceof Error ? e.message : 'Could not load dashboard data.') }
    finally { setLoading(false) }
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
  if (loading) return <div className="page-content"><Loading /></div>
  return <div className="page-content">
    {error && <ApiError message={error} retry={() => { setLoading(true); void refresh() }} />}
    <section className="hero-panel"><div className="hero-copy"><div className="eyebrow"><span className="pulse" /> THAI AUDIO WORKFLOWS</div><h1>Hear clearly.<br /><em>Catch every key word.</em></h1><p>Manage microphones, start transcription workflows, and track the words that matter.</p><Link to="/profiles/new" className="button button-lime">Set up a microphone <span>↗</span></Link></div><div className="wave-stage" aria-label="Audio waveform"><div className="wave-caption">INPUT MONITOR <b>READY</b></div><div className="waveform">{Array.from({ length: 43 }, (_, i) => <i key={i} style={{ height: `${12 + Math.abs(Math.sin(i * 2.08) * Math.cos(i * .39)) * 78}%`, opacity: .35 + Math.abs(Math.cos(i * .52)) * .65 }} />)}</div><div className="wave-scale"><span>00:00</span><span>TH · 16 KHZ</span><span>LIVE</span></div><div className="wave-orbit orbit-one" /><div className="wave-orbit orbit-two" /></div><div className="hero-index">STT <span>01</span></div></section>
    {notice && <div className="notice" role="status">{notice}<button onClick={() => setNotice('')} aria-label="Dismiss">×</button></div>}
    <div className="metric-row"><Metric label="Microphone profiles" value={profiles.length} detail="Saved on this backend" icon="◉" /><Metric label="Active workflows" value={runs.filter(r => active(r.status)).length} detail="Refreshes every 5 seconds" icon="◌" /><Metric label="Audio input devices" value={devices.length} detail={`${devices.filter(d => d.selectable).length} selectable`} icon="⌁" /></div>
    <section className="content-section"><div className="section-heading"><div><div className="eyebrow">READY TO START</div><h2>Start from a profile</h2><p>Choose a saved microphone and keyword set.</p></div><Link to="/profiles" className="text-link">All profiles <span>→</span></Link></div>
      {profiles.length ? <div className="profile-grid">{profiles.slice(0, 3).map((p, i) => <article className="profile-card" key={p.profile_id}><div className="card-top"><span className={`mic-glyph mic-${i}`}>⌁</span><span className="card-tag">{p.execution_mode === 'shared' ? 'SHARED MODEL' : 'PROCESS ISOLATED'}</span></div><h3>{p.name}</h3><div className="profile-device">◉ {p.device || 'System default microphone'} · {p.model}</div><div className="keyword-list">{p.keywords.slice(0, 3).map(k => <span key={k}>{k}</span>)}{p.keywords.length > 3 && <small>+{p.keywords.length - 3}</small>}</div><button className="button button-dark card-action" disabled={busy === p.profile_id || !!error} onClick={() => void start(p)}>{busy === p.profile_id ? 'Starting…' : 'Start listening'} <span>↗</span></button></article>)}</div> : <EmptyState title="No microphone profiles yet" action={<Link className="button button-dark" to="/profiles/new">Create a profile <span>↗</span></Link>}>Create your first profile to choose an input device and keywords, then start a workflow.</EmptyState>}</section>
    <section className="content-section runs-section"><div className="section-heading"><div><div className="eyebrow">LIVE MONITOR</div><h2>Recent workflows</h2></div><span className="refresh-note"><i /> Auto-refreshing</span></div><RunList runs={runs.slice(0, 5)} stopping={stopping} onStop={stop} /></section>
  </div>
}

function active(status: string) { return ['queued', 'running', 'recording', 'stopping'].includes(status) }
function Metric({ label, value, detail, icon }: { label: string; value: number; detail: string; icon: string }) { return <div className="metric"><div className="metric-icon">{icon}</div><div><span>{label}</span><strong>{value.toString().padStart(2, '0')}</strong><small>{detail}</small></div></div> }
export function RunList({ runs, stopping, onStop }: { runs: Transcription[]; stopping: string | null; onStop: (run: Transcription) => void }) {
  if (!runs.length) return <EmptyState title="No transcription workflows yet">Start a workflow from a microphone profile to see its status and results here.</EmptyState>
  return <div className="run-list">{runs.map(run => {
    const name = run.profile_name || (run.kind === 'microphone' ? 'Live microphone' : 'Audio clip')
    const isStopping = stopping === run.workflow_id || run.status === 'stopping'
    return <div className="run-row" key={run.workflow_id}>
      <Link to={`/runs/${run.workflow_id}`} className="run-row-link" aria-label={`Open workflow ${name}`}>
        <span className={`run-state ${active(run.status) ? 'is-active' : ''}`} />
        <div className="run-main"><strong>{name}</strong><span>{run.device || 'Default input'} · {run.keywords.join(' / ')}</span></div>
        <span className={`status-pill status-${run.status}`}>{statusLabel(run.status)}</span>
        <time>{new Date(run.created_at).toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit' })}</time>
        <span className="row-arrow">↗</span>
      </Link>
      {run.kind === 'microphone' && active(run.status) && <button className="button button-outline danger-button run-stop" type="button" aria-label={`Stop workflow ${name}`} disabled={isStopping} onClick={() => onStop(run)}>{isStopping ? 'Stopping…' : 'Stop'}</button>}
    </div>
  })}</div>
}
export function statusLabel(status: string) { const labels: Record<string, string> = { queued: 'Queued', running: 'Running', recording: 'Listening', stopping: 'Stopping', stopped: 'Stopped', completed: 'Completed', failed: 'Failed' }; return labels[status] || status }
