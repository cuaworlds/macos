// Backend client: JWT auth (access + refresh in localStorage), transparent
// refresh-on-401, and adapters mapping backend runs/rollouts/tasks onto the
// dashboard's existing types.
import type { RunInfo, StepRecord, TaskDef, TaskResult } from './trajectory'
import { baseTaskId, parseJsonl } from './trajectory'

const API_BASE: string =
  (import.meta.env.VITE_API_BASE_URL as string | undefined) ?? 'http://localhost:8000/api/v1'

// Offline/local mode: read runs straight from the repo's outputs/ via the dev
// Vite plugin instead of the hosted backend. No auth. Dev-only (npm run dev).
export const IS_LOCAL = (import.meta.env.VITE_DATA_SOURCE as string | undefined) === 'local'

const ACCESS_KEY = 'cua_access'
const REFRESH_KEY = 'cua_refresh'

export type User = {
  id: number
  username: string
  email?: string
  role?: string
  first_name?: string | null
  last_name?: string | null
  status?: string
  created_at?: string
  last_login?: string | null
  has_api_key?: boolean
  api_key_prefix?: string | null
  api_key_created_at?: string | null
}

// -- token storage ---------------------------------------------------------

export const getAccess = () => localStorage.getItem(ACCESS_KEY)
export const getRefresh = () => localStorage.getItem(REFRESH_KEY)

export function setTokens(access: string, refresh: string) {
  localStorage.setItem(ACCESS_KEY, access)
  localStorage.setItem(REFRESH_KEY, refresh)
}

export function clearTokens() {
  localStorage.removeItem(ACCESS_KEY)
  localStorage.removeItem(REFRESH_KEY)
}

// Called when auth is unrecoverable (refresh failed). The AuthProvider registers
// a handler that drops the user so the route guard sends them to /login.
let onAuthError: (() => void) | null = null
export const setAuthErrorHandler = (fn: () => void) => {
  onAuthError = fn
}

// -- core fetch ------------------------------------------------------------

let refreshing: Promise<boolean> | null = null

async function doRefresh(): Promise<boolean> {
  const token = getRefresh()
  if (!token) return false
  const res = await fetch(`${API_BASE}/auth/refresh`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ refresh_token: token }),
  })
  if (!res.ok) return false
  const data = await res.json()
  setTokens(data.access_token, data.refresh_token)
  return true
}

function withAuth(init: RequestInit): RequestInit {
  const token = getAccess()
  return token ? { ...init, headers: { ...init.headers, Authorization: `Bearer ${token}` } } : init
}

async function apiFetch(path: string, init: RequestInit = {}): Promise<Response> {
  let res = await fetch(`${API_BASE}${path}`, withAuth(init))
  if (res.status === 401 && getRefresh()) {
    refreshing = refreshing ?? doRefresh()
    const ok = await refreshing
    refreshing = null
    if (ok) res = await fetch(`${API_BASE}${path}`, withAuth(init))
  }
  if (res.status === 401) {
    clearTokens()
    onAuthError?.()
    throw new Error('Session expired — please sign in again.')
  }
  if (!res.ok) {
    const detail = await res.json().catch(() => null)
    throw new Error(detail?.detail || `HTTP ${res.status}`)
  }
  return res
}

async function apiJson<T>(path: string, init?: RequestInit): Promise<T> {
  return (await apiFetch(path, init)).json() as Promise<T>
}

async function fetchAll<T>(path: string): Promise<T[]> {
  const sep = path.includes('?') ? '&' : '?'
  const items: T[] = []
  let page = 1
  for (;;) {
    const data = await apiJson<{ items: T[]; total_pages: number }>(
      `${path}${sep}page=${page}&per_page=100`,
    )
    items.push(...data.items)
    if (page >= data.total_pages || data.items.length === 0) break
    page++
  }
  return items
}

// -- auth ------------------------------------------------------------------

export async function login(username: string, password: string): Promise<void> {
  const res = await fetch(`${API_BASE}/auth/login`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username, password }),
  })
  if (!res.ok) {
    const detail = await res.json().catch(() => null)
    throw new Error(detail?.detail || `Login failed (HTTP ${res.status})`)
  }
  const data = await res.json()
  setTokens(data.access_token, data.refresh_token)
}

export const me = () => apiJson<User>('/auth/me')

export type ProfileUpdate = { first_name?: string; last_name?: string }

export const updateProfile = (data: ProfileUpdate) =>
  apiJson<User>('/auth/me', {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  })

export async function changePassword(newPassword: string, currentPassword?: string): Promise<void> {
  await apiFetch('/auth/password', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ new_password: newPassword, current_password: currentPassword }),
  })
}

/** Generate (and replace) the caller's API key. Returned in plaintext once. */
export async function generateApiKey(): Promise<string> {
  return (await apiJson<{ api_key: string }>('/auth/key', { method: 'POST' })).api_key
}

// -- backend shapes (only the fields we read) ------------------------------

