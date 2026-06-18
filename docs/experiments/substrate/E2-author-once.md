# E2 — author once, materialize twice (KVM leg)

**Lock:** KVM-BOX (`ssh user@kvm-host`, NixOS, dockur/macOS, x86_64). **Date:** 2026-06-15/16.
**Interop link #2.** Goal: prove ONE declarative install recipe (`apps.recipe.sh`) produces a
valid, distributable `+apps` layer — and measure the **authoring-parity tax** (the FORK RATE)
honestly by escalating from a trivial CLI tool (jq, W2's parity case) to a real GUI `.app` (VLC).

**Verdict: ✅ "AUTHOR ONCE" HOLDS for the recipe text** through a CLI tool *and* a notarized GUI
cask — **4 arch-conditional code lines / 76 (5.3%)**, and *zero* of those 4 are in the install verbs.
**BUT** the strong honest caveat is a **base-provisioning fork OUTSIDE the recipe**: the dockur/macOS
gold base ships **without Homebrew**, while W2's VZ base ships it preinstalled. So the precise finding
is **"author the install intent once; provision the base toolchain per-substrate."** Details below.

The VZ leg of E2 is **deferred** (needs the Mac, held by E3). The exact follow-up is in §7.

---

## 1. The shared recipe contract — `explorations/env-build/apps.recipe.sh`

ONE file, run **unchanged** by both legs. It is the substrate-blind authoring surface; the two
freeze scripts (qcow2 backing-chain vs APFS clonefile) are the substrate.

| Contract clause | Value |
|---|---|
| **Where it runs** | Inside the macOS guest, over SSH, as the admin account (KVM `user` / VZ `admin`), NOPASSWD sudo, non-interactive, no tty, no GUI. |
| **Preconditions it assumes** | (1) Homebrew on PATH (arm64 `/opt/homebrew`, x86_64 `/usr/local`) — recipe self-locates via `_ensure_brew`; (2) build-time network egress to brew + vendor CDN; (3) NOPASSWD sudo; (4) stock `curl`/`hdiutil`/`ditto`/`xattr`/`file`/`defaults`. |
| **App selection** | `APPS` env var (default `jq`). `APPS="jq vlc"` adds the GUI escalation. Each app has an `install_<app>` handler. |
| **Success signal** | exit 0 **and** last stdout line `RECIPE-OK <recipe-id> <iso8601>`. A non-zero exit or missing trailer MUST abort the freeze (never freeze a half-install). |
| **Self-description** | writes `~/.cua-apps-recipe.json` (recipe id, arch, build time, app+version list) so a booted instance can describe its own `+apps` layer. |

The recipe is **the same bytes** both legs run. The KVM freeze (`freeze-layer.sh`) and the VZ freeze
(`vz-freeze-layer.sh`) both gate on the `RECIPE-OK` trailer — that's the shared success contract.

## 2. KVM leg — what ran (all on the box, mirroring the harness idioms)

1. **Overlay clone** on the current gold base (`make_overlay_clone` bare-base idiom):
   `qemu-img create -f qcow2 -F qcow2 -b /base/data.qcow2` → 196 KiB instance overlay.
2. **Boot** (dockur `run_container` overlay mode, ports 50211/50311/50411) → SSH up in ~12s
   (re-boot of an already-installed base). Guest: **x86_64, macOS 14.8.7 (23J520)**.
3. **Brew bootstrap** (precondition gap — see §6): base had no brew/CLT; ran the official
   `NONINTERACTIVE=1` Homebrew installer → CLT for Xcode-16.2 + `brew` at `/usr/local/bin/brew`.
4. **Run the shared recipe over SSH**, twice (idempotent):
   - `APPS=jq   bash -s < apps.recipe.sh` → `jq-1.8.1`, `RECIPE-OK`, exit 0.
   - `APPS="jq vlc" bash -s < apps.recipe.sh` → `jq-1.8.1` + `VLC 3.0.23`, `RECIPE-OK`, exit 0.
5. **Verify in-guest:** `which jq` → `/usr/local/bin/jq`, `jq-1.8.1`; `/Applications/VLC.app`
   present, `CFBundleShortVersionString=3.0.23`, `MACH-O 64-bit executable x86_64`.
6. **Stop clean** (`docker stop` → Exited 0), `docker rm`, then **freeze** the overlay.

`jq-1.8.1` is the **exact same version W2 got on VZ** — the parity case is byte-comparable.

## 3. The KVM +apps layer (deliverable artifact, kept on box)

- **Path:** `~/cua-worlds/layers/00944de70ca6/14/data.qcow2`
- **Digest:** `sha256:00944de70ca6033dd6a09684d81cd053c7f4e7e31a4bd9bbe3698a3a0efdcf51`
- **Tag:** `~/cua-worlds/layers/by-name/jq-vlc-1 -> 00944de70ca6`
- **Disk size:** 4.76 GiB (jq + VLC + Homebrew + CLT). `backing_file = /base/data.qcow2` (header-rebased).
- **layer.json:** `role=apps, os=macos, arch=x86_64, format=qcow2, macos_version=14`,
  `parent=sha256:89ed5870…` (the os-base digest), `built=2026-06-16T03:07:02Z`.
- **Contents:** `/usr/local/bin/jq` (1.8.1), `/Applications/VLC.app` (3.0.23),
  `~user/.cua-apps-recipe.json` breadcrumb.

## 4. Chain-boot proof — jq + VLC through the chain (the W1 chain path)

A **fresh instance** overlay parented on the `+apps` layer (`-b /apps/data.qcow2`), booted with
`/base` + `/apps` mounted read-only. `qemu-img info --backing-chain` on the live overlay:

```
image: /out/data.qcow2     disk size: 196 KiB     backing file: /apps/data.qcow2   (instance)
image: /apps/data.qcow2    disk size: 4.76 GiB    backing file: /base/data.qcow2   (+apps 00944de70ca6)
image: /base/data.qcow2    disk size: 14.9 GiB                                     (os-base)
```

The instance overlay was **empty at boot** (196 KiB), so seeing the apps proves reads fall through
`instance → +apps → os-base`:

```
$ ssh -p 50212 user@localhost 'zsh -lc "which jq && jq -n .ok=true"'
/usr/local/bin/jq
{ "ok": true }
$ ssh -p 50212 user@localhost 'defaults read /Applications/VLC.app/Contents/Info.plist CFBundleShortVersionString'
3.0.23
$ ssh -p 50212 user@localhost 'file /Applications/VLC.app/Contents/MacOS/VLC'
… Mach-O 64-bit executable x86_64
```

jq runs and parses (`{"ok":true}`); VLC is a native x86_64 bundle — **both live only in the +apps
layer**. Chain proven end-to-end.

## 5. The env package — `explorations/substrate/envs/macos-jq/env.toml`

Declares the os-base + this `+apps` layer (KVM/x86_64 chain). Resolves cleanly through the existing
`env/pkg.py` resolver (`validate_paths=False`, remote box):

```
name=macos-jq  macos_version=14  runtime=kvm-qcow2
layers=[(os-base, …/base), (apps, …/layers/00944de70ca6)]
apps-layer digest=sha256:00944de70ca6…   (matches layer.json)   RESOLVE-OK
```

It adds a provenance `[build]` table (recipe id, `APPS=["jq","vlc"]`, the precondition-gap note) —
ignored cleanly by the single-target resolver — and a **PLACEHOLDER comment block** for the
arm64/`vz-clonefile` chain the VZ leg will fill, so **E1's index can later unify** the two per-arch
byte-chains under one logical `macos-jq`. The one missing value is the VZ `+apps` digest.

## 6. THE FORK RATE — the core finding

I tagged every arch-conditional line in the recipe `#FORK` (grep-able). Measured against the
**76 non-blank/non-comment lines** of recipe body:

| App | Install verb (the intent) | Fork lines | What forked | Verdict |
|---|---|---|---|---|
| **jq** (CLI formula) | `brew install jq` | **0** | nothing — formula name, verb, `jq --version` verify are all arch-identical | ✅ truly author-once |
| **VLC** (GUI cask) | `brew install --cask vlc` | **0** *(in the install verb)* | the cask name resolves the right arch build automatically | ✅ author-once for install |
| *shared scaffolding* | `_ensure_brew` arch→prefix | **2** | `/opt/homebrew` vs `/usr/local` (`case $ARCH`) | unavoidable, 1 block |
| *VLC verify* | expected Mach-O slice | **2** | `want=arm64` vs `want=x86_64` for the `file` check | cosmetic (verify-only) |

**Total: 4 arch-conditional code lines / 76 = 5.3%.** All 4 are in 2 small `case "$ARCH"` blocks;
**none are in an install verb.** Crucially, the GUI escalation (VLC) added **zero** install-side
forks — the worry that a real `.app` would force per-arch download URLs / cask names / bundle paths
**did not materialise** for a Homebrew cask: Homebrew abstracts the arch (it picks the arm64 vs
x86_64 build from one cask name), the bundle path `/Applications/VLC.app` is arch-identical, and
quarantine-clear is arch-identical. The only place arch leaks in is the *post-install verify* (which
Mach-O slice to expect) — and that's belt-and-braces, not load-bearing.

**Verdict on "author once":** for **Homebrew-deliverable apps (formula OR cask)** "author once"
**HOLDS** — the recipe text is ~95% shared with the residual forks confined to brew-prefix detection
and a cosmetic verify. It does **NOT** degrade to "two recipes."

### The honest asterisk — the precondition fork is OUTSIDE the recipe

The real authoring-parity tax this experiment surfaced is **not in the recipe — it's in the base**:

- **W2 VZ base** (cirruslabs macOS) ships **Homebrew preinstalled** → recipe precondition #1 met for free.
- **KVM dockur base** ships **no Homebrew, no CLT** (`/usr/local/bin/brew` absent, `xcode-select -p`
  fails) → I had to run a one-time `NONINTERACTIVE=1` Homebrew bootstrap (which pulls CLT for
  Xcode-16.2, ~minutes) **before** the recipe.

This is a genuine fork, but it lives at the **base-provisioning layer**, not in the per-app recipe.
The clean framing for E1's index and E6's delivery: **"author the install *intent* once
(`apps.recipe.sh`); guarantee the *toolchain precondition* per-substrate (base build)."** The recipe
contract already states "brew present" as a precondition the env-build harness must guarantee — the
two bases just meet it differently. **Recommendation:** add a Homebrew bootstrap to the KVM
base-build (one-time, baked into the os-base, not re-run per layer) so the recipe's precondition #1
is uniformly true; then the recipe really is the *only* authoring artifact. Had VLC been a non-cask
DMG (hand-rolled `curl URL && hdiutil attach`), the fork rate would have jumped — the per-arch
download URL alone is 1–2 forks per app — which is exactly why **preferring Homebrew casks over raw
DMGs is the single biggest lever for keeping the fork rate near zero**, and should be a recipe-author
guideline.

## 7. VZ leg — DEFERRED (clean follow-up spec)

The VZ leg is explicitly deferred (the Mac is held by E3). To complete interop link #2, the VZ worker
must, on the LOCAL-MAC:

1. `tart clone` the cirruslabs macOS base (Homebrew already present → **no bootstrap needed**, the
   one asymmetry vs KVM).
2. Run **the exact same** `explorations/env-build/apps.recipe.sh` with **`APPS="jq vlc"`** over SSH
   (`tart ip`). It will auto-detect arm64 → `/opt/homebrew`, install `jq` (expect `jq-1.8.1`, matching
   KVM) + the `vlc` cask (arm64 build), write the breadcrumb, emit `RECIPE-OK`.
3. `tart stop` (consistent disk) → freeze with `explorations/substrate/vz-freeze-layer.sh` → an
   **arm64/raw** content-addressed `+apps` bundle under `~/.tart/_layers/<digest>` + `layer.json`
   (`arch=arm64, format=raw`).
4. **Fill the placeholder** in `envs/macos-jq/env.toml`: add the `[[targets]]` arm64/`vz-clonefile`
   block with the VZ layer's `ref` + `digest`. Then E1's index resolves `macos-jq` →
   `{x86_64→00944de70ca6 (qcow2), arm64→<vz-digest> (raw)}` — ONE recipe, TWO materialized byte-chains.
5. **Cross-check parity:** both legs should report `jq-1.8.1` and `VLC 3.0.23` (or note any version
   drift if the casks updated between runs — pin if E4 needs bit-identical app versions).

That is the entire VZ leg: same recipe text, VZ freeze script, one env.toml edit. No recipe changes.

## 8. Box hygiene

- **Created + removed:** overlays `runs/e2-apps-build` + `runs/e2-chain-instance`; containers
  `e2-apps-build` + `e2-chain-instance` (both `docker stop`→Exited 0, then `rm`); `/tmp/apps.recipe.sh`,
  `/tmp/freeze-layer.sh`, `/tmp/_layer.json`. All gone (verified).
- **Kept (deliverable):** `+apps` layer `layers/00944de70ca6` + `by-name/jq-vlc-1` tag (digest §3).
- **Untouched:** gold base `base/14/`, shared `_base_qcow2/14/`, W1's layer `a22c98a5d55e` +
  `cua-marker-1` tag.
- **Pre-existing (NOT mine):** the two 4-week-old `Exited (255)` containers
  (`odoo-review-analytic_cost_allocation`, `fix-git__pzkdyqs-main-1`) — left as-is.
  The six `runs/mw-*` dirs are prior `mw remote run` rsync copies from earlier sessions — **not mine**,
  left as-is. **Orphans report:** the 2 Exited containers above are the only stray guests on the box.
- Disk: box has 663 GiB free; the layer adds 4.76 GiB.

---

# E2 — VZ LEG (author once, the SECOND materialization)

**Lock:** LOCAL-MAC (Apple M4 Pro, macOS 15.7.3 Sequoia host, Virtualization.framework, Tart 2.32.1).
**Date:** 2026-06-16. **Interop link #2, arm64 half.** Builds on E3 (`e3-base` frozen, SSH key baked
in, `VzMacOSEnv`), the W2 `vz-freeze-layer.sh` clonefile recipe. Goal: run the **exact same**
`apps.recipe.sh` text (`APPS="jq vlc"`) the KVM leg ran, on a `tart clone` of the cirruslabs Sequoia
base, freeze a content-addressed `+apps` layer (clonefile), boot a fresh instance over it, prove
jq+VLC through the chain, fill the `env.toml` arm64 placeholder, and report the VZ FORK RATE vs KVM.

