#!/usr/bin/env bash
# freeze-layer.sh — prototype of the Phase-A `mw env build` core (RFC 0002 §7.4.1, §8).
#
# Given a STOPPED guest's post-install overlay (the qcow2 the +apps install ran into),
# freeze it into an immutable, content-addressed `+apps` layer: a qcow2 whose
# backing_file points at the shared os-base, registered in a local layer cache. A
# subsequent fleet boot then stacks a per-instance overlay on top of THIS layer instead
# of straight on the base.
#
# This mirrors exactly how infra/cli/benchmark/env/kvm/host.py makes overlays:
#   - the shared read-only base lives at  <qcow2_base_dir>/<ver>/data.qcow2
#   - an overlay is `qemu-img create -f qcow2 -F qcow2 -b <parent> child`
#   - qemu-img is run inside the dockur image (no qemu needed on the host)
# We add: post-install settle, qcow2 rebase to the cache-relative parent, content
# addressing (sha256 of the frozen layer), and a manifest so the fleet can resolve it.
#
# ─────────────────────────────────────────────────────────────────────────────
# ⚠️  RUNS ON THE BOX HOST. It is OFF-GUEST (operates on disk files only) BUT it
#     REQUIRES that the guest whose overlay it freezes is ALREADY STOPPED
#     (`docker stop`) so the qcow2 is consistent. THIS SCRIPT IS NOT RUN DURING THIS
#     PREP TASK — it is handed to the on-box execution agent. Do not run it while a
#     guest is writing to the overlay.
# ─────────────────────────────────────────────────────────────────────────────
#
# Usage:
#   freeze-layer.sh \
#       --overlay  <guest-volume-dir>     e.g. ~/workspace/kvm-spike/volumes/freecad-build \
#       --name     <layer-name>           e.g. freecad-1.1.1 \
#       --role     apps                    (default: apps) \
#       [--base-qcow2 <dir>]               default: ~/workspace/kvm-spike/volumes/_base_qcow2 \
#       [--cache     <dir>]                default: ~/workspace/kvm-spike/layers \
#       [--image     dockurr/macos:latest] \
#       [--ver       14]
#
# Output: an immutable layer at <cache>/<digest>/data.qcow2 (+ companions + layer.json),
# and a friendly tag symlink <cache>/by-name/<name> -> <digest>.

set -euo pipefail

OVERLAY=""; NAME=""; ROLE="apps"; VER="14"
BASE_QCOW2_DIR="$HOME/workspace/kvm-spike/volumes/_base_qcow2"
CACHE="$HOME/workspace/kvm-spike/layers"
IMAGE="dockurr/macos:latest"

while [ $# -gt 0 ]; do
  case "$1" in
    --overlay)    OVERLAY="$2"; shift 2;;
    --name)       NAME="$2"; shift 2;;
    --role)       ROLE="$2"; shift 2;;
    --base-qcow2) BASE_QCOW2_DIR="$2"; shift 2;;
    --cache)      CACHE="$2"; shift 2;;
    --image)      IMAGE="$2"; shift 2;;
    --ver)        VER="$2"; shift 2;;
    *) echo "unknown arg: $1" >&2; exit 2;;
  esac
done
[ -n "$OVERLAY" ] && [ -n "$NAME" ] || { echo "need --overlay and --name" >&2; exit 2; }

log() { printf '[freeze] %s\n' "$*"; }
qemu_img() {  # run qemu-img from inside the dockur image; args mounted via $MOUNTS
  docker run --rm --entrypoint qemu-img $MOUNTS "$IMAGE" "$@"
}

SRC_QCOW2="$OVERLAY/$VER/data.qcow2"
BASE_QCOW2="$BASE_QCOW2_DIR/$VER/data.qcow2"
[ -f "$SRC_QCOW2" ]  || { echo "no overlay qcow2 at $SRC_QCOW2 (is the guest an overlay-mode clone, and stopped?)" >&2; exit 1; }
[ -f "$BASE_QCOW2" ] || { echo "no shared base qcow2 at $BASE_QCOW2" >&2; exit 1; }

# ── 0. Safety: refuse to run if a container is still using this overlay. ────────
if docker ps --format '{{.Mounts}} {{.Names}}' 2>/dev/null | grep -q "$(basename "$OVERLAY")"; then
  echo "ERROR: a running container still references $OVERLAY — docker stop it first." >&2
  exit 1
fi

# ── 1. Sanity-check the overlay's backing chain points at our base. ────────────
MOUNTS="-v $(dirname "$SRC_QCOW2"):/in:ro -v $(dirname "$BASE_QCOW2"):/base:ro"
log "overlay info:"
qemu_img info /in/data.qcow2 | sed 's/^/    /' || true

