#!/usr/bin/env bash
# Run the MyPCBench 17 web apps as a lightweight native container (no QEMU, no
# root, no --privileged) so a browser anywhere — including Safari in our macOS
# KVM guest — can drive the real seeded persona apps on ports 3001-3017.
#
# Source image: mypcbench-desktop:michael_scott, fetched from
# huggingface ljang0/mypcbench-qemu-baseline :: mypcbench-desktop.tar.zst
# (an OCI image, not a VM disk). `pull` fetches + loads it; we never commit the
# ~3 GB blob. `up` strips the desktop programs (gnome/vnc/electron/control-api/
# libreoffice) from supervisord and keeps only the Next.js web apps + mail stack.
# The entrypoint still runs the generator + date-rebase, so seed dates track today.
#
# Usage:  ./run-apps.sh [pull|up|down|status|logs|reset]
set -euo pipefail

IMG="${MYPCBENCH_IMG:-mypcbench-desktop:michael_scott}"
NAME="${MYPCBENCH_CONTAINER:-mypc-apps}"
BIND="${MYPCBENCH_BIND:-127.0.0.1}"   # set to 0.0.0.0 to expose on the LAN/Tailscale
PERSONA="${PERSONA:-michael_scott}"
WORLD="${WORLD:-scranton-office}"
CACHE="${MYPCBENCH_CACHE:-$HOME/.cache/mypcbench}"
IMG_URL="https://huggingface.co/datasets/ljang0/mypcbench-qemu-baseline/resolve/main/mypcbench-desktop.tar.zst"

# supervisord programs that need an X display — we don't, so drop them.
DESKTOP_CONFS='gnome,vnc,control-api,firefox-cookies,buzzchat-desktop,workbuzz-desktop,libreoffice-service'

have_image() { docker image inspect "$IMG" >/dev/null 2>&1; }

pull() {
  if have_image && [ "${1:-}" != "--force" ]; then
    echo "image $IMG already loaded (use 'pull --force' to refetch)"; return 0
  fi
  mkdir -p "$CACHE"
  local tarball="$CACHE/mypcbench-desktop.tar.zst"
  echo "fetching $IMG_URL (~3 GB, resumable) -> $tarball"
  curl -fSL -C - -o "$tarball" "$IMG_URL"
  echo "loading into docker..."
  zstd -dc "$tarball" | docker load
  echo "loaded $IMG"
}

up() {
  if ! have_image; then
    echo "ERROR: image $IMG not loaded. Run: $0 pull" >&2; exit 1
  fi
  docker rm -f "$NAME" >/dev/null 2>&1 || true
  docker run -d --name "$NAME" \
    -e PERSONA="$PERSONA" -e WORLD="$WORLD" \
    -p "${BIND}:3001-3018:3001-3018" \
    --entrypoint /bin/bash "$IMG" \
    -c "rm -f /etc/supervisor/conf.d/{${DESKTOP_CONFS}}.conf; exec /opt/entrypoint.sh" \
    >/dev/null
  echo "started $NAME (persona=$PERSONA world=$WORLD) — waiting for apps..."
  for i in $(seq 1 60); do
    if [ "$(curl -s -o /dev/null -w '%{http_code}' --max-time 3 "http://localhost:3001/" 2>/dev/null)" = "307" ]; then
      echo "apps up on ${BIND}:3001-3017 after ~$((i*2))s"; return 0
    fi
    sleep 2
  done
  echo "WARN: apps did not report ready in time; check: $0 logs" >&2; return 1
}

down()   { docker rm -f "$NAME" >/dev/null 2>&1 && echo "removed $NAME" || echo "not running"; }
status() { docker exec "$NAME" supervisorctl status 2>/dev/null || docker ps --filter "name=$NAME"; }
logs()   { docker logs -f "$NAME"; }
# Recreate from the image for a true clean slate. An in-place generator reseed
# is an upsert: it re-adds seed rows but never deletes rows the app wrote during
# a run, so it would not restore pristine state.
reset()  { echo "reset: recreating $NAME from the pristine image..."; up; }

case "${1:-up}" in
  pull) shift; pull "${1:-}" ;;
  up) up ;; down) down ;; status) status ;; logs) logs ;; reset) reset ;;
  *) echo "usage: $0 [pull|up|down|status|logs|reset]"; exit 2 ;;
esac
