import { useState, type FormEvent } from 'react'
import { Link } from 'react-router-dom'
import {
  changePassword,
  generateApiKey,
  inviteUser,
  me,
  updateProfile,
  type InviteUserResult,
  type User,
} from '../lib/api'
import { useAuth } from '../lib/auth-context'
import Modal from '../components/Modal'

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
      {user.role === 'admin' && <InviteCard />}
    </div>
  )
}

function InviteCard() {
  const [open, setOpen] = useState(false)

  return (
    <section className="card profile-section">
      <div className="label">Invite a user</div>
      <p className="muted hint">
        Create a new account and send them an invitation. They’ll be able to sign in with the username and
        password you set here.
      </p>
      <div className="form-actions">
        <button type="button" className="btn" onClick={() => setOpen(true)}>
          Invite user
        </button>
      </div>
      {open && <InviteModal onClose={() => setOpen(false)} />}
    </section>
  )
}

function InviteModal({ onClose }: { onClose: () => void }) {
  const [email, setEmail] = useState('')
  const [name, setName] = useState('')
  const [username, setUsername] = useState('')
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState<Msg>(null)
  const [invited, setInvited] = useState<InviteUserResult | null>(null)
  const [copied, setCopied] = useState(false)

  const canSubmit = Boolean(email.trim()) && Boolean(username.trim()) && !busy

  const onSubmit = async (e: FormEvent) => {
    e.preventDefault()
    setBusy(true)
    setMsg(null)
    try {
      // Backend takes first/last name; split the single name field on first space.
      const trimmed = name.trim()
      const sp = trimmed.indexOf(' ')
      const first_name = sp === -1 ? trimmed : trimmed.slice(0, sp)
      const last_name = sp === -1 ? undefined : trimmed.slice(sp + 1).trim()
      const result = await inviteUser({
        email: email.trim(),
        username: username.trim(),
        first_name: first_name || undefined,
        last_name: last_name || undefined,
      })
      setInvited(result)
    } catch (e) {
      setMsg({ ok: false, text: errText(e) })
    } finally {
      setBusy(false)
    }
  }

  if (invited) {
    const { user, temp_password } = invited
    const creds =
      `Created a CUA Worlds account for you 🎉\n\n` +
      `Here are your credentials:\n` +
      `Username: ${user.username}\n` +
      `Email: ${user.email ?? email.trim()}\n` +
      `Password: ${temp_password}\n\n` +
      `Sign in at ${window.location.origin} and change your password after your first login.`

    const copy = async () => {
      await navigator.clipboard.writeText(creds)
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    }

    const inviteAnother = () => {
      setEmail('')
      setName('')
      setUsername('')
      setMsg(null)
      setCopied(false)
      setInvited(null)
    }

    return (
      <Modal title="User invited" onClose={onClose}>
        <p className="muted hint">
          <strong>{user.username}</strong> was created with{' '}
          <span className="pill amber">{user.status ?? 'new'}</span> status. Copy their credentials and share
          them securely — the password won’t be shown again.
        </p>
        <pre className="creds-box">{creds}</pre>
        <div className="form-actions form-actions-stack">
          <button type="button" className="btn btn-block" onClick={copy}>
            {copied ? 'Copied' : 'Copy credentials'}
          </button>
          <button type="button" className="btn btn-block" onClick={inviteAnother}>
            Invite another
          </button>
        </div>
      </Modal>
    )
  }

  return (
    <Modal title="Invite a user" onClose={onClose}>
      <form onSubmit={onSubmit}>
        <label className="field">
          <span className="label">Email</span>
          <input
            type="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            autoComplete="off"
            required
          />
        </label>
        <label className="field">
          <span className="label">Name</span>
          <input value={name} onChange={(e) => setName(e.target.value)} autoComplete="off" />
        </label>
        <label className="field">
          <span className="label">Username</span>
          <input
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            autoComplete="off"
            required
          />
        </label>
        <p className="muted hint">A temporary password is generated automatically and shown once after you invite.</p>
        <div className="form-actions form-actions-stack">
          <button type="submit" className="btn btn-block" disabled={!canSubmit}>
            {busy ? 'Inviting…' : 'Invite'}
          </button>
          {msg && <span className={msg.ok ? 'form-ok' : 'form-err'}>{msg.text}</span>}
        </div>
      </form>
    </Modal>
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
