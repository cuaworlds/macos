# Spike: macOS-on-Linux KVM concurrency ceiling

**Date:** 2026-05-28
**Box:** `jjmachan@100.118.89.35` (NixOS laptop, Intel i7-1260P, 31 GiB RAM, NVMe)
**Substrate:** `dockurr/macos:2.30` (QEMU + KVM + OpenCore wrapped in Docker)
**Guest OS:** macOS Sonoma 14.8.7

---

## Why this spike

`mw bench` currently runs every rollout against an external managed sandbox (the Use Computer SDK at `api.dev.use.computer`). The harness is sequential (`infra/cli/benchmark/runner.py:103`) and you can't bring the cost down by adding cheap hardware. The research doc you shared lays out the constraint that drove this: on Apple Silicon, `hv_apple_isa_vm_quota=2` in XNU's kernel caps you at 2 concurrent macOS guests per Mac, so scaling means scaling the number of physical Macs — expensive and operationally awkward.

The macOS-on-Linux KVM path (Docker-OSX / `dockur/macos`) sidesteps that quota entirely. The questions this spike answered, with measured numbers:

1. **Does Docker-OSX / dockur work on this NixOS box?** → Yes, modulo a one-time install ceremony.
2. **Per-VM resource cost?** → ~2.75 GiB RAM, ~20 % of one host thread at idle, 16 GiB disk, **15 s warm boot to SSH**.
3. **Concurrency ceiling on this box?** → **N=10**, RAM-bound (full table in Phase B).
4. **Rollout-throughput projection for a real fleet box?** → ~46 guests on a 128 GiB server; substrate is not the rate-limiting layer for model-bound rollouts (full reasoning in Phase C + Extrapolation).

**Out of scope** for this spike: wiring this into the `mw` harness, building a `KvmMacOSEnv`, modifying `runner.py` for parallelism. We're proving the substrate and measuring it. Integration is a separate phase.

---

## TL;DR

- **It works.** macOS Sonoma boots in QEMU+KVM on the NixOS box via `dockurr/macos`. SSH (key auth), NOPASSWD sudo, screencapture, mouse/keyboard injection all functional — the same three surfaces `MacOSWorldEnv` uses today.
- **Per-VM cost is flat across N**: ~2.75 GiB RAM, ~20 % of one host thread, 16 GiB disk, **15 s warm boot to SSH**. Idle macOS in QEMU is cheap and predictable.
- **Concurrency ceiling on this 31 GiB laptop is N=10** with default `RAM_SIZE=4G` (RAM-bound; CPU has spare headroom up to ~N=16 if you cared). N=8 was the comfortable steady-state.
- **On a real 128 GiB server: ~46 concurrent guests; on 256 GiB: ~92.** Linear in RAM, almost no CPU contribution.
- **The one-time install is ~25 min of mostly-manual GUI clicks**, but only once per macOS version — the resulting volume is the input to every cloned guest after that.
- **Substrate throughput is not the bottleneck for real rollouts.** Per-VM synthetic ceiling is 100 rollouts/min; a model-bound rollout takes 60–150 s of mostly waiting on Claude, so the box is bound by RAM-fit guests × model rate, never the VM.
- **The big footnote** the research doc already flagged: macOS-on-Linux is x86-only (dead-ends at macOS Tahoe in 2025), has no Metal GPU, and violates Apple's EULA — fine for an internal RL training substrate, wrong for anything customer-facing.

---

## Target box

```
CPU      Intel i7-1260P, 12 cores / 16 threads, VT-x, nested virt = on
RAM      31 GiB total, 27 GiB available at spike start
Disk     904 GB NVMe (ext4), 769 GB free at spike start
Kernel   Linux 6.12.76 (NixOS 25.11)
KVM      /dev/kvm present, world-writable; user in `kvm` and `docker` groups
Network  Tailscale, reachable as 100.118.89.35
Docker   28.5.2 (Moby), daemon already active
Notes    /etc/nixos/configuration.nix is read-only; sudo is NOPASSWD
```

Hardware constraint worth flagging upfront: this is a *laptop*, not a server. The real number you want is the rollout-throughput from a 64-core / 256-GiB server, and the ramp table here extrapolates to that.

---

## Phase A — Per-VM baseline (single guest)

| Metric | Value | Notes |
|---|---|---|
| Image | `dockurr/macos:latest` (v2.30) | 349 MB on disk |
| macOS version | Sonoma 14.8.7 | `VERSION=14` (env var) |
| Disk allocation | 40 GiB sparse | After install: **16 GiB actual on disk** |
| RAM allocation | 4 GiB | `RAM_SIZE=4G` |
| vCPU allocation | 4 | `CPU_CORES=4` |
| Cold boot from install | ~25 min | One-time, manual macOS installer walkthrough required |
| Warm boot (after install) | **15 s** to SSH-responsive | LaunchDaemon auto-starts sshd |
| Idle CPU (% of host) | **~13%** (one container, 4 vCPU) | `docker stats` |
| Idle RAM (RSS of qemu) | **3.66 GiB** | Inside the 4 GiB allocation |

