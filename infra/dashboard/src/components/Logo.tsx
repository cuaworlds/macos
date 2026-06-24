// The CUA Worlds mark: a world (disc) with an agent's action-dot leading a
// trajectory arc around it — the same click-ring language the trajectory viewer
// draws on screenshots. The artwork lives in public/logo.svg (single source of
// truth, shared shape with the favicon); rendered decoratively next to the
// wordmark, so it's aria-hidden.
export default function Logo({ size = 22 }: { size?: number }) {
  return <img className="logo" src="/logo.svg" width={size} height={size} alt="" aria-hidden="true" />
}
