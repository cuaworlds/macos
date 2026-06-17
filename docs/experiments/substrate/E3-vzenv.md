# E3 — `VzMacOSEnv` as a first-class `Env` (interop link #3)

**Lock:** LOCAL-MAC (Apple M4 Pro, macOS 15.7.3 Sequoia host, ~96GB free, Virtualization.framework).
**Date:** 2026-06-15. **Builds on:** W2 (`vz-feasibility.md` — proved the recipe), the W2-patched
`env/kvm/rfb.py` (DES type-2 auth + DesktopSize, gated on `vnc_password`).

**Goal (interop link #3):** productize W2's manual VZ probe into a first-class `VzMacOSEnv` that
implements the substrate-blind `Env` protocol, so VZ is a PEER runner to KVM — the precondition for
E4 (grade identity). Thin class mirroring `KvmMacOSEnv`: `tart run --vnc-experimental` →
`RfbClient(…, vnc_password=pw)` + `GuestSsh` via `tart ip` + reset = `tart delete` + re-`clone`;
per-clone ECID regen (closes the W2 FLAG); `pmset` keep-awake; 10× reset → base byte-identical.

**Status:** INCREMENTAL — each milestone appended as it lands so a crash doesn't lose the proof.

---

## Milestone log (append-only)

### M0 — base pulled ✅ (2026-06-15)

`tart clone ghcr.io/cirruslabs/macos-sequoia-base:latest e3-base`. The prior dead attempt had
left a ~22GB **partial** in `~/.tart/tmp/b2c2c0f3…/` (an interrupted pull — config.json confirmed
it was the sequoia base). Tart does NOT treat a `tmp/` partial as a resumable cache entry (the
completed layer lives under `cache/OCIs/`, which was empty), so the relaunch re-pulled from
scratch; the partial was discarded automatically. Final: `e3-base` registered local VM, **50GB
logical / 33GB on disk / ~27GB physical** (sparse raw). `tart list`:

```
local  e3-base   50  33   stopped
OCI    ghcr.io/cirruslabs/macos-sequoia-base:latest   50  33   stopped
```

Guest is macOS Sequoia (cirruslabs `macos-sequoia-base`, arm64, 4 vCPU / 8GB RAM, display 1024×768
in config — the live retina framebuffer reports its real size at RFB ServerInit, W2 saw 2048×1536).

### M1 — `VzMacOSEnv` design (each Env method) ✅

New package `infra/cli/benchmark/env/vz/` — three modules, KVM untouched:

| file | role | KVM analogue |
|---|---|---|
| `config.py` | `VzConfig` (base_vm, instance_name, ssh creds/key, timeouts, `regen_identity`, `keep_display_awake`) | `kvm/config.py` |
| `tart.py` | host-control: clone / delete / stop, `TartRun` (boot + scrape `vnc://` line), `get_ip`, **`regen_ecid`**, ssh-key provision | `kvm/host.py` |
| `__init__.py` | **`VzMacOSEnv`** — the Env implementation | `kvm/__init__.py` |

**Reused unchanged (the substrate-blind seam):** `env/kvm/rfb.py` (W2-patched, `vnc_password`-gated),
`env/kvm/ssh.py` `GuestSsh` (key-based, host:port — works over `tart ip` per W2),
`benchmark/grading.py` `grade_checkpoints`. No KVM file edited.

**Env method → VZ wiring:**
- `__init__(VzConfig)` — `tart clone base_vm instance` → **regen ECID** (before first boot) →
  `tart run --vnc-experimental --no-graphics` (backgrounded) → scrape `vnc://:<pw>@127.0.0.1:<port>`
  → `tart ip` for SSH host → wait for key SSH → keep-display-awake → `RfbClient(host, port,
  vnc_password=pw)`; capture real framebuffer size for `scale_x/scale_y` (Claude's 1024×768 space).
- `sandbox_id` — the instance VM name.
- `screenshot()` — RFB full-frame → RGB → resize to 1024×768 → PNG; `_grab_with_reconnect` re-opens a
  fresh VNC socket on the *same* live `tart run` listener if one read wedges (mirrors KVM).
- `dispatch(action_input)` — the **identical** Claude-computer-tool → RFB dispatch dict copied from
  `KvmMacOSEnv` (key/type/click/drag/scroll/hold, coordinate up-scaling).
- `run_pre_command(task)` / `grade(task)` — over `GuestSsh`; `grade()` is the literal
  `grade_checkpoints` callable — same code path KVM grades through (E4 will compare the two).
- `guest_conn()` — `{host: tart-ip, port: 22, user, key_path}` for a host-side `grading_script`.
- `reset()` — **reset-by-discard:** stop+`tart delete instance` → re-`tart clone base_vm instance`
  → fresh ECID → reboot. (Extra method beyond the Env minimum; E5 leans on it.)
- `close()` — close RFB → stop `tart run` → `tart delete instance` (destroys the guest, no leak).

**isinstance(Env) — structural check (pre-live):** `VzMacOSEnv` implements every member of the
`@runtime_checkable Env` protocol (`screenshot, dispatch, run_pre_command, grade, guest_conn,
sandbox_id, close`) — verified by import + member scan; the live `isinstance(env, Env)` assertion
runs in M2 against a booted instance.

### M3 — ECID regen closes the W2 FLAG (distinct sibling identity) ✅ (2026-06-15)

**Recipe (`tart.py::regen_ecid`):** the guest's Hardware-UUID + serial derive from the ECID in the
bundle's `config.json`. That field is a base64-encoded **binary plist** `{"ECID": <uint64>}`
(decoded from `e3-base`: `13239895588774939608`). `tart clone` keeps it, so siblings collide (W2).
Fix = on a *stopped* clone, **before first boot**, write a fresh `secrets.randbits(64)` into that
same binary-plist shape and re-base64 it back. No Tart flag does this; it's a deterministic
`config.json` edit we own.

**Live proof — 2 concurrent siblings (respects the 2-VM cap; base stopped):**

| field | base `e3-base` | sib1 (regen) | sib2 (regen) | distinct? |
|---|---|---|---|---|
| ECID (config.json) | `13239895588774939608` | `11458756341305730060` | `3939580698887585418` | ✅ |
| **IOPlatformUUID** | `50DCC3EF-…-451AD340113C` | `1D9AFE1C-44AD-5726-B98C-613A43E85549` | `8310AC31-9195-5366-9E0D-7E7FD9075273` | ✅ **YES** |
| **Serial (system)** | `ZFQYR4XYHG` | `ZF4RXPF5HC` | `ZDLWXP2YWC` | ✅ **YES** |
| en0 MAC | `86:44:0f:12:49:26` | `3e:17:f5:d2:ef:58` | `de:53:12:50:61:33` | ✅ (Tart regen) |

Read over SSH on both booted siblings (`ioreg -rd1 -c IOPlatformExpertDevice | grep IOPlatformUUID`
and `system_profiler SPHardwareDataType | grep "Serial Number"`). The base reproduces W2's exact
shared `50DCC3EF…` / `ZFQYR4XYHG` — confirming the FLAG was real and the regen closes it. The guest
boots fine with the new ECID (no UUID-sensitive boot breakage → the E3 kill-criterion does NOT fire).
Both siblings `tart delete`d after.

### M2 — isinstance(Env) + end-to-end grade on a live VZ guest ✅ (2026-06-15)

Booted a real instance through the actual `VzMacOSEnv` class (not a hand-rolled driver) and ran the
full Env surface. **One-time env-build step done first:** generated `~/.tart/_e3/id_vz`, booted
`e3-base`, provisioned the public key into it via `tart exec` (cirruslabs guest agent), verified
key-based SSH, stopped → froze. Every clone now inherits the key, so the harness's key-based
`GuestSsh` works with no password (W2's recommended flow). Guest = macOS **Sequoia 15.7.7**, arm64.

Live results (instance `e3-inst-detail`, auto-deleted on close):

```
isinstance(env, Env):  True              # runtime_checkable, against a BOOTED instance
sandbox_id:            e3-inst-detail
ip:                    192.168.64.12      # via `tart ip`
framebuffer:           2048 x 1536  scale 2.0 x 2.0   # retina; Claude's 1024x768 upscaled
screenshot:            1,414,097-byte PNG, image 1024x768
luminance extrema:     (0, 255)           # NOT black -> display-sleep handled (see below)
dispatch mouse_move:   True / ok
dispatch key escape:   True / ok
guest_conn:            {host: 192.168.64.12, port: 22, user: admin, key_path: ~/.tart/_e3/id_vz}
grade BEFORE seed:     0.0 / 100.0
seed (mkdir+printf over SSH): rc=0
grade AFTER seed:      100.0 / 100.0      # ckpts 30/30, 30/30, 40/40
```

Task = `tasks/file_management/925bdc48` (clean-POSIX: create `~/Desktop/Reports/2026/summary.txt`).
The grade goes through the **identical** substrate-blind `grade_checkpoints` path KVM uses — this is
the exact seam E4 will compare across substrates. The pytest equivalent
(`test_live_isinstance_and_grade_e2e`) also passed in 24.5s.

### M4 — 10× reset-by-discard; frozen base byte-immutable ✅ (2026-06-15)

`VzMacOSEnv.reset()` = stop+`tart delete <instance>` → `tart clone e3-base <instance>` → regen ECID →
reboot to SSH-ready. Ran 10 cycles, sha256'ing the frozen base each cycle:

```
BASE  disk.img  sha256 = aed3ff9edb59e90c787a77012820d7b44b58c042de7db2568924bc4231036e04
BASE  nvram.bin sha256 = 97459b39349027b809f2081e56ff041431277a43a74706d1aa487082728ebdc7
reset  1..10: 12.5–15.8 s each   nvram_sha_ok=True  disk_stat_ok=True  nvram_stat_ok=True  (all 10)
AFTER disk.img  sha256 = aed3ff9e…  (IDENTICAL)
AFTER nvram.bin sha256 = 97459b39…  (IDENTICAL)
```

**Verdict:** the frozen base `disk.img` + `nvram.bin` are byte-for-byte unchanged across 10 full
reset cycles — a clone never writes through to its parent (W2's clonefile-CoW finding, now confirmed
through the productized `reset()`). Reset is **deterministic** and cheap (~14s wall to a fresh
SSH-ready guest, dominated by macOS boot, not the 0.07s clone). Load-bearing for E5.

### Display-sleep handling (W2 gotcha #1) ✅

W2 saw all-black framebuffers under an idle guest (the VZ virtual display powers down). `VzMacOSEnv`
applies a keep-awake over SSH on boot (`config.keep_display_awake`, default on):
`sudo pmset -a displaysleep 0 disksleep 0 sleep 0` + a backgrounded `caffeinate -dimsu`. The M2
screenshot's luminance extrema `(0, 255)` (vs a black frame's `(0, 0)`) confirms the framebuffer
stays live. `screenshot()` additionally keeps KVM's `_grab_with_reconnect` fallback (a fresh VNC
socket on the same live `tart run` listener) so a transiently-wedged read self-heals.

### Conformance + CI

- Structural: `VzMacOSEnv` implements every `Env` member + `reset()` (hermetic test
  `test_vz_env_implements_env_surface`).
- ECID regen logic, the `vnc://` scrape regex: hermetic tests (no VM) —
  `test_regen_ecid_*`, `test_vnc_line_regex_*`.
- Live e2e + reset-immutability: gated behind `MACOSWORLD_VZ_LIVE=1` (skipped in CI / off-Mac).
- Full suite green: **90 passed, 2 skipped** (`cd infra/cli && uv run --group dev pytest`). KVM path
  untouched (rfb.py/ssh.py/grading.py unchanged; only NEW files under `env/vz/` + the new test).

---

## What the E2 VZ-leg freeze needs later (carry-over)

E2 (author-once → two +apps layers) drives `vz-freeze-layer.sh`. To compose an +apps layer under
`VzMacOSEnv`, set `VzConfig.base_vm` to the **frozen +apps bundle name** instead of `e3-base` — the
env clones/instances/resets from it identically (it's just another stopped bundle). Open items E2
should own, surfaced here:
- **Key inheritance:** the SSH key must be baked into the +apps bundle (provision it into the base
  *before* the freeze, or re-provision per layer). `e3-base` now has `~/.tart/_e3/id_vz.pub` baked in,
  so a +apps layer cloned from it inherits the key for free.
- **ECID at freeze:** freeze with the *base* ECID; `VzMacOSEnv` regens per instance anyway, so the
  frozen bundle's ECID value is irrelevant (every clone overwrites it pre-boot).
- **Display config:** the base config.json declares display 1024×768 but the live VZ framebuffer is
  2048×1536 (retina ×2); `scale_x/scale_y` are read from RFB ServerInit at runtime, so a +apps layer
  needs no display change.
- **2-VM cap:** an +apps build (1 VM) + one instance (1 VM) already hits the cap; serialize
  freeze-then-test rather than running them concurrently.

## Cleanup

- All instance/sibling VMs `tart delete`d (`e3-inst-detail`, `e3-inst-test`, `e3-inst-reset`,
  `e3-sib1`, `e3-sib2`). `tart list` shows only **`e3-base`** (the frozen base — kept as the E3
  deliverable artifact for E4/E5; delete with `tart delete e3-base` to reclaim ~29GB).
- **No running VMs**, **no leaked `tart run` processes**.
- VZ XPC hosts: PID 21900 (pre-existing since May 20, W2-documented) + PID 89350 (idle since 18:57,
  predates all E3 VM launches). Neither holds any `.tart` disk open (`lsof` clean) — framework-level
  XPC singletons, not E3 guests; left untouched.
- `~/.tart` = **60 GB** (29 GB `e3-base` bundle + ~31 GB OCI layer cache; `tmp` = 0 B, the dead
  attempt's partial was discarded on re-pull). **Free disk: 74 GiB.** Temp driver scripts removed.

---

## Verdict

✅ **E3 SUCCESS.** `VzMacOSEnv` is a real, first-class `Env` implementation: `isinstance(env, Env)`
true on a live VZ guest; a real task grades end-to-end (0→100) through the substrate-blind grader;
per-clone ECID regen gives concurrent siblings **distinct Hardware UUID + serial** (W2 FLAG closed);
reset-by-discard is **deterministic** and leaves the frozen base **byte-identical** over 10 cycles.
VZ is now a peer runner to KVM under one protocol — the precondition for **E4 (grade identity)** is
met. No kill-criterion fired (ECID regen did not break boot; retina full-frames are fine on loopback,
~14ms/frame per W2). Files: `infra/cli/benchmark/env/vz/{__init__,config,tart}.py`,
`infra/cli/tests/test_vz_env.py`.



