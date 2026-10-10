import { useCallback, useEffect, useRef, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { api } from '../../shared/api'
import type { Profile, Transcription } from '../../shared/types'
import { ApiError, EmptyState, Loading } from '../../shared/States'

const signalHeights = [12, 20, 31, 17, 42, 66, 27, 18, 48, 73, 35, 22, 55, 30, 17, 44, 63, 24, 15, 37, 57, 28, 18, 12]
const ACTIVE_STATUSES = new Set(['queued', 'running', 'recording', 'stopping'])

export default function ProfileList() {
  const [profiles, setProfiles] = useState<Profile[]>([])
  const [runs, setRuns] = useState<Transcription[]>([])
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(true)
  const [removing, setRemoving] = useState('')
  const [starting, setStarting] = useState('')
  const [stopping, setStopping] = useState('')
  const [actionError, setActionError] = useState('')
  const [message, setMessage] = useState('')
  const actionInFlight = useRef(false)
  const refreshInFlight = useRef<Promise<void> | null>(null)
  const navigate = useNavigate()
  const load = useCallback(async (afterCurrentRefresh = false) => {
    while (refreshInFlight.current) {
      const current = refreshInFlight.current
      if (!afterCurrentRefresh) return current
      await current
      if (refreshInFlight.current === current) refreshInFlight.current = null
    }
    const request = (async () => {
      try {
        const [nextProfiles, nextRuns] = await Promise.all([api.profiles(), api.transcriptions()])
        setProfiles(nextProfiles)
        setRuns(nextRuns)
        setError('')
      } catch (e) {
        setError(e instanceof Error ? e.message : 'Could not load profiles and workflows.')
      } finally {
        setLoading(false)
      }
    })()
    refreshInFlight.current = request
    void request.finally(() => {
      if (refreshInFlight.current === request) refreshInFlight.current = null
    })
    return request
  }, [])
  useEffect(() => {
    void load()
    const timer = window.setInterval(() => { void load() }, 5000)
    return () => window.clearInterval(timer)
  }, [load])
  const remove = async (p: Profile) => {
    if (actionInFlight.current || stopping) return
    if (!window.confirm(`Delete profile “${p.name}”? Workflows that have already started will keep running.`)) return
    setRemoving(p.profile_id); setMessage('')
    try { await api.deleteProfile(p.profile_id); await load(true); setMessage(`Deleted profile “${p.name}”.`) }
    catch (e) { setMessage(e instanceof Error ? e.message : 'Could not delete the profile.') }
    finally { setRemoving('') }
  }
  const start = async (profile: Profile) => {
    if (actionInFlight.current || stopping || removing || error) return
    actionInFlight.current = true
    setStarting(profile.profile_id)
    setActionError('')
    setMessage('')
    try {
      const workflow = await api.startProfile(profile.profile_id)
      navigate(`/runs/${workflow.workflow_id}`)
    } catch (e) {
      setActionError(`Could not start workflow for “${profile.name}”. ${e instanceof Error ? e.message : 'Try again.'}`)
    } finally {
      actionInFlight.current = false
      setStarting('')
    }
  }
  const stop = async (profile: Profile) => {
    if (stopping || actionInFlight.current || removing || error) return
    const activeRuns = runs.filter(run => run.kind === 'microphone' && run.profile_id === profile.profile_id && ACTIVE_STATUSES.has(run.status))
    const runsToStop = activeRuns.filter(run => run.status !== 'stopping')
    if (!runsToStop.length) return
    actionInFlight.current = true
    setStopping(profile.profile_id)
    setActionError('')
    setMessage('')
    const results = await Promise.allSettled(runsToStop.map(run => api.stop(run.workflow_id)))
    await load(true)
    const failures = results.flatMap((result, index) => result.status === 'rejected' ? [{ run: runsToStop[index], error: result.reason }] : [])
    if (failures.length) {
      const detail = failures.map(({ run, error: cause }) => `${run.workflow_id}: ${cause instanceof Error ? cause.message : 'request failed'}`).join('; ')
      setActionError(`Could not stop ${failures.length} workflow${failures.length === 1 ? '' : 's'} for “${profile.name}”. ${detail}`)
    }
    actionInFlight.current = false
    setStopping('')
  }

  return <div className="page-content profile-list-page">
    {error && <ApiError message={error} retry={() => { setLoading(true); void load() }} />}

    <header className="profile-desk-heading">
      <div className="profile-desk-copy">
        <div className="eyebrow">TRANSCRIPTION / INPUT DESK</div>
        <h1>Profiles <span>Microphone setups, ready to run.</span></h1>
        <p>Choose a saved input and keyword set when you start a workflow.</p>
      </div>
      <div className="profile-signal" aria-hidden="true">
        <div className="profile-signal-label"><span>INPUT SIGNAL</span><b>TH · MIC</b></div>
        <div className="profile-signal-wave">{signalHeights.map((height, index) => <i key={index} style={{ height: `${height}px` }} />)}</div>
        <div className="profile-signal-scale"><span>−24 dB</span><span>0 dB</span></div>
      </div>
      <Link to="/profiles/new" className="button profile-create-button"><span aria-hidden="true">＋</span> Create profile</Link>
    </header>

    {message && <div className="notice profile-notice" role="status">{message}<button onClick={() => setMessage('')} aria-label="Dismiss notification">×</button></div>}
    {actionError && <div className="profile-start-error" role="alert">{actionError}<button type="button" onClick={() => setActionError('')} aria-label="Dismiss action error">×</button></div>}

    <div className="profile-library-bar">
      <div><span className="eyebrow">SAVED INPUTS</span><span className="profile-library-count">{profiles.length}</span><span>profiles</span></div>
      <small>Saved on this backend · SQLite</small>
    </div>

    {loading ? <Loading /> : profiles.length ? <>
      <div className="profile-record-head" aria-hidden="true">
        <span>PROFILE / MICROPHONE</span><span>KEYWORDS</span><span>MODEL / RUNTIME</span><span>EXECUTION MODE</span><span>ACTIONS</span>
      </div>
      <section className="profile-record-list" aria-label="Saved microphone profiles">
        {profiles.map(profile => {
          const activeRuns = runs.filter(run => run.kind === 'microphone' && run.profile_id === profile.profile_id && ACTIVE_STATUSES.has(run.status))
          const isStopping = stopping === profile.profile_id || (activeRuns.length > 0 && activeRuns.every(run => run.status === 'stopping'))
          return <article className="profile-record" key={profile.profile_id}>
          <div className="profile-record-identity">
            <span className="profile-field-label">PROFILE / MICROPHONE</span>
            <h2>{profile.name}</h2>
            <p><span className="profile-mic-mark" aria-hidden="true">◉</span>{profile.device || 'System default microphone'}</p>
            <small>Updated {new Date(profile.updated_at).toLocaleString('en-US')}</small>
          </div>
          <div className="profile-record-keywords">
            <span className="profile-field-label">KEYWORDS</span>
            <div className="profile-keyword-chips">{profile.keywords.map(keyword => <span key={keyword}>{keyword}</span>)}</div>
          </div>
          <div className="profile-record-model">
            <span className="profile-field-label">MODEL / RUNTIME</span>
            <strong>Model: {profile.model} · {profile.runtime}</strong>
            <small className="profile-threshold">RMS floor {profile.silence_threshold}</small>
          </div>
          <div className="profile-record-mode">
            <span className="profile-field-label">EXECUTION MODE</span>
            <span className={`profile-mode-chip ${profile.execution_mode === 'shared' ? 'is-shared' : 'is-isolated'}`}>
              <i aria-hidden="true" />{profile.execution_mode === 'shared' ? 'Shared model' : 'Separate process'}
            </span>
          </div>
          <div className="profile-record-actions">
            {activeRuns.length ? <button className="button profile-play-button" type="button" disabled={loading || Boolean(error) || Boolean(removing) || Boolean(starting) || Boolean(stopping) || isStopping} onClick={() => void stop(profile)} aria-label={`Stop workflow ${profile.name}`}>
              <span className="profile-play-icon" aria-hidden="true">||</span>{isStopping ? 'Stopping…' : 'Stop'}
            </button> : <button className="button profile-play-button" type="button" disabled={loading || Boolean(error) || Boolean(removing) || Boolean(starting) || Boolean(stopping)} onClick={() => void start(profile)} aria-label={`Start workflow ${profile.name}`}>
              {starting ? <>{starting === profile.profile_id ? 'Starting…' : 'Play'}</> : <><span className="profile-play-icon" aria-hidden="true">▶</span>Play</>}
            </button>}
            <Link to={`/profiles/${profile.profile_id}`} className="button profile-edit-button">Edit</Link>
            <Link to={`/profiles/new?duplicate=${encodeURIComponent(profile.profile_id)}`} className="button profile-duplicate-button" aria-label={`Duplicate profile ${profile.name}`}>Duplicate</Link>
            <button className="button profile-delete-button" disabled={Boolean(starting) || Boolean(stopping) || removing === profile.profile_id} onClick={() => void remove(profile)} aria-label={`Delete profile ${profile.name}`}>
              {removing === profile.profile_id ? 'Deleting…' : 'Delete'}
            </button>
          </div>
          </article>
        })}
      </section>
    </> : <EmptyState title="No profiles yet">Create a reusable setup by selecting a microphone and adding the keywords you want to track.<Link className="button button-dark" to="/profiles/new">Create your first profile <span>↗</span></Link></EmptyState>}
  </div>
}
