# Runbook: stand up a Linux box as a macOS-on-KVM eval server

This walks you through turning a fresh Linux box into a server that can run the
`mw` benchmark against a fleet of macOS guests (the `--backend kvm` path). When
you're done you'll be able to run, on the box:

```bash
uv run mw bench run --backend kvm --tasks smoke --model claude-haiku-4-5 --kvm-fleet-size 4
```

The "why" and the scaling numbers live in [`docs/experiments/kvm-on-linux.md`](../experiments/kvm-on-linux.md).
This doc is the "how to set up the box."

> ⚠️ **Heads-up (EULA):** this runs macOS inside QEMU/KVM on non-Apple hardware,
> which violates Apple's EULA. Fine as an internal research/eval substrate; do not
> ship it in anything customer-facing. The guests are x86 macOS (Sonoma), no Metal.

---

## 0. What you're building

```
your Linux box
├── Docker  ──>  dockurr/macos containers (QEMU + KVM)   ← the macOS guests
│                 each guest ≈ 2.75 GiB RAM, ~16 GiB disk, boots to SSH in ~15-60s
├── the mw harness (this repo, via uv)                    ← drives the fleet
└── ~/workspace/kvm-spike/
    ├── volumes/base/        the gold macOS image (cloned per guest)
    └── ssh/id_kvm           the key the harness uses to SSH into guests
```

The harness clones the base volume N times, boots N containers in parallel, runs
rollouts across them concurrently, and tears them down.

---

## 1. Hardware & OS requirements

| Need | Why | Check |
|---|---|---|
| **x86-64 CPU with VT-x/AMD-V** | macOS-on-KVM is x86 only; no nested-virt-less ARM | `grep -Ec 'vmx|svm' /proc/cpuinfo` (>0) |
| **Nested virt** *(only if the box is itself a VM)* | QEMU runs inside the container | `cat /sys/module/kvm_intel/parameters/nested` → `Y/1` |
| **RAM** | ~4 GiB allocated per guest (~2.75 GiB real), keep ~4 GiB for host | fleet size ≈ `(RAM_GiB - 4) / 4` |
| **Disk** | base = ~16 GiB; per-guest overlay = ~tens of MB (qcow2 backing-chain default) | ~30 GiB free covers any sane fleet size |
| **Linux kernel with KVM** | `/dev/kvm` must exist & be accessible | `ls -l /dev/kvm` |

A modern Ubuntu/Debian server works out of the box on bare metal. If the box is a
cloud VM, the provider must expose nested virtualization (e.g. GCP `--enable-nested-virtualization`,
or a bare-metal/`.metal` instance on AWS).

Commands below assume **Ubuntu/Debian**. NixOS notes are flagged inline (our
reference box is NixOS).

---

## 2. Host setup

### 2.1 Verify virtualization + KVM

```bash
# CPU supports virtualization?
grep -Ec 'vmx|svm' /proc/cpuinfo        # must be > 0

# KVM module loaded and device present?
lsmod | grep kvm
ls -l /dev/kvm                          # should exist; group is usually 'kvm'
```

If `/dev/kvm` is missing: `sudo modprobe kvm_intel` (Intel) or `kvm_amd` (AMD), and
ensure virtualization is enabled in BIOS/firmware.

### 2.2 Install Docker

```bash
# Ubuntu/Debian — Docker's official convenience script
curl -fsSL https://get.docker.com | sh
sudo systemctl enable --now docker
```

> **NixOS:** add `virtualisation.docker.enable = true;` to `configuration.nix` and rebuild.

### 2.3 Give your user access to KVM + Docker (no sudo per command)

```bash
sudo usermod -aG docker,kvm "$USER"
# log out and back in (or: `newgrp docker` then `newgrp kvm`) for groups to take effect
id                                       # confirm 'docker' and 'kvm' appear
docker run --rm hello-world              # confirm docker works without sudo
```

The dockur containers need `/dev/kvm`, `/dev/net/tun`, and `NET_ADMIN`; the harness
passes these automatically. You just need your user in the `kvm` + `docker` groups.

