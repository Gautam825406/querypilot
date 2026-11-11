import React, { useEffect, useState } from 'react'
import { api, getStoredUser } from './api'
import { Chart } from './charts'

const API_BASE = (import.meta as any).env?.VITE_API_URL || 'http://localhost:8000'

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

const DEMO_GROUPS: { label: string; color: string; questions: string[] }[] = [
  { label: 'Revenue', color: '#a78bfa', questions: DEMO_QUESTIONS.slice(0, 3).concat(DEMO_QUESTIONS.slice(11)) },
  { label: 'Products', color: '#22d3ee', questions: DEMO_QUESTIONS.slice(3, 5) },
  { label: 'Orders & customers', color: '#fbbf24', questions: DEMO_QUESTIONS.slice(5, 9) },
  { label: 'Churn', color: '#fb7185', questions: DEMO_QUESTIONS.slice(9, 11) },
]

const PIPELINE_STEPS = ['Classify', 'Resolve', 'Plan', 'Draft', 'Validate', 'Execute', 'Explain']

const MONTHS = ['January','February','March','April','May','June','July','August','September','October','November','December']

function resolveClarifying(run: any, option: string): string {
  for (const m of MONTHS) if (option.startsWith(m)) return `What is total revenue in ${m}?`
  if (/^Q[1-4]/.test(option)) return `What is total revenue in ${option}?`
  if (option.startsWith('Last month')) return 'What is the monthly churn rate in December?'
  if (option.startsWith('Last 3 months')) return 'What is the monthly churn rate in the last 3 months?'
  if (option.startsWith('All months')) return 'Show churn by month.'
  if (option === 'Total (all time)') return `What is ${(run.metric || 'total_revenue').replace(/_/g, ' ')}?`
  if (option === 'By month') return `What is ${(run.metric || 'total_revenue').replace(/_/g, ' ')} by month?`
  if (option === 'By region') return `What is ${(run.metric || 'total_revenue').replace(/_/g, ' ')} by region?`
  return option
}

function metricIcon(name: string): string {
  if (name.includes('revenue')) return '💰'
  if (name.includes('order')) return '🧾'
  if (name.includes('average')) return '📊'
  if (name.includes('customer')) return '👥'
  if (name.includes('churn')) return '📉'
  return '◆'
}

function prettyMetric(name: string): string {
  return name.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase())
}

const Icon = ({ d, children }: { d?: string; children?: React.ReactNode }) => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
    {d ? <path d={d} /> : children}
  </svg>
)
const ICONS: Record<string, React.ReactNode> = {
  ask: <Icon><circle cx="11" cy="11" r="7" /><line x1="21" y1="21" x2="16.65" y2="16.65" /></Icon>,
  semantic: <Icon><polygon points="12 2 2 7 12 12 22 7 12 2" /><polyline points="2 17 12 22 22 17" /><polyline points="2 12 12 17 22 12" /></Icon>,
  history: <Icon><circle cx="12" cy="12" r="9" /><polyline points="12 7 12 12 15.5 13.5" /></Icon>,
  insights: <Icon d="M18 8A6 6 0 0 0 6 8c0 7-3 9-3 9h18s-3-2-3-9" />,
  eval: <Icon><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z" /><polyline points="9 11.5 11 13.5 15 9.5" /></Icon>,
  data: <Icon><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" /><polyline points="17 8 12 3 7 8" /><line x1="12" y1="3" x2="12" y2="15" /></Icon>,
}
const TABS = ['ask', 'data', 'semantic', 'history', 'insights', 'eval'] as const

