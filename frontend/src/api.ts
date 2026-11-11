const API = (import.meta as any).env?.VITE_API_URL || 'http://localhost:8000'

const TOKEN_KEY = 'qp_token'
const USER_KEY = 'qp_user'

export function getToken(): string | null {
  return localStorage.getItem(TOKEN_KEY)
}

export function getStoredUser(): { email: string; name: string; role: string } | null {
  try { return JSON.parse(localStorage.getItem(USER_KEY) || 'null') } catch { return null }
}

function storeAuth(d: any) {
  if (d?.access_token) localStorage.setItem(TOKEN_KEY, d.access_token)
  if (d?.email) localStorage.setItem(USER_KEY, JSON.stringify({ email: d.email, name: d.name, role: d.role }))
}

async function req(path: string, init?: RequestInit) {
  const headers: Record<string, string> = { 'Content-Type': 'application/json' }
  const token = getToken()
  if (token) headers['Authorization'] = `Bearer ${token}`
  const r = await fetch(`${API}${path}`, {
    headers,
    ...init,
  })
  if (!r.ok) {
    if (r.status === 401) throw new Error('Please sign in to continue — this action needs an account.')
    throw new Error(`API ${r.status}: ${await r.text()}`)
  }
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
  runInsightNow: (id: string) => req(`/api/insights/${id}/run-now`, { method: 'POST', body: JSON.stringify({}) }),
  feedback: (runId: string, rating: string) =>
    req('/api/feedback', { method: 'POST', body: JSON.stringify({ run_id: runId, rating }) }),
  runEval: () => req('/api/evals/run', { method: 'POST', body: JSON.stringify({}) }),
  // ---- Auth (JWT). The app works fully logged out; signing in unlocks
  // saving insights, running evals and admin limits. ----
  login: async (email: string, password: string) => {
    const d = await req('/api/auth/login', { method: 'POST', body: JSON.stringify({ email, password }) })
    storeAuth(d)
    return d
  },
  register: async (email: string, name: string, password: string) => {
    const d = await req('/api/auth/register', { method: 'POST', body: JSON.stringify({ email, name, password }) })
    storeAuth(d)
    return d
  },
  me: () => req('/api/auth/me'),
  logout: () => { localStorage.removeItem(TOKEN_KEY); localStorage.removeItem(USER_KEY) },
}
