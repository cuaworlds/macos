#!/usr/bin/env bash
# vz_freeze_layer.sh — VZ/APFS analogue of explorations/env-build/freeze-layer.sh.
#
# KVM recipe: qcow2 overlay with backing_file -> shared base; freeze = rebase header.
# VZ recipe:  APFS clonefile (cp -c) the base VM bundle -> install into the clone ->
#             stop -> mark the clone bundle read-only -> content-address it as a
#             +apps layer. A fresh INSTANCE is then another clonefile of THIS frozen
#             bundle, so it inherits the installed app via CoW block-sharing.
#
# Bundle = ~/.tart/vms/<name>/ { disk.img (raw sparse), config.json, nvram.bin }.
# CoW sharing comes from clonefile(2) (cp -c / cp -cR), NOT from `tart clone`
# (which does a full logical copy of a local VM).
set -uo pipefail
VMS="$HOME/.tart/vms"
CACHE="${LAYER_CACHE:-$HOME/.tart/_layers}"
log(){ printf '[vz-freeze] %s\n' "$*"; }

BASE="$1"; NAME="$2"; ROLE="${3:-apps}"
SRC="$VMS/$BASE"
[ -d "$SRC" ] || { echo "no base bundle $SRC" >&2; exit 1; }

# Content-address the frozen layer by sha256 of config.json + nvram + a sampled disk
# fingerprint (full-disk sha is too slow for a 50GB sparse file; use ls -l size+mtime
# of disk.img plus the small files — good enough for a prototype cache key).
DIGEST="sha256:$( { cat "$SRC/config.json" "$SRC/nvram.bin"; stat -f '%z %m' "$SRC/disk.img"; } | shasum -a 256 | awk '{print $1}')"
SHORT="${DIGEST#sha256:}"; SHORT="${SHORT:0:12}"
LAYER="$CACHE/$SHORT"

if [ -d "$LAYER" ]; then
  log "layer $SHORT already cached — reusing"
else
  mkdir -p "$CACHE"
  log "clonefiling frozen layer (CoW) -> $LAYER"
  cp -cR "$SRC" "$LAYER"
  chmod -R a-w "$LAYER" 2>/dev/null || true   # freeze read-only
fi

cat > "/tmp/_layer.json" <<JSON
{
  "schemaVersion": "cua.layer.v1",
  "name": "$NAME",
  "role": "$ROLE",
  "os": "macos",
  "arch": "arm64",
  "format": "raw",
  "digest": "$DIGEST",
  "built": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "builder": "vz_freeze_layer.sh (RFC 0002 Phase-A VZ prototype)",
  "note": "instance = clonefile(this bundle); CoW block-shared until written"
}
JSON
# layer.json lives OUTSIDE the read-only bundle copy so the cache dir stays writable.
mkdir -p "$LAYER.meta" 2>/dev/null || true
cp /tmp/_layer.json "$LAYER.meta/layer.json" 2>/dev/null || true
log "DONE: layer=$LAYER digest=$DIGEST"
echo "$SHORT"
