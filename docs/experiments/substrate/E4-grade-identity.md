# E4 — Cross-substrate GRADE IDENTITY (interop link #4, THE HEADLINE)

**Locks held:** BOTH — KVM-BOX (`ssh jjmachan@100.118.89.35`, NixOS) + LOCAL-MAC (M4 Pro,
Sequoia host, Tart). **Date:** 2026-06-15/16. **Builds on:** E3 (`VzMacOSEnv` first-class `Env`,
`e3-base` frozen), the proven SSH/sqlite SEEDING recipes (`explorations/mining/candidate-trust.md`,
`explorations/validation/calendar-reminders-persistence.md`), the substrate-blind grader
(`infra/cli/benchmark/grading.py` `grade_checkpoints`).

**Live guests this run (the exact version PAIR certified):**
- **KVM** = dockur/macOS **14.8.7 (Sonoma) x86_64**, build `23J520`, `iMacPro1,1` SMBIOS, 4 GB.
- **VZ** = cirruslabs `e3-base` macOS **15.7.7 (Sequoia) arm64**, build `24G720`, `VirtualMac2,1`,
  `Apple M4 Pro (Virtual)`, 8 GB. Booted `--no-graphics` (headless) via the E3 `VzMacOSEnv` boot path.

**Method (the critical principle).** We certify the **VERIFIER's** cross-substrate agreement, not agent
policy. For each task we **SSH-seed the EXACT correct end-state on each substrate** (agent removed as a
variable), run the task's `pre_command` then the `grading_command` through a byte-faithful copy of
`grade_checkpoints`, and compare the full grade **triple** `(score, max_score, per-checkpoint-credit-list)`.
Identical triple on both, deterministic across N=3 trials = the verifier agrees across substrates. The
seeders are *identical logic on both guests*; for version-self-adjusting tasks the seeder reads the LIVE
value on THAT guest (so it self-adjusts) — the triple agreeing across substrates is then the portability
proof. Store-bound tasks seeded via the **daemon path** (osascript create after a TCC grant for the
headless `sshd-keygen-wrapper`), exactly as the trust docs validated, so real schema/path drift is exposed
rather than papered over by a direct INSERT.

---

## ① n=1 GO/NO-GO — **PASS. M-INTEROP GATE OPEN.** ✅

The cheap decisive gate, run first. SSH-seed the exact correct artifact, grade the 3 (resp. 4) checkpoints,
N=3, on BOTH substrates:

| task | KVM/Sonoma triple | VZ/Sequoia triple | identical? |
|---|---|---|---|
| **`fc7d32bd`** (TextEdit → `~/Desktop/fruits.txt`, pure-POSIX) | `(100, 100, [30,30,40])` | `(100, 100, [30,30,40])` | ✅ **YES ×3** |
| **`e847156f`** (nested folders + `notes.txt`) | `(100, 100, [25,30,20,25])` | `(100, 100, [25,30,20,25])` | ✅ **YES ×3** |

**Verdict: the same task grades byte-identically on x86-Sonoma-KVM and arm64-Sequoia-VZ.** The M-INTEROP
milestone's grade-identity requirement is met. "Author once, ship either format" holds **for the
clean-POSIX class** — proven, not asserted.

---

## ② Battery — per-task triple on BOTH substrates (N=3, deterministic)

All 17 tasks: seed → grade ×3 on each substrate. **Every one of the 34 (17×2) runs was deterministic
across its 3 trials** (zero starting-grade variance from the seeded state) and **no seeder raised**. KVM
scored **100/100 on all 17**. Triples below are the (identical-across-3-trials) triple per substrate.

