# RFC 0002 — Portable macOS CUA env spec: layered, content-addressed VM images on Linux/KVM **and** Apple/VZ

- **Status:** Accepted — validated end-to-end on both substrates (see §6); ready to build.
- **Author:** jjmachan
- **Created:** 2026-05-29 · **Validated:** 2026-06-16 (experiment program W0–W2 + E1–E7)
- **Scope:** macOS only — the macOS computer-use (CUA) benchmark/RL substrate, on two substrates: x86 Linux/KVM and Apple-Silicon Virtualization.framework (VZ). No other OS is in scope.
- **Supersedes:** the monolithic "ship the base volume" plan in `docs/runbooks/kvm-server-setup.md` §4 (the runbook stays correct; this RFC is a better way to package and distribute the same thing).
- **Related:** RFC 0001 (experimentation principles), `docs/experiments/kvm-on-linux.md`, and the experiment deliverables under `docs/experiments/substrate/`.
- **Finalization gate (SATISFIED):** the original gate required validating the Apple/VZ adapter on real Apple Silicon. **Done** — RISK-1 is retired (§6, experiment W2/E3). The Apple/VZ leg is no longer a paper design.

---

## 1. Summary

A portable, declarative **env-package** format for macOS CUA environments — composed of small, content-addressed layers (`os-base ← +apps ← +task-state ← instance`) distributed as **OCI artifacts via ORAS**. One declarative package targets **two substrates** and is proven to behave as **one system**:

- **x86 Linux/KVM** (dockur/QEMU, qcow2 backing chains) — the cheap, dense **fan-out engine** for RL training + internal dev.
- **Apple-Silicon Virtualization.framework** (via Tart, APFS `clonefile()`) — the legal, native format we **ship** for eval/delivery.

The bytes never port across substrates (x86-macOS only boots under QEMU/OpenCore; arm64-macOS only boots on Apple hardware). Portability + interoperability live in the **manifest**: one logical name resolves, by the host's target descriptor, to the right per-platform layer chain (Docker multi-arch model), and **the same task grades identically on both** for a certified set. Routing: *training → KVM, eval/delivery → VZ.*

**Validation outcome (§6):** all seven design links are proven on real hardware. The headline — *author once → materialize on both substrates → grade identically* — holds for a measured certified-portable task set, with the cross-version "silent schema drift" risk **disproven**.

## 2. Motivation

`mw bench` runs against a `--backend kvm` fleet today, but the **environment** is a single opaque ~16 GB volume. Six pains this spec removes:

1. **No version axis.** "macOS 14 + Firefox + the pre-state for task-42" is the same disk as "macOS 14 + nothing." Can't name, share, or compare environments.
2. **No layered reuse.** Adding an app to one env is a fresh ~16 GB.
3. **No content-addressed dedup.** Two near-identical envs = two near-identical blobs.
4. **No pull semantics.** A colleague brings up a tarball out-of-band; there's no `oras pull` story.
5. **No portable task/env format.** Each CUA effort invents its own VM-snapshot scheme.
6. **Substrate split.** KVM is x86-only (Tahoe is the last x86 macOS) and violates Apple's EULA — fine for internal R&D, wrong for anything customer-facing. VZ is the legal, ARM-native, future-proof path but caps at **2 macOS VMs/host**. The two serve *different workloads*; one spec over both lets us **train cheaply on Intel and ship legally on Apple** against the same package.

## 3. Principles we adopt (validated)

- **Harbor (the shape).** Terminal-Bench 2.0's harness: a task is a declarative dir (`instruction.md` + `task.toml` with `schema_version` + nested sections + `environment/` + `tests/test.sh`), a **file-based reward contract** (`/logs/verifier/reward.json` named metrics, fallback `reward.txt`), and a backend-agnostic `BaseEnvironment` selected by an `-e`/`--env` flag. Our `Env` protocol (`infra/cli/benchmark/env/base.py`) is exactly this pattern — adopt the versioned, nested `env.toml` and the reward-contract shape.
- **KubeVirt containerDisk + Fedora bootc (the registry).** Ship VM disks as OCI artifacts; split past the ~10 GB/layer registry cap into 7–8 GiB sub-layers; **`zstd:chunked`** for content-addressed dedup (preserves the uncompressed digest; reflink/CoW partial pulls). Caveat we measured: `oras` has **no chunked flag** — `zstd:chunked` is a container-image construct, so for VM artifacts we compress (plain zstd) before push.
- **qcow2 backing chains (KVM layering).** Each link stores only its delta; lower layers read-only and shareable. Literal block-level composition.
- **Reset by overlay/clone discard (the reset primitive).** Per-rollout reset = discard the instance overlay (KVM) / clone (VZ) and recreate — deterministic by construction. (We tested and **rejected** QEMU `savevm`/`loadvm`: ~22 s restore, +3.8 GB/guest, no shared-golden — §6 W0a.)
- **Tart (the Apple/VZ mechanism).** Runs macOS VMs on Virtualization.framework, distributes them as OCI artifacts, and uses APFS `clonefile()` for instant clones. We adopt Tart as the VZ launch/clone mechanism; license is **Fair Source (FSL-1.1-ALv2)** — royalty-free for our footprint (paid only above 100 CPU cores; our VZ delivery footprint is small).

