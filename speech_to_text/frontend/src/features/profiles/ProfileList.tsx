import { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../../shared/api'
import type { Profile } from '../../shared/types'
import { ApiError, EmptyState, Loading } from '../../shared/States'

export default function ProfileList() {
  const [profiles, setProfiles] = useState<Profile[]>([])
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(true)
  const [removing, setRemoving] = useState('')
  const [message, setMessage] = useState('')
  const load = useCallback(async () => { try { setProfiles(await api.profiles()); setError('') } catch (e) { setError(e instanceof Error ? e.message : 'Could not load profiles.') } finally { setLoading(false) } }, [])
  useEffect(() => { void load() }, [load])
  const remove = async (p: Profile) => {
    if (!window.confirm(`Delete profile “${p.name}”? Workflows that have already started will keep running.`)) return
    setRemoving(p.profile_id); setMessage('')
    try { await api.deleteProfile(p.profile_id); await load(); setMessage(`Deleted profile “${p.name}”.`) }
    catch (e) { setMessage(e instanceof Error ? e.message : 'Could not delete the profile.') }
    finally { setRemoving('') }
  }
  return <div className="page-content">
    {error && <ApiError message={error} retry={() => { setLoading(true); void load() }} />}
    <div className="page-heading"><div><div className="eyebrow">INPUT SETUP / PROFILE LIBRARY</div><h1>Microphone<span>profiles</span></h1><p>Save an input device and keyword set, then start a workflow in one click.</p></div><Link to="/profiles/new" className="button button-dark">Create profile <span>＋</span></Link></div>
    {message && <div className="notice" role="status">{message}<button onClick={() => setMessage('')} aria-label="Dismiss">×</button></div>}
    <div className="profile-summary"><span><b>{profiles.length.toString().padStart(2, '0')}</b> saved profiles</span><small>Stored by the backend · SQLite</small></div>
    {loading ? <Loading /> : profiles.length ? <div className="profile-library">{profiles.map((p, i) => <article className="library-card" key={p.profile_id}><div className="library-index">{String(i + 1).padStart(2, '0')}</div><div className="library-main"><span className="eyebrow">{p.execution_mode === 'shared' ? 'SHARED MODEL' : 'PROCESS ISOLATED'}</span><h2>{p.name}</h2><p>◉ {p.device || 'System default microphone'}</p><div className="keyword-list">{p.keywords.map(k => <span key={k}>{k}</span>)}</div><small className="updated-line">Model: {p.model} · {p.runtime}</small><small className="updated-line">Noise floor (RMS silence threshold): {p.silence_threshold}</small><small className="updated-line">Last updated {new Date(p.updated_at).toLocaleString('en-US')}</small></div><div className="library-actions"><Link to={`/profiles/${p.profile_id}`} className="button button-outline">Edit</Link><button className="button button-outline danger-button" disabled={removing === p.profile_id} onClick={() => void remove(p)}>{removing === p.profile_id ? 'Deleting…' : 'Delete'}</button></div></article>)}</div> : <EmptyState title="No profiles yet">Create a reusable setup by selecting a microphone and adding the keywords you want to track.<Link className="button button-dark" to="/profiles/new">Create your first profile <span>↗</span></Link></EmptyState>}
  </div>
}