**Status:** INCREMENTAL — milestones appended as they land.

## V0 — preconditions confirmed (2026-06-16)

`tart list`: `e3-base` (50G logical / 30G on disk, stopped) is the frozen E3 base — cirruslabs
`macos-sequoia-base`, arm64, Sequoia, with `~/.tart/_e3/id_vz.pub` baked into `admin`'s
`authorized_keys` (E3 M2). The cirruslabs base **ships Homebrew preinstalled** → recipe precondition
#1 (`/opt/homebrew/bin/brew`) is met for free — **NO brew bootstrap needed** (the single asymmetry vs
the KVM dockur base, which required a one-time `NONINTERACTIVE=1` Homebrew install before the recipe).
Disk: 83 GiB free, `~/.tart` = 60 GB. 2-VM cap respected throughout (serialize build→freeze→test).

## V1 — author-once run: SAME recipe text, APPS="jq vlc" over SSH ✅ (2026-06-16)

`tart clone e3-base e2vz-build` (0.07s CoW) → regen ECID → `tart run --vnc-experimental --no-graphics`
→ `tart ip` = `192.168.64.33` → key-SSH ready. Guest: **arm64, macOS 15.7.7 Sequoia (24G720)**,
Homebrew **5.1.15** at `/opt/homebrew/bin/brew` (preinstalled — no bootstrap). Ran the **byte-identical**
`explorations/env-build/apps.recipe.sh` over SSH exactly per the recipe contract:

