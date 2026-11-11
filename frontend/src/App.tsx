import React, { useEffect, useState } from 'react'
import { api } from './api'
import { Chart } from './charts'

const DEMO_QUESTIONS = [
  'What is total revenue?',
  'What is total revenue by month?',
  'What is total revenue by region?',
  'What are the top products by revenue?',
  'What are the top product categories by revenue?',
  'How many orders were there?',
  'What is the average order value?',
  'What is the average order value by region?',
  'How many active customers do we have?',
  'What is the monthly churn rate?',
  'Show churn by month.',
  'What is total revenue in March?',
  'What is total revenue by channel?',
  'What is total revenue and total orders by month?',
]

const MONTHS = ['January','February','March','April','May','June','July','August','September','October','November','December']

function resolveClarifying(run: any, option: string): string {
  for (const m of MONTHS) if (option.startsWith(m)) return `What is total revenue in ${m}?`
  if (/^Q[1-4]/.test(option)) return `What is total revenue in ${option}?`
  if (option.startsWith('Last month')) return 'What is the monthly churn rate in December?'
  if (option.startsWith('Last 3 months')) return 'What is the monthly churn rate in the last 3 months?'
  if (option.startsWith('All 12 months')) return 'Show churn by month.'
  if (option === 'Total (all time)') return `What is ${(run.metric || 'total_revenue').replace(/_/g, ' ')}?`
  if (option === 'By month') return `What is ${(run.metric || 'total_revenue').replace(/_/g, ' ')} by month?`
  if (option === 'By region') return `What is ${(run.metric || 'total_revenue').replace(/_/g, ' ')} by region?`
  return option
}

