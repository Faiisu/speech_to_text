import { NavLink, Route, Routes } from 'react-router-dom'
import Dashboard from '../features/dashboard/Dashboard'
import ProfileList from '../features/profiles/ProfileList'
import ProfileEditor from '../features/profiles/ProfileEditor'
import RunDetail from '../features/runs/RunDetail'
import StressTests from '../features/stress-tests/StressTests'

function Shell() {
  return <div className="app-shell">
    <aside className="sidebar">
      <NavLink to="/" className="brand"><span className="brand-mark"><i /><i /><i /><i /><i /></span><span>Echo<span className="brand-light">Desk</span><small>AUDIO TRANSCRIPTION</small></span></NavLink>
      <div className="nav-label">WORKSPACE</div>
      <nav className="main-nav">
        <NavLink to="/" end><span className="nav-icon">◫</span>Overview</NavLink>
        <NavLink to="/profiles"><span className="nav-icon">◉</span>Microphones & profiles</NavLink>
        <NavLink to="/stress-tests"><span className="nav-icon">⌁</span>Stress test</NavLink>
      </nav>
      <div className="sidebar-bottom"><div className="sidebar-note"><span className="tiny-signal" />API v1 <span>LOCAL</span></div><p>Every word<br />starts here</p></div>
    </aside>
    <main className="main-area">
      <header className="topbar"><div className="crumb">ECHODESK <span>/</span> <b>WORKSPACE</b></div><div className="topbar-right"><span className="live-dot" />Control center<span className="avatar">E</span></div></header>
      <Routes>
        <Route path="/" element={<Dashboard />} />
        <Route path="/profiles" element={<ProfileList />} />
        <Route path="/profiles/new" element={<ProfileEditor />} />
        <Route path="/profiles/:profileId" element={<ProfileEditor />} />
        <Route path="/runs/:workflowId" element={<RunDetail />} />
        <Route path="/stress-tests" element={<StressTests />} />
        <Route path="*" element={<NotFound />} />
      </Routes>
      <footer className="footer"><span>EchoDesk <i>●</i> Transcription workflows</span><span>Run status from the current backend</span></footer>
    </main>
  </div>
}

function NotFound() {
  return <section className="not-found"><div className="eyebrow">404 / ROUTE NOT FOUND</div><h1>Lost the signal?<br /><em>Find your way back.</em></h1><p>We couldn't find that page. Return to the overview to continue monitoring your workflows.</p><NavLink className="button button-primary" to="/">Back to overview <span>↗</span></NavLink><div className="lost-wave" aria-hidden="true">〰</div></section>
}

export default function App() { return <Shell /> }
