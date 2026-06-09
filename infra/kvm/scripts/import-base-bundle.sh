#!/usr/bin/env bash
# Restore the gold macOS base volume + SSH key from the distributed bundle into the
# default paths the mw KVM backend expects. Run on the box that will run evals.
#
#   bash import-base-bundle.sh [BUNDLE.tar.zst]
#
# Env:
#   SPIKE_DIR   target dir (default ~/workspace/kvm-spike)
#   FORCE=1     overwrite an existing base volume
set -euo pipefail

# Default lookup order: explicit arg, then a sibling in /tmp, then the repo's
# infra/kvm/dist/ (the historical LFS path, kept as inert scaffolding).
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
DEFAULT_BUNDLE="/tmp/kvm-base-bundle.tar.zst"
[ -f "$DEFAULT_BUNDLE" ] || DEFAULT_BUNDLE="$REPO_ROOT/infra/kvm/dist/kvm-base-bundle.tar.zst"
BUNDLE="${1:-$DEFAULT_BUNDLE}"
SPIKE_DIR="${SPIKE_DIR:-$HOME/workspace/kvm-spike}"

[ -f "$BUNDLE" ] || { echo "ERROR: bundle not found: $BUNDLE" >&2; echo "Download it from the URL in docs/runbooks/kvm-server-setup.md §4 first." >&2; exit 1; }
command -v zstd >/dev/null || { echo "ERROR: zstd not installed (apt install zstd)" >&2; exit 1; }

# Sanity-check: a real bundle is multi-GB. A few hundred bytes is either an unfetched
# LFS pointer (legacy) or a truncated/failed download.
size=$(stat -c%s "$BUNDLE" 2>/dev/null || stat -f%z "$BUNDLE")
if [ "$size" -lt 1000000 ]; then
  echo "ERROR: $BUNDLE is only ${size} bytes — too small for a real bundle." >&2
  echo "       Did the download succeed? Or is this an unfetched git-LFS pointer?" >&2
  exit 1
fi

if [ -d "$SPIKE_DIR/volumes/base" ] && [ "${FORCE:-0}" != "1" ]; then
  echo "ERROR: $SPIKE_DIR/volumes/base already exists. Set FORCE=1 to overwrite." >&2
  exit 1
fi

mkdir -p "$SPIKE_DIR"
echo "Extracting $BUNDLE -> $SPIKE_DIR ..."
zstd -dc --long=27 "$BUNDLE" | tar --sparse -C "$SPIKE_DIR" -xf -
chmod 700 "$SPIKE_DIR/ssh" 2>/dev/null || true
chmod 600 "$SPIKE_DIR/ssh/id_kvm"

echo "Done."
echo "  base volume: $SPIKE_DIR/volumes/base"
echo "  ssh key:     $SPIKE_DIR/ssh/id_kvm"
echo
echo "Smoke test:  uv run mw bench run --backend kvm --tasks smoke --kvm-fleet-size 4"