**Synthesis:** *Harbor's shape × OCI's registry × the per-substrate layering mechanism (qcow2 chain on KVM, APFS `clonefile()` on VZ).*

## 4. The two substrates

The bytes don't port; the manifest does. Numbers below are **measured** (§6), correcting the earlier paper estimates.

| Concern | Linux / KVM | Apple / VZ |
|---|---|---|
| Hypervisor | QEMU + KVM (dockur/macos) | `VZVirtualMachine` via Tart |
| Host arch | x86_64 Linux | Apple Silicon (ARM64) |
| Guest macOS | Sonoma 14.8.7 (x86; Tahoe is end-of-line) | Sequoia 15.7.x (ARM64) |
| Disk format | qcow2 (sparse) | raw/sparse bundle (**ASIF is Tahoe-only**; Sequoia uses raw) |
| Layer composition | qcow2 `backing_file` chain | APFS `clonefile()` (file-level CoW) |
| Per-instance clone | `qemu-img create -b parent` (**~0.86 s**, W1) | `tart clone` = `clonefile` (**~0.07 s / 4–8 KiB** for a ~50 GB-logical bundle) |
| Reset | overlay discard + recreate (deterministic) | clone discard + re-clone (deterministic) |
| Distribution | OCI/ORAS, `application/x-qemu-qcow2+zstd` (or block-delta) | OCI/ORAS, `application/vnd.cua.layer.delta.v1+json` (zstd block-delta) |
| Input + exec | VNC (RFB) + SSH | VNC (RFB, VNC-DES auth + `DesktopSize`) + SSH (`tart ip`) — same harness drivers |
| Density | RAM-bound, KSM≈0 for macOS: **~4 GB/guest, ~6 usable / 31 GB box**; scales linearly with RAM | **2 macOS VMs / host** (kernel-enforced cap; no shippable bypass) |
| Identity per clone | strip dockur `macos.id/mac/mlb/sn` → MAC+UUID regen; **serial needs per-clone regen** (see §6 E7) | regenerate **ECID/`VZMacMachineIdentifier`** per clone → distinct UUID/serial |
| EULA | violates Apple EULA (macOS on non-Apple HW) — internal R&D only | compliant (macOS on Apple HW), within SLA §2(B)(iii) dev/test use |

**Strategic read.** KVM = the *fan-out engine* (cheap, scales by RAM, no Apple hardware spend) for RL training + dev. VZ = the *delivery vehicle* (compliant, ARM-native, 2 VMs/host) for eval + shipping to customers. Same env package; each substrate used where its constraint doesn't bite.

## 5. What does NOT change

This RFC is **below the env-creation line.** The harness (`runner.py`, `agent.py`), the `Env` protocol (`env/base.py`), `agent.step()`, screenshot capture, the dashboard, RFC 0001's score model, and the task instruction/grading format are unaffected. What changes is *where the env's disk comes from* and *how it's composed at runtime*. The `Env` protocol is the substrate-blind seam: `KvmMacOSEnv` and `VzMacOSEnv` both implement it; everything above is substrate-blind. (Proven: the grading path graded a task 0→100 on a live VZ guest with **zero changes** — §6 W2.)

## 6. Validation status — PROVEN (RISK-1 retired)

A multi-agent experiment program (orchestrator + workers, two hardware locks: the KVM box and one Apple M4 Pro) validated every link on real hardware. Substrates pinned for the certification: **KVM = macOS 14.8.7 Sonoma x86_64**, **VZ = macOS 15.7.7 Sequoia arm64**.

