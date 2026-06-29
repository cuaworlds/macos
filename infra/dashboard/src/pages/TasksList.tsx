import { useEffect, useMemo, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { Loading } from '../components/Loading'
import { listTasks } from '../lib/api'
import { cmpIdDesc, maxScoreOf, type TaskDef } from '../lib/trajectory'

const enc = encodeURIComponent

export default function TasksList() {
  const navigate = useNavigate()
  const [tasks, setTasks] = useState<TaskDef[] | null>(null)
  const [err, setErr] = useState<string | null>(null)

  useEffect(() => {
    listTasks()
      .then(setTasks)
      .catch((e) => setErr(String(e)))
  }, [])

  // Default order: by task id, descending (newest first); numeric-aware.
  const sorted = useMemo(
    () => (tasks ? [...tasks].sort((a, b) => cmpIdDesc(a.task_id, b.task_id)) : null),
    [tasks],
  )

  return (
    <div className="page">
      <div className="crumbs">
        <span>CUA Worlds</span>
        <span className="sep">/</span>
        <span>dataset</span>
      </div>
      <h1 className="h1">Dataset</h1>

      {err && <div className="empty">Failed to load tasks: {err}</div>}
      {!err && sorted === null && <Loading />}
      {!err && sorted && sorted.length === 0 && (
        <div className="empty">
          No tasks yet. Register some with <code>mw tasks push</code>.
        </div>
      )}
      {!err && sorted && sorted.length > 0 && (
        <div className="dataset-list">
          {sorted.map((t) => {
            const n = t.grading_command.length
            return (
              <Link
                key={t.task_id}
                to={`/dataset/${enc(t.task_id)}`}
                className="card task-row"
                onClick={(e) => {
                  if (e.metaKey || e.ctrlKey || e.shiftKey) return
                  e.preventDefault()
                  navigate(`/dataset/${enc(t.task_id)}`)
                }}
              >
                <div className="task-row-head">
                  {t.category && <span className="cat-tag">{t.category}</span>}
                  <span className="task-id-short">#{t.task_id}</span>
                  {t.status && <span className="pill">{t.status}</span>}
                </div>
                <div className="task-text task-row-text">{t.instruction}</div>
                <div className="task-row-meta muted">
                  {n} checkpoint{n === 1 ? '' : 's'} · {maxScoreOf(t)} pts
                </div>
              </Link>
            )
          })}
        </div>
      )}
    </div>
  )
}
