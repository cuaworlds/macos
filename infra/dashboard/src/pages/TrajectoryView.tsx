import { useEffect, useMemo, useRef, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { Loading } from '../components/Loading'
import Scrubber from '../components/Scrubber'
import { getRollout, getTaskDef, getTrajectory, listRollouts } from '../lib/api'
import {
  actionMarkers,
  baseTaskId,
  cmpIdDesc,
  DISPLAY_H,
  DISPLAY_W,
  flattenFrames,
  groupTasks,
  scoreTone,
  statusPill,
  type GradeLogEntry,
  type StepRecord,
  type TaskDef,
  type TaskResult,
} from '../lib/trajectory'

const PRELOAD_AHEAD = 5

const fmtDur = (s: number) =>
  s < 60 ? `${Math.round(s)}s` : `${Math.floor(s / 60)}m ${Math.round(s % 60)}s`

export default function TrajectoryView() {
  const { runId = '', taskId = '' } = useParams()
  const [steps, setSteps] = useState<StepRecord[] | null>(null)
  const [screens, setScreens] = useState<Record<string, string>>({})
  const [result, setResult] = useState<TaskResult | null>(null)
  const [taskDef, setTaskDef] = useState<TaskDef | null>(null)
  const [rollouts, setRollouts] = useState<TaskResult[] | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const [frameIdx, setFrameIdx] = useState(0)
  const [playing, setPlaying] = useState(false)
  // Pin the side column to the screenshot's rendered height so long model-thinking
  // text scrolls inside it instead of stretching the whole row taller than the image.
  const stageRef = useRef<HTMLDivElement>(null)
  const [sideMax, setSideMax] = useState<number | undefined>(undefined)

  useEffect(() => {
    if (!runId || !taskId) return
    let cancelled = false
    getTrajectory(runId, taskId)
      .then((t) => {
        if (cancelled) return
        setSteps(t.steps)
        setScreens(t.screenshots)
      })
      .catch((e) => !cancelled && setErr(String(e)))

    getRollout(runId, taskId)
      .then((r) => !cancelled && setResult(r))
      .catch(() => {})
    return () => {
      cancelled = true
    }
  }, [runId, taskId])

  // The task definition lives on a separate resource keyed by the rollout's task_id.
  useEffect(() => {
    const defId = result?.task_def_id
    if (defId === undefined) return
    let cancelled = false
    getTaskDef(defId)
      .then((d) => !cancelled && setTaskDef(d))
      .catch(() => {})
    return () => {
      cancelled = true
    }
  }, [result?.task_def_id])

  // Sidebar: all rollouts in this run. Keyed on runId so it survives navigating
  // between rollouts of the same run.
  useEffect(() => {
    if (!runId) return
    let cancelled = false
    listRollouts(runId)
      .then((rs) => !cancelled && setRollouts(rs))
      .catch(() => {})
    return () => {
      cancelled = true
    }
  }, [runId])

  // Flatten to one rail row per rollout, ordered so a task's trials sit together.
  const railItems = useMemo(() => {
    if (!rollouts) return []
    const groups = groupTasks(rollouts)
    groups.sort((a, b) => cmpIdDesc(a.baseId, b.baseId))
    return groups.flatMap((g) =>
      g.trials.map((t, i) => ({ t, group: g, firstOfGroup: i === 0 })),
    )
  }, [rollouts])
  const railMultiTrial = useMemo(
    () => railItems.some((it) => it.group.nTrials > 1),
    [railItems],
  )

  const shotUrl = (file: string) => (file ? (screens[file] ?? '') : '')

  const frames = useMemo(() => (steps ? flattenFrames(steps) : []), [steps])
  const cur = frames[frameIdx]
  const markers = useMemo(
    () => (cur ? actionMarkers(cur.action, cur.input) : []),
    [cur],
  )
  const dragLine =
    markers.length === 2 && markers[0].kind === 'start' && markers[1].kind === 'end'
      ? { a: markers[0], b: markers[1] }
      : null

  const gradeLog = useMemo<GradeLogEntry[]>(
    () => (Array.isArray(result?.grade_log) ? (result!.grade_log as GradeLogEntry[]) : []),
    [result],
  )

  // Track the stage (screenshot) height and mirror it onto the side column.
  useEffect(() => {
    const el = stageRef.current
    if (!el) return
    const update = () => setSideMax(el.offsetHeight)
    const ro = new ResizeObserver(update)
    ro.observe(el)
    update()
    return () => ro.disconnect()
  }, [steps])

  useEffect(() => {
    if (!cur) return
    for (let i = 1; i <= PRELOAD_AHEAD; i++) {
      const next = frames[frameIdx + i]
      const url = next ? shotUrl(next.screenshot) : ''
      if (!url) continue
      const img = new Image()
      img.src = url
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [frameIdx, frames, cur, screens])

  const pill = statusPill(result?.status)
  const scoreCls =
    result?.score && result.score > 0 ? 'pill green' : 'pill red'

  return (
    <div className="page rollout-page">
      <div className="crumbs">
        <Link to="/">CUA Worlds</Link>
        <span className="sep">/</span>
        <Link to={`/r/${encodeURIComponent(runId)}`}>{runId}</Link>
        <span className="sep">/</span>
        <span>{taskId}</span>
        {result?.status && (
          <>
            <span className="sep">·</span>
            <span className={pill.cls}>{pill.label}</span>
          </>
        )}
        {result && result.score !== undefined && result.score !== null && (
          <>
            <span className="sep">·</span>
            <span className="muted">
              score {result.score}
              {result.max_score ? ` / ${result.max_score}` : ''}
            </span>
          </>
        )}
        {result?.n_steps !== undefined && (
          <>
            <span className="sep">·</span>
            <span className="muted">{result.n_steps} steps</span>
          </>
        )}
      </div>

      <div className="rollout-shell">
        <aside className="rollout-rail">
          <div className="rail-head">
            Rollouts
            {rollouts && <span className="muted"> · {rollouts.length}</span>}
          </div>
          <nav className="rail-list">
            {railItems.map(({ t, firstOfGroup }) => {
              const tone = scoreTone(t.score, t.max_score)
              const active = t.task_id === taskId
              return (
                <Link
                  key={t.task_id}
                  to={`/r/${encodeURIComponent(runId)}/t/${encodeURIComponent(t.task_id)}`}
                  className={`rail-item${active ? ' active' : ''}`}
                  aria-current={active ? 'true' : undefined}
                >
                  <span className={`rail-dot ${tone}`} />
                  <code className="rail-id">
                    {firstOfGroup ? baseTaskId(t.task_id).slice(0, 8) : ''}
                  </code>
                  {railMultiTrial && (
                    <span className="rail-trial muted">t{(t.trial ?? 0) + 1}</span>
                  )}
                  <span className="rail-score muted">{t.score ?? '—'}</span>
                </Link>
              )
            })}
          </nav>
        </aside>
        <div className="rollout-main">

      {result?.model && (
        <div className="rollout-header">
          <span className="pill blue model-pill">{result.model}</span>
          <div className="rollout-meta">
            {result.category && <span className="meta-item">{result.category}</span>}
            {typeof result.passed === 'boolean' && (
              <span className={`pill ${result.passed ? 'green' : 'red'}`}>
                {result.passed ? 'passed' : 'failed'}
              </span>
            )}
            {result.n_steps !== undefined && (
              <span className="meta-item">{result.n_steps} steps</span>
            )}
            {result.duration_s != null && result.duration_s > 0 && (
              <span className="meta-item">{fmtDur(result.duration_s)}</span>
            )}
            {(result.input_tokens ?? 0) + (result.output_tokens ?? 0) > 0 && (
              <span className="meta-item">
                {(result.input_tokens ?? 0).toLocaleString()} in /{' '}
                {(result.output_tokens ?? 0).toLocaleString()} out tok
              </span>
            )}
            {result.cost_usd != null && result.cost_usd > 0 && (
              <span className="meta-item">${result.cost_usd.toFixed(3)}</span>
            )}
          </div>
        </div>
      )}

      {taskDef?.instruction && (
        <div className="card task-card">
          <div className="task-card-head">
            <div className="label">
              Task{taskDef.category ? ` · ${taskDef.category}` : ''}
            </div>
            {(() => {
              const m = taskId.match(/__t(\d+)$/)
              return m ? <span className="pill trial-pill">trial {Number(m[1]) + 1}</span> : null
            })()}
          </div>
          <div className="task-text">{taskDef.instruction}</div>
        </div>
      )}

      {err && <div className="empty">Failed to load trajectory: {err}</div>}
      {!err && steps === null && <Loading />}
      {!err && steps && frames.length === 0 && (
        <div className="empty">No frames in this trajectory.</div>
      )}

      {!err && cur && (
        <>
          <div className="viewer">
            <div className="stage" ref={stageRef}>
              {cur.screenshot ? (
                <div className="shot-wrap">
                  <img src={shotUrl(cur.screenshot)} alt={`step ${cur.step}`} />
                  <div className="shot-overlay">
                    {dragLine && (
                      <svg
                        className="drag-svg"
                        viewBox="0 0 100 100"
                        preserveAspectRatio="none"
                      >
                        <line
                          x1={(dragLine.a.x / DISPLAY_W) * 100}
                          y1={(dragLine.a.y / DISPLAY_H) * 100}
                          x2={(dragLine.b.x / DISPLAY_W) * 100}
                          y2={(dragLine.b.y / DISPLAY_H) * 100}
                          stroke="var(--accent)"
                          strokeWidth="0.4"
                          strokeDasharray="1 1"
                          vectorEffect="non-scaling-stroke"
                        />
                      </svg>
                    )}
                    {markers.map((m, i) => (
                      <div
                        key={i}
                        className={`marker ${m.kind}`}
                        style={{
                          left: `${(m.x / DISPLAY_W) * 100}%`,
                          top: `${(m.y / DISPLAY_H) * 100}%`,
                        }}
                        title={`${m.label} (${m.x}, ${m.y})`}
                      >
                        <span className="ring" />
                        <span className="marker-label">
                          {m.label} · {m.x},{m.y}
                        </span>
                      </div>
                    ))}
                  </div>
                </div>
              ) : (
                <div className="muted">(no screenshot)</div>
              )}
            </div>
            <div className="side" style={sideMax ? { maxHeight: sideMax } : undefined}>
              <div className="card">
                <div className="label">Step {cur.step} · Action</div>
                <div style={{ marginBottom: 6 }}>
                  <span className={`pill ${cur.ok ? 'green' : 'red'}`}>
                    {cur.action || '—'}
                  </span>
                </div>
                <pre>{JSON.stringify(cur.input, null, 2)}</pre>
                {cur.msg && (
                  <>
                    <div className="label" style={{ marginTop: 10 }}>
                      msg
                    </div>
                    <pre className={cur.ok ? '' : 'muted'}>{cur.msg}</pre>
                  </>
                )}
              </div>

              <div className="card thinking-card">
                <div className="label">Model thinking</div>
                <pre>{cur.text?.trim() || '(none captured)'}</pre>
              </div>
            </div>
          </div>

          <Scrubber
            value={frameIdx}
            total={frames.length}
            playing={playing}
            onChange={(v) => setFrameIdx(v)}
            onTogglePlay={() => setPlaying((p) => !p)}
          />

          <div className="card verifier-card">
            <div className="label">Verifier</div>
            <div className="verifier-score">
              <span className={scoreCls}>
                score {result?.score ?? 0}
                {result?.max_score ? ` / ${result.max_score}` : ''}
              </span>
            </div>
            {gradeLog.length > 0 ? (
              gradeLog.map((g, i) => {
                const cls = g.error
                  ? 'pill amber'
                  : g.hit
                    ? 'pill green'
                    : 'pill red'
                const verdict = g.error ? 'error' : g.hit ? 'pass' : 'no match'
                return (
                  <div className="check" key={i}>
                    <div className="check-head">
                      <span className={cls}>{verdict}</span>
                    </div>
                    <pre className="check-cmd">{g.cmd}</pre>
                    <div className="label" style={{ marginTop: 6 }}>
                      output
                    </div>
                    <pre>{g.error ?? g.stdout ?? '(empty)'}</pre>
                  </div>
                )
              })
            ) : taskDef && taskDef.grading_command.length > 0 ? (
              <>
                <div className="muted" style={{ marginBottom: 6 }}>
                  not evaluated — grading commands:
                </div>
                {taskDef.grading_command
                  .filter(([, v]) => v === 100)
                  .map(([cmd], i) => (
                    <pre className="check-cmd" key={i}>
                      {cmd}
                    </pre>
                  ))}
              </>
            ) : (
              <div className="muted">(no grading info)</div>
            )}
          </div>
        </>
      )}
        </div>
      </div>
    </div>
  )
}