| # | Link | Experiment | Result |
|---|---|---|---|
| — | Reset architecture | W0a | `savevm`/`loadvm` works + deterministic but ~22 s + 3.8 GB/guest + no shared-golden → **KILLED**; reset = discard+recreate on both. |
| — | VZ feasibility (RISK-1) | W2 | macOS VZ guest boots; SSH+grade path works **unmodified**; RFB client patched (VNC-DES + `DesktopSize`, gated so KVM is byte-identical); `clonefile` +apps layer; sibling isolation + base immutability; clonefile CoW = qcow2-like (RFC Q2 = **YES**). **RISK-1 retired.** |
| 1 | **Index / target-resolution** | E1 | One logical name → the right per-platform chain by host `[target]`, via an OCI image-index; unmatched/cross-runtime targets **fail loud**; proven via a credential-free `oras --oci-layout` round-trip; adversarially re-verified (same name → two disjoint chains). Decision: **index** over sibling-tags; the runtime axis rides a `cua.target.runtime` annotation (OCI `platform` has no runtime slot). |
| 2 | **Author-once** | E2 | One declarative `apps.recipe.sh` (jq + the VLC GUI app) ran on **both** substrates → KVM `+apps` (`00944de70ca6`) and VZ `+apps` (`801695f74c0d`). **Fork rate 4/76 = 5.3% identical** on both; GUI app added 0 install forks. "Author once" holds. One asymmetry: dockur KVM base ships no Homebrew (cirruslabs VZ base does) → bake brew into the KVM os-base; framing = "author the install intent once, provision the base toolchain per-substrate." |
| 3 | **VZ adapter** | E3 | `VzMacOSEnv` (`env/vz/`) implements the **full `Env` protocol**; live grade 0→100 on a real Sequoia guest; **ECID-regen** yields distinct IOPlatformUUID/serial on concurrent siblings (closes the W2 identity flag); 10× reset-by-discard left the base byte-identical. Build-vs-buy: **patch RFB + thin adapter over a Tart guest**, not a custom host app. |
| 4 | **Grade identity (headline)** | E4 | SSH-seeded the exact correct end-state on both live guests and compared the full grade triple. n=1 PASS (`fc7d32bd` and `e847156f` byte-identical ×3). Battery (N=3): clean-POSIX **6/6 = 100%**, settings 2/2, version-self-adjusting 2/4, store-bound 4/5 → **14/17 = 82% certified-portable**. The 3 misses are **shallow one-line grader defects** (hardcoded Sonoma Calendar path; `Processor Name` vs `Chip:`; headless display) → projected 17/17. **Skew probe: the load-bearing risk DISPROVEN** — `CalendarItem`/`ZREMCDREMINDER`/`ABCDContact` schemas + the `+978307200` epoch are byte-stable Sonoma↔Sequoia; substrates disagree only on moved paths / host-chrome strings, always **visibly/deterministically, never silently**. |
| 5 | **Determinism parity** | E5 (+E7) | A `+task-state` layer frozen after quiesce (`PRAGMA wal_checkpoint(TRUNCATE)` + graceful quit) → **ZERO starting-grade variance** across N=10 fresh instances and 10× reset on **VZ**, and across n=2/4/6/8 + a 44-grade reset-storm on **KVM** (even under a swap-storm). Daemon-perturbation kill criterion did not fire → store-bound `+task-state` is viable. Cross-substrate determinism **parity holds**. |
| 6 | **Thin-layer delivery (legal)** | E6 | The legal model is real end-to-end: ship a **zstd block-delta** (`e3-base → +apps` = 3,298 blocks → **326 MiB**) carrying our IP only, **proven to contain zero macOS-base bytes** (0/3,298 shipped blocks match the base; 153× smaller than the **50 GB logical** base). A foreign host provisions its **own** public base, pulls our 326 MiB delta, composes via `clonefile`, and grades `fc7d32bd` to the identical triple. **Base provenance must be pinned** (gate the composer on `cua.layer.base.disk.sha256`). clonefile CoW preserved; ship plain zstd. |
| 7 | **Training fan-out** | E7 | The same env-package + overlay-chain + reset-by-discard (production fleet code unmodified) feeds the RL engine. Density RAM-bound (KSM≈0): ~4 GB/guest, ~6 usable on the 31 GB box (~900–1,080 rollouts/day = a dev/CI rig); a 256 GB x86 host ≈ 10.4k/day → **training wants one fat high-RAM x86 box.** Flag: macOS **serial collides** at fleet scale (dockur `macserial --num 1` is deterministic) — a training-blocker for serial-coupled tasks, fixed by per-clone serial regen; the certified clean-POSIX set reads no serial and is unaffected. |

