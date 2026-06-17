# W2 — VZ-leg feasibility spike (RFC 0002 RISK-1)

**Lock:** LOCAL-MAC (Apple M4 Pro, macOS 15.7.3 Sequoia host, 24GB, Virtualization.framework).
**Date:** 2026-06-12. **Guest:** `ghcr.io/cirruslabs/macos-sequoia-base:latest` (macOS **15.7.7**, arm64).
**Overall verdict: ✅ VZ-LEG-FEASIBLE** end-to-end via the thin "patch RFB + Tart-launched guest" path. No custom VZ host app was forced. One **FLAG** (shared Hardware UUID/serial across siblings) and two operational gotchas to engineer around — none are blockers. Feeds W5 → **build-vs-buy = patch, not build.**

This builds on W0b (`vz-vnc-probe.md`), which pinned the RFB patch against the real Apple `_VZVNCServer` (via a Debian guest) but could not pull a macOS image. W2 pulled + booted a real macOS Sequoia guest and validated every sub-step on it.

---

## Per-sub-step results

| # | Sub-step | Result | Verdict |
|---|---|---|---|
| 1 | Pull + boot real macOS base | `tart clone …macos-sequoia-base:latest` = 25.4GB pull / ~28GB physical; booted to GUI desktop, retina 2048×1536 | ✅ PASS |
| 2 | SSH + `grade()` path | harness `GuestSsh` + `grade_checkpoints` (the literal `KvmMacOSEnv.grade()` path) over `tart ip`: ssh rc=0; real task graded **0/100 → 100/100** after seeding | ✅ PASS |
| 3 | RFB attach (patched) — screenshot + input round-trip | DES auth + DesktopSize → real desktop screenshot (2048×1536); RFB `type_text` visibly landed in TextEdit | ✅ PASS |
| 4 | `+apps` layer via APFS clonefile | clone base → install `jq` (Homebrew) + marker → freeze → 2 fresh instances both see `jq-1.8.1` + marker | ✅ PASS |
| 5 | Sibling isolation + base immutability | distinct files per sibling, no cross-visibility; base `disk.img`+`nvram.bin` byte-identical after siblings ran | ✅ PASS |
| 6 | Clone time + block-sharing | `tart clone` (= APFS clonefile) = **0.07–0.08s**, **4–8 KiB** for full 47GB bundle → CoW confirmed | ✅ PASS |
| 7 | Identity regen | MAC + Provisioning UDID **distinct** per clone; **Hardware UUID + serial IDENTICAL** (shared ECID) | ⚠️ FLAG (fixable) |

---

## Sub-step 1 — pull + boot ✅
`tart clone ghcr.io/cirruslabs/macos-sequoia-base:latest seq-w2` — 25.4GB compressed pull (~6 min on this host), `disk.img` 50GB logical / **47GB apparent / 27.8GB physical** (sparse). Config: arm64, 4 vCPU, 8GB RAM, `diskFormat:raw`. `tart run seq-w2 --vnc-experimental --no-graphics` booted to a full Sequoia GUI desktop (redwood wallpaper, Dock, Finder menu bar) — confirmed both by host-side `screencapture` over SSH and by our RFB client. **The "lab pulls a prebuilt image" premise holds.** Needs ≥60GB free per the W0b caveat (this host had 105GB).

