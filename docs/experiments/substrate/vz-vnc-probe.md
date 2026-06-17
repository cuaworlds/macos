# W0b — VZ VNC probe: does our RFB client attach to an Apple-VZ macOS guest?

**Lock:** LOCAL-MAC (Apple M4 Pro, macOS 15.7.3 Sequoia, 24GB, Virtualization.framework).
**Date:** 2026-06-12. **Verdict:** ❌ unmodified RfbClient does NOT attach — **but the patch is small and bounded.** Feeds W5 toward "patch our RFB client," NOT a custom VZ host app.

---

## TL;DR

Our `rfb.py` fails against Tart's VZ-native VNC at **two** points, both now measured against the **real Apple `_VZVNCServer`**:

1. **Security:** server offers **only RFB security type 2 (VNC Authentication, DES challenge-response)**. `rfb.py` accepts **only type 1 (None)** and raises immediately. *(hypothesis confirmed)*
2. **Encoding handshake (the surprise):** the Apple VZ VNC server **requires the client to advertise the `DesktopSize` (-223) pseudo-encoding** in `SetEncodings`. `rfb.py` advertises **only `[Raw]` (encoding 0)** — and the server **resets the connection AND kills its own listener** (an internal Apple FIXME path). Add `-223` and the server stays up and sends **pure `Raw` (encoding 0)** — which `rfb.py` already decodes byte-for-byte.