```
ssh … 'APPS="jq vlc" bash -s' < apps.recipe.sh
[apps.recipe] using brew: /opt/homebrew/bin/brew (prefix /opt/homebrew)
[apps.recipe] installing jq (brew formula)     → verified: jq-1.8.1
[apps.recipe] installing VLC (brew cask …)      → verified: VLC.app 3.0.23 (arm64 slice ok)
[apps.recipe] wrote breadcrumb /Users/admin/.cua-apps-recipe.json
RECIPE-OK apps.recipe/v1 2026-06-16T06:30:25Z          exit 0
```

Exit 0 + `RECIPE-OK` trailer present → the freeze gate is satisfied. **No recipe edits** — the same
text the KVM leg ran.

**In-guest verify (build VM):** `/opt/homebrew/bin/jq` → `jq-1.8.1` (arm64 Mach-O); `/Applications/VLC.app`
present, `CFBundleShortVersionString=3.0.23`, `Contents/MacOS/VLC` = `Mach-O 64-bit executable arm64`.

### VZ-specific gotcha (worth recording for E4/E1): Sequoia ships a system `/usr/bin/jq`

A **bare non-login** `ssh … 'which jq'` resolves to **`/usr/bin/jq` → `jq-1.7.1-apple`** — Sequoia ships
a system jq that shadows the brew one on the default non-login PATH. Sonoma (KVM dockur base) does **not**
ship `/usr/bin/jq`, so KVM's bare `which jq` → `/usr/local/bin/jq`. The brew `jq-1.8.1` is correctly at
`/opt/homebrew/bin/jq`; under a **login shell** (`zsh -lc "which jq"`, which the KVM leg also used, §4)
the brew jq wins → `jq-1.8.1`. **Implication:** any jq-dependent grader or chain proof must use a login
shell (or call `/opt/homebrew/bin/jq` explicitly) on VZ — exactly as the KVM §4 proof did. This is a
*base-PATH* difference (system tool present on Sequoia), NOT a recipe fork and NOT an install failure;
the recipe installs the same brew jq-1.8.1 on both substrates.