## Sub-step 2 — SSH + grade() ✅
`tart ip` → `192.168.64.x` (VZ NAT). The cirruslabs guest uses `admin/admin`; we installed a dedicated ed25519 key into `~/.ssh/authorized_keys` (one provisioning step, mirrors Tart's standard flow) so the harness's **key-based** `GuestSsh` (`env/kvm/ssh.py`) connects unchanged. Then ran `benchmark.grading.grade_checkpoints(task.grading_command, ssh_exec)` — the **exact** callable `KvmMacOSEnv.grade()` builds — against a real file-management task (`tasks/file_management/925bdc48…json`). Result: `ssh exec rc=0`; grade **0/100** before seeding the expected `~/Desktop/Reports/2026/summary.txt`, **100/100** after. The grading seam is substrate-blind and already VZ-ready: no code change needed for the grade path.

## Sub-step 3 — RFB attach (the load-bearing step) ✅
The patched `RfbClient(host, port, vnc_password=pw)` connected to `_VZVNCServer` (port + 4-word passphrase scraped from `tart run`'s `vnc://:<pw>@127.0.0.1:<port>` line), took a **2048×1536 full-color screenshot of the live macOS desktop**, and dispatched `cmd+space` / `left_click` / `type_text` that **visibly landed** (the marker text `VZ-RFB-ROUNDTRIP-OK…` rendered in a TextEdit window; before→after frame diff = 2.1M bytes). Full-frame fetch+decode latency = **~14 ms** for a 12.6 MB (2048×1536×4) Raw frame over loopback.

**Two gotchas beyond W0b (both engineered around, no protocol research left):**
1. **The VZ display sleeps/blanks when idle.** Early screenshots were all-black (extrema 0,0,0) — not a protocol failure: the VZ virtual display powers down when nothing drives it, and our non-incremental request returns the cleared buffer. Once the guest GUI is actively driven (an app open / mouse moving), the framebuffer is live and correct. **Mitigation:** keep the display awake (`pmset -a displaysleep 0`, or a periodic mouse-jiggle/incremental poll) during a task. Input itself always reached the guest even while the framebuffer read black — input and framebuffer-liveness are independent.
2. **The server emits a private pseudo-encoding (-13)** during active GUI use that W0b's Debian guest never sent. The original loop `raise`d on it. **Fix (below):** advertise only `[Raw, DesktopSize]`, correctly consume DesktopSize/Cursor payloads, and fail-loud on truly unknown encodings.

**Bandwidth note:** retina full-screen Raw = 12.6 MB/frame. Fine on loopback (lab/eval). For remote/many-client use prefer incremental `FramebufferUpdateRequest` (dirty rects only) or reconnect-per-shot (the existing `KvmMacOSEnv._grab_with_reconnect` already reconnects a fresh socket per failed grab).

## Sub-step 4 — `+apps` layer via clonefile ✅
Ported `freeze-layer.sh`'s idiom from qcow2-backing-chains to **APFS clonefile** (`explorations/substrate/vz-freeze-layer.sh`). Recipe: `tart clone base apps-build` → boot → install over SSH (`brew install jq` → **jq-1.8.1**, plus a `~/apps_layer/MARKER.txt`) → `tart stop` (freeze; consistent disk) → the stopped bundle **is** the content-addressable `+apps` layer. Two fresh `tart clone apps …` instances **both** saw `jq-1.8.1` + the marker. The VZ chain is **clone-of-clone** (instance ⟶ +apps ⟶ base), the direct analogue of the KVM instance⟶+apps⟶os-base overlay chain.

> Note on `tart clone` vs raw `cp -cR`: **`tart clone` already uses clonefile(2)** (same 0.08s / 4KiB CoW) AND regenerates the MAC, so it is the right primitive — no need to hand-roll `cp -c` (which also copies a live `control.sock` and leaves a duplicate MAC). The freeze script documents both.

## Sub-step 5 — sibling isolation + base immutability ✅
Two concurrent instance clones (`seq-inst1`, `seq-inst2`) over the same `+apps` parent each wrote a distinct `~/sibling_test.txt`; each read back **only its own** value — no cross-sibling leakage. The parent base `disk.img` (size+mtime) and `nvram.bin` (**sha256 `eb125adc…` identical before/after**) were **byte-for-byte unchanged** after both siblings booted, wrote, and were deleted. **Reset-by-discard is safe on VZ: a clone never writes through to its parent.**

## Sub-step 6 — clone time + block-sharing ✅ (RFC Q2 answered)
`tart clone` of the full 47GB/27.8GB-physical bundle: **0.067–0.080s** (sub-second ✓). Disk consumed: **4 KiB** for one clone, **8 KiB** for two — despite `du` reporting 27.8 GB logical each. **APFS does share blocks like qcow2 backing chains** (CoW; only divergent blocks claim space). Q2 (does APFS share like qcow2) = **YES.**

## Sub-step 7 — identity regen ⚠️ FLAG (fixable, not a kill) (RFC Q4 / T2.2)
Read over SSH on two concurrent siblings (`ioreg`/`system_profiler`/`ifconfig`):

| field | inst1 | inst2 | distinct? |
|---|---|---|---|
| en0 MAC | `96:83:da:fd:f7:98` | `f6:61:fe:67:8d:e5` | ✅ yes (Tart regenerates) |
| Provisioning UDID | `1fa122a0…` | `9e9cceb1…` | ✅ yes |
| **IOPlatformUUID** | `50DCC3EF-…-451AD340113C` | `50DCC3EF-…-451AD340113C` | ❌ **IDENTICAL** |
| **Serial (system)** | `ZFQYR4XYHG` | `ZFQYR4XYHG` | ❌ **IDENTICAL** |

**Finding:** `tart clone` regenerates the **MAC** (so no L2/NAT collision — siblings get distinct IPs, confirmed) but **keeps the ECID/`VZMacMachineIdentifier`**. The guest derives its Hardware UUID + serial from that shared identifier, so concurrent siblings collide on UUID/serial. **Not a kill:** networking + isolation are unaffected, and tasks keyed off UUID/serial are rare. **Fix (T2.2 work item, mirrors KVM's `IDENTITY_FILES` strip):** the ECID is just a base64 plist field in `config.json` — regenerate a fresh random `ECID` (and let VZ rebuild aux storage) per clone before first boot. We must do this ourselves; `tart clone` has no flag for it. *(Not re-tested live here — at the 2-VM cap and it's a deterministic config edit; flagged for W5/T2.2.)*

---

## The `rfb.py` patch — what changed, how it's gated to NOT break KVM

File: `infra/cli/benchmark/env/kvm/rfb.py`. The KVM (QEMU) path is the `vnc_password is None` branch and is **byte-for-byte unchanged** (asserted by a fake-server test, `tests/test_rfb_vnc_auth.py`).

1. **`__init__(…, vnc_password: str | None = None)`** — new optional kwarg. `None` ⇒ KVM behaviour. Set ⇒ VZ behaviour. This is the single gate.
2. **`_handshake`** — if `vnc_password` set **and** the server offers type 2 but not type 1, do **RFB security type 2 (VNC-DES)**: select 2, read the 16-byte challenge, send `_vnc_des_response()`. Else if type 1 offered, the original `None` path. Else raise. (KVM offers type 1 → original path, untouched.)
3. **`_vnc_des_response` + a stdlib DES** (~90 lines, FIPS-46-3 tables) — VNC's bit-reversed-key DES, validated against two known-answer vectors (`85e813540f0ab405`, `8ca64de9c1b123a7`). Zero new deps (keeps the module's stdlib-only promise).
4. **`_set_pixel_format`** — `SetEncodings` is `[Raw]` for KVM (unchanged); `[Raw, DesktopSize(-223)]` for VZ. **We deliberately do NOT advertise Cursor(-239)** — it (and other VZ private pseudo-encodings) would emit data-bearing pseudo-rects that desync our Raw-only reader. `[Raw, DesktopSize]` is the exact set W0b validated.
5. **`screenshot` rect loop hardened** — DesktopSize(-223): no payload, update w/h + resize canvas. Cursor(-239): consume `rw*rh*4 + ⌈rw/8⌉*rh` bytes (kept in sync even if sent unsolicited). Unknown encoding: **raise** (can't know payload length → fail loud, never desync silently). This is what catches the surprise -13 pseudo without corrupting the stream.

**Tests:** `tests/test_rfb_vnc_auth.py` (DES KATs + a fake RFB server asserting: no-password ⇒ selects type 1 + advertises `[Raw]`; with-password ⇒ type-2 DES handshake verifies + advertises `[Raw, DesktopSize]`). Full suite: **73 passed** (70 prior + 3 new). The existing `test_rfb_reconnect.py` still passes — KVM path intact.

**To wire VZ into a real backend (W5/W8):** a `VzMacOSEnv` mirrors `KvmMacOSEnv` but (a) launches `tart run --vnc-experimental --no-graphics`, scrapes the `vnc://` line for port+passphrase, (b) constructs `RfbClient(host, port, vnc_password=pw)`, (c) resolves SSH host via `tart ip` for `GuestSsh` + the already-VZ-ready `grade()`. The screenshot/dispatch/grade surfaces need **no** further change.

---

## clonefile freeze recipe
`explorations/substrate/vz-freeze-layer.sh` — VZ/APFS analogue of `freeze-layer.sh`:
`tart clone base build` → install over SSH → `tart stop` → the frozen bundle is the content-addressed `+apps` layer; instances are `tart clone` of it (CoW, MAC-regenerated). `layer.json`: `arch=arm64, format=raw` (ASIF is Tahoe-only; raw/sparse works on Sequoia). Instance = clonefile(layer) ⟶ CoW-shared until written.

---

## W5 build-vs-buy input (the gate this flips)
**The thin path satisfies the Env protocol.** Nothing forced a custom host app:
- screenshot + full input round-trip work through the **patched stdlib RFB client** against the genuine Apple `_VZVNCServer` on a real macOS guest;
- `GuestSsh` + the substrate-blind `grade()` work unmodified over `tart ip`;
- clonefile gives sub-second, block-shared, sibling-isolated, base-immutable layering — the substrate model RFC 0002 assumes.

A custom VZ host app remains an **optimization** (headless multi-client, no random-pw/port scrape, finer framebuffer/perf control), not a prerequisite. **Recommend W5 pick "patch RFB + Tart-launched guest."**

**Engineering carry-overs for W5/W8 (none are blockers):**
- Keep the VZ display awake during a task (`pmset displaysleep 0` + periodic poll) or the framebuffer reads black.
- Prefer incremental FramebufferUpdateRequests / reconnect-per-shot at retina res (12.6 MB full frames).
- Regenerate the ECID per clone (fresh `VZMacMachineIdentifier`) so siblings get distinct Hardware UUID/serial — the one identity gap (T2.2).
- Provision the SSH key into the base image during env-build (one-time), so the key-based `GuestSsh` works on every clone (inheritance confirmed).

---

## Cleanup
All 4 local VMs (`seq-w2`, `seq-apps`, `seq-inst1`, `seq-inst2`) `tart delete`d. OCI cache pruned. `~/.tart/{vms,cache,tmp}` = **0 B**. No leaked `tart run` processes. The only `com.apple.Virtualization.VirtualMachine.xpc` is **PID 21900, started Wed May 20** — three weeks pre-session, pre-existing (matches W0b), **left untouched**. Temp probe scripts + keys + screenshots removed from `/tmp`. **Final free disk: 95 GiB.**
