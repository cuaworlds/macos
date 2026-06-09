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
}

export type RunInfo = {
  run_id: string
  n_tasks: number
  mtime: number
  has_summary: boolean
}

export type TaskDef = {
  task_id: string
  category: string
  instruction: string
  pre_command: string
  grading_command: [string, number][]
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

export function screenshotUrl(runId: string, taskId: string, file: string): string {
  if (!file) return ''
  return `/outputs/runs/${encodeURIComponent(runId)}/${encodeURIComponent(taskId)}/context/${file}`
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
