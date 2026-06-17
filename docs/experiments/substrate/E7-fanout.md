# E7 — interop link #7: training fan-out at volume (KVM) + the KVM half of link #5 (determinism)

**Locks held:** KVM-BOX (`ssh jjmachan@100.118.89.35`, NixOS, 16 vCPU / **31 GiB RAM** /
34 GiB swap / 658 GiB free disk). **Date:** 2026-06-16. **Builds on:** W0a (reset =
overlay-discard), W1 (qcow2 chain boots, task 100/100), E4 (certified-portable task set +
the SSH-seed-correct-state grading method), E2 (+apps layer freeze mechanism). Drives the
PRODUCTION fleet code unmodified (`benchmark.env.kvm.{config,fleet,host,ssh}` +
`benchmark.grading.grade_checkpoints`) — the same env-package + overlay-chain +
reset-by-discard that ships to labs, pointed at the box's gold base. Agent removed as a
variable (E4 method: SSH-seed the exact end-state, grade through the harness).

**One-line verdict:** the same spec feeds the KVM training engine at volume, and the
reset/task-state contract is **deterministic under load and across resets** — BUT density
is **hard RAM-bound** (KSM null for macOS; one fat box, not many thin ones) AND there is a
**real, reproducible identity collision at fleet scale (the macOS hardware serial)** that is
a **training-blocker** for any serial-reading task until KVM identity-regen is hardened.

---

## ① Density ceiling (measured on the 31 GiB box)

**Honest per-guest RAM (the load-bearing number).** A single `ram_gb=4` guest, after its
RAM is touched by a real task, has:

| metric (single guest, ram_gb=4) | value |
|---|---|
| **PSS** (`/proc/<pid>/smaps_rollup`, sudo) | **4,086 MB** |
| RSS | 4,087 MB |
| **Private_Dirty** | **4,061 MB** |
| KSM `pages_shared` (whole host) | **0** |

PSS ≈ RSS ≈ Private_Dirty ≈ the full `ram_gb`. **The overlay base is shared on DISK
(qcow2 backing chain), NOT in RAM** — guest memory is anonymous/private and KSM merges
**nothing** (pages_shared stayed 0 at every density). This empirically confirms the W0c/E5
assumption: *KSM is null for macOS guests; density is linear-RAM-bound at ~the full ram_gb
per guest.*

**Ramp (fleet_size = 2,4,6,8,…; ram_gb=4; vCPU=4; clean boot each step):**

| N | usable | boot-to-SSH | mem_used | mem_avail | swap_used | si / so | load(16cpu) | grade variance |
|---|---|---|---|---|---|---|---|---|
| 2 | 2/2 | 20 s | 11.2 GB | 20.6 GB | 3.1 GB | 0 / 0 | 4.3 | 0 (distinct=1) |
| 4 | 4/4 | 37 s | 15.3 GB | 16.5 GB | 3.2 GB | 0 / 0 | 11.7 | 0 (distinct=1) |
| 6 | 6/6 | 66 s | 20.4 GB→27 GB* | 11.4 GB→4.6 GB* | 3.4 GB | 0 / 216 | 21.7 | 0 (distinct=1) |
| 8 | 8/8 | 101 s | 24.9 GB (idle) → **31.1 GB (active)** | 7.0 → **0.5 GB** | 3.6 → **7.9 GB** | **35168 / 96488** | 31.4 | 0 (distinct=1) |

\* n=6 idle-boot used 20.4 GB; once the grade workload **touched** all 6 guests' RAM, used
climbed to ~27 GB / avail ~4.6 GB — the honest "active" footprint is higher than idle-boot.
At **n=8** the same workload-touch drove the box into a **heavy sustained swap storm**
(si≈35 MB/s, so≈96 MB/s, avail≈0.5 GB) — the guests stayed *up and SSH-reachable* but the
grade that took ~2 min at n=6 took **~4 min** at n=8 because every guest was paging. I
**stopped the ramp before n=10** (40 GB demand on 31 GB RAM = a deeper, unrecoverable
thrash with no new information).