So the pixel decode path is fine (Raw, and the server's pixel format already matches our BGRX request). The patch is: **(a) add a VNC-DES type-2 auth branch, (b) add `-223` to the `SetEncodings` list** (and skip the `-223` pseudo-rect, which the code already does at line 164). No new image decoder (Tight/ZRLE/Hextile) needed. **~30–50 lines, stdlib-only DES.**

SSH to the guest is reachable. ✅

---

## 1. Tart install + VNC modes

- `brew trust cirruslabs/cli && brew install cirruslabs/cli/tart` → **tart 2.32.1**. (Homebrew 5.x now refuses untrusted taps; `tart` pulls in `softnet` from the same tap, so trust the whole tap.)
- `tart run` exposes two VNC modes:
  - **`--vnc-experimental`** → Apple **Virtualization.framework native VNC** (`_VZVNCServer` via `Dynamic`). Host-side, guest-OS-agnostic, available even in recovery/installer. **This is the mode our harness would drive.**
  - **`--vnc`** → the **guest's macOS Screen Sharing / ARD** server (needs Remote Login enabled inside the guest), reached at `vnc://<guest-ip>:5900`.

**Disk reality:** `macos-sequoia-base:latest` is **25.4 GB compressed (96 layers)** and decompresses to a ~50 GB sparse disk. This host had only **~28 GB free** — the macOS pull **cannot complete here** (aborted mid-pull to protect the shared machine's system disk; `~/.tart/tmp` cleaned).

**Fallback that gave the real answer:** `--vnc-experimental`'s VNC server is the **host-side Apple `_VZVNCServer`**, identical regardless of guest OS. I pulled **`ghcr.io/cirruslabs/debian:latest` (0.63 GB)** and ran it with `--vnc-experimental`. This exercises the *exact* Apple VZ VNC server a macOS guest would expose — the security type, auth, and encoding behavior are properties of `_VZVNCServer`, not the guest. So every byte below is from the genuine Apple VZ VNC server.

**Credentials (from Tart source `FullFledgedVNC.swift` + observed):** Tart constructs `_VZVNCAuthenticationSecurityConfiguration(password:)` with a **randomly generated 4-word passphrase** (`PassphraseGenerator().prefix(4)`, hyphen-joined, e.g. `dignity-essay-equip-fortune`), on an **OS-assigned random port** (`port: 0`). It prints the full URL: `vnc://:<password>@127.0.0.1:<port>`. **We launch the VM, so we know the password and port** — no credential-discovery problem.

---

## 2. The EXACT failure our RfbClient hits

Standalone probe imported the real `RfbClient` and pointed it at the live VZ VNC server:

```
=== PART 1: unmodified RfbClient ===
  RfbError (EXPECTED): server requires VNC auth (security types=[2])
```

This is `rfb.py:113`:
```python
if 1 not in types:
    raise RfbError(f"server requires VNC auth (security types={list(types)})")
```
The server's `number-of-security-types`=1, the one offered type is **2** (VNC-DES). Type 1 (None) is **not** on offer, so the client dies before auth.

**Raw handshake capture (hand-rolled client, doing the DES auth):**
```
ProtocolVersion server greeting: b'RFB 003.008\n'
number-of-security-types: 1
>>> OFFERED SECURITY TYPES: [2]        # 2 = VNC Authentication (DES)
DES challenge: 1c09d79ecbb1702059b921ab9e2aa0d4
SecurityResult: 0 (OK)                 # our DES response accepted
ServerInit: 1024x768 name=b'Virtualization'
server pixel-format: 2018000100ff00ff00ff100800000000
   = 32 bpp, depth 24, big-endian=0, true-color=1, RGBmax=255, shifts R16/G8/B0
```
The server's **default pixel format already equals what `rfb.py`'s `SetPixelFormat` requests** (BGRX little-endian). Good — no pixel-format mismatch.

**Then the second, non-obvious failure.** Mimicking `rfb.py` exactly (`SetPixelFormat` + `SetEncodings([Raw])` + non-incremental `FramebufferUpdateRequest`):
```
first FramebufferUpdateRequest -> ConnectionResetError: [Errno 54] reset by peer
```
And the Tart/VZ log printed (from inside Apple's framework, not Tart's code):
```
FIXME IF: "It is unclear if we can support clients that don't support this pseudo encoding." line 234
```
After this reset the **VNC listener stops** — subsequent connects get `ECONNREFUSED`. The Apple `_VZVNCServer` *tears itself down* when the client doesn't advertise a pseudo-encoding it mandates.

---

## 3. Patch distance — measured, not guessed

### (a) Security type
**VNC Authentication, type 2** (16-byte DES challenge → DES-encrypt with bit-reversed password key → 16-byte response). NOT type 1, NOT type 30 (ARD). I implemented the modified-DES in **pure stdlib** (validated against two standard DES known-answer vectors `KAT1`/`KAT2` ✓) and the server returned `SecurityResult: 0`. → A type-2 branch in `_handshake()` is **~25 lines + a ~60-line stdlib DES block** (or `pip install pycryptodome` for a 4-liner; stdlib keeps the zero-dep promise in `rfb.py`'s docstring).

### (b) Credential
Known at launch: the 4-word passphrase Tart prints. We control the launch, so we pass it straight to the auth branch.

### (c) Encoding — the decisive measurement
Isolated exactly which `SetEncodings` advertisement keeps the server alive (each on a fresh server instance, because the bad case kills the listener):

| `SetEncodings` advertised | Result |
|---|---|
| `[Raw]` (== current rfb.py) | **RESET + server dies** |
| `[Raw, Cursor(-239)]` | RESET + dies |
| `[Raw, ExtendedDesktopSize(-308)]` | RESET + dies |
| `[Raw, Tight(7)]` | RESET + dies |
| **`[Raw, DesktopSize(-223)]`** | **ALIVE → first rect enc=0 (Raw)** ✓ (reproduced 2×) |
| full rich set incl. `-223` | ALIVE → **3 updates, ALL Raw** (1024×768 full + 64×64 dirty rects) |

**Conclusion:** the Apple VZ VNC server **requires `DesktopSize` (-223)** in the client's encoding list; with it present the server delivers **plain `Raw` (encoding 0)** — *no Tight/ZRLE/Hextile/CopyRect ever appears.* `rfb.py` already (i) decodes `Raw` and (ii) skips `-223`/`-239` pseudo-rects (line 164: `elif enc == -239 or enc == -223: continue`).

→ **The only encoding change needed is one line:** add `-223` (and harmlessly `-239`) to the `SetEncodings` request at `rfb.py:142`. **No new decoder.**

### Total patch
- `_handshake`: accept type 2, do the DES challenge/response (carry a `password`/`vnc_password` ctor arg). ~25 lines + stdlib DES (~60 lines) **or** pycryptodome.
- `_set_pixel_format`: change `SetEncodings([Raw])` → `SetEncodings([Raw, DesktopSize, Cursor])`. 1 line.
- Everything else (pixel format, Raw decode, input events) is **unchanged** and already correct for VZ.

**Patch distance: SMALL and BOUNDED (~30–90 lines, no protocol research left, no image-codec work).**

---

## 4. SSH reachability — ✅

`tart ip vz-probe` → `192.168.64.4` (shared `192.168.64.x` VZ NAT). `nc -z 192.168.64.4 22` → **open**, banner `SSH-2.0-OpenSSH_10.0p2`. The grading path's SSH dependency works against Tart-launched VZ guests; resolve the IP via `tart ip` (vs. KVM's fixed forward). cirruslabs images default to `admin/admin`.

---

## 5. Recommendation → W5 build-vs-buy

**Patch our RFB client. Do NOT build a custom VZ host app.**

Rationale:
- The two gaps are **(1) VNC-DES auth and (2) one extra pseudo-encoding in `SetEncodings`** — both fully characterized here against the genuine Apple `_VZVNCServer`, both small.
- Critically, **after auth the VZ server speaks `Raw`** — the same encoding our client already decodes, with a pixel format already matching our request. We dodged the worst-case (Tight/ZRLE forcing a new decoder).
- A custom VZ host app (our own RFB-None+Raw framebuffer exposed from a Swift `VZVirtualMachine`) is **only warranted if** we later need (a) headless multi-client, (b) to avoid Tart's random-password/port dance at scale, or (c) finer framebuffer/perf control. None are blockers for eval/lab-delivery. Revisit as an *optimization*, not a *prerequisite*.

**Caveats for W2/W5 to verify on a real macOS guest (couldn't pull the 25 GB image on this 28 GB-free host):**
- Re-confirm `[Raw, DesktopSize]` → Raw on a **macOS** guest (expected identical — it's a host-server property, but verify the larger/ retina framebuffer still comes as Raw and check update sizes/latency, since full-screen Raw at retina res is bandwidth-heavy).
- The VZ VNC server is **fragile to malformed encoding lists** (a wrong `SetEncodings` kills the listener, forcing a VM restart). The patched client must get the list right on the first request; add a guard/retry. Provision a host with **≥80 GB free** for the macOS base.

---

## Cleanup

VM `vz-probe` deleted; OCI cache removed (`~/.tart` = 0 B); disk back to 29 GB free. All `tart run` processes I started are gone. One `com.apple.Virtualization.VirtualMachine.xpc` (PID 21900) remains but **started 2026-05-20, three weeks before this session** — pre-existing (orchestrator/other VM), **left untouched**. Docker's `com.docker.virtualization` left untouched. Temp probe scripts removed from `/tmp`.