## 7. Design

### 7.1 Layer model (substrate-agnostic)

```
os-base       macos:sonoma-14.8.7 (x86) / sequoia-15.7.7 (arm64)   (shared, immutable;           L0
              KVM qcow2 ~15 GB · VZ ~50 GB logical / ~27 GiB physical)
  +apps       e.g. jq + VLC                                         (tens of MB–GB delta)         L1 (optional)
  +task-state task pre-state, frozen after quiesce                  (MB)                          L2 (per task, optional)
  instance    per-rollout overlay/clone (discarded on reset)        (KB–MB, transient)            L3
```
L0–L2 are immutable, content-addressed, registry-distributable. L3 is transient. Depth is open-ended.

### 7.2 Env package (Harbor-shaped)

```
envs/macos-jq/
├── env.toml      # [env] + [target]/[[platform]] + layer chain (per substrate)
├── README.md
└── build/        # provenance: apps.recipe.sh + expectations
```
Tasks remain Harbor-shaped dirs on top of an env (`task.toml` + `instruction.md` + `grading/`), referencing an env by name. Same task format across substrates (graded by SSH/sqlite/file checks — the property that makes grade-identity possible).

### 7.3 Platform-target descriptor + the one-name-two-chains mechanism (E1)

Every env declares its `[target]` (the dispatch key the runtime matches against the host); a logical name resolves through a **standard OCI image-index** to one of `(os=macos, arch=x86_64, runtime=kvm-qcow2)` or `(arch=arm64, runtime=vz-clonefile)`. `runtime` rides a `cua.target.runtime` annotation (OCI `platform` carries only os/arch). The resolver (`env/pkg.py`, `detect_host_target()` overridable via `MW_HOST_*`) picks the matching entry; **mismatch fails loud** ("no matching platform"). Decision: index over sibling-tags.

### 7.4 Substrate adapters

Same shape on both: resolve env → pull/locate layers → assemble a runnable disk → boot. Assembly differs.

- **KVM (qcow2 chain)** — `host.make_overlay_clone` parents the per-instance overlay on the top `+apps` layer (`qemu-img create -b /apps/data.qcow2`), mounting `/base` + `/apps` read-only so the multi-deep backing chain resolves inside the dockur container. Proven 3-deep boot (`instance → +apps → os-base`).
- **VZ (clonefile)** — `VzMacOSEnv` (`env/vz/`): `tart clone` the frozen layer, regenerate the ECID/serial per clone, `tart run --vnc-experimental`, drive the guest via the patched RFB client + SSH (`tart ip`), `grade()` through the shared substrate-blind grader. Reset = `tart delete` + re-`clone`.

### 7.5 Distribution (OCI / ORAS) — thin layers only, legal

Each immutable layer is an OCI artifact. **For customer delivery we ship only our thin `+apps`/`+task-state` layers** (our IP) — never the macOS base. Because a VZ "layer" is a full CoW bundle on disk, "ship the layer" means **ship a zstd block-delta** against a pinned public base (E6): the artifact is `application/vnd.cua.layer.delta.v1+json` with `cua.layer.{role,os,arch,parent,format}` + `cua.layer.base.disk.sha256` annotations, proven to contain zero macOS bytes. The customer provisions its **own** base from the pinned public Tart image/IPSW and composes our delta over it via `clonefile`. The KVM ~15 GB base, when distributed *internally*, splits into 7–8 GiB sub-layers under registry caps. Ship plain zstd (oras has no `zstd:chunked`).

### 7.6 Runtime composition

`mw bench run --env <name> --tasks ...`: resolve env → `[target]` → OCI-index → per-platform chain; pull missing layers to a content-addressed cache; KVM `qemu-img rebase -u` to local paths (header-only) / VZ `clonefile` compose over the pinned base; hand the top immutable layer to `KvmFleet`/`VzMacOSEnv`, which creates the L3 instance and boots.

### 7.7 Reset & lifecycle

Per-rollout reset = discard + recreate the L3 instance — deterministic by construction, proven zero starting-grade variance on both substrates (E5). GC of unreferenced layers is content-addressed and trivial.

