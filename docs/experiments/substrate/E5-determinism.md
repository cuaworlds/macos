# E5 — Task-state + reset DETERMINISM (interop link #5) — **VZ HALF**

**Lock:** LOCAL-MAC (Apple M4 Pro, macOS 15.7.3 Sequoia host, Virtualization.framework, Tart 2.32.1).
**Date:** 2026-06-16. **Interop link #5, arm64 half.** Builds on E3 (`VzMacOSEnv`, `e3-base`,
reset-by-discard), E4 (certified-portable task set + the Sequoia container-path facts), E2-VZ (the
`e2vz-apps` +apps base), the proven sqlite seeding recipes
(`explorations/validation/calendar-reminders-persistence.md`, `explorations/mining/candidate-trust.md`).

**Goal (the load-bearing RL claim).** A +task-state layer frozen after an explicit **quiesce** must give
**ZERO starting-grade variance** across N=10 fresh VZ instances AND across 10× reset-by-discard cycles —
so that a flaky starting grade can never be mistaken for a flaky verifier (which would poison GRPO: a
non-deterministic reward baseline collapses the advantage estimate). This is the **VZ half**; the KVM half
(E7/E5-KVM) is synthesized against it for cross-substrate parity.

**Task chosen:** `apple_suite/2b13e970` — **Reminders** "Submit Q3 expense report", due 2026-07-13 09:00.
Picked over the Calendar task (`92acd9b8`) because E4 certified `2b13e970` as **portable on Sequoia**
(`100 [40,30,30]`, agrees with KVM) whereas `92acd9b8`'s grader reads the **Sonoma** path
`~/Library/Calendars/` which does not exist on Sequoia (E4 path-drift finding). The Reminders store lives
at the **Sequoia-stable** Group Containers path on both versions, so the grader is byte-portable and the
seed/grade idiom is clean.

**Status:** INCREMENTAL — milestones appended as they land.

## D0 — preconditions (2026-06-16)

Part-1 left `e2vz-apps` (the frozen +apps layer registered as a clonable base) + `e3-base` intact, no
running VMs. E5 builds the +task-state layer on top of `e2vz-apps` (realistic full chain
`os-base ← +apps ← +task-state`). The Reminders grader is pure sqlite over `GuestSsh` (TCC-free, per the
persistence doc) — no daemon/consent wall needed for *grading*; the question E5 answers is whether the
*seeded store state* is deterministic across instances/resets.

## D1 — Sequoia Reminders store facts (the seed target)

Stores dir = **`~/Library/Group Containers/group.com.apple.reminders/Container_v1/Stores`** (the
Sequoia-stable path; matches Sonoma). Three stores on the cirruslabs base, all carrying
`ZREMCDREMINDER`:

| store | default list | reminders (clean) |
|---|---|---|
| `Data-85387F7F-…sqlite` | **"Reminders"** (`ZREMCDBASELIST.ZNAME='Reminders'`, `ZDADISPLAYORDER=1`) | 0 |
| `Data-AE355E30-…sqlite` | "SiriFoundInApps" (internal) | 0 |
| `Data-local.sqlite` | (no list) | 0 |

The default-list UUID (`85387F7F…`) is **base-image-specific** (the persistence doc warned: do not
hardcode); the *grader* scans all `Data-*.sqlite` and sums, so it is robust to the UUID. The *seeder*
targets the store that holds the default "Reminders" list (here `85387F7F`).

`ZREMCDREMINDER` schema (Sequoia): `Z_ENT=39` (`REMCDReminder` in `Z_PRIMARYKEY`); columns the grader
reads are `ZTITLE` (VARCHAR), `ZDUEDATE` (TIMESTAMP, Core-Data epoch), `ZMARKEDFORDELETION` (INTEGER).
Guest timezone = **UTC** (`+0000`), so the grader's `'localtime'` read == UTC.

## D2 — the deterministic seed (direct INSERT at the Sequoia path) + QUIESCE ✅

Chose a **direct sqlite INSERT** over a daemon/osascript create — for a *determinism* layer the seed must
be byte-reproducible and independent of TCC/daemon timing (E4 already validated daemon-create *grading*;
here we measure determinism of the frozen *state*, not create fidelity). Seed (`/tmp/e2e5/seed_reminder.sh`,
run over SSH):

```sql
DELETE FROM ZREMCDREMINDER WHERE ZTITLE='Submit Q3 expense report';   -- idempotent
INSERT INTO ZREMCDREMINDER (Z_ENT, ZTITLE, ZDUEDATE, ZMARKEDFORDELETION, ZCOMPLETED, ZLIST, ZFLAGGED, ZPRIORITY)
  VALUES (39, 'Submit Q3 expense report', 805626000, 0, 0, 1, 0, 0);
UPDATE Z_PRIMARYKEY SET Z_MAX=(SELECT MAX(Z_PK) FROM ZREMCDREMINDER) WHERE Z_ENT=39;
```

