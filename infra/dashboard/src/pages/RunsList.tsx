import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import type { RunInfo } from '../lib/trajectory'

export default function RunsList() {
  const [runs, setRuns] = useState<RunInfo[] | null>(null)
  const [err, setErr] = useState<string | null>(null)

  useEffect(() => {
    fetch('/api/runs')
      .then((r) => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`)
        return r.json()
      })
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
      {!err && runs === null && <div className="empty muted">Loading…</div>}
      {!err && runs && runs.length === 0 && (
        <div className="empty">
          No runs in <code>outputs/runs/</code>. Trigger one with{' '}
          <code>just bench claude-haiku-4-5 smoke</code>.
        </div>
      )}
      {!err && runs && runs.length > 0 && (
        <table className="table">
          <thead>
            <tr>
              <th>Run ID</th>
              <th>Tasks</th>
              <th>Modified</th>
            </tr>
          </thead>
          <tbody>
            {runs.map((r) => (
              <tr key={r.run_id} className="clickable">
                <td>
                  <Link to={`/r/${encodeURIComponent(r.run_id)}`}>
                    <code>{r.run_id}</code>
                  </Link>
                </td>
                <td>{r.n_tasks}</td>
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