### 2.4 Filesystem note

`ext4` is fine. The harness uses **qcow2 overlay cloning** by default
(`--kvm-disk-mode overlay` — see commit `a26efd9`): clones become a sub-second
header-only operation on a shared read-only base, regardless of the host filesystem.
The earlier guidance about putting `volumes/` on a CoW filesystem (btrfs/xfs/zfs) is
no longer load-bearing.

---

## 3. Get the harness

```bash
git clone --recurse-submodules git@github.com:vibrantlabsai/macos-world.git
cd macos-world

# install uv if you don't have it: https://docs.astral.sh/uv/
uv sync                                  # installs the mw CLI + deps

export ANTHROPIC_API_KEY=sk-ant-...      # required to drive the agent
```

`USE_COMPUTER_API_KEY` is **not** needed for the KVM backend (only for `--backend use-computer`).

---

## 4. Get the base macOS volume

The harness boots from a prepped macOS image at `~/workspace/kvm-spike/volumes/base`
(with the SSH key at `~/workspace/kvm-spike/ssh/id_kvm`). The supported path is
downloading the prebuilt bundle from object storage:

### 4.1 Download + verify + import (the supported path)

```bash
# 1. Download the bundle (~11 GB) via the presigned URL.
#    Ask the maintainer (or check the project's internal channel) for a fresh URL —
#    the one below is a sigv4 presigned URL with a 7-day expiry; regenerable any time.
curl -L -o /tmp/kvm-base-bundle.tar.zst "https://vibrantlabsai-macos-world.s3.amazonaws.com/kvm-base-bundle.tar.zst?X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Credential=AKIASRDO234M3LUNBBXU%2F20260601%2Fus-east-1%2Fs3%2Faws4_request&X-Amz-Date=20260601T203103Z&X-Amz-Expires=604800&X-Amz-SignedHeaders=host&X-Amz-Signature=5fb5f3bef38329e1190ab81c4b635b5386bb941f358d6449c79d163660f343e7"

# 2. (Recommended) verify integrity before extracting:
echo "ab9aa076dc3777ed3f95a81bbd917d93164aae5027bd567d83a90684c449ee06  /tmp/kvm-base-bundle.tar.zst" | sha256sum -c

# 3. Extract into the default paths the harness expects.
bash infra/kvm/scripts/import-base-bundle.sh /tmp/kvm-base-bundle.tar.zst
```

`import-base-bundle.sh` lands the contents at:
```
~/workspace/kvm-spike/volumes/base
~/workspace/kvm-spike/ssh/id_kvm   (+ id_kvm.pub)
```
That's it — skip to §5.

> **Security note.** The bundle contains macOS (EULA caveat in §0) and a *throwaway
> eval-VM SSH key + the guest login password* (`haime`). The presigned URL is
> sensitive in transit; don't paste it in public channels. Keep guest ports
> internal (Tailscale / private network).

#### Regenerating the URL when it expires

Anyone with AWS access to the `vibrantlabsai-macos-world` bucket can regenerate:
```bash
aws s3 presign \
  s3://vibrantlabsai-macos-world/kvm-base-bundle.tar.zst \
  --expires-in 604800        # 7 days, the sigv4 max
```

### 4.2 Build the base from scratch (fallback, ~25 min, some manual clicks)

If you can't/won't use the prebuilt bundle, build your own. dockur downloads macOS
from Apple directly (so no macOS bits come from us). Summary — see
[`docs/experiments/kvm-on-linux.md` §A.5](../experiments/kvm-on-linux.md) for the full ceremony:

1. Generate a key: `mkdir -p ~/workspace/kvm-spike/ssh && ssh-keygen -t ed25519 -N '' -f ~/workspace/kvm-spike/ssh/id_kvm`
2. Boot the installer container with web VNC on `:8006`:
   ```bash
   mkdir -p ~/workspace/kvm-spike/volumes/base
   docker run -it --name macos-build \
     --device /dev/kvm --device /dev/net/tun --cap-add NET_ADMIN \
     -e VERSION=14 -e RAM_SIZE=4G -e CPU_CORES=4 -e DISK_SIZE=40G \
     -p 8006:8006 -p 50922:22 \
     -v ~/workspace/kvm-spike/volumes/base:/storage \
     dockurr/macos:latest
   ```