`ZDUEDATE = 805626000` = `unix(2026-07-13 09:00 guest-local/UTC) − 978307200` (Core-Data epoch). Verified
read: `Submit Q3 expense report | 2026-07-13 | 09:00 | not-deleted`.

**QUIESCE recipe (VZ):** quit Reminders (`killall Reminders; sleep 2`) so `remindd` isn't mid-write, then
`PRAGMA wal_checkpoint(TRUNCATE)` on **every** `Data-*.sqlite` → all three WALs truncated to **0 bytes**.
This pins the row into the main DB files so a fresh boot's WAL replay can't reorder/discard it
differently. **Pre-freeze grade through the harness `grade_checkpoints`:** `(100.0, 100.0, [40.0, 30.0,
30.0])` — matches E4's certified VZ triple for `2b13e970`.

**Daemon-perturbation pre-check (before committing to the freeze):** on the seeded instance, launched
Reminders (`open -gj`, wakes `remindd`), waited 15s, re-graded → still `(100.0, 100.0, [40,30,30])`, row
count=1, `ZMARKEDFORDELETION=0`. `remindd` wrote a small WAL but did **not** mutate the seeded row or flip
the grade. Encouraging, but the load-bearing test is across fresh boots (each replays launchd/cfprefsd/
remindd startup) — that is N=10 below.

## D3 — frozen +task-state layer (deliverable, kept) ✅

`tart stop e5-explore` (graceful, consistent disk, no leaked run proc) → `vz-freeze-layer.sh e5-explore
macos-jq-reminder-seeded task-state`:

- **Layer:** `~/.tart/_layers/c8aaf8c6c87b/` (read-only bundle).
- **Digest:** `sha256:c8aaf8c6c87b760d882e46aa72e89aac2bea9a68610c86e6eb83a049accbf1a9`
- **layer.json:** `role=task-state, os=macos, arch=arm64, format=raw, name=macos-jq-reminder-seeded,
  built=2026-06-16T06:39:30Z`. Chain: `os-base ← +apps (801695f74c0d) ← +task-state (c8aaf8c6c87b)`.
- Registered as a clonable base `~/.tart/vms/e5-taskstate` (CoW); `e5-explore` `tart delete`d.

## D4 — DETERMINISM: N=10 fresh instances, graded at t=0 (NO agent) ✅ ZERO VARIANCE

Serialized (2-VM cap): each iteration `tart clone e5-taskstate e5-det` → regen ECID → boot to SSH →
`grade_checkpoints` at t=0 (no agent, no pre_command) → stop + `tart delete` → next. Each on a distinct
VZ-NAT IP (distinct ECID/identity), each a **fresh cold boot** (full launchd/cfprefsd/remindd startup):

```
[fresh  1/10] ip=192.168.64.36  TRIPLE=(100.0, 100.0, [40.0, 30.0, 30.0])  wall=19.8s
[fresh  2/10] ip=192.168.64.37  TRIPLE=(100.0, 100.0, [40.0, 30.0, 30.0])  wall=24.0s
[fresh  3/10] ip=192.168.64.38  …                                          wall=15.2s
 … (4–9 identical) …
[fresh 10/10] ip=192.168.64.45  TRIPLE=(100.0, 100.0, [40.0, 30.0, 30.0])  wall=14.4s

distinct triples across 10 fresh instances: 1   →   (100.0, 100.0, (40.0, 30.0, 30.0))
ZERO-VARIANCE: True
```

**Every one of 10 fresh instances grades the identical triple `(100, 100, [40,30,30])` at t=0.** The
seeded Reminders row survives every cold boot bit-identically; starting-grade variance == 0.

## D5 — DETERMINISM: 10× reset-by-discard on ONE instance, graded each cycle ✅ ZERO VARIANCE

Drove the **productized `VzMacOSEnv.reset()`** (`base_vm=e5-taskstate`) — delete + re-clone from the
frozen +task-state base + regen ECID + reboot — grading via `env.grade()` (the same substrate-blind seam)
at t=0 after each reset, NO agent, pre_command skipped (so the grade reflects the FROZEN state, not a
pre_command that would delete the row):

```
[initial-boot] TRIPLE=(100.0, 100.0, [40.0, 30.0, 30.0])
[reset#1..#10] TRIPLE=(100.0, 100.0, [40.0, 30.0, 30.0])   (reset wall 12.6–18.3 s each)

distinct triples across initial + 10 resets (11 grades): 1  →  (100.0, 100.0, (40.0, 30.0, 30.0))
ZERO-VARIANCE: True
```

