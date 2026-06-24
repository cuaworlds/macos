import { useState, type FormEvent } from 'react'
import { Link } from 'react-router-dom'
import { changePassword, generateApiKey, me, updateProfile, type User } from '../lib/api'
import { useAuth } from '../lib/auth-context'

const fmtDate = (iso?: string | null) =>
  iso ? new Date(iso).toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' }) : 'n/a'

type Msg = { ok: boolean; text: string } | null
const errText = (e: unknown) => String(e instanceof Error ? e.message : e)

export default function Profile() {
  const { user, setUser } = useAuth()
  if (!user) return null

  return (
    <div className="page profile-page">
      <div className="crumbs">
        <Link to="/">CUA Worlds</Link>
        <span className="sep">/</span>
        <span>profile</span>
      </div>
      <h1 className="h1">Profile</h1>

      <AccountCard user={user} />
      <NameCard user={user} onSaved={setUser} />
      <PasswordCard />
      <ApiKeyCard />
    </div>
  )
}

function AccountCard({ user }: { user: User }) {
  return (
    <section className="card profile-section">
      <div className="label">Account</div>
      <dl className="kv">
        <dt>Username</dt>
        <dd>{user.username}</dd>
        <dt>Email</dt>
        <dd>{user.email ?? 'n/a'}</dd>
        <dt>Role</dt>
        <dd>
          <span className="pill">{user.role ?? 'user'}</span>
        </dd>
        <dt>Member since</dt>
        <dd>{fmtDate(user.created_at)}</dd>
        <dt>Last login</dt>
        <dd>{fmtDate(user.last_login)}</dd>
      </dl>
    </section>
  )
}

function NameCard({ user, onSaved }: { user: User; onSaved: (u: User) => void }) {
  const [first, setFirst] = useState(user.first_name ?? '')
  const [last, setLast] = useState(user.last_name ?? '')
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState<Msg>(null)

  const dirty = first !== (user.first_name ?? '') || last !== (user.last_name ?? '')

  const onSubmit = async (e: FormEvent) => {
    e.preventDefault()
    setBusy(true)
    setMsg(null)
    try {
      onSaved(await updateProfile({ first_name: first, last_name: last }))
      setMsg({ ok: true, text: 'Saved.' })
    } catch (e) {
      setMsg({ ok: false, text: errText(e) })
    } finally {
      setBusy(false)
    }
  }

  return (
    <form className="card profile-section" onSubmit={onSubmit}>
      <div className="label">Display name</div>
      <div className="field-row">
        <label className="field">
          <span className="label">First name</span>
          <input value={first} onChange={(e) => setFirst(e.target.value)} autoComplete="given-name" />
        </label>
        <label className="field">
          <span className="label">Last name</span>
          <input value={last} onChange={(e) => setLast(e.target.value)} autoComplete="family-name" />
        </label>
      </div>
      <div className="form-actions">
        <button type="submit" className="btn" disabled={busy || !dirty}>
          {busy ? 'Saving…' : 'Save'}
        </button>
        {msg && <span className={msg.ok ? 'form-ok' : 'form-err'}>{msg.text}</span>}
      </div>
    </form>
  )
}

function PasswordCard() {
  const [current, setCurrent] = useState('')
  const [next, setNext] = useState('')
  const [confirm, setConfirm] = useState('')
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState<Msg>(null)

  const tooShort = next.length > 0 && next.length < 8
  const mismatch = confirm.length > 0 && next !== confirm
  const canSubmit = Boolean(current) && next.length >= 8 && next === confirm && !busy

  const onSubmit = async (e: FormEvent) => {
    e.preventDefault()
    setBusy(true)
    setMsg(null)
    try {
      await changePassword(next, current)
      setMsg({ ok: true, text: 'Password changed.' })
      setCurrent('')
      setNext('')
      setConfirm('')
    } catch (e) {
      setMsg({ ok: false, text: errText(e) })
    } finally {
      setBusy(false)
    }
  }

  return (
    <form className="card profile-section" onSubmit={onSubmit}>
      <div className="label">Change password</div>
      <label className="field">
        <span className="label">Current password</span>
        <input
          type="password"
          value={current}
          onChange={(e) => setCurrent(e.target.value)}
          autoComplete="current-password"
        />
      </label>
      <label className="field">
        <span className="label">New password</span>
        <input type="password" value={next} onChange={(e) => setNext(e.target.value)} autoComplete="new-password" />
      </label>
      <label className="field">
        <span className="label">Confirm new password</span>
        <input
          type="password"
          value={confirm}
          onChange={(e) => setConfirm(e.target.value)}
          autoComplete="new-password"
        />
      </label>
      {tooShort && <div className="muted hint">Must be at least 8 characters.</div>}
      {mismatch && <div className="form-err">Passwords don’t match.</div>}
      <div className="form-actions">
        <button type="submit" className="btn" disabled={!canSubmit}>
          {busy ? 'Changing…' : 'Change password'}
        </button>
        {msg && <span className={msg.ok ? 'form-ok' : 'form-err'}>{msg.text}</span>}
      </div>
    </form>
  )
}

function ApiKeyCard() {
  const { user, setUser } = useAuth()
  const [key, setKey] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [copied, setCopied] = useState(false)
  const [err, setErr] = useState<string | null>(null)

  const hasKey = Boolean(user?.has_api_key)
  const prefix = user?.api_key_prefix
  const createdAt = user?.api_key_created_at

  const generate = async () => {
    setBusy(true)
    setErr(null)
    setCopied(false)
    try {
      setKey(await generateApiKey())
      setUser(await me()) // refresh status (prefix + created date)
    } catch (e) {
      setErr(errText(e))
    } finally {
      setBusy(false)
    }
  }

  const copy = async () => {
    if (!key) return
    await navigator.clipboard.writeText(key)
    setCopied(true)
    setTimeout(() => setCopied(false), 2000)
  }

  return (
    <section className="card profile-section">
      <div className="label">API key</div>
      <p className="muted hint">
        Use an API key to authenticate the <code>mw</code> CLI. Generating a key replaces any existing one. The
        old key stops working immediately.
      </p>

      {hasKey ? (
        <div className="key-status">
          <span className="pill green">active</span>
          {prefix ? <code className="key-prefix">{prefix}…</code> : null}
          <span className="muted">
            {prefix ? (createdAt ? `created ${fmtDate(createdAt)}` : '') : 'generated before key details were tracked'}
          </span>
        </div>
      ) : (
        <div className="key-status muted">No API key yet.</div>
      )}

      {key && (
        <>
          <div className="key-box">
            <code className="key-value">{key}</code>
            <button type="button" onClick={copy}>
              {copied ? 'Copied' : 'Copy'}
            </button>
          </div>
          <div className="notice">Copy this key now. It won’t be shown again.</div>
        </>
      )}

      <div className="form-actions">
        <button type="button" className="btn" onClick={generate} disabled={busy}>
          {busy ? 'Generating…' : hasKey ? 'Regenerate' : 'Generate API key'}
        </button>
        {err && <span className="form-err">{err}</span>}
      </div>
    </section>
  )
}