## V2 — frozen VZ +apps layer (deliverable artifact, kept) ✅

`tart stop e2vz-build` (graceful → consistent disk, no leaked run proc) → `vz-freeze-layer.sh e2vz-build
macos-jq apps` (clonefile CoW):

- **Layer dir:** `~/.tart/_layers/801695f74c0d/` (read-only bundle: `config.json`, `disk.img` 50 GB
  logical, `nvram.bin`).
- **Digest:** `sha256:801695f74c0d85a7ef776b1fff13158d23c75830f179e5ed332e73d4ff06ae9f`
- **layer.json** (`~/.tart/_layers/801695f74c0d.meta/layer.json`): `role=apps, os=macos, arch=arm64,
  format=raw, schemaVersion=cua.layer.v1, name=macos-jq, built=2026-06-16T06:31:09Z`.
- **Format note (Sequoia):** **`raw`** (sparse raw `disk.img`) — NOT ASIF. ASIF is Tahoe-only; on Sequoia
  the Tart bundle disk is a sparse raw image, so the VZ +apps layer format is `raw`, matching the
  `vz-freeze-layer.sh` shape.
- **CoW confirmed:** freezing the layer dropped **free disk by only ~4 GiB** (83→79 GiB) even though the
  disk.img is 50 GB logical — the clonefile shares blocks with its source; only the apps delta (jq + VLC
  + brew metadata) is new physical bytes. (`du` over-reports because it can't see APFS CoW sharing; free
  disk is the truth.) The `cp: control.sock is a socket (not copied)` warning is benign — the live VM
  socket is regenerated at boot.

