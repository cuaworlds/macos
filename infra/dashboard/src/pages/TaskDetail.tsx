import { useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { Loading } from '../components/Loading'
import { getTaskDef } from '../lib/api'
import { maxScoreOf, type TaskDef } from '../lib/trajectory'

export default function TaskDetail() {
  const { taskDefId = '' } = useParams()
  const [task, setTask] = useState<TaskDef | null>(null)
  const [err, setErr] = useState<string | null>(null)

  useEffect(() => {
    if (!taskDefId) return
    getTaskDef(taskDefId)
      .then(setTask)
      .catch((e) => setErr(String(e)))
  }, [taskDefId])

  return (
    <div className="page">
      <div className="crumbs">
        <Link to="/dataset">dataset</Link>
        <span className="sep">/</span>
        <span>#{taskDefId}</span>
      </div>
      <h1 className="h1">Task</h1>

      {err && <div className="empty">Failed to load task: {err}</div>}
      {!err && task === null && <Loading />}
      {!err && task && (
        <>
          <div className="card task-card">
            <div className="task-card-head">
              <div className="task-label">
                {task.category && <span className="cat-tag">{task.category}</span>}
                <span className="task-id-short">#{task.task_id}</span>
                {task.status && <span className="pill">{task.status}</span>}
              </div>
              <span className="muted">{maxScoreOf(task)} pts</span>
            </div>
            <div className="task-text">{task.instruction}</div>
          </div>

          {task.pre_command && (
            <div className="card">
              <div className="label">Pre-command</div>
              <pre className="check-cmd">{task.pre_command}</pre>
            </div>
          )}

          <div className="card">
            <div className="label">
              Grading · {task.grading_command.length} checkpoint
              {task.grading_command.length === 1 ? '' : 's'} · {maxScoreOf(task)} pts
            </div>
            {task.grading_command.length === 0 ? (
              <span className="muted">No grading checkpoints.</span>
            ) : (
              task.grading_command.map(([cmd, weight], i) => (
                <div key={i} className="check">
                  <div className="task-label">
                    <span className="pill">{weight} pts</span>
                  </div>
                  <pre className="check-cmd">{cmd}</pre>
                </div>
              ))
            )}
          </div>
        </>
      )}
    </div>
  )
}
