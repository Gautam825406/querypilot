const API = (import.meta as any).env?.VITE_API_URL || 'http://localhost:8000'

async function req(path: string, init?: RequestInit) {
  const r = await fetch(`${API}${path}`, {
    headers: { 'Content-Type': 'application/json' },
    ...init,
  })
  if (!r.ok) throw new Error(`API ${r.status}: ${await r.text()}`)
  return r.json()
}

export const api = {
  health: () => req('/api/health'),
  ask: (question: string, workspace = 'demo') =>
    req('/api/ask', { method: 'POST', body: JSON.stringify({ question, workspace }) }),
  followUp: (runId: string, question: string) =>
    req(`/api/ask/${runId}/follow-up`, { method: 'POST', body: JSON.stringify({ question }) }),
  run: (id: string) => req(`/api/runs/${id}`),
  history: () => req('/api/history'),
  metrics: () => req('/api/semantic/metrics'),
  models: () => req('/api/semantic/models'),
  insights: () => req('/api/insights'),
  createInsight: (body: any) => req('/api/insights', { method: 'POST', body: JSON.stringify(body) }),
  patchInsight: (id: string, body: any) =>
    req(`/api/insights/${id}`, { method: 'PATCH', body: JSON.stringify(body) }),
  deleteInsight: (id: string) => req(`/api/insights/${id}`, { method: 'DELETE' }),
  feedback: (runId: string, rating: string) =>
    req('/api/feedback', { method: 'POST', body: JSON.stringify({ run_id: runId, rating }) }),
  runEval: () => req('/api/evals/run', { method: 'POST', body: JSON.stringify({}) }),
}