## V3 — fresh instance over the +apps layer: jq+VLC THROUGH THE CHAIN ✅

Registered the frozen layer as a clonable base (`cp -cR _layers/801695f74c0d vms/e2vz-apps`, the E3
carry-over: `VzConfig.base_vm` = frozen +apps bundle) → `tart clone e2vz-apps e2vz-inst` (fresh
instance, the CoW chain `instance ← +apps ← os-base`) → regen ECID → boot → `192.168.64.34`. On the
fresh instance (which inherited NONE of jq/VLC except through the layer):

```
$ ssh … 'zsh -lc "which jq && jq --version && jq -n .ok=true"'
/opt/homebrew/bin/jq      jq-1.8.1      { "ok": true }
$ ssh … 'defaults read /Applications/VLC.app/Contents/Info.plist CFBundleShortVersionString; file …/VLC'
3.0.23      /Applications/VLC.app/Contents/MacOS/VLC: Mach-O 64-bit executable arm64
$ ssh … 'cat ~/.cua-apps-recipe.json'   → built 2026-06-16T06:30:25Z, apps jq-1.8.1 + vlc-3.0.23
$ IOPlatformUUID = A4BCA182-…-120FDDF3D013   Serial = ZYHXKYHC6G    (distinct → fresh clone, ECID regen)
```

jq runs and parses; VLC is a native arm64 bundle; the breadcrumb (built-timestamp = the build run)
survived through the CoW chain — all three live ONLY in the +apps layer. **Chain proven end-to-end on
VZ**, mirroring the KVM §4 proof. Instance stopped + `tart delete`d after.

## V4 — env.toml placeholder FILLED (E1 can now unify both arches) ✅

Filled the arm64/`vz-clonefile` block in `explorations/substrate/envs/macos-jq/env.toml` with this
layer's `ref` (`~/.tart/_layers/801695f74c0d`) + `digest`
(`sha256:801695f74c0d…`) + `format=raw`. The env now declares BOTH per-arch byte-chains under the one
logical name `macos-jq`:
- `x86_64 → kvm-qcow2 → 00944de70ca6` (KVM leg, §3)
- `arm64  → vz-clonefile → 801695f74c0d` (this VZ leg)