type BackendRun = { id: number; total_tasks: number; created_at: string }
type BackendRollout = {
  id: number
  task_id: number
  model?: string
  status: string
  result?: { passed?: boolean } | null
  error?: { message?: string } | null
  tokens?: { input?: number; output?: number } | null
  duration_seconds?: number | null
  metadata?: Record<string, unknown> | null
}
type BackendTask = { id: number; prompt: string; metadata?: Record<string, unknown> | null }
type ArtifactManifest = {
  trajectory_url?: string | null
  result_url?: string | null
  screenshots: Record<string, string>
}

// -- adapters --------------------------------------------------------------

const runToInfo = (r: BackendRun): RunInfo => ({
  run_id: String(r.id),
  n_tasks: r.total_tasks,
  mtime: Date.parse(r.created_at),
  has_summary: true,
})

function rolloutToResult(r: BackendRollout): TaskResult {
  const m = (r.metadata ?? {}) as Record<string, unknown>
  return {
    task_id: String(r.id),
    task_def_id: r.task_id,
    base_task_id: (m.base_task_id as string) ?? String(r.task_id),
    category: m.category as string | undefined,
    model: r.model,
    score: m.score as number | undefined,
    max_score: m.max_score as number | undefined,
    n_steps: m.n_steps as number | undefined,
    // terminal_reason carries the original done/fail/max_steps/error verdict.
    status: (m.terminal_reason as string) ?? r.status,
    duration_s: r.duration_seconds ?? undefined,
    input_tokens: r.tokens?.input,
    output_tokens: r.tokens?.output,
    cost_usd: m.cost_usd as number | undefined,
    grade_log: m.grade_log,
    trial: m.trial as number | undefined,
    passed: r.result?.passed,
    error: r.error?.message ?? null,
  }
}

function taskToDef(t: BackendTask): TaskDef {
  const m = (t.metadata ?? {}) as Record<string, unknown>
  return {
    task_id: String(t.id),
    category: (m.category as string) ?? '',
    instruction: t.prompt,
    pre_command: (m.pre_command as string) ?? '',
    grading_command: (m.grading_command as [string, number][]) ?? [],
  }
}

// -- local source (dev Vite plugin reading outputs/) -----------------------

const enc = encodeURIComponent

async function localJson<T>(path: string): Promise<T> {
  const res = await fetch(path)
  if (!res.ok) throw new Error(`HTTP ${res.status}`)
  return res.json() as Promise<T>
}

/** Tag a local summary row with the id used to fetch its task definition. */
const withDefId = (r: TaskResult): TaskResult => ({
  ...r,
  task_def_id: r.base_task_id ?? baseTaskId(r.task_id),
})

// -- domain calls (dispatch on data source) --------------------------------

export type Trajectory = { steps: StepRecord[]; screenshots: Record<string, string> }

export async function listRuns(): Promise<RunInfo[]> {
  if (IS_LOCAL) return localJson<RunInfo[]>('/api/runs')
  return (await fetchAll<BackendRun>('/runs')).map(runToInfo)
}

export async function listRollouts(runId: string): Promise<TaskResult[]> {
  if (IS_LOCAL) return (await localJson<TaskResult[]>(`/api/runs/${enc(runId)}`)).map(withDefId)
  const rollouts = await fetchAll<BackendRollout>(`/rollouts?run_id=${enc(runId)}`)
  return rollouts.map(rolloutToResult)
}

export async function getRollout(runId: string, rolloutId: string): Promise<TaskResult> {
  if (IS_LOCAL) {
    return withDefId(await localJson<TaskResult>(`/outputs/runs/${enc(runId)}/${enc(rolloutId)}/result.json`))
  }
  return rolloutToResult(await apiJson<BackendRollout>(`/rollouts/${enc(rolloutId)}`))
}

export async function getTaskDef(taskId: number | string): Promise<TaskDef> {
  if (IS_LOCAL) return localJson<TaskDef>(`/api/taskdef/${enc(taskId)}`)
  return taskToDef(await apiJson<BackendTask>(`/tasks/${enc(taskId)}`))
}

export async function getTrajectory(runId: string, rolloutId: string): Promise<Trajectory> {
  if (IS_LOCAL) {
    const base = `/outputs/runs/${enc(runId)}/${enc(rolloutId)}`
    const text = await (await fetch(`${base}/trajectory.jsonl`)).text()
    const steps = parseJsonl<StepRecord>(text)
    const screenshots: Record<string, string> = {}
    for (const s of steps)
      for (const a of s.actions ?? [])
        if (a.screenshot) screenshots[a.screenshot] = `${base}/context/${a.screenshot}`
    return { steps, screenshots }
  }
  const manifest = await apiJson<ArtifactManifest>(`/rollouts/${enc(rolloutId)}/artifacts`)
  let steps: StepRecord[] = []
  if (manifest.trajectory_url) {
    steps = parseJsonl<StepRecord>(await (await fetch(manifest.trajectory_url)).text())
  }
  return { steps, screenshots: manifest.screenshots ?? {} }
}