| task | W0c class | KVM (Sonoma) triple | VZ (Sequoia) triple | **agree** |
|---|---|---|---|:---:|
| `fc7d32bd` textedit-save | clean | `100 [30,30,40]` | `100 [30,30,40]` | ✅ |
| `e847156f` nested-folders | clean | `100 [25,30,20,25]` | `100 [25,30,20,25]` | ✅ |
| `22afcaf9` preview-export-jpeg | clean | `100 [40,40,20]` | `100 [40,40,20]` | ✅ |
| `ee0751c6` preview-rotate-png | clean | `100 [35,25,40]` | `100 [35,25,40]` | ✅ |
| `71fdb51d` stickies-from-textedit | clean | `100 [40,60]` | `100 [40,60]` | ✅ |
| `78073675` stickies-create | clean | `100 [50,50]` | `100 [50,50]` | ✅ |
| `03dfd972` system-snapshot-note | version | `100 [25,20,30,25]` | `100 [25,20,30,25]` | ✅ |
| `ab78364a` about-to-note | version | `100 [34,33,33]` | `100 [34,33,33]` | ✅ |
| `496ae7dc` display-report-note | version | `100 [25,25,25,25]` | `75 [25,25,`**`0`**`,25]` | ❌ |
| `be92dd7f` version-chip-to-textedit | version | `100 [20,40,40]` | `60 [20,40,`**`0`**`]` | ❌ |
| `92acd9b8` calendar-allday | store | `100 [35,35,30]` | `0 [`**`0,0,0`**`]` | ❌ |
| `2b13e970` reminders-due | store | `100 [40,30,30]` | `100 [40,30,30]` | ✅ |
| `00507a0d` file-name-to-reminder | store | `100 [45,30,25]` | `100 [45,30,25]` | ✅ |
| `d3697775` contacts-create | store | `100 [40,20,20,20]` | `100 [40,20,20,20]` | ✅ |
| `d98edd22` notes-two-notes | store | `100 [20,30,20,30]` | `100 [20,30,20,30]` | ✅ |
| `8eecaf26` dock-two-settings | settings | `100 [40,40,20]` | `100 [40,40,20]` | ✅ |
| `97b5eb42` screensaver-delay | settings | `100 [70,30]` | `100 [70,30]` | ✅ |

---

## ③ Cross-substrate AGREEMENT RATE per W0c class

`agree = (triple_KVM == triple_VZ)` exactly, AND deterministic on both.

| W0c class | agreement | tasks that agree |
|---|---|---|
| **clean-POSIX** | **6 / 6 = 100%** | `fc7d32bd e847156f 22afcaf9 ee0751c6 71fdb51d 78073675` |
| **version-self-adjusting** | **2 / 4 = 50%** | `03dfd972 ab78364a` (fail: `496ae7dc`, `be92dd7f`) |
| **store-bound** | **4 / 5 = 80%** | `2b13e970 00507a0d d3697775 d98edd22` (fail: `92acd9b8`) |
| **settings-bound** | **2 / 2 = 100%** | `8eecaf26 97b5eb42` |
| **TOTAL** | **14 / 17 = 82%** | — |

### Per-disagreement root cause (the diverging checkpoint + why)

1. **`496ae7dc` ck2 (Display Resolution, w25) — VZ scores 0.**
   Grader: `system_profiler SPDisplaysDataType | awk '/Resolution/…'`. On KVM/Sonoma this yields
   `Resolution: 1920 x 1080` → `1920x1080`. On the **headless VZ guest** (`tart run --no-graphics`)
   `SPDisplaysDataType` has **no display and no `Resolution:` line at all** → `RES` empty → ck2 false.
   **Root cause: headless-display absence (substrate/runtime), not a version-string drift.** A VZ guest
   booted with an attached display would report a resolution, but it would be the *VZ virtual panel size*
   (E3 saw 2048×1536 retina), not 1920×1080 — so even non-headless this checkpoint is display-config-coupled
   and would NOT match KVM's value. **Non-portable as written.**

2. **`be92dd7f` ck2 (Chip / Processor, w40) — VZ scores 0.**
   Grader: `PN=$(system_profiler SPHardwareDataType | awk -F': ' '/Processor Name/{print $2}')` then
   require the file's `Chip:` line to contain `$PN`. On KVM/Sonoma (Intel) About shows
   **`Processor Name: Quad-Core Intel Core i5`** → `PN` non-empty → match. On VZ/Sequoia (Apple Silicon)
   there is **no `Processor Name:` line — Apple Silicon reports `Chip: Apple M4 Pro (Virtual)`** →
   `PN` empty → the `grep -qiF "$PN"` (empty needle) plus the `[ -n "$PN" ]` guard → ck2 false.
   **Root cause: `system_profiler` LABEL drift Intel→Apple-Silicon (`Processor Name` vs `Chip`).** This is
   the same `system_profiler`-string risk the W0c hybrid-scoping doc flagged. **Non-portable as written;**
   fixable by reading `Chip:` OR `Processor Name:`.

