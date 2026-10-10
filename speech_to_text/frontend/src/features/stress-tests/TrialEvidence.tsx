import type { StressReport, StressTrial } from '../../shared/types'

const topologies = [
  { key: 'shared-model', title: 'Shared model' },
  { key: 'per-input-model', title: 'Per workflow process' },
]
const counts = [1, 2, 4]

function seconds(value: unknown) {
  return typeof value === 'number' && Number.isFinite(value) ? `${value.toFixed(2)} s` : '—'
}
function bytes(value: unknown) {
  return typeof value === 'number' && Number.isFinite(value) ? `${(value / 1024 ** 3).toFixed(2)} GB` : '—'
}
function metric(summary: StressTrial['chunk_elapsed_seconds'], key: 'p50' | 'p95' | 'maximum') {
  const value = summary?.[key]
  return typeof value === 'number' ? value.toFixed(2) : '—'
}

export default function TrialEvidence({ report }: { report: StressReport }) {
  return <section className="evidence-section"><div className="matrix-heading"><div><span className="eyebrow">MEASURED OUTPUT</span><h2>Compare trial evidence</h2></div><span className="evidence-note">Values come from completed replay chunks</span></div>
    {topologies.map(topology => <div className="evidence-topology" key={topology.key}><h3>{topology.title}</h3><div className="evidence-scroll"><table><caption>{topology.title} trial measurements</caption><thead><tr><th scope="col">Workflows</th><th scope="col">Verdict</th><th scope="col">Response p50 / p95 / max</th><th scope="col">Chunk RTF p50 / p95 / max</th><th scope="col">Utilization by model</th><th scope="col">Startup</th><th scope="col">Peak memory</th><th scope="col">Queue max</th><th scope="col">Dropped / failed</th></tr></thead><tbody>{counts.map(count => { const trial = report[topology.key]?.trials.find(item => item.workflow_count === count); return <tr key={count}><th scope="row">{count}</th><td><span className={`evidence-verdict verdict-${trial?.capacity_verdict ?? 'unavailable'}`}>{trial?.capacity_verdict ?? 'pending'}</span></td><td>{seconds(trial?.chunk_elapsed_seconds?.p50)} / {seconds(trial?.chunk_elapsed_seconds?.p95)} / {seconds(trial?.chunk_elapsed_seconds?.maximum)}</td><td>{metric(trial?.chunk_rtf, 'p50')} / {metric(trial?.chunk_rtf, 'p95')} / {metric(trial?.chunk_rtf, 'maximum')}</td><td>{trial?.inference_utilization_by_model ? Object.entries(trial.inference_utilization_by_model).map(([key, value]) => `${key}: ${value.toFixed(2)}`).join(', ') : '—'}</td><td>{seconds(trial?.model_startup_seconds)}</td><td>{bytes(topology.key === 'shared-model' ? trial?.peak_total_process_rss_bytes : trial?.peak_child_process_rss_bytes)}</td><td>{trial?.max_observed_queue_depth ?? '—'}</td><td>{trial?.dropped_or_failed_chunks ?? '—'} / {trial?.failed_inference_chunks ?? '—'}</td></tr> })}</tbody></table></div>
      {counts.map(count => { const trial = report[topology.key]?.trials.find(item => item.workflow_count === count); if (!trial) return null; return <details className="chunk-details" key={count}><summary>{count} {count === 1 ? 'workflow' : 'workflows'} · {trial.chunks?.length ?? 0} measured chunks{trial.unavailable_reason ? ` · ${trial.unavailable_reason}` : ''}</summary><div className="evidence-scroll"><table><caption>{topology.title}, {count} workflow chunk evidence</caption><thead><tr><th scope="col">Source</th><th scope="col">Sequence</th><th scope="col">Queue wait</th><th scope="col">Inference</th><th scope="col">Elapsed</th><th scope="col">Audio</th><th scope="col">RTF</th><th scope="col">Status</th></tr></thead><tbody>{(trial.chunks ?? []).map((chunk, index) => <tr key={`${chunk.source_id ?? 'source'}-${chunk.sequence ?? index}`}><td>{String(chunk.source_id ?? '—')}</td><td>{chunk.sequence ?? '—'}</td><td>{seconds(chunk.queue_wait_seconds)}</td><td>{seconds(chunk.inference_seconds)}</td><td>{seconds(chunk.elapsed_seconds)}</td><td>{seconds(chunk.audio_seconds)}</td><td>{typeof chunk.rtf === 'number' ? chunk.rtf.toFixed(2) : '—'}</td><td>{String(chunk.status ?? '—')}</td></tr>)}</tbody></table></div><details className="raw-trial-details"><summary>Full trial evidence fields</summary><pre>{JSON.stringify(trial, null, 2)}</pre></details></details> })}</div>)}
  </section>
}
