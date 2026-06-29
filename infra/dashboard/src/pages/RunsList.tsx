import { useEffect, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { Loading } from '../components/Loading'
import { listRuns } from '../lib/api'
import type { RunInfo } from '../lib/trajectory'

export default function RunsList() {
  const navigate = useNavigate()
  const [runs, setRuns] = useState<RunInfo[] | null>(null)
  const [err, setErr] = useState<string | null>(null)

  useEffect(() => {
    listRuns()
      .then(setRuns)
      .catch((e) => setErr(String(e)))
  }, [])

  return (
    <div className="page">
      <div className="crumbs">
        <span>CUA Worlds</span>
        <span className="sep">/</span>
        <span>runs</span>
      </div>
      <h1 className="h1">Runs</h1>

      {err && <div className="empty">Failed to load runs: {err}</div>}
      {!err && runs === null && <Loading />}
      {!err && runs && runs.length === 0 && (
        <div className="empty">
          No runs yet. Trigger one with{' '}
          <code>mw bench run --model claude-haiku-4-5 --tasks smoke</code>.
        </div>
      )}
      {!err && runs && runs.length > 0 && (
        <table className="table">
          <thead>
            <tr>
              <th>Run ID</th>
              <th>Rollouts</th>
              <th>Modified</th>
            </tr>
          </thead>
          <tbody>
            {runs.map((r) => (
              <tr
                key={r.run_id}
                className="clickable"
                onClick={(e) => {
                  // let modified clicks and the inner link do their default (new tab, etc.)
                  if (e.metaKey || e.ctrlKey || e.shiftKey) return
                  navigate(`/r/${encodeURIComponent(r.run_id)}`)
                }}
              >
                <td>
                  <Link
                    to={`/r/${encodeURIComponent(r.run_id)}`}
                    onClick={(e) => e.stopPropagation()}
                  >
                    <code>{r.run_id}</code>
                  </Link>
                </td>
                <td>{r.n_rollouts}</td>
                <td className="muted">
                  {new Date(r.mtime).toLocaleString()}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  )
}