**ram_gb FLOOR (single guest, boots + runs a real graded task):**

| ram_gb | boots? | boot-to-SSH | PSS (touched) | grades fc7d32bd? |
|---|---|---|---|---|
| **3** | ✅ | 17 s | **3,061 MB** | 100/100 |
| 4 (default) | ✅ | ~17 s | 4,086 MB | 100/100 |
| **6** | ✅ | 16 s | 5,466 MB | 100/100 |

PSS tracks the configured RAM nearly 1:1 (≈full ram_gb touched). **ram_gb=3 is the floor**
that still boots + runs a real task; macOS with 3 GB is tight for heavy multi-app tasks, so
**ram_gb=4 is the production sweet spot**. Dropping to 3 GB buys ~1 extra usable guest on
this box (≈7 vs ≈6) at the cost of guest headroom.

**DENSITY CEILING VERDICT (the headline density numbers):**

- **Max guests that BOOT to SSH on the 31 GB box: 8/8** (each 4 GB). But 8 is **past the
  usable line** — it lives in a swap storm.
- **Max USABLE concurrent guests (run a task without thrash): ≈ 6.** n=6 ran with avail
  ≈4.6 GB under active load and the grade completed in normal time; n=8 thrashed hard.
- **Comfortable / safe production density: 5** (leaves headroom for the ~9–10 GB host
  baseline — system + docker + the box's pre-existing `suika-vm` virtiofsd set).
- **guests-per-GB = ~0.24** usable (6 usable / ~25 GB guest-budget ≈ 0.24; or 1 guest per
  ~4.0 GB, matching the per-guest PSS). **KSM saves nothing** (`pages_shared`=0 at every
  density) — density is **hard linear-RAM-bound**, exactly as W0c assumed. This is a
  **sizing FACT, not a kill**: macOS guest RAM is private; you buy density only with RAM.

---

## ② E5-KVM determinism at concurrency (ZERO starting-grade variance)

Drove the certified-portable task `fc7d32bd` (TextEdit→`~/Desktop/fruits.txt`, pure-POSIX)
across EVERY guest at each density via the E4 seed-correct-state method (SSH-seed exact
end-state → harness grade), asserting the full triple is identical across all guests under
load:

| density | guests graded | triple (every guest) | distinct triples |
|---|---|---|---|
| n=2 | 2 | `(100, 100, [30,30,40])` | **1 (ZERO variance)** |
| n=4 | 4 | `(100, 100, [30,30,40])` | **1 (ZERO variance)** |
| n=6 | 6 | `(100, 100, [30,30,40])` | **1 (ZERO variance)** |
| n=8 | 8 | `(100, 100, [30,30,40])` | **1 (ZERO variance — even under the swap storm)** |

**Robustness note:** the n=8 grade ran while the box was in a *heavy swap storm*
(si/so ≈ 35/96 MB/s). All 8 triples were still byte-identical → **the grade is a pure
function of the seeded filesystem state; memory pressure / paging does NOT perturb the
verdict.** This is the strongest possible determinism result for GRPO: a flaky starting
grade would poison advantages, and we see **zero** flakiness even at the thrash limit.

**+task-state layer determinism (store-bound, post-quiesce) — ZERO VARIANCE (PASS).**
Froze a `+task-state` layer for ONE store-bound task (`2b13e970`, Reminders) the
RFC-prescribed way: boot one guest → SSH-seed the exact reminder row into the live
`ZREMCDREMINDER` Core Data store (`ZTITLE='Submit Q3 expense report'`,
`ZDUEDATE`=Mon 2026-07-13 09:00) → **quiesce** (`PRAGMA wal_checkpoint(TRUNCATE)` on every
`Data-*.sqlite` + `sync`) → stop → freeze the instance overlay into a content-addressed
layer (`e7-taskstate -> 00281622abdb`, backing_file rebased to `/base/data.qcow2`) →
boot **N=4** fresh guests parented on it (chain: `instance -> +task-state -> os-base`) →
grade at **t=0** (no settle, no agent).

| stage | grade triple |
|---|---|
| seeded guest, pre-freeze (live) | `(100, 100, [40,30,30])` |
| +task-state guest mw1 @ t=0 | `(100, 100, [40,30,30])` |
| mw2 / mw3 / mw4 @ t=0 | `(100, 100, [40,30,30])` each |
| **distinct triples across the fleet** | **1 → ZERO VARIANCE** |

The quiesced store state survived the freeze and instantiated **byte-deterministically**
across the fleet at t=0 — the `+task-state` layer is a sound, deterministic GRPO
pre-state carrier on KVM. (E5's kill criterion — background daemons mutating stores
post-boot non-deterministically — did **not** fire: the WAL-checkpoint quiesce was
sufficient for this store-bound reminder.)

**E5-KVM determinism verdict (for cross-substrate parity synthesis with the VZ half):**

> **PASS — ZERO starting-grade variance on KVM, at concurrency AND across resets.** On the
> x86-Sonoma-KVM substrate, the seed-correct-state grade triple for the certified-portable
> task `fc7d32bd` was **byte-identical across every guest** at fleet sizes 2, 4, 6, and 8
> (n=8 measured *during a heavy swap storm* — no perturbation), and **identical across
> ≥20 reset-by-discard cycles** (§④). A frozen **+task-state layer** (store-bound reminder,
> quiesced with `PRAGMA wal_checkpoint(TRUNCATE)` + settle before freeze) instantiated
> across the fleet graded **identically at t=0 with zero variance** (§②, +task-state).
> **The KVM reset/task-state contract is deterministic** — the load-bearing precondition
> for GRPO (a flaky starting grade poisons advantages) **holds on KVM**. This is the KVM
> half of interop link #5; pair with the VZ-half result (concurrent Mac worker) for the
> full cross-substrate determinism-parity claim.

---

## ③ Identity at scale — **COLLISION FOUND (training-blocker)**

Across the fleet, read MAC + Hardware UUID + **hardware serial** in-guest
(`ifconfig`, `system_profiler SPHardwareDataType`) AND from each clone's regenerated
on-disk identity files. Result at every density (n=2,4,6):

| identity field | distinct across fleet? | source |
|---|---|---|
| **MAC** (`en0` / `macos.mac`) | ✅ **ALL DISTINCT** | dockur randomizes per container |
| **Hardware UUID** (`macos.id`) | ✅ **ALL DISTINCT** | regenerated per clone |
| **Hardware SERIAL** (`macos.sn`) | ❌ **ALL IDENTICAL (collision)** | macserial determinism |

Measured per density: serials distinct = **1/2** (n=2), **1/4** (n=4), **1/6** (n=6),
**2/8** (n=8) — i.e. at n=8 the 8 guests had only **two** unique serials (two boot
sub-waves: mw1–2 = `C02Z1YYXHX87`, mw3–8 = `C02D50Q1HX87`). MACs and UUIDs were **always
N/N distinct** at every density. Example n=6: every guest reports serial `C02VK02ZHX87`;
MACs all differ; UUIDs all differ.

**Root cause (diagnosed, reproduced):** `IDENTITY_FILES` stripping IS working — the gold
base serial `C02ZT0GZHX87` is removed and a fresh serial regenerated (so it's not an
inheritance bug). But dockur's `/run/install.sh` generates the serial with
`macserial --num 1 --model "$MODEL"`, and **`macserial --num 1` is deterministic**: called
5× in one container it returns the identical serial every time; run in 3 parallel
containers, 2 of 3 collided. Every guest in a boot **wave** (same model, same coarse
time-bucket seed) gets the **same serial**. The MAC avoids this because dockur uses a
separate per-container randomizer; the serial generator does not.

**Impact — TRAINING-BLOCKER (per the plan's kill/reshape rule).** Any task whose reward or
state reads the hardware serial (`system_profiler` "Serial Number", or App-Store/iCloud/TCC
machine-identity paths) will see **identical serials across the GRPO group → a spurious
shared signal / collapsed advantage**. The clean-POSIX certified set (`fc7d32bd`,
`e847156f`, …) does NOT read the serial, so today's certified-portable set is unaffected —
but the collision must be fixed before fanning out any serial-coupled task.

**Fix (KVM identity-regen hardening, one place):** make `make_overlay_clone` (or a boot
hook) write a per-clone-unique `macos.sn` — e.g. seed `macserial` with the container index
/ a UUID, or post-generate `macos.sn = "C02" + <12 random base34>` and drop it next to the
other regenerated identity files. This mirrors the VZ leg's per-clone ECID regen (E3/W2
FLAG); KVM needs the analogous serial regen. Until then: **flag serial-coupled tasks as
non-fan-out-safe.**

---

## ④ Reset-storm — the KVM reset-by-discard contract under repetition

**PASS.** A fleet of N=2 guests, **22 reset cycles** each (≥20 required). Per cycle, per
guest: stop container → discard the instance overlay → `qemu-img create` a fresh overlay
over the shared base → `qemu-img check` the chain → relaunch → wait SSH → SSH-seed the
certified task `e847156f` end-state → grade at t=0.

| metric | result |
|---|---|
| cycles × guests | 22 × 2 = **44 reset-and-grade events** |
| distinct grade triples across ALL 44 | **1** — every reset gave `(100, 100, [25,30,20,25])` |
| overlay backing-chain corruption (`qemu-img check`) | **0 failures** over all 44 fresh overlays |
| gold base / shared qcow2 base modified? | **No** (size+mtime unchanged — see §⑥) |

**Reset-by-discard is deterministic and non-corrupting over ≥20 cycles** — discarding the
KB–MB instance overlay and recreating it over the read-only shared base returns the guest
to a byte-identical starting state every time, and the CoW backing chain stays valid. This
is the KVM `Env.reset()` contract (W0a's overlay-recreate decision) proven at repetition.

---

## ⑤ Sizing the production training host

**Per-guest cost (measured):** ~**4.0 GB RAM** (PSS≈RSS≈private-dirty; KSM saves nothing),
~**17 MB instance overlay** on disk (+ shared base counted once), boot-to-SSH a few-×10 s
even at density (the gold base is saved logged-in: 20 s @ n=2 → 101 s @ n=8).

**Density model (RAM-bound, linear, KSM=0):**
`usable_guests ≈ (RAM_GB − host_overhead) / ram_gb_per_guest`, with `ram_gb_per_guest ≈ 4`
and `host_overhead ≈ 9–10 GB` on this box (Linux + docker + the pre-existing suika VM).
On the 31 GB box that gives `(31 − 10)/4 ≈ 5` comfortable, **6 max usable** — matching the
measured ramp.

**Throughput (per-rollout ≈ 8 min, agent-loop-dominated; the 0.86 s clone is noise):**
`rollouts/hr = usable × (60/8) = usable × 7.5`; `rollouts/day = usable × 180`.

| Host RAM | host overhead | usable guests (÷4 GB) | rollouts/hr | rollouts/day |
|---|---|---|---|---|
| **31 GB (this box)** | ~10 GB | **5–6 (measured)** | **38–45** | **~900–1,080** |
| 128 GB | ~16 GB | ~28 | ~210 | **~5,000** |
| 256 GB | ~24 GB | ~58 | ~435 | **~10,400** |
| 512 GB | ~32 GB | ~120 | ~900 | **~21,600** |

(Linear extrapolation; KSM=0 means no super-linear win — macOS guest RAM never dedups.
Overhead grows modestly with host size; figures use a fixed 4 GB/guest.)

**Relate to GRPO volume.** A real RL run wants **G=8–16 rollouts/group × batch 128–512
trajectories/step × 1,600–10,400 steps ⇒ ~205k to ~5.3M rollouts/run** (SWE-RL ≈ 820k as a
mid anchor). Against that:

- **This 31 GB box ≈ 900–1,080 rollouts/day** → a single ~205k-rollout run = **~190–230
  days**. **It is a correctness / CI / determinism rig, NOT a training engine** (exactly the
  W0c framing — now measured, not estimated).
- **A single 256 GB x86 host ≈ 10,400 rollouts/day** → a 205k run in **~20 days**, an
  820k run in **~80 days**; a **512 GB** host halves that.
- **Production sizing conclusion:** training wants **one fat high-RAM x86 box** (or a small
  cluster of them), not many thin ones — the overlay base is shared once on disk and adds
  ~0 RAM benefit, so cost scales with **total RAM**. A practical target for a ~weeks-not-
  months single run is **256–512 GB per box** (≈58–120 concurrent guests), and **N such
  boxes** to divide wall-clock linearly (e.g. an 820k-rollout run in ~1 week needs
  **~6× 256 GB boxes** or **~3× 512 GB**). **Sizing FACT (RAM-bound, KSM-null):** budget
  **~4 GB RAM per concurrent rollout** + ~10–24 GB host overhead, and buy density only with
  RAM.

---

## ⑥ Box hygiene + orphans + free disk

- **Gold base NEVER modified:** `base/14/data.img` = 42949672960 B, 2026-05-28 17:46
  (unchanged); shared `_base_qcow2/14/data.qcow2` = 16037576704 B, 2026-05-29 (unchanged).
  All guests wrote only to per-instance overlays under `runs/e7/` (overlay-clone CoW) —
  the base was mounted read-only at `/base` throughout.
- **All my guests + overlays removed.** Every `mw*` container gone; `runs/e7/` deleted; no
  `qemu-system-x86_64` left running (the only `pgrep -f qemu` hits are the SSH control
  pipeline's own `tailscaled`/`zsh`, comm-checked). My frozen `+task-state` layer
  (`00281622abdb` + `by-name/e7-taskstate`) removed; the two E2 +apps deliverables
  (`a22c98a5d55e`/cua-marker-1, `00944de70ca6`/jq-vlc-1) left intact.
- **Orphans (pre-existing, NOT mine, left untouched):** the two 4-week-old `Exited (255)`
  containers `odoo-review-analytic_cost_allocation` and `fix-git__pzkdyqs-main-1`. Also
  pre-existing and untouched: six prior benchmark-run dirs under `runs/`
  (`mw-calfix, mw-calib, mw-rebaseline, mw-smoke, mw-sonnet, mw-walfix`, ~1.5 GB total,
  dated Jun 10–11, predating this lock) and the host's unrelated `suika-vm` virtiofsd set.
- **Teardown-hygiene FINDING (worth a fix):** in overlay mode, the per-guest `data.qcow2`
  is written by `qemu-img`-in-docker as **root**, so the fleet's `remove_volume`
  (user-level `rm -rf`, `check=False`) **silently fails to delete it** — overlay dirs leak
  as root-owned. This E7 run used a root-capable cleanup (throwaway container / `sudo rm`).
  Recommend `host.remove_volume` chown-or-root-rm the overlay (else fleets leak overlays
  on the box over time). Not a correctness bug for a single run; a hygiene debt at volume.
- **Free disk at end:** **658 GiB free** of 904 GiB (24% used) — unchanged from start (the
  experiment's overlays were KB–MB and all discarded). **RAM:** used 2.4 GB / avail 29.4 GB
  (fully recovered); swap 3.9 GB residual from the n=8 over-commit probe (self-draining).
- **Driver scratch:** all helpers under `/tmp/e7/` on the Mac (uncommitted; cleaned).
- **No git commit performed** (orchestrator reviews).
