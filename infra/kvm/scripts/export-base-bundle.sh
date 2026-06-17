#!/usr/bin/env bash
# Bundle the gold macOS base volume + its matching SSH key into ONE sparse-aware,
# zstd-compressed artifact for distribution (S3 is the supported path; see
# infra/kvm/dist/README.md). Run this ON the box that holds the prepped base volume.
#
#   bash export-base-bundle.sh [OUT.tar.zst]
#
# Env:
#   SPIKE_DIR   where the base lives (default ~/workspace/kvm-spike)
#   ZSTD_LEVEL  compression level (default 15; higher = smaller + slower)
set -euo pipefail

SPIKE_DIR="${SPIKE_DIR:-$HOME/workspace/kvm-spike}"
OUT="${1:-kvm-base-bundle.tar.zst}"
ZSTD_LEVEL="${ZSTD_LEVEL:-15}"

VOL="$SPIKE_DIR/volumes/base"
KEY="$SPIKE_DIR/ssh/id_kvm"
[ -d "$VOL" ] || { echo "ERROR: no base volume at $VOL" >&2; exit 1; }
[ -f "$KEY" ] || { echo "ERROR: no ssh key at $KEY" >&2; exit 1; }
command -v zstd >/dev/null || { echo "ERROR: zstd not installed (apt install zstd)" >&2; exit 1; }

mkdir -p "$(dirname "$OUT")"
echo "Bundling base volume + ssh key -> $OUT"
echo "  (GNU tar --sparse preserves the 40G-apparent/16G-actual holes; zstd -$ZSTD_LEVEL)"
echo "  This takes several minutes..."

# Paths are relative to SPIKE_DIR so the bundle extracts cleanly back into it.
tar --sparse -C "$SPIKE_DIR" -cf - volumes/base ssh/id_kvm ssh/id_kvm.pub \
  | zstd "-$ZSTD_LEVEL" --long=27 -T0 -f -o "$OUT"

echo "Done: $(du -h "$OUT" | cut -f1)  ->  $OUT"
echo "sha256: $(sha256sum "$OUT" | awk '{print $1}')"
echo
echo "To distribute via S3 (the supported path — see infra/kvm/dist/README.md):"
echo "  aws s3 cp '$OUT' s3://your-kvm-base-bucket/kvm-base-bundle.tar.zst"
echo "  aws s3 presign s3://your-kvm-base-bucket/kvm-base-bundle.tar.zst --expires-in 604800"
echo "Then update the <PRESIGNED_URL> + <BUNDLE_SHA256> placeholders in"
echo "docs/runbooks/kvm-server-setup.md §4."
