import Logo from './Logo'

/** A small spinning ring. `size="lg"` for the full-page loader. */
export function Spinner({ size }: { size?: 'lg' }) {
  return <span className={size === 'lg' ? 'spinner spinner-lg' : 'spinner'} aria-hidden="true" />
}

/** Inline loader for content areas (the topbar and page chrome are already shown). */
export function Loading({ label = 'Loading…' }: { label?: string }) {
  return (
    <div className="loading" role="status">
      <Spinner />
      <span>{label}</span>
    </div>
  )
}

/** Full-page branded loader for the auth gate, before the app shell renders. */
export function PageLoader() {
  return (
    <div className="page-loader" role="status" aria-label="Loading">
      <div className="page-loader-mark">
        <Logo size={42} />
        <span className="brand-name">
          cua<span className="dim">worlds</span>
        </span>
      </div>
      <Spinner size="lg" />
    </div>
  )
}