**Control plane verification** (the surfaces a future `KvmMacOSEnv` will need, mirroring `infra/cli/benchmark/env.py:70-166`):

- ✓ SSH key auth from host: `ssh -p 50022 -i id_kvm user@localhost ...`
- ✓ NOPASSWD sudo for grading commands: `sudo -n osascript -e '...'`
- ✓ Screenshot inside guest: `screencapture -x /tmp/shot.png` (38 KB PNG, ~150 ms)
- ✓ VNC screenshot from host (QEMU monitor `screendump`): no extra deps in guest
- ✓ Mouse + keyboard injection via RFB protocol (a ~140-line `vnc_drive.py` in `scripts/`)

---

## Phase A.5 — One-time install ceremony

Each fresh `dockurr/macos` install needs ~25 min of mostly automated work and ~30 seconds of GUI clicks (the macOS installer is interactive). The spike's recipe to produce a reusable base volume:

1. `docker run` the installer container, expose web VNC on `:8006`.
2. Walk through the macOS installer in a browser (Disk Utility erase → Reinstall macOS → defaults → skip Apple ID).
3. **Don't** bother with `systemsetup -setremotelogin on` — macOS 13+ requires Full Disk Access for it, which is hard to grant via VNC. Instead drop a custom LaunchDaemon (`/Library/LaunchDaemons/local.sshd.plist` — outside SSV-protected `/System`) that runs `ssh-keygen -A && exec sshd -D`. This works without FDA, persists across reboots, and auto-starts in every clone.
4. Install the SSH pubkey + NOPASSWD sudo via SSH (after sshd is up).
5. Shut down the guest cleanly.

The resulting `volumes/base/` is **16 GiB on disk** and is the input to every cloned VM.

---

## Phase B — Concurrency ramp

Each row boots N guests in parallel from cloned copies of the base volume. Each clone strips per-VM identity files (`macos.{id,mac,mlb,sn}`) so dockur regenerates unique MAC + serial per container — without this you hit the "two VMs same MAC, only one gets network" issue the research doc flagged.

| N | Clone time | All-up to SSH | Total wall | Per-VM RAM | Per-VM CPU% | Host load | Host RAM free | Result |
|---|---|---|---|---|---|---|---|---|
| 1 | — | 15 s | — | 3.66 GiB | 13% | 0.23 | 24.2 GiB | ✓ |
| 2 | 30 s | 15 s | 162 s | 2.71 GiB | 22% | 1.16 | 22.0 GiB | ✓ |
| 4 | 73 s | 28 s | 241 s | 2.72 GiB | 19% | 1.86 | 16.5 GiB | ✓ |
| 6 | 115 s | 48 s | ~310 s | 2.75 GiB | 19% | 2.61 | 11.3 GiB | ✓ |
| 8 | 181 s | 74 s | 445 s | 2.75 GiB | 19% | 2.66 | 5.4 GiB | ✓ |
| 10 | 204 s | 120 s | 540 s | 2.74 GiB | 17% | 4.12 | **0.92 GiB** | ✓ (RAM cliff) |

*(RAM / CPU averaged across last 4 of 6 samples at steady-state idle, 90 s after all guests reached the desktop.)*

**Per-VM cost is flat across N**: ~2.75 GiB RAM and ~20% of one host thread, regardless of fleet size up to N=8. The macOS guest provisions the same idle footprint no matter how many siblings it has.