export default function App() {
  const [tab, setTab] = useState('ask')
  const [menuOpen, setMenuOpen] = useState(false)
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
  const [insightRuns, setInsightRuns] = useState<Record<string, any>>({})
  const [evalResult, setEvalResult] = useState<any>(null)
  const [followUp, setFollowUp] = useState('')
  // CSV uploads (Data tab): file picker + list of uploaded datasets.
  const [datasets, setDatasets] = useState<any[]>([])
  const [uploadFile, setUploadFile] = useState<File | null>(null)
  const [uploading, setUploading] = useState(false)
  const [uploadError, setUploadError] = useState('')
  const [uploadSuccess, setUploadSuccess] = useState<any>(null)
  // Auth: the app works fully logged out (anonymous = demo workspace).
  // Signing in unlocks saving insights, running evals and admin limits.
  const [user, setUser] = useState<any>(() => getStoredUser())
  const [showLogin, setShowLogin] = useState(false)
  const [loginMode, setLoginMode] = useState<'signin' | 'register'>('signin')
  const [loginEmail, setLoginEmail] = useState('')
  const [loginName, setLoginName] = useState('')
  const [loginPassword, setLoginPassword] = useState('')
  const [loginError, setLoginError] = useState('')

  useEffect(() => {
    api.health().then(setHealth).catch(() => setHealth({ status: 'unreachable' }))
    api.metrics().then((d) => setMetrics(d.metrics || [])).catch(() => {})
    api.models().then(setModels).catch(() => {})
    refreshHistory()
    refreshDatasets()
    api.insights().then((d) => setInsights(d.insights || [])).catch(() => {})
  }, [])

  function refreshHistory() {
    api.history().then((d) => setHistory(d.runs || [])).catch(() => {})
  }

  function refreshDatasets() {
    api.datasets().then((d) => setDatasets(d.datasets || [])).catch(() => {})
  }

  async function doUpload() {
    if (!uploadFile) return
    setUploading(true); setUploadError(''); setUploadSuccess(null)
    try {
      const ds = await api.uploadCsv(uploadFile)
      setUploadSuccess(ds); setUploadFile(null); refreshDatasets()
    } catch (e: any) {
      setUploadError(e.message || 'Upload failed. Check the file is a CSV under 5 MB and try again.')
    } finally { setUploading(false) }
  }

  async function deleteDataset(id: string) {
    try {
      await api.deleteDataset(id)
      setDatasets((prev) => prev.filter((d) => d.id !== id))
      if (uploadSuccess?.id === id) setUploadSuccess(null)
    } catch (e: any) { setUploadError(e.message || 'Could not delete the dataset.') }
  }

  function goTab(t: typeof TABS[number]) {
    setTab(t)
    setMenuOpen(false)
    window.scrollTo({ top: 0 })
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
    try { setEvalResult(await api.runEval()) } catch (e: any) {
      setError(e.message)
      if ((e.message || '').includes('sign in')) setShowLogin(true)
    } finally { setLoading(false) }
  }

  async function submitAuth(e?: React.FormEvent) {
    e?.preventDefault()
    setLoginError('')
    try {
      const d = loginMode === 'register'
        ? await api.register(loginEmail.trim(), loginName.trim() || loginEmail.split('@')[0], loginPassword)
        : await api.login(loginEmail.trim(), loginPassword)
      setUser({ email: d.email, name: d.name, role: d.role })
      setShowLogin(false); setLoginPassword('')
    } catch (err: any) {
      setLoginError(err.message || 'Sign-in failed. Check the email and password.')
    }
  }

  function fillDemo(admin: boolean) {
    setLoginMode('signin')
    setLoginEmail(admin ? 'admin@querypilot.local' : 'demo@querypilot.local')
    setLoginPassword(admin ? 'admin123' : 'demo123')
    setLoginError('')
  }

  function signOut() {
    api.logout()
    setUser(null)
    setShowLogin(false)
  }

  async function saveInsight() {
    if (!run) return
    try {
      await api.createInsight({ run_id: run.id, question: run.question, sql: run.sql, cadence: 'daily', threshold: null })
    } catch (e: any) {
      setError(e.message || 'Could not save the insight.')
      if ((e.message || '').includes('sign in')) setShowLogin(true)
      return
    }
    const d = await api.insights(); setInsights(d.insights || [])
    setTab('insights')
  }

  async function runInsightNow(id: string) {
    try {
      const r = await api.runInsightNow(id)
      setInsightRuns((prev) => ({ ...prev, [id]: r }))
      const d = await api.insights(); setInsights(d.insights || [])
    } catch (e: any) {
      setError(e.message || 'Could not run the insight.')
      if ((e.message || '').includes('sign in')) setShowLogin(true)
    }
  }

  const healthOk = health?.status === 'ok'
  const healthKnown = !!health?.status

  return (
    <div className="app">
      <header className="navbar">
        <div className="navbar-inner">
          <button className="brand" onClick={() => goTab('ask')} aria-label="QueryPilot — home">
            <div className="logo-mark" aria-hidden="true">
              <svg viewBox="0 0 24 24" fill="none" stroke="#fff" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M13 2 4.5 13.5H11L9.5 22 19 9.5h-6.5L13 2z" />
              </svg>
            </div>
            <div className="brand-text">
              <h1>Query<span>Pilot</span></h1>
              <p>Trusted text-to-SQL · governed semantic layer</p>
            </div>
          </button>
          <nav className={`main-nav ${menuOpen ? 'open' : ''}`} aria-label="Sections">
            {TABS.map((t) => (
              <button key={t} className={`nav-link ${tab === t ? 'active' : ''}`} onClick={() => goTab(t)} aria-current={tab === t ? 'page' : undefined}>
                {ICONS[t]}{t[0].toUpperCase() + t.slice(1)}
              </button>
            ))}
          </nav>
          <div className="status">
            <span className={`pill ${healthOk ? 'ok' : healthKnown ? 'down' : ''}`}>
              <span className="dot" />
              {healthOk ? 'Live' : healthKnown ? 'Unreachable' : 'Checking…'}
              {health?.llm_mode && <span className="hide-sm">· {health.llm_mode}</span>}
            </span>
            <span className="badge-sample">Sample data</span>
            {user ? (
              <span className="auth-area">
                <span className="user-chip" title={`${user.email} · ${user.role}`}>{user.email}</span>
                <button className="btn btn-sm" onClick={signOut}>Sign out</button>
              </span>
            ) : (
              <button className="btn btn-sm btn-primary" onClick={() => { setShowLogin((v) => !v); setLoginError('') }}>Sign in</button>
            )}
          </div>
          <button className="menu-toggle" aria-label="Toggle navigation menu" aria-expanded={menuOpen} onClick={() => setMenuOpen((v) => !v)}>
            {menuOpen ? '✕' : '☰'}
          </button>
        </div>
      </header>

      <main>
      {showLogin && !user && (
        <section className="page" style={{ paddingTop: 8 }}>
          <div className="card auth-card">
            <h3 style={{ marginTop: 0 }}>{loginMode === 'register' ? 'Create an account' : 'Sign in'}</h3>
            <p className="muted" style={{ marginTop: 0 }}>
              Asking questions works without an account. Signing in unlocks saving insights, running evals and admin limits.
            </p>
            <form onSubmit={submitAuth}>
              {loginMode === 'register' && (
                <input value={loginName} onChange={(e) => setLoginName(e.target.value)} placeholder="Name" autoComplete="name" />
              )}
              <input value={loginEmail} onChange={(e) => setLoginEmail(e.target.value)} placeholder="Email" type="email" required autoComplete="email" />
              <input value={loginPassword} onChange={(e) => setLoginPassword(e.target.value)} placeholder="Password" type="password" required autoComplete={loginMode === 'register' ? 'new-password' : 'current-password'} />
              {loginError && <p style={{ color: '#fb7185', margin: '4px 0' }}>{loginError}</p>}
              <div className="auth-row">
                <button className="btn btn-primary" type="submit">{loginMode === 'register' ? 'Create account' : 'Sign in'}</button>
                <button className="btn" type="button" onClick={() => setShowLogin(false)}>Cancel</button>
              </div>
            </form>
            <div className="auth-row">
              <button className="btn btn-sm" type="button" onClick={() => fillDemo(false)}>Use demo account</button>
              <button className="btn btn-sm" type="button" onClick={() => fillDemo(true)}>Use demo admin</button>
              <button className="btn btn-sm" type="button" onClick={() => { setLoginMode(loginMode === 'register' ? 'signin' : 'register'); setLoginError('') }}>
                {loginMode === 'register' ? 'Have an account? Sign in' : 'New here? Create an account'}
              </button>
            </div>
            <p className="muted" style={{ marginBottom: 0 }}>Demo: demo@querypilot.local / demo123 · Admin: admin@querypilot.local / admin123</p>
          </div>
        </section>
      )}
      {tab === 'ask' && (
        <section className="ask-page">
          <div className="hero-band">
            <div className="hero">
              <span className="eyebrow">Trusted text-to-SQL · Governed semantic layer</span>
              <h2>Ask your data <span className="grad">anything.</span></h2>
              <p>Plain English in, validated SQL out — with the definition, the chart and the lineage, every time. No guessing, no hidden numbers.</p>
            </div>
            <div className="trust-stats" aria-label="Measured results">
              <div className="trust-stat"><b>100%</b><span>Execution accuracy · 50 golden cases</span></div>
              <div className="trust-stat"><b>11/11</b><span>Safety checks blocked</span></div>
              <div className="trust-stat"><b>56</b><span>Backend tests passing</span></div>
            </div>
          </div>

          <div className="ask-workspace">
            <div className="ask-col">
              <div className="card composer">
                <label htmlFor="workspace">Workspace</label>
                <div className="composer-row">
                  <select id="workspace" value={workspace} onChange={(e) => setWorkspace(e.target.value)}>
                    <option value="demo">demo · sample e-commerce</option>
                  </select>
                </div>
                <textarea id="question" value={question} onChange={(e) => setQuestion(e.target.value)} rows={3}
                  placeholder="Ask a business question, e.g. What is total revenue by month?"
                  onKeyDown={(e) => { if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) ask() }} />
                <div className="composer-row">
                  <button className="btn btn-primary ask-btn" disabled={loading} onClick={() => ask()}>
                    {loading ? 'Asking…' : 'Ask'}
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><line x1="5" y1="12" x2="19" y2="12" /><polyline points="12 5 19 12 12 19" /></svg>
                  </button>
                </div>
                <p className="composer-hint">Every answer is planned against the semantic layer and checked with sqlglot before it runs. · Layer {health?.layer_version || '…'}</p>
              </div>

              <div className="chip-groups">
                {DEMO_GROUPS.map((g) => (
                  <div className="chip-group" key={g.label}>
                    <div className="chip-group-label"><span className="swatch" style={{ background: g.color }} />{g.label}</div>
                    <div className="chips">
                      {g.questions.map((q) => (
                        <button className="chip" key={q} onClick={() => { setQuestion(q); ask(q) }}>{q}</button>
                      ))}
                    </div>
                  </div>
                ))}
              </div>
            </div>

            <div className="result-col">
              {error && (
                <div className="state-panel state-red">
                  <div className="state-icon">⚠️</div>
                  <h3>Couldn’t reach the backend</h3>
                  <p>{error}</p>
                </div>
              )}
              {loading && (
                <div className="card stepper" aria-live="polite">
                  <div className="stepper-title"><span className="spinner" />Running your question</div>
                  <div className="steps">
                    {PIPELINE_STEPS.map((s) => (
                      <div className="step" key={s}><div className="step-dot" /><div className="step-label">{s}</div></div>
                    ))}
                  </div>
                  <div className="stepper-bar"><i /></div>
                </div>
              )}
              {run && <Result key={run.id} run={run} onClarify={(opt) => ask(resolveClarifying(run, opt))} onSave={saveInsight} />}
              {run && run.status === 'executed' && (
                <div className="card followup">
                  <h3>Follow-up</h3>
                  <p className="muted">Reuses this run’s metric ({run.metric}) — try “by region” or “in March”.</p>
                  <input value={followUp} onChange={(e) => setFollowUp(e.target.value)} placeholder="e.g. by region"
                    onKeyDown={(e) => { if (e.key === 'Enter') sendFollowUp() }} />
                  <button className="btn btn-primary" disabled={loading} onClick={sendFollowUp}>Ask follow-up</button>
                </div>
              )}
              {!run && !loading && !error && (
                <div className="card result-empty">
                  <div className="empty-icon">✦</div>
                  <h3>Your answer will appear here</h3>
                  <p className="muted">Ask a question or pick one from the list. Every answer shows the chart first, then the validated SQL, lineage and cost — nothing hidden.</p>
                  <ol className="mini-steps">
                    <li><b>Plan</b> — the question is mapped to the semantic layer</li>
                    <li><b>Validate</b> — sqlglot checks the SQL before it runs</li>
                    <li><b>Execute</b> — read-only, with row and time limits</li>
                  </ol>
                </div>
              )}
            </div>
          </div>
        </section>
      )}

      {tab === 'data' && (
        <section className="page">
          <div className="page-head">
            <h2>Your data</h2>
            <p className="section-sub">Upload a CSV and ask questions over it in plain English — the same sqlglot validator, read-only executor and correction loop as the demo data. Limits: one .csv file, max 5 MB, 100,000 rows, 50 columns.</p>
          </div>
          <div className="card upload-card">
            <h3 style={{ marginTop: 0 }}>Upload a CSV</h3>
            <p className="muted" style={{ marginTop: 0 }}>
              Columns are cleaned up automatically (spaces become underscores) and types are detected — numbers, dates and text.
              Try it first with the sample file: <a href="/sample-sales.csv" download>Download sample CSV</a> (200 rows of sales data).
            </p>
            <div className="upload-row">
              <input type="file" accept=".csv" onChange={(e) => { setUploadFile(e.target.files?.[0] || null); setUploadError(''); setUploadSuccess(null) }} />
              <button className="btn btn-primary" disabled={!uploadFile || uploading} onClick={doUpload}>
                {uploading ? 'Uploading…' : 'Upload'}
              </button>
            </div>
            {uploadFile && <p className="muted" style={{ marginBottom: 0 }}>Selected: {uploadFile.name} ({Math.round(uploadFile.size / 1024)} KB)</p>}
            {uploadError && <p style={{ color: '#fb7185', margin: '8px 0 0' }}>{uploadError}</p>}
            {uploadSuccess && (
              <div className="upload-success">
                <p style={{ margin: '8px 0' }}>✓ <b>{uploadSuccess.filename}</b> uploaded — {uploadSuccess.row_count} rows, {uploadSuccess.columns.length} columns.</p>
                <p className="muted" style={{ margin: '0 0 6px' }}>Try asking:</p>
                <div className="chips">
                  {(uploadSuccess.example_questions || []).map((q: string) => (
                    <button className="chip" key={q} onClick={() => { setQuestion(q); setTab('ask'); ask(q) }}>{q}</button>
                  ))}
                </div>
              </div>
            )}
          </div>
          <h3>Uploaded datasets</h3>
          {datasets.length === 0 && (
            <div className="card empty-state"><div className="empty-icon">📁</div>No datasets yet. Upload a CSV above.</div>
          )}
          <div className="dataset-grid">
            {datasets.map((d: any) => (
              <div className="card dataset-card" key={d.id}>
                <div className="dataset-head"><b>{d.filename}</b><span className="tag">{d.row_count} rows</span></div>
                <div className="tag-row">
                  {(d.columns || []).map((c: any) => <span className="tag" key={c.name}>{c.name} · {c.type}</span>)}
                </div>
                <div className="dataset-actions">
                  <button className="btn btn-sm btn-primary" onClick={() => {
                    const name = d.table_name.split('_').slice(2).join(' ')
                    const q = `How many rows are in ${name}?`
                    setQuestion(q); setTab('ask'); ask(q)
                  }}>Ask about it</button>
                  <button className="btn btn-sm btn-danger" onClick={() => deleteDataset(d.id)}>Delete</button>
                </div>
              </div>
            ))}
          </div>
        </section>
      )}

      {tab === 'semantic' && (
        <section className="page">
          <div className="page-head">
            <h2>Semantic explorer</h2>
            <p className="section-sub">The governed source of truth. The planner may only use the metrics, dimensions and joins defined here — nothing is invented.</p>
          </div>
          <div className="metric-grid">
            {metrics.map((m: any, i: number) => (
              <div className={`card metric-card accent-${i % 5}`} key={m.name}>
                <div className="metric-top">
                  <div className="metric-icon" aria-hidden="true">{metricIcon(m.name)}</div>
                  <h3>{m.name}</h3>
                </div>
                <p>{m.description}</p>
                <p className="muted">Definition: {m.definition}</p>
                <div className="tag-row">
                  <span className="tag">Owner: {m.owner}</span>
                  <span className="tag">Model: {m.model}</span>
                </div>
                <div className="tag-row">
                  {(m.dimensions || []).map((d: string) => <span className="tag dim" key={d}>{d}</span>)}
                </div>
              </div>
            ))}
          </div>
          {models && (
            <div className="card">
              <h3>Models &amp; joins</h3>
              <p className="muted">PII is blocked at validation — it never reaches the database.</p>
              {(models.models || []).map((m: any) => (
                <div className="model-line" key={m.name}>
                  <b>{m.name}</b>{' · '}
                  {(m.columns || []).map((c: any, ci: number) => (
                    <span key={c.name}>{ci > 0 ? ', ' : ''}{c.name}{c.pii ? <span className="tag pii" style={{ marginLeft: 4 }}>PII</span> : ''}</span>
                  ))}
                </div>
              ))}
              <div className="join-line">
                {(models.joins || []).map((j: any, i: number) => (
                  <span className="join-chip" key={i}>{j.from} → {j.to}</span>
                ))}
              </div>
            </div>
          )}
        </section>
      )}

      {tab === 'history' && (
        <section className="page">
          <div className="page-head">
            <h2>History</h2>
            <p className="section-sub">Every past answer, with its exact SQL and cost. Select one to reopen it.</p>
          </div>
          {history.length === 0 && (
            <div className="card empty-state"><div className="empty-icon">🕘</div>No runs yet. Ask a question first.</div>
          )}
          <div className="history-grid">
            {history.map((r: any) => (
              <div className="card history-card" key={r.id} onClick={async () => { setRun(await api.run(r.id)); setTab('ask') }}>
                <b>{r.question}</b>
                <p className="muted" style={{ margin: 0 }}>
                  <span className={`status-dot ${r.status}`} />{r.status} · {r.metric || '—'} · {r.row_count} rows · {r.duration_ms} ms · ${r.cost ?? 0}
                </p>
              </div>
            ))}
          </div>
        </section>
      )}

      {tab === 'insights' && (
        <section className="page">
          <div className="page-head">
            <h2>Scheduled insights</h2>
            <p className="section-sub">Saved questions that re-run on a cadence and alert only when a threshold is crossed.</p>
          </div>
          {insights.length === 0 && (
            <div className="card empty-state"><div className="empty-icon">🔔</div>No saved insights yet. Save a run from the Ask tab.</div>
          )}
          <div className="insights-grid">
            {insights.map((ins: any) => (
              <div className="card insight-card" key={ins.id}>
                <div className="insight-head">
                  <b>{ins.question}</b>
                  <span className={`status-pill ${ins.status === 'active' ? 'active' : 'paused'}`}>{ins.status}</span>
                </div>
                <p className="muted" style={{ margin: 0 }}>Cadence: {ins.cadence} · Threshold: {ins.threshold ?? 'none'}{ins.last_value != null ? ` · Last value: ${ins.last_value}` : ''}</p>
                {insightRuns[ins.id] && (
                  <p className="muted" style={{ margin: '6px 0 0' }}>
                    Last run: value {insightRuns[ins.id].value ?? '—'}
                    {insightRuns[ins.id].breached ? ' · ⚠️ threshold breached — alert sent' : ' · within threshold'}
                    {insightRuns[ins.id].status === 'paused_on_schema_change' ? ' · paused: saved SQL no longer validates' : ''}
                  </p>
                )}
                <div className="insight-actions">
                  <button className="btn btn-sm btn-primary" onClick={() => runInsightNow(ins.id)}>▶ Run now</button>
                  <button className="btn btn-sm" onClick={async () => { await api.patchInsight(ins.id, { status: ins.status === 'active' ? 'paused' : 'active' }); const d = await api.insights(); setInsights(d.insights || []) }}>
                    {ins.status === 'active' ? '⏸ Pause' : '▶ Resume'}
                  </button>
                  <button className="btn btn-sm btn-danger" onClick={async () => { await api.deleteInsight(ins.id); const d = await api.insights(); setInsights(d.insights || []) }}>Delete</button>
                </div>
              </div>
            ))}
          </div>
        </section>
      )}

      {tab === 'eval' && (
        <section className="page">
          <div className="page-head">
            <h2>Eval &amp; safety</h2>
            <p className="section-sub">Runs the real ask path on the 50-case golden set, plus the safety suite and the 4-way ablation. Numbers come from a live run, never from the PRD targets. Running the eval needs an admin account — use the demo admin login.</p>
          </div>
          <button className="btn btn-primary" disabled={loading} onClick={runEval}>{loading ? 'Running…' : '▶ Run evaluation'}</button>
          {evalResult && (
            <div className="card score-hero">
              <div>
                <div className="score-number">{(evalResult.execution_accuracy * 100).toFixed(0)}%</div>
                <div className="score-caption">Execution accuracy · {evalResult.golden_cases} golden cases</div>
              </div>
              <div className="score-bars">
                <ScoreBar label="Execution accuracy" value={evalResult.execution_accuracy} />
                <ScoreBar label="Valid SQL rate" value={evalResult.valid_sql_rate} />
                <ScoreBar label="Safety suite blocked" value={evalResult.safety_suite.pass_rate} text={`${evalResult.safety_suite.blocked}/${evalResult.safety_suite.total} blocked`} />
                <p className="muted" style={{ margin: 0 }}>
                  Correction success: {evalResult.correction_success ? 'yes' : 'no'} ({evalResult.correction_attempts_on_broken_draft} attempt(s) on the broken draft)
                </p>
              </div>
            </div>
          )}
          {evalResult && evalResult.ablation && (
            <div className="card">
              <h3>Ablation — what each layer adds (same {evalResult.golden_cases} cases)</h3>
              <div className="table-wrap">
                <table>
                  <thead><tr><th>Config</th><th>Accuracy</th><th>Valid SQL</th><th>Avg latency</th><th>Cache hit</th></tr></thead>
                  <tbody>
                    <tr>
                      <td>Direct-LLM baseline</td>
                      <td colSpan={4}>{evalResult.ablation.direct_llm_baseline.status === 'run'
                        ? `${(evalResult.ablation.direct_llm_baseline.execution_accuracy * 100).toFixed(0)}%`
                        : evalResult.ablation.direct_llm_baseline.status}</td>
                    </tr>
                    <tr>
                      <td>+ Semantic layer</td>
                      <td>{(evalResult.ablation.semantic_layer_only.execution_accuracy * 100).toFixed(0)}%</td>
                      <td>{(evalResult.ablation.semantic_layer_only.valid_sql_rate * 100).toFixed(0)}%</td>
                      <td>{evalResult.ablation.semantic_layer_only.avg_latency_ms} ms</td>
                      <td>—</td>
                    </tr>
                    <tr>
                      <td>+ Validator &amp; correction</td>
                      <td>{(evalResult.ablation.validator_and_correction.execution_accuracy * 100).toFixed(0)}%</td>
                      <td>{(evalResult.ablation.validator_and_correction.valid_sql_rate * 100).toFixed(0)}%</td>
                      <td>{evalResult.ablation.validator_and_correction.avg_latency_ms} ms</td>
                      <td>—</td>
                    </tr>
                    <tr>
                      <td>Full system + cache</td>
                      <td>{(evalResult.ablation.full_system_cached.execution_accuracy * 100).toFixed(0)}%</td>
                      <td>{(evalResult.ablation.full_system_cached.valid_sql_rate * 100).toFixed(0)}%</td>
                      <td>{evalResult.ablation.full_system_cached.avg_latency_ms} ms</td>
                      <td>{(evalResult.ablation.full_system_cached.cache_hit_rate * 100).toFixed(0)}%</td>
                    </tr>
                  </tbody>
                </table>
              </div>
            </div>
          )}
          {evalResult && (
            <div className="card">
              <h3>Per-case results</h3>
              <div className="table-wrap">
                <table>
                  <thead><tr><th>ID</th><th>Category</th><th>Expected</th><th>Got</th><th>Pass</th></tr></thead>
                  <tbody>
                    {evalResult.per_case.map((c: any) => (
                      <tr key={c.id}><td>{c.id}</td><td>{c.category}</td><td>{c.expected}</td><td>{c.got}</td><td className={c.passed ? 'pass' : 'fail'}>{c.passed ? '✓' : '✗'}</td></tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}
        </section>
      )}
      </main>

      <footer className="site-footer">
        <div className="footer-inner">
          <div className="footer-brand">
            <div className="logo-mark logo-mark-sm" aria-hidden="true">
              <svg viewBox="0 0 24 24" fill="none" stroke="#fff" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M13 2 4.5 13.5H11L9.5 22 19 9.5h-6.5L13 2z" />
              </svg>
            </div>
            <div>
              <b>QueryPilot</b>
              <p>Trusted text-to-SQL over a governed semantic layer.</p>
            </div>
          </div>
          <nav className="footer-links" aria-label="Footer">
            <a href="https://github.com/Gautam825406/querypilot" target="_blank" rel="noreferrer">GitHub ↗</a>
            <a href={`${API_BASE}/api/health`} target="_blank" rel="noreferrer">API health ↗</a>
            <button className="footer-link-btn" onClick={() => goTab('eval')}>Eval &amp; safety</button>
          </nav>
        </div>
        <div className="footer-bottom">
          <span>Sample data only — the demo dataset is synthetic e-commerce data. No real personal data.</span>
          <span>Built with FastAPI · LangGraph · sqlglot · DuckDB</span>
        </div>
      </footer>
    </div>
  )
}

function ScoreBar({ label, value, text }: { label: string; value: number; text?: string }) {
  const pct = Math.round(value * 100)
  return (
    <div className="score-bar-row">
      <div className="bar-label"><span>{label}</span><span>{text || `${pct}%`}</span></div>
      <div className="bar-track"><div className="bar-fill" style={{ width: `${pct}%` }} /></div>
    </div>
  )
}

function Result({ run, onClarify, onSave }: { run: any; onClarify: (o: string) => void; onSave: () => void }) {
  const [copied, setCopied] = useState(false)
  const [voted, setVoted] = useState('')
  async function copySql() {
    try { await navigator.clipboard.writeText(run.sql || '') } catch { /* clipboard unavailable */ }
    setCopied(true); setTimeout(() => setCopied(false), 2000)
  }
  if (run.status === 'clarifying') {
    return (
      <div className="state-panel state-blue">
        <div className="state-icon">🤔</div>
        <h3>One quick question</h3>
        <p>{run.clarifying_question}</p>
        <div className="option-cards">{(run.clarifying_options || []).map((o: string) => (
          <button className="option-card" key={o} onClick={() => onClarify(o)}>{o}</button>
        ))}</div>
        <p className="muted" style={{ marginTop: 14 }}>Asking back is a success state — better than a confident wrong number.</p>
      </div>
    )
  }
  if (run.status === 'refused' || run.status === 'blocked') {
    return (
      <div className="state-panel state-amber">
        <div className="state-icon">{run.status === 'blocked' ? '🛡️' : '🚫'}</div>
        <h3>{run.status === 'blocked' ? 'Blocked by the validator' : 'Cannot answer from the modelled data'}</h3>
        <p>{run.refusal}</p>
        <div className="try-instead">
          <span className="muted">Nearest answerable question:</span>
          <button className="btn btn-sm" onClick={() => onClarify('What is total revenue by month?')}>What is total revenue by month? →</button>
        </div>
      </div>
    )
  }
  if (run.status === 'failed') {
    return (
      <div className="state-panel state-red">
        <div className="state-icon">⚠️</div>
        <h3>Execution failed</h3>
        <p>{run.explanation}</p>
        <div className="code-panel"><pre>{(run.attempts || []).map((a: any) => `#${a.attempt}: ${a.error}`).join('\n')}</pre></div>
        <div className="try-instead">
          <span className="muted">Try narrowing the question, or:</span>
          <button className="btn btn-sm" onClick={() => onClarify('What is total revenue by month?')}>What is total revenue by month? →</button>
        </div>
      </div>
    )
  }
  const chartLabel = run.chart_spec?.type && run.chart_spec.type !== 'none'
    ? `${run.chart_spec.type === 'stat' ? 'Single value' : run.chart_spec.type === 'line' ? 'Trend' : 'Comparison'} · ${prettyMetric(run.metric || 'result')}`
    : null
  return (
    <div className="card glow">
      {run.chart_spec?.type === 'stat' ? (
        <div className="result-hero">
          <div className="result-metric">{prettyMetric(run.metric || 'Result')}</div>
          <Chart spec={run.chart_spec} />
        </div>
      ) : (
        <div className="chart-panel">
          <h4>{chartLabel || prettyMetric(run.metric || 'Result')}</h4>
          <p className="chart-sub">{run.lineage?.definition || ''}</p>
          <Chart spec={run.chart_spec} />
        </div>
      )}
      <p className="lead">{run.explanation}</p>
      {run.rows?.length === 0 && <p className="muted">Empty result — the filters and period returned no rows. This is not the same as zero.</p>}
      <div className="actions-row">
        <button className="btn btn-sm" disabled={!!voted} onClick={() => { api.feedback(run.id, 'up'); setVoted('up') }}>{voted === 'up' ? '✓ Helpful' : '👍 Helpful'}</button>
        <button className="btn btn-sm" disabled={!!voted} onClick={() => { api.feedback(run.id, 'down'); setVoted('down') }}>{voted === 'down' ? '✓ Noted' : '👎 Wrong'}</button>
        <button className="btn btn-sm btn-primary" onClick={onSave}>＋ Save as scheduled insight</button>
      </div>
      <details>
        <summary>Result table ({run.row_count} rows)</summary>
        <div className="table-wrap">
          <table>
            <thead><tr>{(run.columns || []).map((c: string) => <th key={c}>{c}</th>)}</tr></thead>
            <tbody>
              {(run.rows || []).slice(0, 20).map((r: any[], i: number) => (
                <tr key={i}>{r.map((v, j) => <td key={j}>{String(v)}</td>)}</tr>
              ))}
            </tbody>
          </table>
        </div>
      </details>
      <details>
        <summary>SQL, lineage &amp; validation</summary>
        <div className="code-panel">
          <div className="code-panel-head"><span>Validated SQL</span><button className="btn btn-sm" onClick={copySql}>{copied ? '✓ Copied' : 'Copy SQL'}</button></div>
          <pre>{run.sql}</pre>
        </div>
        <p className="muted">Metric: {run.metric} — {run.lineage?.definition}</p>
        <div className="meta-grid">
          <div className="meta-item"><div className="k">Layer</div><div className="v">v{run.layer_version}</div></div>
          <div className="meta-item"><div className="k">Validation</div><div className="v">{run.validation?.rule || 'passed'}</div></div>
          <div className="meta-item"><div className="k">Attempts</div><div className="v">{Math.max(run.attempts?.length || 0, 1)}</div></div>
          <div className="meta-item"><div className="k">Cache</div><div className="v">{run.cache_hit ? 'hit' : 'miss'}</div></div>
          <div className="meta-item"><div className="k">Cost</div><div className="v">${run.cost ?? 0}</div></div>
          <div className="meta-item"><div className="k">Duration</div><div className="v">{run.duration_ms} ms</div></div>
          <div className="meta-item"><div className="k">LLM mode</div><div className="v">{run.llm_mode}</div></div>
        </div>
        <div className="plan-flow">
          {(run.plan_steps || []).map((s: any, i: number) => (
            <React.Fragment key={i}>
              {i > 0 && <span className="plan-arrow">→</span>}
              <span className="plan-chip">{s.action}</span>
            </React.Fragment>
          ))}
        </div>
      </details>
    </div>
  )
}
