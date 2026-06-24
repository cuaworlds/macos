import { useState, type FormEvent } from 'react'
import { Navigate, useNavigate } from 'react-router-dom'
import Logo from '../components/Logo'
import { useAuth } from '../lib/auth-context'

export default function Login() {
  const { user, login } = useAuth()
  const navigate = useNavigate()
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [err, setErr] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  if (user) return <Navigate to="/" replace />

  const onSubmit = async (e: FormEvent) => {
    e.preventDefault()
    setErr(null)
    setBusy(true)
    try {
      await login(username, password)
      navigate('/', { replace: true })
    } catch (e) {
      setErr(String(e instanceof Error ? e.message : e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="page login-page">
      <form className="card login-card" onSubmit={onSubmit}>
        <h1 className="login-brand">
          <Logo size={36} />
          <span className="brand-name">
            cua<span className="dim">worlds</span>
          </span>
        </h1>
        <div className="muted" style={{ marginBottom: 16 }}>
          Sign in to view benchmark runs.
        </div>
        <label className="field">
          <span className="label">Username or email</span>
          <input
            autoFocus
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            autoComplete="username"
          />
        </label>
        <label className="field">
          <span className="label">Password</span>
          <input
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            autoComplete="current-password"
          />
        </label>
        {err && <div className="login-err">{err}</div>}
        <button type="submit" className="btn" disabled={busy || !username || !password}>
          {busy ? 'Signing in…' : 'Sign in'}
        </button>
      </form>
    </div>
  )
}