# ── 2. Stage the frozen layer in a temp dir, then content-address it. ──────────
STAGE="$(mktemp -d "${TMPDIR:-/tmp}/freeze.XXXXXX")"
mkdir -p "$STAGE/$VER"
log "copying overlay qcow2 + companions into stage"
cp -a "$SRC_QCOW2" "$STAGE/$VER/data.qcow2"
# Carry the boot companions the fleet expects next to a volume (rom/vars/dmg) so a
# layer is self-contained for booting. Identity files are intentionally NOT copied
# (each instance regenerates a unique MAC/serial — see config.IDENTITY_FILES).
for f in macos.rom macos.vars base.dmg; do
  [ -f "$OVERLAY/$VER/$f" ] && cp -a "$OVERLAY/$VER/$f" "$STAGE/$VER/$f" || true
done

# ── 3. Rebase the frozen qcow2's backing_file to the cache-relative base path. ──
# At runtime the fleet mounts the shared base read-only at /base inside the dockur
# container (host.py run_container: -v <qcow2_base_dir>:/base:ro). So the immutable
# layer must record backing_file=/base/data.qcow2 — a header-only edit (-u = unsafe/
# no-data-touch, instant). This is the literal §7.6 step-3 "qemu-img rebase -u".
log "rebasing backing_file -> /base/data.qcow2 (header-only)"
MOUNTS="-v $STAGE/$VER:/out"
qemu_img rebase -u -F qcow2 -b /base/data.qcow2 /out/data.qcow2

# ── 4. Content address: sha256 of the frozen layer qcow2 = the layer digest. ───
log "computing content digest (sha256 of frozen layer)"
DIGEST="sha256:$(sha256sum "$STAGE/$VER/data.qcow2" | awk '{print $1}')"
SHORT="${DIGEST#sha256:}"; SHORT="${SHORT:0:12}"
LAYER_DIR="$CACHE/$SHORT"
log "digest $DIGEST"

# ── 5. Register in the content-addressed cache (idempotent). ───────────────────
if [ -d "$LAYER_DIR" ]; then
  log "layer $SHORT already in cache — reusing (content-addressed dedup)"
  rm -rf "$STAGE"
else
  mkdir -p "$(dirname "$LAYER_DIR")"
  mv "$STAGE" "$LAYER_DIR"
  log "registered layer at $LAYER_DIR"
fi

# Base digest (parent pointer) for the manifest.
BASE_DIGEST="sha256:$(sha256sum "$BASE_QCOW2" | awk '{print $1}')"

# ── 6. Write the layer manifest (the seed of env.toml's [[layers]] / OCI annots). ─
cat > "$LAYER_DIR/layer.json" <<JSON
{
  "schemaVersion": "cua.layer.v1",
  "name": "$NAME",
  "role": "$ROLE",
  "os": "macos",
  "arch": "x86_64",
  "format": "qcow2",
  "macos_version": "$VER",
  "digest": "$DIGEST",
  "parent": "$BASE_DIGEST",
  "backing_file_runtime": "/base/data.qcow2",
  "built": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "builder": "freeze-layer.sh (RFC 0002 Phase-A prototype)"
}
JSON

# Friendly name tag -> digest (like an OCI tag).
mkdir -p "$CACHE/by-name"
ln -sfn "../$SHORT" "$CACHE/by-name/$NAME"

log "DONE."
log "  layer:   $LAYER_DIR/$VER/data.qcow2"
log "  manifest:$LAYER_DIR/layer.json"
log "  tag:     $CACHE/by-name/$NAME -> $SHORT"
cat <<EON

How a subsequent fleet boot stacks on this layer
────────────────────────────────────────────────
The committed fleet (host.py) makes a per-guest overlay with:
    qemu-img create -f qcow2 -F qcow2 -b /base/data.qcow2  <guest>/data.qcow2
and mounts the shared base read-only at /base. To boot ON TOP of this +apps layer
instead of bare base, the Phase-B composer changes the parent the instance overlay
points at — from the os-base to THIS frozen layer:

  1. Mount the frozen layer dir read-only at /apps inside the container
     ( -v $LAYER_DIR/$VER:/apps:ro ), AND keep the base at /base
     (the frozen layer's backing_file=/base/data.qcow2 must still resolve).
  2. Create the instance overlay with backing = the apps layer:
        qemu-img create -f qcow2 -F qcow2 -b /apps/data.qcow2  <guest>/data.qcow2
  3. The chain at runtime is:  instance  ->  +apps ($SHORT)  ->  os-base.
     Base + apps layer are read-only and shared; only the KB-MB instance overlay
     is per-guest and discarded on release (RFC 0002 §7.1 / §7.7).

This is the Phase-B promotion of KvmConfig.base_volume to "resolve an env package ->
a chain of layer paths -> the top one is what the instance overlay backs onto."
Where layers live:  $CACHE/<digest-short>/  (content-addressed; tag aliases in by-name/).
EON