export default function App() {
  const [tab, setTab] = useState('ask')
  const [question, setQuestion] = useState('')
  const [workspace, setWorkspace] = useState('demo')
  const [run, setRun] = useState<any>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [health, setHealth] = useState<any>(null)
  const [history, setHistory] = useState<any[]>([])
  const [metrics, setMetrics] = useState<any[]>([])
  const [models, setModels] = useState<any>(null)
  const [insights, setInsights] = useState<any[]>([])
  const [evalResult, setEvalResult] = useState<any>(null)
  const [followUp, setFollowUp] = useState('')

  useEffect(() => {
    api.health().then(setHealth).catch(() => setHealth({ status: 'unreachable' }))
    api.metrics().then((d) => setMetrics(d.metrics || [])).catch(() => {})
    api.models().then(setModels).catch(() => {})
    refreshHistory()
    api.insights().then((d) => setInsights(d.insights || [])).catch(() => {})
  }, [])

  function refreshHistory() {
    api.history().then((d) => setHistory(d.runs || [])).catch(() => {})
  }

  async function ask(q?: string) {
    const text = (q || question).trim()
    if (!text) return
    setLoading(true); setError(''); setRun(null)
    try {
      const r = await api.ask(text, workspace)
      setRun(r); refreshHistory()
    } catch (e: any) {
      setError(e.message || 'Request failed. Is the backend running on http://localhost:8000?')
    } finally { setLoading(false) }
  }

  async function sendFollowUp() {
    if (!run?.id || !followUp.trim()) return
    setLoading(true)
    try {
      const r = await api.followUp(run.id, followUp)
      setRun(r); setFollowUp(''); refreshHistory()
    } catch (e: any) { setError(e.message) } finally { setLoading(false) }
  }

  async function runEval() {
    setLoading(true)
    try { setEvalResult(await api.runEval()) } catch (e: any) { setError(e.message) } finally { setLoading(false) }
  }

  async function saveInsight() {
    if (!run) return
    await api.createInsight({ run_id: run.id, question: run.question, sql: run.sql, cadence: 'daily', threshold: null })
    const d = await api.insights(); setInsights(d.insights || [])
    setTab('insights')
  }

  return (
    <div className="app">
      <header>
        <h1>QueryPilot</h1>
        <p>Trusted text-to-SQL over a governed semantic layer · <span className="badge">Sample data</span></p>
        <p className="health">Backend: {health?.status || '…'} · LLM mode: {health?.llm_mode || '…'} · Layer {health?.layer_version || '…'}</p>
      </header>
      <nav>
        {['ask', 'semantic', 'history', 'insights', 'eval'].map((t) => (
          <button key={t} className={tab === t ? 'active' : ''} onClick={() => setTab(t)}>{t[0].toUpperCase() + t.slice(1)}</button>
        ))}
      </nav>

      {tab === 'ask' && (
        <section>
          <div className="card">
            <label>Workspace
              <select value={workspace} onChange={(e) => setWorkspace(e.target.value)}>
                <option value="demo">demo (sample e-commerce)</option>
              </select>
            </label>
            <textarea value={question} onChange={(e) => setQuestion(e.target.value)} rows={3}
              placeholder="Ask a business question, e.g. What is total revenue by month?" />
            <button className="primary" disabled={loading} onClick={() => ask()}>{loading ? 'Asking…' : 'Ask'}</button>
            <div className="chips">
              {DEMO_QUESTIONS.map((q) => (
                <button key={q} onClick={() => { setQuestion(q); ask(q) }}>{q}</button>
              ))}
            </div>
          </div>
          {error && <div className="card error">{error}</div>}
          {loading && <div className="card">Planning, validating and executing…</div>}
          {run && <Result run={run} onClarify={(opt) => ask(resolveClarifying(run, opt))} onSave={saveInsight} />}
          {run && run.status === 'executed' && (
            <div className="card">
              <h3>Follow-up</h3>
              <p className="muted">Reuses this run's metric ({run.metric}) — try “by region” or “in March”.</p>
              <input value={followUp} onChange={(e) => setFollowUp(e.target.value)} placeholder="e.g. by region" />
              <button className="primary" onClick={sendFollowUp}>Ask follow-up</button>
            </div>
          )}
        </section>
      )}

      {tab === 'semantic' && (
        <section>
          <h2>Semantic explorer</h2>
          {metrics.map((m: any) => (
            <div className="card" key={m.name}>
              <h3>{m.name}</h3>
              <p>{m.description}</p>
              <p className="muted">Definition: {m.definition} · Owner: {m.owner} · Model: {m.model}</p>
              <p className="muted">Dimensions: {(m.dimensions || []).join(', ')}</p>
            </div>
          ))}
          {models && (
            <div className="card">
              <h3>Models &amp; joins</h3>
              <p className="muted">PII blocked: customers.email, customers.phone</p>
              {(models.models || []).map((m: any) => (
                <p key={m.name}><b>{m.name}</b>: {(m.columns || []).map((c: any) => c.name + (c.pii ? ' (PII)' : '')).join(', ')}</p>
              ))}
              <p className="muted">Allowed joins: {(models.joins || []).map((j: any) => `${j.from} → ${j.to}`).join(' · ')}</p>
            </div>
          )}
        </section>
      )}

      {tab === 'history' && (
        <section>
          <h2>History</h2>
          {history.length === 0 && <p className="muted">No runs yet. Ask a question first.</p>}
          {history.map((r: any) => (
            <div className="card" key={r.id} onClick={async () => { setRun(await api.run(r.id)); setTab('ask') }} style={{ cursor: 'pointer' }}>
              <b>{r.question}</b>
              <p className="muted">{r.status} · {r.metric || '—'} · {r.row_count} rows · {r.duration_ms} ms · ${r.cost ?? 0}</p>
            </div>
          ))}
        </section>
      )}

      {tab === 'insights' && (
        <section>
          <h2>Scheduled insights</h2>
          {insights.length === 0 && <p className="muted">No saved insights yet. Save a run from the Ask tab.</p>}
          {insights.map((ins: any) => (
            <div className="card" key={ins.id}>
              <b>{ins.question}</b>
              <p className="muted">Cadence: {ins.cadence} · Status: {ins.status} · Threshold: {ins.threshold ?? 'none'}</p>
              <button onClick={async () => { await api.patchInsight(ins.id, { status: ins.status === 'active' ? 'paused' : 'active' }); const d = await api.insights(); setInsights(d.insights || []) }}>
                {ins.status === 'active' ? 'Pause' : 'Resume'}
              </button>{' '}
              <button onClick={async () => { await api.deleteInsight(ins.id); const d = await api.insights(); setInsights(d.insights || []) }}>Delete</button>
            </div>
          ))}
        </section>
      )}

      {tab === 'eval' && (
        <section>
          <h2>Eval &amp; safety</h2>
          <p className="muted">Runs the real ask path on the 20-case golden set, plus the safety suite. Numbers come from a live run, never from the PRD targets.</p>
          <button className="primary" disabled={loading} onClick={runEval}>{loading ? 'Running…' : 'Run evaluation'}</button>
          {evalResult && (
            <div className="card">
              <p><b>Execution accuracy:</b> {(evalResult.execution_accuracy * 100).toFixed(1)}% ({evalResult.golden_cases} cases)</p>
              <p><b>Valid SQL rate:</b> {(evalResult.valid_sql_rate * 100).toFixed(1)}%</p>
              <p><b>Correction success:</b> {evalResult.correction_success ? 'yes' : 'no'} ({evalResult.correction_attempts_on_broken_draft} attempt(s) on the broken draft)</p>
              <p><b>Safety suite:</b> {evalResult.safety_suite.blocked}/{evalResult.safety_suite.total} blocked ({(evalResult.safety_suite.pass_rate * 100).toFixed(0)}%)</p>
              <table>
                <thead><tr><th>ID</th><th>Category</th><th>Expected</th><th>Got</th><th>Pass</th></tr></thead>
                <tbody>
                  {evalResult.per_case.map((c: any) => (
                    <tr key={c.id}><td>{c.id}</td><td>{c.category}</td><td>{c.expected}</td><td>{c.got}</td><td>{c.passed ? '✓' : '✗'}</td></tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>
      )}
    </div>
  )
}

function Result({ run, onClarify, onSave }: { run: any; onClarify: (o: string) => void; onSave: () => void }) {
  if (run.status === 'clarifying') {
    return (
      <div className="card">
        <h3>One quick question</h3>
        <p>{run.clarifying_question}</p>
        <div className="chips">{(run.clarifying_options || []).map((o: string) => (
          <button key={o} onClick={() => onClarify(o)}>{o}</button>
        ))}</div>
        <p className="muted">Asking back is a success state — better than a confident wrong number.</p>
      </div>
    )
  }
  if (run.status === 'refused' || run.status === 'blocked') {
    return (
      <div className="card error">
        <h3>{run.status === 'blocked' ? 'Blocked by the validator' : 'Cannot answer from the modelled data'}</h3>
        <p>{run.refusal}</p>
        <p className="muted">Nearest answerable question: “What is total revenue by month?”</p>
      </div>
    )
  }
  if (run.status === 'failed') {
    return (
      <div className="card error">
        <h3>Execution failed</h3>
        <p>{run.explanation}</p>
        <pre>{(run.attempts || []).map((a: any) => `#${a.attempt}: ${a.error}`).join('\n')}</pre>
      </div>
    )
  }
  return (
    <div className="card">
      <Chart spec={run.chart_spec} />
      <p>{run.explanation}</p>
      {run.rows?.length === 0 && <p className="muted">Empty result — the filters and period returned no rows. This is not the same as zero.</p>}
      <details>
        <summary>Result table ({run.row_count} rows)</summary>
        <table>
          <thead><tr>{(run.columns || []).map((c: string) => <th key={c}>{c}</th>)}</tr></thead>
          <tbody>
            {(run.rows || []).slice(0, 20).map((r: any[], i: number) => (
              <tr key={i}>{r.map((v, j) => <td key={j}>{String(v)}</td>)}</tr>
            ))}
          </tbody>
        </table>
      </details>
      <details>
        <summary>SQL, lineage &amp; validation</summary>
        <pre>{run.sql}</pre>
        <p className="muted">Metric: {run.metric} — {run.lineage?.definition}</p>
        <p className="muted">Layer v{run.layer_version} · Validation: {run.validation?.rule || 'passed'} · Attempts: {Math.max(run.attempts?.length || 0, 1)} · Cache: {run.cache_hit ? 'hit' : 'miss'} · Cost: ${run.cost ?? 0} · {run.duration_ms} ms · LLM mode: {run.llm_mode}</p>
        <p className="muted">Plan: {(run.plan_steps || []).map((s: any) => s.action).join(' → ')}</p>
      </details>
      <button onClick={() => api.feedback(run.id, 'up')}>👍 Helpful</button>{' '}
      <button onClick={() => api.feedback(run.id, 'down')}>👎 Wrong</button>{' '}
      <button onClick={onSave}>Save as scheduled insight</button>
    </div>
  )
}