**All 11 grades identical.** Reset-by-discard reconstructs a bit-identical seeded store every cycle.
**Frozen base byte-immutable across the 10 resets** (clones never write through to the parent — E3 M4
confirmed; the registered `e5-taskstate` base disk.img/nvram.bin sha256 are stable post-test:
`disk.img=ff7c3772…`, `nvram.bin=4303e116…`). `env.close()` deleted the instance — no leaked VM/proc.

## D6 — DAEMON-PERTURBATION finding (the E5 kill criterion) — **does NOT fire** ✅

The plan's kill criterion: *if launchd/cfprefsd/remindd perturb the store non-deterministically post-boot
→ restrict +task-state to filesystem-only pre-state + runtime SSH seeding for store-bound.*

**Two probes, both negative (no perturbation):**
1. **Wake-the-daemon probe (D2):** launched Reminders (`open -gj`, wakes `remindd`) on the seeded
   instance, waited 15s, re-graded → unchanged `(100,[40,30,30])`, row count 1, not-deleted.
2. **Settle probe (D6):** a fresh instance over the +task-state layer, graded at **t=0** then again at
   **t=90s** (letting launchd/cfprefsd/remindd fully run after a cold boot) → **identical** triple both
   times; row count stayed 1; the Reminders WAL touched once early in boot (4152 B) then **stable**, never
   mutating the seeded row.

**Finding:** on Sequoia/VZ the background daemons do **NOT** perturb the seeded Reminders store
post-boot. The seeded row is durable in the main DB (quiesced via `wal_checkpoint(TRUNCATE)` before
freeze), and `remindd` reads it without rewriting/marking it. **The kill criterion does not fire** — a
store-bound +task-state layer IS deterministic on VZ for this store. The filesystem-only-pre-state +
runtime-SSH-seeding fallback is therefore **not required** for the Reminders store.

**Why the quiesce matters (the mechanism):** the WAL-checkpoint-TRUNCATE before freeze is load-bearing —
it pins the row into the main DB file so a fresh boot's SQLite WAL handling is a no-op rather than a
replay that could resolve differently. Had we frozen with a non-empty `-wal`, determinism would depend on
WAL-replay timing across boots (the exact risk the quiesce removes). This is the **same idiom as the KVM
half** (`PRAGMA wal_checkpoint(TRUNCATE)` on every store + a graceful app quit) — it ports unchanged; the
only VZ-specific differences are (a) the **path is the Sequoia-stable Group Containers path** (same as
Sonoma for Reminders — no drift here, unlike Calendar which moved, per E4), and (b) `tart stop` provides
the graceful guest quiesce that flushes the guest filesystem before the clonefile freeze.

## D7 — VZ DETERMINISM RESULT (for cross-substrate parity synthesis)

**VZ determinism HOLDS, unconditionally, for a quiesced store-bound +task-state layer.** Concretely, on
arm64 Sequoia 15.7.7 via Tart/`VzMacOSEnv`, the `2b13e970` Reminders +task-state layer gives:

| determinism axis | result |
|---|---|
| N=10 fresh instances, t=0 starting grade | **ZERO variance** — all `(100, 100, [40,30,30])` |
| initial + 10× reset-by-discard, t=0 each | **ZERO variance** — all `(100, 100, [40,30,30])` |
| frozen base byte-immutability over 10 resets | **immutable** (disk.img/nvram.bin sha stable) |
| daemon perturbation (launchd/cfprefsd/remindd) | **none** (t=0 == t=90s; wake-remindd unchanged) |
| quiesce idiom vs KVM | **identical** (`wal_checkpoint(TRUNCATE)` + graceful quit) |
| store path vs KVM | **same** Group Containers path (Reminders does NOT drift; cf. Calendar E4) |

**Cross-substrate parity statement (pairs with the KVM half from E7/E5-KVM):** the +task-state + reset
contract is **deterministic on VZ exactly as on KVM** — same quiesce idiom, zero starting-grade variance
across both fresh-instance and reset-storm axes, no daemon drift. The E5 link (determinism) **holds on the
VZ substrate**. A flaky starting grade is therefore NOT a confound on VZ: any grade variance an RL trainer
sees on this substrate is attributable to the agent/verifier, not to the reset/layer machinery. **No
kill-criterion fired** (daemons don't perturb; store-bound +task-state is viable on VZ — the
filesystem-only fallback is unneeded for this store).

