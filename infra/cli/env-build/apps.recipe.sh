#!/usr/bin/env bash
# apps.recipe.sh — the SHARED, DECLARATIVE +apps install recipe (RFC 0002 §7.4.1).
#
# ════════════════════════════════════════════════════════════════════════════
# "AUTHOR ONCE, MATERIALIZE TWICE."  This ONE file is the install intent for a
# +apps layer. It is run UNCHANGED by BOTH substrate legs:
#
#   • KVM  / x86_64 : booted on the gold dockur/macOS base, run over SSH, then the
#                     post-install qcow2 overlay is frozen by freeze-layer.sh.
#   • VZ   / arm64  : booted via Tart on a cirruslabs macOS base, run over SSH, then
#                     the post-install APFS bundle is frozen by vz-freeze-layer.sh.
#
# The two freeze scripts differ (qcow2 backing-chain vs APFS clonefile) — that is
# the substrate. THIS recipe is the substrate-blind authoring surface. The whole
# point of E2 is to measure how much of it actually stays shared (the FORK RATE)
# once you go past a trivial CLI tool to a real GUI .app.
# ════════════════════════════════════════════════════════════════════════════
#
# ──────────────────────────── RECIPE CONTRACT ──────────────────────────────
# WHERE IT RUNS
#   Inside the macOS GUEST, over SSH, as the guest admin account
#   (KVM: `user`; VZ: `admin`) which has NOPASSWD sudo. Non-interactive,
#   no controlling tty, no GUI. Stdout/stderr are captured by the caller.
#
# WHAT IT MAY ASSUME (preconditions the env-build harness guarantees)
#   1. Homebrew is installed and on PATH for some prefix:
#        • arm64  guests:  /opt/homebrew/bin/brew
#        • x86_64 guests:  /usr/local/bin/brew
#      The recipe locates brew itself (see _ensure_brew) — callers need not export it.
#      (If a base ships WITHOUT brew, that is an env-build precondition failure, not a
#       recipe bug — the recipe says so loudly and exits non-zero.)
#   2. Network egress to Homebrew + the app vendor's CDN is available DURING BUILD
#      (the frozen layer is then offline-usable; build-time only).
#   3. `sudo` works without a password (both bases provide this).
#   4. `curl`, `hdiutil`, `ditto`, `xattr` exist (stock macOS; no install needed).
#
# HOW IT SIGNALS SUCCESS  (the success contract both freeze scripts check)
#   • exit 0  ⇔  every requested app installed AND verified in-guest.
#   • The LAST line of stdout is exactly:   RECIPE-OK <recipe-id> <iso8601>
#     A non-zero exit, or a missing RECIPE-OK trailer, MUST abort the freeze
#     (never freeze a half-installed layer).
#   • A breadcrumb file is written in-guest at ~/.cua-apps-recipe.json recording
#     what was installed + arch + timestamp (so a booted instance can self-describe).
#
# WHAT IT INSTALLS  (declarative, selected by env var; default = jq)
#   APPS="jq"            → the W2 parity case: `brew install jq`. Identical text on
#                          both arches, so it ISOLATES the authoring-parity question
#                          from app-availability. This is interop link #2's anchor.
#   APPS="jq vlc"        → adds a real GUI .app (VLC) — the FORK-RATE escalation. VLC
#                          is a notarized cask present on BOTH x86_64 and arm64, so the
#                          *intent* ("install VLC") is shared; we record honestly every
#                          place the *mechanics* must branch on arch.
#
# Usage (run over SSH by the env-build harness; NOT run by an agent):
#   ssh … 'bash -s' < apps.recipe.sh                 # default APPS=jq
#   ssh … 'APPS="jq vlc" bash -s' < apps.recipe.sh   # jq + the GUI escalation
# ────────────────────────────────────────────────────────────────────────────
set -euo pipefail

RECIPE_ID="apps.recipe/v1"
APPS="${APPS:-jq}"

log()  { printf '[apps.recipe] %s\n' "$*" >&2; }
fail() { printf '[apps.recipe] FATAL: %s\n' "$*" >&2; exit 1; }

# ── arch detection. The ONE unavoidable per-arch fact: where brew lives + cask
#    bundle conventions. We compute it once and branch on $ARCH below. Every such
#    branch is a "fork" we COUNT for the fork-rate finding (grep '#FORK' this file).
ARCH="$(uname -m)"   # arm64 (VZ) | x86_64 (KVM)
case "$ARCH" in
  arm64)  BREW_PREFIX_DEFAULT=/opt/homebrew ;;   #FORK arch→brew-prefix
  x86_64) BREW_PREFIX_DEFAULT=/usr/local   ;;    #FORK arch→brew-prefix
  *) fail "unsupported arch: $ARCH" ;;
esac