3. Open `http://<box>:8006`, walk the macOS installer (Disk Utility erase →
   Reinstall macOS → defaults → skip Apple ID).
4. Get sshd running **without** Full Disk Access by dropping a LaunchDaemon
   (`/Library/LaunchDaemons/local.sshd.plist` running `ssh-keygen -A && exec sshd -D`),
   then install your pubkey + NOPASSWD sudo. (Details in the experiment doc.)
5. Run the prep script over SSH to make clones harness-ready (symlink
   `/Users/ec2-user`, auto-login, warm the built-in apps):
   ```bash
   ssh -i ~/workspace/kvm-spike/ssh/id_kvm -p 50922 user@localhost 'bash -s' haime \
     < infra/cli/benchmark/env/kvm/prep_base.sh
   ```
6. Cleanly stop the container (`docker stop macos-build`) so the disk is consistent,
   then `docker rm macos-build`. The volume at `volumes/base` is now your gold image.

---

## 5. Smoke test

**One guest, eyeball it:**
```bash
uv run mw sandbox open --backend kvm        # boots 1 VM, opens dockur web VNC, prints SSH
# you should see the macOS desktop (auto-logged-in). Press Enter to tear down.
```

**A real run across a fleet:**
```bash
uv run mw bench run --backend kvm --tasks smoke --model claude-haiku-4-5 --kvm-fleet-size 4
```

Expected: 4 guests boot in parallel, the 10 smoke tasks run concurrently, results
land in `outputs/runs/<run-id>/`. Cap steps while testing with
`MACOSWORLD_MAX_STEPS=40` to keep cost/time down.

**See the traces:** `cd infra/dashboard && npm install && npm run dev` → open the
printed URL → pick the run.

---

## 6. Sizing & scaling

From the spike (measured), per guest is **flat**: ~2.75 GiB RAM, ~20 % of one host
thread at idle, ~16 GiB disk, ~15 s warm boot to SSH.

| Box RAM | Suggested `--kvm-fleet-size` |
|---|---|
| 32 GiB | ~6 |
| 64 GiB | ~14 |
| 128 GiB | ~28 |
| 256 GiB | ~56 |

RAM is the binding constraint (CPU has headroom well past it). Rule of thumb:
`fleet_size ≈ (RAM_GiB - 4) / 4`. Useful flags: `--kvm-ram-gb`, `--kvm-vcpu`,
`--kvm-host` (drive a remote box), `--kvm-base-volume`, `--kvm-ssh-key`.

---

## 7. Troubleshooting

| Symptom | Fix |
|---|---|
| `/dev/kvm` not found | Enable VT-x/AMD-V in BIOS; `modprobe kvm_intel`/`kvm_amd`. On a cloud VM, enable nested virt. |
| `permission denied` on docker/kvm | User not in `docker`/`kvm` groups, or you didn't re-login. `id` to check. |
| Guest never reaches SSH (boot timeout) | First cold boot fills page cache and is slow; raise patience. Check `docker logs mw1`. Ensure `--device /dev/net/tun --cap-add NET_ADMIN` (the harness sets these). |
| Port already in use | Default port bases: SSH 50200+i, VNC 50300+i, web 50400+i. Free them or change in `infra/cli/benchmark/env/kvm/config.py`. |
| `host RAM` collapses at high N | You exceeded the fleet ceiling; lower `--kvm-fleet-size`. |
| Clones are slow | ext4 has no reflink; move `volumes/` to btrfs/xfs/zfs (§2.4). |

---

## 8. Security notes

- The prebuilt bundle ships a **throwaway eval-VM SSH key** and a known guest
  password (`haime`). Treat the box as internal; don't expose guest ports publicly.
- macOS auto-login is enabled in the base so clones reach the desktop unattended.
- No App Store / iCloud / iMessage (spoofed serials are rejected) — expected.
