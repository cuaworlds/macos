export type ActionRecord = {
  action: string
  input: Record<string, unknown>
  ok: boolean
  msg: string
  screenshot: string
}

export type StepRecord = {
  step: number
  input_tokens?: number
  output_tokens?: number
  latency_s?: number
  actions?: ActionRecord[]
  text?: string
  status?: string
}

export type Frame = {
  frame_idx: number
  step: number
  action: string
  input: Record<string, unknown>
  ok: boolean
  msg: string
  screenshot: string
  text?: string
  status?: string
}

export type TaskResult = {
  task_id: string
  category?: string
  model?: string
  score?: number
  max_score?: number
  n_steps?: number
  status?: string
  duration_s?: number
  input_tokens?: number
  output_tokens?: number
  cost_usd?: number
  sandbox_id?: string
  error?: string | null
  grade_log?: unknown
  // Multi-trial (pass@k) fields, present when a run was launched with --trials > 1.
  base_task_id?: string
  trial?: number
  passed?: boolean
  // Id used to fetch the task definition: the backend Task id, or the local
  // base task id in offline mode.
  task_def_id?: number | string
}

export type RunInfo = {
  run_id: string
  n_rollouts: number
  mtime: number
  has_summary: boolean
}

export type TaskDef = {
  task_id: string
  category: string
  instruction: string
  pre_command: string
  grading_command: [string, number][]
  // Backend-only metadata (absent in local/offline mode).
  tags?: string[]
  status?: string
}

/** Sum of a task's grading-checkpoint weights — the maximum achievable score. */
export function maxScoreOf(def: Pick<TaskDef, 'grading_command'>): number {
  return def.grading_command.reduce((acc, [, w]) => acc + (w || 0), 0)
}

export type GradeLogEntry = {
  cmd: string
  value: number
  stdout?: string
  hit?: boolean
  error?: string
}

// The model always operates on a virtual 1024x768 display; saved screenshots are
// captured at exactly that size, so action coordinates map onto the rendered image
// by a simple fraction (no per-backend resolution conversion needed).
export const DISPLAY_W = 1024
export const DISPLAY_H = 768

export type Marker = {
  kind: 'click' | 'start' | 'end' | 'move'
  x: number
  y: number
  label: string
}

/** Extract pointer markers (in 1024x768 space) from an action's input. */
export function actionMarkers(action: string, input: Record<string, unknown>): Marker[] {
  const asXY = (v: unknown): [number, number] | null =>
    Array.isArray(v) && v.length === 2 && typeof v[0] === 'number' && typeof v[1] === 'number'
      ? [v[0], v[1]]
      : null

  const markers: Marker[] = []
  if (action === 'left_click_drag') {
    const start = asXY(input.start_coordinate)
    const end = asXY(input.coordinate)
    if (start) markers.push({ kind: 'start', x: start[0], y: start[1], label: 'drag from' })
    if (end) markers.push({ kind: 'end', x: end[0], y: end[1], label: 'drag to' })
    return markers
  }
  const coord = asXY(input.coordinate)
  if (!coord) return markers
  const kind: Marker['kind'] = action === 'mouse_move' ? 'move' : 'click'
  markers.push({ kind, x: coord[0], y: coord[1], label: action })
  return markers
}

export function parseJsonl<T>(text: string): T[] {
  const out: T[] = []
  for (const line of text.split('\n')) {
    const trimmed = line.trim()
    if (!trimmed) continue
    try {
      out.push(JSON.parse(trimmed) as T)
    } catch {
      // skip malformed lines
    }
  }
  return out
}