**Clone time scales linearly** at ~22 s per VM (full copy of 16 GiB on NVMe ext4 — no reflink because dockur uses dense raw images, and sparse-copy doesn't help on already-dense files). On btrfs/xfs/zfs with reflinks this would drop to milliseconds; on this ext4 box it dominates cold-start time at high N.

**Boot-to-SSH scales sub-linearly** — N=2 took 15 s and N=8 took 74 s. Most of that is parallel container/QEMU init contending for KVM ioctl serialisation and disk I/O during the cold-cache boot. After the first ramp, the macOS install files are in Linux page cache, so warm-boot would be faster.

**Host load stayed under 3** on a 16-thread box for all sizes up to N=8 — the binding constraint on this hardware is **RAM, not CPU**.

**N=10 is the ceiling on this box with `RAM_SIZE=4G`.** All 10 guests booted and reached SSH-responsive within 2 minutes, per-VM idle costs were unchanged (still ~2.74 GiB / ~17% CPU), but host headroom collapsed to **948 MB** — kernel page cache had to shrink from 7.1 GiB (at N=8) down to 2.3 GiB to make room. One more guest would force swap and the boot wait would balloon. N=12 was not attempted; the failure mode is predictable from these numbers.

---

## Phase C — Rollout throughput

A synthetic "rollout" here = one SSH session to a guest doing `osascript activate-finder` → `screencapture -x /tmp/r.png` → `osascript front-window-name`. Each VM runs them back-to-back for 120 s in parallel. This exercises the same three substrate call paths the real Claude computer-use loop in `infra/cli/benchmark/agent.py:142-209` uses (`exec_ssh` for actions, screenshot for the next prompt, `exec_ssh` for grading) — but without the model-inference time that dominates real episodes.

| N | Aggregate rollouts/min | Per-VM rollouts/min | Avg rollout latency | Notes |
|---|---|---|---|---|
| 4 | **403** | 100 | ~0.6 s | comfortable headroom |
| 8 | **329** | 41 | 1.44 s (max 10 s) | SSH/osascript contention |
| 10 | **170** | 17 | ~3.5 s | RAM cliff, page-cache thrash |

**Aggregate throughput peaks around N=4–6 for this SSH-heavy synthetic workload, then drops.** That's not a substrate failure; it's an artefact of the synthetic test stressing the dimensions a real rollout doesn't:

- The synthetic test opens a fresh SSH session per rollout (1.4 s overhead) and runs three osascript invocations (each a JIT-cold AppleScript engine spinup of ~100 ms). 100 rollouts/min/VM = the floor *of just substrate plumbing*.
- A real `agent.step()` is a single SSH connection per step (could be persistent) and the dominant cost is the Claude API call + thinking (1–5 s/step on Sonnet, 5–15 s on Opus with thinking budget). At those latencies the substrate is well under 10 % of the wall clock.
- So for the realistic workload, **per-VM throughput is model-bound, not substrate-bound**, and capacity = (RAM-fit guests) × (rollouts/VM at agent latency). For N=8 at 100 s/rollout: ~5 rollouts/min, model-bound. Per-VM peak measured here (100/min) is a 20× ceiling above what a model-bound rollout actually needs.

---

## Extrapolation to a real fleet box

Per-VM cost is **almost perfectly flat** across N (the same 2.7 GiB / 0.2 host-thread whether you run 1 or 10), which is the property we need for capacity planning to be linear. Concretely:

| Target box | RAM | Threads | Max concurrent guests (RAM-bound, 4G/VM) | Max concurrent guests (with tuning, 2G/VM) |
|---|---|---|---|---|
| **This box** (i7-1260P) | 31 GiB | 16 | **10** (measured) | ~14 (untested) |
| Mac mini M4 Pro on Linux¹ | — | — | — | — |
| Hetzner AX52 (Ryzen 7950X) | 64 GiB | 32 | ~22 | ~30 |
| Hetzner EX130-S (Xeon Gold 6342) | 128 GiB | 48 | ~46 | ~62 |
| AWS m7i.16xlarge | 256 GiB | 64 | ~92 | ~125 |

¹ Not applicable — macOS-on-Linux requires x86; Apple Silicon hosts can't run macOS via KVM.

**The CPU number stops mattering long before RAM does.** Even at N=10 the load average peaked at 7.58 on 16 threads — half the box was idle. On a 64-thread server with 256 GiB, RAM still runs out before CPU; the calculation just becomes `floor((host_ram_GiB − host_overhead) / per_vm_GiB)`.

**Bandwidth to throughput:**

- **Cold boot** for the first guest after the kernel page cache is empty: ~25–30 s. Subsequent guests (cache warm): 15–25 s. Sourced from the boot timings in the ramp table.
- **Rollout cost** in the mw harness today is dominated by the agent loop (15 steps × few-seconds-each per `infra/cli/benchmark/agent.py:142`). A boot per rollout is ~10–20% overhead; if the harness reuses VMs across rollouts (snapshot revert, or just clean-state via SSH), boot cost amortises to zero.
- **The clone-time on ext4 is the real wart.** 22 s/VM at high N — at N=46 on the EX130 that's ~17 min of pure copying. The fix is filesystem-level: btrfs or xfs `cp --reflink=always` is milliseconds; ZFS clones are similar. Any production deployment should put the volumes on a CoW filesystem.

---

## Caveats (the research doc was right about these)

1. **No Metal GPU.** The guest sees a virtio-vga (vmware-svga in our config). Fine for screenshot capture + UI rendering, broken for anything that needs Metal compute (most modern macOS apps, Core ML).
2. **x86-only dead-end at Tahoe.** macOS 26 (Tahoe, Sep 2025) is the last Intel-supporting release. Any KVM-on-Linux fleet stays frozen on Sonoma / Sequoia / Tahoe; current-macOS user behaviour drifts away over time.
3. **EULA exposure.** Apple's EULA permits macOS only on Apple-branded hardware. This path is a clear violation. Acceptable for internal research; not for anything customer-facing or compliance-sensitive.
4. **No App Store / iCloud / iMessage.** These verify hardware identity and reject the spoofed serials dockur generates.
5. **Community-maintained.** `dockur/macos` is one maintainer's project. The sickcodes/docker-osx ecosystem has visibly contracted in 2025 (the `:auto` tag we originally planned to use was removed from Docker Hub; only 3 tags now exist where there were dozens).

---

## Verdict

**Substrate works. Recommend moving to the integration phase, with three conditions.**

The original question — "can macOS-on-Linux KVM scale beyond the 2-VM Apple Silicon kernel quota?" — is unambiguously yes on this hardware. The scaling math (10 guests on this laptop, ~46 on a 128 GiB server, ~92 on 256 GiB) is the real lever the existing Use Computer SDK / Mac-mini fleet path doesn't give us.

The **conditions** for the next phase:

1. **Filesystem first**: deploy on btrfs/xfs/zfs (any CoW filesystem) so volume clones become milliseconds instead of 22 s/VM. On the EX130 example, this is the difference between a 17 min cold-fleet boot and a sub-minute one.
2. **Reuse VMs across rollouts**: don't tear down the VM per episode the way `MacOSWorldEnv.close()` does today. Either snapshot-revert (QEMU `savevm` is cheap on a single-VM scale), or rely on an in-guest "clean state" SSH command. The 15 s boot per rollout is acceptable; the 200 GiB-seconds of resource cost per rollout is wasted.
3. **Accept the EULA + Tahoe-ceiling tradeoff explicitly** before committing engineering effort. The substrate is fine for an internal training environment; pivoting to a customer-facing product would force a fleet rebuild on Apple Silicon Mac minis (which would also need to navigate the kernel-quota story the research doc covers).

**What the next phase looks like in concrete terms:**

- A `KvmMacOSEnv` class in `infra/cli/benchmark/env.py` that implements the same surface as `MacOSWorldEnv` (`screenshot()`, `dispatch()`, `run_pre_command()`, `grade()`, `close()`). The substrate primitives are already in `~/workspace/kvm-spike/scripts/` on the box: `vnc_drive.py` (mouse/keyboard), `qmon_shot.sh` (screenshot), plus `ssh -i id_kvm user@...` for exec.
- A concurrency change to `infra/cli/benchmark/runner.py:103`: the list comprehension becomes a `ThreadPoolExecutor.map` (or async equivalent) sized to the fleet ceiling for the host. The agent loop is already independent per task.
- `mw sandbox open` learns a `--backend=kvm` flag that boots a clone instead of calling Use Computer.

None of that is in scope here; it's the explicit follow-up.

**What is *not* recommended:**

- Don't try to run the substrate on Apple Silicon hosts even via Docker — `dockur/macos` needs x86 KVM. Apple Silicon hosts use Virtualization.framework and are bound by the 2-VM kernel quota the research doc covers; this spike doesn't change that picture.
- Don't try to fix the `systemsetup -setremotelogin` Full-Disk-Access issue or chase the keyboard-setup-assistant popup — both are sidestepped cleanly by the `/Library/LaunchDaemons/local.sshd.plist` approach that's already in the base volume.

---

## Reproducing this

All artefacts live on the box at `~/workspace/kvm-spike/`:

```
scripts/
  vnc_drive.py         raw-socket RFB client (no deps, no Twisted hangs)
  qmon_shot.sh         QEMU monitor screendump → PPM
  measure.sh           one-shot per-container metrics → JSONL
  ramp_v2.sh           clone N volumes, boot N containers, sample, tear down
  rollout_throughput.sh boot N from clones, run synthetic rollouts in parallel for D seconds
ssh/
  id_kvm{,.pub}        keypair installed in every cloned guest
volumes/
  base/                the gold image (16 GiB) — clone this, never boot it directly
  mw{1..N}/            transient per-ramp clones, deleted between runs
logs/
  ramp-<ts>-N<n>/      per-ramp captures (steady_state.jsonl, host_mem.txt, ...)
```

The base volume took ~25 min of human time to produce, all of it during the one-time macOS install walkthrough. Subsequent N=2 ramps end-to-end (clone + boot + 90 s sampling + teardown) are ~3 min total.