**Caveat (honest scope):** proven for the **Reminders** store (sqlite/Core-Data, quiesced). Calendar would
need the **Sequoia Group Containers path** (E4 path-drift: `~/Library/Group Containers/group.com.apple.
calendar/`, not `~/Library/Calendars/`) for both seed and grade; the determinism *mechanism* (quiesce +
clonefile freeze) is store-agnostic and should carry, but each store's quiesce should be re-confirmed when
first used as a +task-state layer (cheap — this whole VZ determinism battery is ~15 min).

## D8 — VZ-leg hygiene (Part-2 / E5)

- **Created + removed:** `e5-explore` (seed/freeze VM, `tart delete`d after freeze); `e5-det` (the 10
  serialized fresh instances, each stopped + `tart delete`d in-loop); `e5-reset` (the reset-storm
  instance, `env.close()` deleted it); `e5-drift` (the daemon-settle probe, stopped + `tart delete`d). No
  leaked `tart run` procs (verified clean after each phase).
- **Kept (deliverable artifacts):** the +task-state layer `~/.tart/_layers/c8aaf8c6c87b` (+ `.meta`) and
  its registered clonable base `~/.tart/vms/e5-taskstate`; the E2 +apps layer `~/.tart/_layers/801695f74c0d`
  (+ `.meta`) and `~/.tart/vms/e2vz-apps`.
- **Untouched:** `e3-base` (kept intact per instructions); the OCI cache.
- Final disk + ~/.tart size + orphan report: see the combined cleanup below.

---

## FINAL CLEANUP + DISK REPORT (both E2-VZ Part 1 and E5 Part 2 on this host)

**VMs:** `tart list --source local` shows **only `e3-base`** (kept intact as the E3/E4/E5 deliverable
base) + the OCI cache (`ghcr.io/cirruslabs/macos-sequoia-base:latest` and its `@sha256:fdd8b72a…` pin).
All working VMs `tart delete`d: `e2vz-build`, `e2vz-inst`, `e2vz-apps` (base), `e5-explore`, `e5-det`
(×10), `e5-reset`, `e5-drift`, `e5-taskstate` (base). **No leaked `tart run` processes** (verified `ps`
clean after every phase).

**Kept artifacts (the deliverables):** the two content-addressed layer bundles in `~/.tart/_layers/`,
each with its `.meta/layer.json`:
- `~/.tart/_layers/801695f74c0d` — VZ **+apps** (jq 1.8.1 + VLC 3.0.23), `sha256:801695f74c0d…`, role=apps.
- `~/.tart/_layers/c8aaf8c6c87b` — VZ **+task-state** (Reminders `2b13e970` seeded), `sha256:c8aaf8c6c87b…`,
  role=task-state.

These are immutable, read-only, content-addressed; the registered clonable bases (`vms/e2vz-apps`,
`vms/e5-taskstate`) were derived from them via `cp -cR` (CoW) and deleted in cleanup — re-creatable from
the layers with one `cp -cR ~/.tart/_layers/<digest> ~/.tart/vms/<name>` when E1/E6 need them bootable.

**Orphans / pre-existing (NOT mine, left untouched):** two Virtualization.framework XPC singletons —
PID **21900** (since 20 May, W2/E3-documented) and PID **89350** (since 18:57, predates this run, also
E3-documented) — framework-level hosts holding **no** `.tart` disk open (`lsof` clean), not my guests.
Also `com.docker.vmnetd` (PID 806, since 13 May) — Docker helper, unrelated. **No VM/process leaks from
this run.**

**Disk:**
- **Free disk: 76 GiB** (`df -h /`; started this session at 83 GiB — the two kept layers' real CoW deltas
  + scratch consumed ~7 GiB total, since clonefiles share base blocks).
- `du -sh ~/.tart` reports **113 GiB**, but that **over-counts APFS CoW sharing** — `e3-base` (~30 GiB
  physical) and the two `_layers` clonefile bundles share most blocks; `du` bills each its full logical
  view. **Free disk (76 GiB) is the truthful figure**, not the `du` sum.

**Scratch (uncommitted, not a repo file):** `/tmp/e2e5/` (recipe copy, boot/seed/grade/determinism
drivers, result jsonls, tart run logs) — laptop-local helpers, not committed.

**Concurrency note:** `git status` also shows `M explorations/substrate/LEDGER.md` and `?? E7-fanout.md` —
those belong to the **concurrent KVM-box worker** (E7 + the KVM half of this same link #5); NOT mine, left
untouched. My edits are confined to the three allowed files (E2-author-once.md, E5-determinism.md,
envs/macos-jq/env.toml). `cd infra/cli && uv run --group dev pytest` → **90 passed, 2 skipped** (unchanged
from E3 — I added no source code). **No git commit performed** (orchestrator reviews).