export function flattenFrames(steps: StepRecord[]): Frame[] {
  const frames: Frame[] = []
  let idx = 0
  for (const step of steps) {
    const actions = step.actions ?? []
    if (actions.length === 0) {
      // step with no actions — surface as a single empty frame so the user sees it.
      frames.push({
        frame_idx: idx++,
        step: step.step,
        action: '(no action)',
        input: {},
        ok: true,
        msg: '',
        screenshot: '',
        text: step.text,
        status: step.status,
      })
      continue
    }
    for (const a of actions) {
      frames.push({
        frame_idx: idx++,
        step: step.step,
        action: a.action,
        input: a.input,
        ok: a.ok,
        msg: a.msg,
        screenshot: a.screenshot,
        text: step.text,
        status: step.status,
      })
    }
  }
  return frames
}

/** Strip a trailing `__tNN` trial suffix to recover the base task id. */
export function baseTaskId(id: string): string {
  return id.replace(/__t\d+$/, '')
}

/** Descending id comparator; numeric when both ids are numbers, else lexicographic. */
export function cmpIdDesc(a: string, b: string): number {
  const na = Number(a)
  const nb = Number(b)
  if (Number.isFinite(na) && Number.isFinite(nb)) return nb - na
  return b.localeCompare(a)
}

/** A passing trial reaches the run's pass threshold (default: full credit). */
export function trialPassed(t: TaskResult): boolean {
  if (typeof t.passed === 'boolean') return t.passed
  if (!t.max_score || t.score === undefined || t.score === null) return false
  return t.score / t.max_score >= 0.99
}

/** Tone of a single trial's score, for chip coloring. */
export function scoreTone(score?: number, max?: number): 'pass' | 'partial' | 'zero' {
  if (!max || score === undefined || score === null) return 'zero'
  const r = score / max
  if (r >= 0.99) return 'pass'
  if (r > 0) return 'partial'
  return 'zero'
}

export type TaskGroup = {
  baseId: string
  category?: string
  trials: TaskResult[] // ordered by trial index
  nTrials: number
  passes: number
  passRate: number
  meanScore: number
  meanSteps: number
  maxScore: number
}

/** Difficulty band from pass count: the 1–2/5 "target band" we mine for. */
export function bandOf(
  passes: number,
  nTrials: number,
): { label: string; cls: string } | null {
  if (nTrials < 2) return null
  if (passes === 0) return { label: 'hard', cls: 'band-hard' }
  if (passes >= Math.ceil(nTrials * 0.6)) return { label: 'easy', cls: 'band-easy' }
  return { label: 'band', cls: 'band-target' }
}

/** Group flat per-trial rows by base task; single-trial runs yield 1-trial groups. */
export function groupTasks(results: TaskResult[]): TaskGroup[] {
  const by = new Map<string, TaskResult[]>()
  for (const r of results) {
    const id = r.base_task_id ?? baseTaskId(r.task_id)
    const arr = by.get(id)
    if (arr) arr.push(r)
    else by.set(id, [r])
  }
  const groups: TaskGroup[] = []
  for (const [baseId, trials] of by) {
    trials.sort((a, b) => (a.trial ?? 0) - (b.trial ?? 0))
    const passes = trials.filter(trialPassed).length
    const scores = trials.map((t) => t.score ?? 0)
    const steps = trials.map((t) => t.n_steps ?? 0)
    groups.push({
      baseId,
      category: trials[0]?.category,
      trials,
      nTrials: trials.length,
      passes,
      passRate: trials.length ? passes / trials.length : 0,
      meanScore: scores.reduce((a, b) => a + b, 0) / (scores.length || 1),
      meanSteps: steps.reduce((a, b) => a + b, 0) / (steps.length || 1),
      maxScore: trials.find((t) => t.max_score)?.max_score ?? 100,
    })
  }
  return groups
}

export function statusPill(status?: string): { label: string; cls: string } {
  switch (status) {
    case 'done':
      return { label: 'done', cls: 'pill green' }
    case 'fail':
      return { label: 'fail', cls: 'pill red' }
    case 'max_steps':
      return { label: 'max steps', cls: 'pill amber' }
    case 'error':
      return { label: 'error', cls: 'pill red' }
    default:
      return { label: status ?? '—', cls: 'pill' }
  }
}