## 8. Build plan & productization to-dos

The experiment program (W0–W2, E1–E7) proved the design and left these productization tasks (each scoped + one-place):

1. **`mw env build/push/pull`** — promote the `infra/cli/env-build/{freeze-layer,vz-freeze-layer}.sh` scripts into the CLI; add `--emit-delta <pinned-base>` so a VZ freeze emits the shippable zstd block-delta (E6 carry-over).
2. **OCI index + registry** — wire `env/pkg.py`'s proven oci-layout resolution to a real registry (GHCR); the only delta from the local proof is the ref + `oras login`. Note: artifact-index children render as `unknown/unknown` in GHCR's UI (cosmetic).
3. **KVM per-clone serial regen** in `host.make_overlay_clone` (the KVM analog of VZ ECID-regen) — closes the E7 serial-collision training-blocker. Plus a root/chown `rm` in `host.remove_volume` (overlay-teardown leak).
4. **Bake Homebrew into the KVM os-base** so the author-once recipe's precondition is uniformly met (E2).
5. **3 grader fixes → 17/17 certified** (E4): the Calendar Group-Containers path on Sequoia, `system_profiler` `Chip:` vs `Processor Name`, and the headless-display checkpoint.
6. **Base-provenance pinning** in the composer (gate on `cua.layer.base.disk.sha256`) — E6.

## 9. Open questions — RESOLVED

| # | Question | Answer (experiment) |
|---|---|---|
| Q1 | Do x86 and arm chains round-trip at the byte level? | **No, by design** (different arch + macOS build). Interop is at the manifest/grade level, not bytes — and that is proven sufficient (E4). |
| Q2 | Does APFS `clonefile` share blocks across siblings like qcow2? | **YES** — ~0.07 s / 4–8 KiB per clone; CoW preserved through OCI-delta compose (W2, E6). |
| Q3 | Multi-platform layout: index vs sibling tags? | **Index** — standard OCI image-index carries both chains under one name; runtime via annotation (E1). |
| Q4 | VZ per-clone identity regen? | Regenerate ECID/`VZMacMachineIdentifier` per clone → distinct UUID/serial (E3). KVM analog: per-clone serial regen (E7). |
| Q5 | Cross-substrate build (one host builds both)? | **No** — each chain builds on its own substrate; "author once" = one recipe text, 5.3% per-arch fork (E2). |
| Q6 | Task-state "settle then freeze"? | Quiesce = `wal_checkpoint(TRUNCATE)` + graceful app quit before freeze → zero starting-grade variance; daemons don't perturb (E5). |
| — | macOS EULA / redistribution? | SLA §2(B)(iii): 2 VMs/host, Apple HW, dev/test/personal; **redistributing a pre-built macOS image and multi-tenant hosting are barred**; users build from IPSW. ⇒ ship thin layers only; customer provisions its own base (E6). |

---

## References

- Harbor / Terminal-Bench 2.0: <https://www.harborframework.com/docs>, <https://github.com/harbor-framework/harbor>
- KubeVirt containerDisk: <https://github.com/kubevirt/kubevirt/blob/main/containerimages/container-disk-images.md>
- Fedora bootc & ORAS: <https://oras.land/>, <https://github.com/containers/bootc>
- `zstd:chunked` (content-addressed OCI dedup): <https://www.redhat.com/en/blog/faster-container-image-pulls>
- QEMU qcow2 backing files: <https://qemu.readthedocs.io/>
- Tart (Cirrus Labs) + Fair Source license: <https://github.com/cirruslabs/tart>, <https://tart.run/licensing/>
- APFS `clonefile`: <https://developer.apple.com/documentation/kernel/clonefile>
- `VZVirtualMachine` save/restore: <https://developer.apple.com/videos/play/wwdc2023/10007/>
- macOS SLA (§2(B)(iii)): <https://www.apple.com/legal/sla/docs/macOSSequoia.pdf>
- Experiment deliverables: `docs/experiments/substrate/{savevm-probe,vz-vnc-probe,hybrid-scoping,mw-env-chain,vz-feasibility,E1-index,E2-author-once,E3-vzenv,E4-grade-identity,E5-determinism,E6-delivery,E7-fanout}.md`
- The committed substrate code: `infra/cli/benchmark/env/{base,pkg,kvm,vz}.py` and `infra/cli/benchmark/env/kvm/{host,fleet,config,rfb,ssh}.py`
