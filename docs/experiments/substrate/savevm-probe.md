# W0a — savevm/loadvm fast-reset probe

**Date:** 2026-06-12 · **Box:** `user@kvm-host` (NixOS, Intel i7-1260P, 31 GiB RAM, 708 GiB free) · **Guest:** dockur/macos VERSION=14 (macOS 14.8.7), RAM_SIZE=4G, 4 vCPU, qcow2 overlay over shared base.

## VERDICT

**SAVEVM-WORKS — but loadvm reset is ~22 s, NOT the hypothesized 1–3 s. → KILL the savevm-based fast-reset for W4.**

savevm/loadvm are functionally correct and perfectly deterministic on this guest, **but only after two config changes** (drop a non-migratable CPU flag + convert the NVRAM pflash to qcow2), and the restore wall-time (~22 s, dominated by reading 3.71 GiB of RAM state off disk) is an order of magnitude slower than the RL-critical target. It also costs +3.8 GB disk per guest and the snapshot is inseparable from the per-instance overlay (no shared-golden path). **Fallback confirmed: overlay-discard + container-recreate.**

---

## How the monitor was reached

dockur launches QEMU (v10.0.8) with an HMP monitor already exposed **inside the container**:
`-monitor telnet:localhost:7100,server,nowait,nodelay` (also a 2nd mux'd monitor on `-serial mon:stdio`).
Reached it via `docker exec -i <ctr> python3` opening a socket to `127.0.0.1:7100` and writing line-based HMP
(`savevm`, `loadvm`, `info ...`). No relaunch needed for monitor access — it ships on by default. `nc`/`python3`
are present in the container. The qemu arg-builders live in `/run/*.sh` (baked in the image): `proc.sh` (CPU),
`boot.sh` (pflash/drives).

## What blocked savevm (two sequential gates), and the fixes

The deployed dockur config hits **two** savevm blockers in order. Both had to be cleared to get a working snapshot:

1. **`Error: State blocked by non-migratable CPU device (invtsc flag)`**
   `proc.sh` builds `-cpu host,…,migratable=no,+invtsc` for Intel hosts (the box has `tsc_scaling`).
   `+invtsc` marks the CPU non-migratable → savevm refused.
   **Fix:** `-e CPU_FLAGS="-invtsc,migratable=on"` (user flags are appended last; QEMU takes last-wins).
   macOS 14.8.7 booted fine and ran SSH/desktop normally **without** invtsc — no observed timing breakage in the probe window.

2. **`Error: Device 'pflash1' is writable but does not support snapshots`**
   `boot.sh:68` hardcodes the NVMRAM vars as `-drive if=pflash,format=raw,file=$DEST.vars`. savevm requires every
   *writable* block device to be qcow2 (to store the snapshot); a raw pflash can't.
   **Fix:** `qemu-img convert -O qcow2 macos.vars → macos.vars.qcow2` + a 1-line `sed` patch to `boot.sh` baked into a
   derived image (`format=qcow2,file=$DEST.vars.qcow2`). No dockur env knob exists for pflash format.

`info block` after both fixes — all writable devices qcow2, read-only ones excluded:
```
data3            data.qcow2        (qcow2)            <- writable, snapshot-capable
pflash1          macos.vars.qcow2  (qcow2)            <- writable, snapshot-capable (was the blocker)
InstallMedia     base.dmg          (dmg, read-only)   <- excluded
OpenCore         OpenCore.img      (raw, read-only)   <- excluded
pflash0          macos.rom         (raw, read-only)   <- excluded
```
With both fixes, `savevm rl0` returned cleanly in **29.6 s** (one-time cost; writes 3.71 GiB RAM into the overlay).
VM auto-resumed (`info status: running`).

## Determinism & restore wall-time (5× loadvm cycles)

Each cycle: dirty the guest over SSH (`echo DIRTIED_RUN_N > ~/sentinel.txt; touch ~/dirtyfile_N`) → `loadvm rl0` → verify.

| run | loadvm wall-time | sentinel reverted | dirtyfile gone |
|----|------------------|-------------------|----------------|
| 1 | 22.06 s | yes (→ `PRE_SNAPSHOT_BASELINE`) | yes |
| 2 | 22.33 s | yes | yes |
| 3 | 21.70 s | yes | yes |
| 4 | 21.38 s | yes | yes |
| 5 | 21.55 s | yes | yes |

**mean ≈ 21.8 s, σ < 0.4 s.** Determinism is **perfect**: every cycle reverted both the file contents and the
filesystem to the exact snapshot state. Desktop **usable post-loadvm**: a fresh RFB connection to the mapped VNC
port grabbed a full 1920×1080 framebuffer (16,947 distinct colors) showing the live macOS desktop — Finder menu bar,
full Dock, and the menu-bar clock **frozen at the snapshot instant** ("Thu Jun 11 10:55 PM"), confirming exact CPU+RAM
restore. RFB reconnects cleanly on a fresh socket each time (matches the env's `_grab_with_reconnect` recovery path).
SSH was responsive after every restore.

**Why ~22 s, not 1–3 s:** loadvm must stream the full 3.71 GiB vmstate back from the qcow2. The data disk runs
`cache=none,aio=native` (direct I/O, no page cache), so the read isn't served from RAM cache. This is inherent to
disk-backed full-RAM snapshots — it is not a tunable that gets us to single-digit seconds.

## RAM / disk cost (decision-critical on the 31 GiB box)

- Warm overlay before savevm: **185 MB**. After `savevm rl0`: **4.0 GB** (qemu-img disk size 3.94 GiB; snapshot VM_SIZE 3.71 GiB).
- **Per-guest cost: +3.8 GB on disk**, persisting the full guest RAM **into the per-instance overlay**. This defeats the
  ~185 MB thin-overlay sharing the architecture relies on. Disk stayed flat at 4.0 GB across all 5 cycles (savevm is taken once; loadvm only reads).
- Box RAM during the probe peaked ~6.5 GiB used (well within 31 GiB for one guest) — disk, not RAM, is the binding cost, and it's per-guest not shared.

## Shared-golden-savevm: NOT viable

`qemu-img snapshot -l` confirms snapshot `rl0` lives **only in the overlay top layer**, never in the shared read-only
base (base has zero snapshots). qcow2 internal snapshots + their vmstate are stored in the image where `savevm` ran and
are **not** inherited through the backing-file CoW chain — a freshly `create -b`'d overlay starts with zero snapshots.
`loadvm` must write-lock the image holding the snapshot, so the RAM state fundamentally cannot live in a read-only
shared base and be restored per-instance. A golden-base savevm would not be visible to child overlays. **Each guest
must carry its own +3.8 GB RAM-snapshot.**

## Recommendation for W4

**KILL savevm/loadvm as the fast-reset primitive.** Reasons, in priority order:
1. **~22 s restore** — not the 1–3 s RL-critical target; ~10× too slow (full-RAM disk read, inherent).
2. **+3.8 GB/guest disk**, un-shareable, defeating the thin-overlay density model.
3. **Requires non-default config** (drop invtsc, qcow2 NVRAM via a patched dockur image) — extra surface to maintain.

**Fallback CONFIRMED: overlay-discard + container-recreate** is the right W4 direction. Discarding the thin overlay
(`rm` the per-guest qcow2) and re-creating it from the shared base is byte-deterministic by construction (you get the
exact golden bytes back, sharing the 14.9 GB base), at near-zero disk cost. The open cost there is **boot time to a
logged-in desktop** (the guest cold-boots), which W4 should measure and weigh against today's non-deterministic
best-effort SSH `_RESET_CMD`. savevm's *only* win over that path was sub-second resets, and it doesn't deliver them.

> Note for W4: if a future need for warm-RAM restore appears, the residual idea is `loadvm` from a *local-NVMe* overlay
> with `cache=writeback` (page-cached reads could cut the 22 s materially) — but it still can't be shared and still
> costs full-RAM disk per guest, so it doesn't change this KILL.

## Box hygiene

Removed: container `savevm-probe`, derived image `dockurr/macos:savevm-probe`, overlay dir `runs/savevm-probe`,
`/tmp` helper scripts, build context. `dockurr/macos:latest` and the gold `base/14` (data.img, macos.vars) **untouched**.
Box RAM back to baseline (2.6 GiB used). **Orphans (pre-existing, NOT mine):** two 3-week-old Exited containers —
`odoo-review-analytic_cost_allocation` and `fix-git__pzkdyqs-main-1` — left in place as instructed.
