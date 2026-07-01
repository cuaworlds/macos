import { useEffect, useMemo, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { Loading } from '../components/Loading'
import { getRun, getTaskDef, listRollouts } from '../lib/api'
import {
  bandOf,
  baseTaskId,
  groupTasks,
  scoreTone,
  statusPill,
  type TaskResult,
} from '../lib/trajectory'

type SortKey = 'pass' | 'score' | 'steps' | 'name' | 'id'

export default function RunDetail() {
  const { runId = '' } = useParams()
  const navigate = useNavigate()
  const [tasks, setTasks] = useState<TaskResult[] | null>(null)
  const [name, setName] = useState<string | undefined>(undefined)
  const [err, setErr] = useState<string | null>(null)
  const [labels, setLabels] = useState<Record<string, string>>({})
  const [grouped, setGrouped] = useState(true)
  const [sort, setSort] = useState<SortKey>('pass')

  useEffect(() => {
    if (!runId) return
    listRollouts(runId)
      .then(setTasks)
      .catch((e) => setErr(String(e)))
    getRun(runId)
      .then((r) => setName(r?.name))
      .catch(() => setName(undefined))
  }, [runId])

  const groups = useMemo(() => (tasks ? groupTasks(tasks) : []), [tasks])
  const isMultiTrial = useMemo(() => groups.some((g) => g.nTrials > 1), [groups])

  // Resolve a human-readable instruction snippet per base task (the ids are uuids).
  useEffect(() => {
    if (!groups.length) return
    let cancelled = false
    Promise.all(
      groups.map(async (g) => {
        const defId = g.trials[0]?.task_def_id
        if (defId === undefined) return [g.baseId, ''] as const
        try {
          const d = await getTaskDef(defId)
          return [g.baseId, d.instruction || ''] as const
        } catch {
          return [g.baseId, ''] as const
        }
      }),
    ).then((pairs) => {
      if (cancelled) return
      setLabels(Object.fromEntries(pairs))
    })
    return () => {
      cancelled = true
    }
  }, [groups])

  const sortedGroups = useMemo(() => {
    const gs = [...groups]
    gs.sort((a, b) => {
      switch (sort) {
        case 'score':
          return b.meanScore - a.meanScore
        case 'steps':
          return a.meanSteps - b.meanSteps
        case 'id':
          return a.baseId.localeCompare(b.baseId)
        case 'name':
          return (labels[a.baseId] || a.baseId).localeCompare(labels[b.baseId] || b.baseId)
        case 'pass':
        default:
          // Ascending pass-rate, then higher partial score — surfaces the gradient.
          return a.passRate - b.passRate || b.meanScore - a.meanScore
      }
    })
    return gs
  }, [groups, sort, labels])

  const snippet = (baseId: string) => {
    const txt = labels[baseId]
    if (!txt) return null
    const oneLine = txt.replace(/\s+/g, ' ').trim()
    return oneLine.length > 90 ? `${oneLine.slice(0, 90)}…` : oneLine
  }

  return (
    <div className="page">
      <div className="crumbs">
        <Link to="/">CUA Worlds</Link>
        <span className="sep">/</span>
        <span>{name ?? runId}</span>
      </div>

      <div className="run-head">
        <h1 className="h1">{name ?? 'Rollouts'}</h1>
        {tasks && tasks.length > 0 && (
          <div className="run-meta muted">
            {groups.length} task{groups.length === 1 ? '' : 's'}
            {isMultiTrial ? ` · ${tasks.length} rollouts` : ''}
          </div>
        )}
        {isMultiTrial && (
          <div className="view-toggle">
            <button
              className={grouped ? 'active' : ''}
              onClick={() => setGrouped(true)}
            >
              Grouped
            </button>
            <button
              className={!grouped ? 'active' : ''}
              onClick={() => setGrouped(false)}
            >
              Flat
            </button>
          </div>
        )}
      </div>

      {err && <div className="empty">Failed to load: {err}</div>}
      {!err && tasks === null && <Loading />}
      {!err && tasks && tasks.length === 0 && (
        <div className="empty">No rollouts in this run.</div>
      )}

      {/* Grouped: one row per task with its trial distribution. */}
      {!err && tasks && tasks.length > 0 && grouped && isMultiTrial && (
        <table className="table grouped">
          <thead>
            <tr>
              <th className="sortable" onClick={() => setSort('id')}>
                Task{sort === 'id' ? ' ▾' : ''}
              </th>
              <th className="sortable" onClick={() => setSort('pass')}>
                Pass{sort === 'pass' ? ' ▾' : ''}
              </th>
              <th>Trials</th>
              <th className="sortable" onClick={() => setSort('score')}>
                Mean{sort === 'score' ? ' ▾' : ''}
              </th>
              <th className="sortable" onClick={() => setSort('steps')}>
                Steps{sort === 'steps' ? ' ▾' : ''}
              </th>
            </tr>
          </thead>
          <tbody>
            {sortedGroups.map((g) => {
              const band = bandOf(g.passes, g.nTrials)
              return (
                <tr key={g.baseId} className={band ? `grp ${band.cls}` : 'grp'}>
                  <td>
                    <div className="task-label">
                      <code className="task-id-short">{g.baseId.slice(0, 8)}</code>
                      {g.category && <span className="cat-tag">{g.category}</span>}
                      {band && <span className={`band-tag ${band.cls}`}>{band.label}</span>}
                    </div>
                    {snippet(g.baseId) && (
                      <div className="task-snippet muted">{snippet(g.baseId)}</div>
                    )}
                  </td>
                  <td>
                    <span className="pass-frac">
                      {g.passes}/{g.nTrials}
                    </span>
                  </td>
                  <td>
                    <div className="chips">
                      {g.trials.map((t) => {
                        const tone = scoreTone(t.score, t.max_score)
                        return (
                          <Link
                            key={t.task_id}
                            to={`/r/${encodeURIComponent(runId)}/t/${encodeURIComponent(t.task_id)}`}
                            className={`trial-chip ${tone}`}
                            title={`trial ${(t.trial ?? 0) + 1} · ${t.score ?? 0}/${t.max_score ?? 100} · ${t.status ?? ''}`}
                          >
                            {t.score ?? 0}
                          </Link>
                        )
                      })}
                    </div>
                  </td>
                  <td>{Math.round(g.meanScore)}</td>
                  <td className="muted">{Math.round(g.meanSteps)}</td>
                </tr>
              )
            })}
          </tbody>
        </table>
      )}

      {/* Flat: one row per rollout (the original view, also used for single-trial runs). */}
      {!err && tasks && tasks.length > 0 && (!grouped || !isMultiTrial) && (
        <table className="table">
          <thead>
            <tr>
              <th>Rollout</th>
              <th>Model</th>
              <th>Status</th>
              <th>Score</th>
              <th>Steps</th>
              <th>Duration</th>
            </tr>
          </thead>
          <tbody>
            {tasks.map((t) => {
              const pill = statusPill(t.status)
              const score =
                t.score === undefined || t.score === null
                  ? '—'
                  : `${t.score}${t.max_score ? ` / ${t.max_score}` : ''}`
              return (
                <tr
                  key={t.task_id}
                  className="clickable"
                  onClick={(e) => {
                    // let modified clicks and the inner link do their default (new tab, etc.)
                    if (e.metaKey || e.ctrlKey || e.shiftKey) return
                    navigate(`/r/${encodeURIComponent(runId)}/t/${encodeURIComponent(t.task_id)}`)
                  }}
                >
                  <td>
                    <Link
                      to={`/r/${encodeURIComponent(runId)}/t/${encodeURIComponent(t.task_id)}`}
                      onClick={(e) => e.stopPropagation()}
                    >
                      <code>{baseTaskId(t.task_id).slice(0, 8)}</code>
                      {t.trial !== undefined && (
                        <span className="muted"> · t{t.trial}</span>
                      )}
                    </Link>
                  </td>
                  <td className="muted">{t.model ?? '—'}</td>
                  <td>
                    <span className={pill.cls}>{pill.label}</span>
                  </td>
                  <td>{score}</td>
                  <td>{t.n_steps ?? '—'}</td>
                  <td className="muted">
                    {t.duration_s ? `${t.duration_s.toFixed(1)}s` : '—'}
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      )}
    </div>
  )
}