3. **`92acd9b8` Calendar all-day — VZ scores 0 on ALL 3 checkpoints.**
   The seeder DID create the correct event on VZ (verified: 3 correct `all_day=1, date=2026-08-21` rows,
   `Store.type=0` join clean). The grader reads `~/Library/Calendars/Calendar.sqlitedb` — **which does not
   exist on Sequoia.** On Sequoia the Calendar store moved to
   **`~/Library/Group Containers/group.com.apple.calendar/Calendar.sqlitedb`**. Pointed at the Sequoia
   path, the grader's exact SQL returns `count=1, all_day=1, date=2026-08-21` → it would score
   `100 [35,35,30]`, *matching KVM*. **Root cause: container-PATH drift Sonoma→Sequoia. The SQL logic,
   `CalendarItem` schema, `Store.type=0` join, and `+978307200` epoch are byte-identical across versions —
   ONLY the hardcoded store path differs.** **Non-portable as written;** trivially fixable by probing both
   paths.

**None of the three disagreements is a SQLite-schema drift or epoch/localtime drift.** They are: one
display-absence (runtime), one `system_profiler` label drift (Intel↔ARM), one container-path move
(Sonoma↔Sequoia). All three are in checkpoints that read **version/arch-coupled host chrome or a moved
path**, never the core grading logic.

---

## ④ Skew probe — direct A/B across the two live guests (the load-bearing risk)

Probed identical commands on both live guests. **Does the verifiers' `pragma_table_info` column-probing
hold, or paper over real drift?** — It **HOLDS**; there is **no SQLite schema drift** in the probed stores.

| probe | KVM / Sonoma 14.8.7 x86 | VZ / Sequoia 15.7.7 arm64 | match? |
|---|---|---|---|
| `sw_vers -productVersion` / build | `14.8.7` / `23J520` | `15.7.7` / `24G720` | n/a (self-adjusting) |
| `sysctl hw.model` = About Model Id | `iMacPro1,1` | `VirtualMac2,1` | n/a (grader reads live) |
| `system_profiler` processor line | **`Processor Name:` Quad-Core Intel Core i5** | **`Chip:` Apple M4 Pro (Virtual)** | ❌ **LABEL DRIFT** |
| `SPDisplaysDataType` Resolution | `1920 x 1080` | *(none — headless)* | ❌ absent |
| **Calendar `CalendarItem` schema** | `summary,start_date,all_day,calendar_id,…` | **identical column set** | ✅ |
| **Calendar store PATH** | `~/Library/Calendars/Calendar.sqlitedb` | **`~/Library/Group Containers/group.com.apple.calendar/…`** | ❌ **PATH DRIFT** |
| Calendar epoch `+978307200` (UTC + localtime) | `2026-08-21` | `2026-08-21` | ✅ |
| Calendar `Store.type=0` (local) join | works | works | ✅ |
| **Reminders `ZREMCDREMINDER` title col** | `ZTITLE` (all 3 stores) | `ZTITLE` | ✅ |
| Reminders scan-all `Data-*.sqlite` + epoch | due `2026-07-13 09:00` | due `2026-07-13 09:00` | ✅ |
| **Contacts `ABCDContact` entity** | `Z_ENT=22` | `Z_ENT=22` | ✅ (17=`ABCDRecord` parent on both) |
| Contacts seeded record `Z_ENT` | 22 | 22 | ✅ |
| Notes body (HTML-div + nbsp normalize) | matches | matches | ✅ |
| `defaults` domains (dock/screensaver) | `autohide/orientation/idleTime` | same keys, same values | ✅ |

**Verdict on the hedging:** the `pragma_table_info('ZREMCDREMINDER')` title-column probe and the
`Store.type=0`/`Z_ENT=22` discriminators are **genuinely stable** Sonoma↔Sequoia — they are NOT papering
over schema drift, because there is no schema drift to paper over (the SQLite table definitions match). The
real cross-version risks are **outside** the SQL: a **container-path move** (Calendar) and **host-chrome
string/label drift** (`system_profiler` Processor→Chip, display resolution). The verifiers do not hedge
those, and that is exactly where the 3 disagreements land.

---

## ⑤ CERTIFIED-PORTABLE task set (identical triple, N=3, both substrates)

**14 / 17 tasks** are certified portable across the **(Sonoma 14.8.7 x86-KVM ↔ Sequoia 15.7.7 arm64-VZ)**
pair — identical `(score, max_score, per-checkpoint-credit-list)` triple, deterministic ×3 on both:

```
CLEAN-POSIX (6/6):  fc7d32bd  e847156f  22afcaf9  ee0751c6  71fdb51d  78073675
VERSION (2/4):      03dfd972  ab78364a
STORE (4/5):        2b13e970  00507a0d  d3697775  d98edd22
SETTINGS (2/2):     8eecaf26  97b5eb42
```

NOT certified (verifier non-portable as written, each with a 1-line fix): `496ae7dc` (display chrome /
headless), `be92dd7f` (`system_profiler` Processor→Chip label), `92acd9b8` (Calendar container path moved).

---

## ⑥ VERDICT — does grade-identity HOLD?

**It holds STRONGLY for the clean-POSIX + settings classes (8/8 = 100%), and broadly (14/17 = 82%) across
the battery — with three precisely-diagnosed, individually-fixable verifier defects, none of which is a
deep schema or epoch incompatibility.**

- **The headline survives for the clean class:** "author once, ship either format, grade identically" is
  **PROVEN** for clean-POSIX (the M-INTEROP set `fc7d32bd`/`e847156f` is the proof). The OSWorld-Verified
  posture applies: the **3 misses are evaluator bugs, not substrate kills** — "fix the evaluator, not the
  task." After three one-line grader fixes (probe both Calendar paths; read `Chip:` OR `Processor Name:`;
  drop or generalize the display-resolution checkpoint) the projected agreement is **17/17**, because the
  *underlying seeded state matched on both substrates in every case* — every miss was the grader reading a
  moved path / renamed label / absent display, never the agent's artifact being wrong.

- **The load-bearing risk is REAL but BOUNDED and NOT in SQLite.** The plan's "highest risk" was silent
  SQLite-schema drift breaking store-bound grade identity. **That did not happen:** `CalendarItem`,
  `ZREMCDREMINDER` (`ZTITLE`), `ABCDContact` (`Z_ENT=22`) schemas + the `+978307200` epoch are
  byte-stable Sonoma↔Sequoia, and 4/5 store-bound tasks agree. The one store miss is a **path move**, not a
  schema change — caught and fixable. The remaining cross-version risk is concentrated in **host-chrome
  strings** (`system_profiler` Processor/Chip, display resolution) — i.e. exactly the
  version-self-adjusting class, which is where 2/4 missed.

- **Pin a tested macOS-version PAIR.** Grade-identity is certified for the specific pair
  **(14.8.7-x86 ↔ 15.7.7-arm64)**. Because the misses are arch/version-coupled (Intel `Processor Name`,
  Sequoia container path), the spec should **declare the certified macOS-version pair**, not claim "any
  macOS"; cross-pair portability must be re-certified when either base version bumps (cheap — this whole
  battery is ~25 min/substrate).

**Bottom line for the program:** **M-INTEROP achievable.** Author once → grade identically on both
substrates is real today for the clean-POSIX set, and reaches 17/17 with three trivial evaluator fixes. The
strategic unlock stands: certify against a pinned macOS-version pair, ship the certified-portable set, and
treat host-chrome-coupled checkpoints (display/processor strings, moved container paths) as the explicit
maintenance surface — they are the *only* place the substrates disagree, and they disagree *visibly and
deterministically*, never silently.

---

## Box + Mac hygiene

- **KVM box:** the single overlay clone container `e4-kvm` + its overlay `runs/../e4-kvm-vol` removed; the
  shared `_base_qcow2/14/data.qcow2` and gold `base/14/data.img` (42949672960 B, May 28 — unchanged) never
  written. Only pre-existing orphans remain: the two 4-week-old `Exited (255)` containers
  (`odoo-review-analytic_cost_allocation`, `fix-git__pzkdyqs-main-1`) — NOT ours, left untouched.
  `/tmp/e4` driver scratch left on the box (uncommitted helpers).
- **Local Mac:** VZ instance `e4-vz` (and any `e3-inst*`) `tart delete`d; `tart list` shows only **`e3-base`**
  (kept intact as the E4/E5 deliverable artifact, per instructions) + the OCI cache. No `tart run` procs
  leaked. `/tmp/e4` scratch local (uncommitted).
- **No git commit performed** (orchestrator reviews). Final free disk reported in the worker summary.