ONE recipe (`apps.recipe.sh`, APPS="jq vlc") → TWO materialized byte-chains. E1's index resolves
`macos-jq` → `{x86_64→qcow2, arm64→raw}` by the host's `[target]`.

## V5 — THE VZ FORK RATE (vs KVM 4/76)

The fork rate is a property of the **recipe text**, which is byte-identical on both legs — so the
arch-conditional line count is **identical**: **4 `#FORK` lines / 76 non-blank-non-comment lines = 5.3%**,
all 4 in two small `case "$ARCH"` blocks (brew-prefix detect ×2, expected-Mach-O-slice ×2), **none in an
install verb**. On VZ the two brew-prefix forks resolve to the `arm64 → /opt/homebrew` branch and the two
slice forks to `want=arm64`; on KVM they took the `x86_64 → /usr/local` / `want=x86_64` branches. The
fork lines are *exercised* differently per arch but the **source is the same** — that is the whole point
of "author once".

**Does "author once" hold IDENTICALLY on VZ?** **YES — and more cleanly than KVM.** The honest asterisk
that the KVM leg surfaced — the **base-provisioning fork** (KVM dockur base ships no Homebrew → needed a
one-time bootstrap; VZ cirruslabs base ships brew) — resolves *in VZ's favour*: **the VZ leg needed ZERO
out-of-recipe provisioning steps**. So on VZ the recipe really is the *only* authoring artifact (the
KVM-leg recommendation "bake brew into the os-base so the recipe is the sole artifact" is already true for
the cirruslabs VZ base). App-version parity is **bit-exact**: jq **1.8.1** and VLC **3.0.23** on BOTH legs
— no cask/formula drift between the two runs, so E4 gets bit-identical app versions for free.

| metric | KVM (x86_64 / Sonoma / dockur) | VZ (arm64 / Sequoia / cirruslabs) |
|---|---|---|
| recipe `#FORK` lines | 4 / 76 (5.3%) | 4 / 76 (5.3%) — same source |
| forks in an install verb | 0 | 0 |
| **out-of-recipe base provisioning** | **brew bootstrap REQUIRED** | **NONE (brew preinstalled)** |
| jq version | 1.8.1 | 1.8.1 (✅ bit-exact) |
| VLC version | 3.0.23 | 3.0.23 (✅ bit-exact) |
| jq PATH gotcha | `/usr/local/bin/jq` (no system jq) | system `/usr/bin/jq-1.7.1-apple` shadows on non-login PATH |
| layer format | qcow2 (backing-chain) | raw (clonefile CoW) |

**Verdict (VZ leg): ✅ "AUTHOR ONCE" HOLDS on VZ, identically to KVM at the recipe level (5.3% fork,
0 install-verb forks) and *better* at the base level (no provisioning fork).** The precise cross-substrate
finding stands: **"author the install intent once (`apps.recipe.sh`); the toolchain precondition is met
per-substrate — for free on the VZ base, via a one-time os-base bootstrap on the KVM base."** No recipe
fork; the only true asymmetry is base provenance, which favours VZ.

## V6 — VZ-leg hygiene (Part-1)

- **Created + removed:** `e2vz-build` (build VM, `tart delete`d after freeze), `e2vz-inst` (chain-proof
  instance, stopped + `tart delete`d). No leaked `tart run` procs (verified `pgrep` clean each step).
- **Kept (deliverable artifacts):** the frozen +apps layer `~/.tart/_layers/801695f74c0d` (+ `.meta`),
  and `~/.tart/vms/e2vz-apps` (the layer registered as a clonable base — reused by E5 as the +task-state
  parent; delete to reclaim its CoW delta when E5 is done).
- **Untouched:** `e3-base` (the E3 frozen base, kept intact per instructions); the OCI cache.
- Final disk (after BOTH Part 1 + Part 2): **free disk 76 GiB**; `~/.tart` `du`=113 GiB (over-counts CoW
  sharing — free disk is the truth). Only `e3-base` + the two `_layers` artifacts kept; no VM/proc leaks.
  Full combined cleanup + orphan report in `explorations/substrate/E5-determinism.md` (FINAL CLEANUP).