_ensure_brew() {
  # Locate brew without the caller having to export PATH. Tries the arch-default
  # prefix, then PATH, then the other prefix (belt-and-braces for odd bases).
  for cand in "$BREW_PREFIX_DEFAULT/bin/brew" "$(command -v brew 2>/dev/null || true)" \
              /opt/homebrew/bin/brew /usr/local/bin/brew; do
    if [ -n "$cand" ] && [ -x "$cand" ]; then BREW="$cand"; break; fi
  done
  [ -n "${BREW:-}" ] || fail "Homebrew not found (precondition #1 violated). \
Expected $BREW_PREFIX_DEFAULT/bin/brew. The env-build base must ship brew."
  eval "$("$BREW" shellenv)"       # sets PATH/HOMEBREW_* for this process
  export HOMEBREW_NO_AUTO_UPDATE=1 # deterministic, faster builds
  export HOMEBREW_NO_ANALYTICS=1
  log "using brew: $BREW (prefix $("$BREW" --prefix))"
}

# ── per-app install handlers ────────────────────────────────────────────────
# Each handler is "intent once": the SAME function body runs on both arches. The
# only arch-conditional lines inside are tagged #FORK so the fork rate is grep-able.

install_jq() {
  # jq — a CLI formula. The W2 VZ leg ran exactly `brew install jq`. ZERO forks:
  # the formula name, the install verb, and the verify command are arch-identical.
  log "installing jq (brew formula)"
  brew list --formula jq >/dev/null 2>&1 || brew install jq
  command -v jq >/dev/null 2>&1 || fail "jq not on PATH after install"
  local v; v="$(jq --version)"
  log "verified: $v"
  printf 'jq\t%s\n' "$v" >> "$MANIFEST"
}

install_vlc() {
  # VLC — a notarized GUI .app delivered as a Homebrew *cask* (a real .app in
  # /Applications, the FORK-RATE escalation past a CLI tool). The INTENT is one
  # line: "install the VLC cask". But verifying/locating a GUI bundle drags in
  # per-arch mechanics — each tagged #FORK below.
  log "installing VLC (brew cask — notarized GUI .app)"
  # The cask NAME is shared across arches (Homebrew resolves the right arch build):
  brew list --cask vlc >/dev/null 2>&1 || brew install --cask vlc
  local app="/Applications/VLC.app"
  [ -d "$app" ] || fail "VLC.app not in /Applications after cask install"
  # Clear any quarantine so the frozen layer launches without a Gatekeeper prompt
  # (casks are notarized so this is usually a no-op, but belt-and-braces):
  sudo xattr -dr com.apple.quarantine "$app" 2>/dev/null || true
  # VERIFY: the headless version probe path differs by arch only in the slice that
  # `file` reports, NOT in the binary location — but the EXPECTED arch string forks:
  local bin="$app/Contents/MacOS/VLC"
  [ -x "$bin" ] || fail "VLC binary missing at $bin"
  case "$ARCH" in
    arm64)  local want="arm64"  ;;   #FORK arch→expected-macho-slice
    x86_64) local want="x86_64" ;;   #FORK arch→expected-macho-slice
  esac
  file "$bin" | grep -q "$want" || log "WARN: $bin is not a native $want binary (universal/rosetta?)"
  # CFBundleShortVersionString is arch-agnostic — one line, no fork:
  local v; v="$(defaults read "$app/Contents/Info.plist" CFBundleShortVersionString 2>/dev/null || echo unknown)"
  log "verified: VLC.app $v ($ARCH slice ok)"
  printf 'vlc\t%s\n' "$v" >> "$MANIFEST"
}

# ── main ────────────────────────────────────────────────────────────────────
_ensure_brew

MANIFEST="$(mktemp)"; : > "$MANIFEST"
trap 'rm -f "$MANIFEST"' EXIT

for app in $APPS; do
  case "$app" in
    jq)  install_jq  ;;
    vlc) install_vlc ;;
    *) fail "no handler for app '$app' (add an install_<app> function)";;
  esac
done

# Breadcrumb the booted instance can read to self-describe what's in its +apps layer.
BREADCRUMB="$HOME/.cua-apps-recipe.json"
{
  printf '{\n'
  printf '  "recipe": "%s",\n' "$RECIPE_ID"
  printf '  "arch": "%s",\n' "$ARCH"
  printf '  "built": "%s",\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  printf '  "apps": ['
  first=1
  while IFS=$'\t' read -r name ver; do
    [ -n "$name" ] || continue
    [ $first -eq 1 ] || printf ', '
    printf '{"name": "%s", "version": "%s"}' "$name" "$ver"
    first=0
  done < "$MANIFEST"
  printf ']\n}\n'
} > "$BREADCRUMB"
log "wrote breadcrumb $BREADCRUMB"

# The success trailer the freeze scripts gate on (MUST be the last stdout line).
printf 'RECIPE-OK %s %s\n' "$RECIPE_ID" "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
