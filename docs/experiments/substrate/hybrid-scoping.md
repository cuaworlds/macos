# W0c — Hybrid-scoping: boundary + cost envelope

**Worker:** W0c (pure analysis, no hardware, no lock).
**Strategy is DECIDED — Hybrid (C).** This doc does not re-litigate the fork; it scopes the boundary so W1/W6/W7 are aimed correctly.

**One-line routing rule:** `training rollouts → x86-KVM` (cheap dense fan-out) · `eval + lab-delivery → arm64-VZ` (legal, the lab's real hardware). One declarative env package + one OCI manifest index targets both; bytes never port (x86-macOS⟺KVM, arm64-macOS⟺VZ, hard-welded by Apple's boot lock).

**Headline output (for W6):** the best portability-proof task is **`fc7d32bd-c73f-45d1-9aa3-91f9f8a4fd76`** (TextEdit → save `~/Desktop/fruits.txt`). Grade is pure POSIX `grep` over a file, no Apple-app store, no System-Settings pane, no version chrome. It grades byte-identically on x86-Sonoma-KVM and arm64-Sequoia-VZ. Runner-up: `e847156f` (nested-folder + `notes.txt` content check). See §2.

---

## 1. Routing boundary — concretely, for OUR artifacts

| Our artifact (this session) | Route | Why |
|---|---|---|
| **RL training rollouts** (high-volume fan-out, millions of resets) | **KVM** | Legality and macOS-version don't matter for gradient signal; you need cheap mass deterministic resets. This is the only workload that justifies the density engine (W1/W4/W7). |
| **The 20 mining tasks** (`tasks/mining/*.json`) — as an **eval suite** | **VZ** | This is the "sellable to Yutori" artifact: it must run on the lab's real Apple Silicon hardware, on the macOS version they ship, EULA-clean. Low concurrency (100 rollouts for 20×5), fits the 2-VM cap comfortably. |
| **The 20 mining tasks** — as **training-reward sources** | **KVM** | If/when these same tasks drive RL reward at volume, they fan out on KVM. Same task JSON, different substrate — that's the whole point of the portable package. |
| **n1.5 / Sonnet calibration runs** | **VZ** (for the delivery story) | Calibration is eval: you're measuring a fixed policy to report a number to a customer. It belongs on the substrate the customer trusts. (Internally you may *also* calibrate on KVM for speed; the *reportable, sellable* calibration is the VZ one.) |
| **`apple_suite/*.json`** (Calendar/Reminders/Notes) | **VZ** for eval, **KVM** for training | Same dual rule. These lean on Apple first-party app stores (sqlite), which exist on both substrates. |

**Concrete read:** everything we built this session is fundamentally *eval-shaped* (a graded task suite + calibration). For the **Yutori story it routes to VZ**. KVM's job is the thing we have *not* built yet — the high-volume RL training fan-out — and that is what W1/W4/W7 exist to enable. The mining-task JSONs are substrate-blind; only the *intent* (reward-at-volume vs reportable-eval) decides the route.

---

## 2. Portable-task fraction (sets W6) — THE KEY OUTPUT

I classified all 26 verifiers (20 mining + 6 apple_suite). **Critical finding: there is no pixel/screenshot/OCR grading anywhere — every grade is shell over SSH** (`grep -lE "screencapture|ocr|pixel..." → NONE`). So the portability axis is *not* "pixel vs scripted"; it is **version-agnostic-shell vs version/store/app-coupled-shell.**

Classification key:
- **PORTABLE** = grade is POSIX file/content check, or a self-adjusting `sw_vers`/`system_profiler` comparison, that yields the same verdict on x86-Sonoma-KVM and arm64-Sequoia-VZ *given the same app behavior*.
- **STORE-BOUND** = grade reads an Apple first-party app's sqlite/plist store (Reminders/Calendar/Notes/Contacts/Stickies). Portable *in principle* (same store schema on both), but **at risk** if the `ZREMCDREMINDER`/Calendar schema or container path differs across macOS versions. The verifiers already hedge (`pragma_table_info` column probing), which is exactly the schema-drift insurance — but it makes these *medium-confidence* portable, not *clean*.
- **SETTINGS-BOUND** = task requires the agent to operate a **System Settings pane whose chrome differs across macOS versions** (Dock, Lock Screen, Hot Corners). Grade itself is `defaults read` (portable), but the *task* is the substrate-coupled part — the pane layout/labels differ Sonoma↔Sequoia, so the agent's path isn't version-agnostic.

### Counts (n=26)

| Class | Count | Tasks |
|---|---|---|
| **PORTABLE — clean** (pure file/content, no Apple-app, no Settings) | **6** | `fc7d32bd`, `e847156f`, `22afcaf9`, `ee0751c6`, `71fdb51d`*, `be92dd7f`† |
| **PORTABLE — version-self-adjusting** (grades vs `sw_vers`/`system_profiler`) | **4** | `03dfd972`, `ab78364a`, `496ae7dc`, `be92dd7f`† |
| **STORE-BOUND** (Apple app sqlite/plist) | **10** | `00507a0d`, `06708e22`, `78073675`*, `ad41428a`, `d3697775`, `d705e2fb`, `d98edd22`, `19b89112`, `2b13e970`, `92acd9b8`, `96f71b10`, `d5bbdfda` |
| **SETTINGS-BOUND** (System Settings pane chrome) | **4** | `809c6a1f`, `8eecaf26`, `97b5eb42`, `ecd1fea4` |

\* `71fdb51d`/`78073675` touch Stickies but the grade reduces to a file/`ls` check → counted clean-ish.
† `be92dd7f` straddles: grade is `sw_vers`/`system_profiler` self-adjusting *and* writes a plain `.txt`; but the **task** asks the agent to read System Settings → About, whose pane differs by version → effectively settings-coupled on the *task* side. Conservatively portable on the *grade* side only.

**Bottom line for W6:** ~**6–10 of 26 (≈25–40%) grade cleanly portably**; ~14 are store- or settings-coupled. The portable fraction is real but minority — W6 only needs ONE, and should pick from the clean-6.

### Best W6 candidate — `fc7d32bd-c73f-45d1-9aa3-91f9f8a4fd76`

- **Task:** TextEdit → type three lines → save `~/Desktop/fruits.txt`.
- **Grade (3 checkpoints, all pure POSIX):**
  - `[ -f "$HOME/Desktop/fruits.txt" ]`
  - `grep -q apple/banana/cherry` (case-folded)
  - line-order `awk` check.
- **Why it's the proof:** TextEdit exists and behaves identically on every macOS version; the grade touches **no Apple-app store, no System Settings, no `sw_vers`, no app build**. Same `(score, max_score)` on x86-Sonoma-KVM and arm64-Sequoia-VZ is guaranteed by construction. It isolates *substrate portability* from *app/version variance* — exactly what the headline needs.
- **Runner-up:** `e847156f` (create `~/Desktop/Archive/2025/Q4/notes.txt` containing `quarterly archive`) — pure Finder + file-content `grep`, equally version-agnostic. Good as a second confirming case so the proof isn't n=1.

**W6 instruction:** materialize the `macos + (no extra app needed — TextEdit is built-in)` package as both chains via one OCI index, run `fc7d32bd` on each, assert identical score. Use `e847156f` as the second case.

---

## 3. KVM fan-out economics (training engine)

Measured inputs (RFC §6 + `env/kvm/config.py`): **~0.86 s/clone**, **~17 MB instance delta** (+~770 MB companions), `ram_gb=4` default per guest, RFC's 46-guests/128 GB. **Our box is ~31 GB**, so realistic density is **~4–7 concurrent guests** (W7 will measure; expect the low end after host overhead).

Per-rollout wall time ≈ **8 min** (dominated by the agent loop + boot/settle, not the 0.86 s clone — clone cost is **noise**).

**Throughput, one ~31 GB box:**

| Concurrency (fleet) | Rollouts/hour (8 min each) | Rollouts/day |
|---|---|---|
| 5 (our realistic box) | 5 × (60/8) ≈ **37/hr** | **~900/day** |
| 7 (optimistic) | ≈ **52/hr** | **~1,250/day** |

**What production RL training needs:** RL fine-tuning of a CUA policy typically wants **10k–100k+ rollouts** per training run. At ~900/day our box is a **dev/CI fan-out**, not a training engine — a single training run would take **weeks**.

**Bigger-RAM x86 host:** density scales ~linearly with RAM (RAM-bound, KSM ≈ null for macOS per W7). The RFC's 128 GB → ~46 guests → ~46×7.5 ≈ **345 rollouts/hr ≈ 8k/day** — a ~9× jump for ~4× the RAM. A **256 GB** box → ~90 guests → **~16k/day**, putting a 10k-rollout run inside a day.

Rough $ (cloud, on-demand, illustrative): our ~31 GB box ≈ a small instance; a **128 GB-class x86** (e.g. `m6i.8xlarge`-ish, 128 GB) is ~**$1.5/hr** ($1.1k/mo); a **256 GB** box ~**$3/hr** ($2.2k/mo). The economics strongly favor **one fat box over many thin ones** (overlay base is shared once; per-guest cost is ~17 MB + RAM). **Conclusion: training wants a single high-RAM x86 host; our 31 GB box is a correctness/dev rig, not the production engine.** This is what W7 (density) must size.

---

## 4. VZ delivery footprint (eval/delivery engine)

Hard cap: **2 macOS VMs/host** (kernel-enforced). Per-rollout ≈ 8 min (same agent loop; `clonefile()` clone is instant, even cheaper than KVM).

**Our eval suite = 20 tasks × 5 trials = 100 rollouts.** At 2 VMs/Mac:

| Hardware | Concurrency | Wall-clock for 100 rollouts |
|---|---|---|
| **1 Mac mini** | 2 | 50 serial batches × 8 min ≈ **~6.7 h** |
| **2 Macs** | 4 | ≈ **3.3 h** |
| **4 Macs** | 8 | ≈ **1.7 h** |
| **5 Macs** | 10 | ≈ **1.3 h** |

So the full sellable eval suite runs **overnight on a single ~$600–1,400 Mac mini (M4)**, or in **~1–2 h on a 4–5 Mac shelf**. Framed for the lab: **"the lab runs N Macs"** — they own delivery hardware; N is tiny because **eval ≠ training volume**. A lab serving even 10× our suite (1,000 rollouts) needs only ~**4–8 Macs** for sub-2-h turnaround. The 2-VM cap is a non-issue at eval scale; it would only bite if someone tried to train on VZ (they shouldn't — that's KVM's job).

---

## 5. Load-bearing vs throwaway, given Hybrid

| Plan item | Serves | Verdict |
|---|---|---|
| **W1 · mw-env-chain** (KVM packaging spine) | TRAINING engine | **LOAD-BEARING.** The base←+apps resolver + OCI index is the shared spine both tracks ride; it's also what makes the W6 proof expressible. Keep priority. |
| **W4 · fast-reset** (savevm/overlay-discard) | TRAINING engine | **LOAD-BEARING.** Deterministic mass reset is the entire reason KVM is the training engine. Gated on W0a. |
| **W7 · density-and-taskstate** (guests/GB, `+task-state`) | TRAINING engine | **LOAD-BEARING.** Directly sets §3's throughput; without it we can't size the training fleet. |
| **W2/W5 · vz-feasibility + adapter** | DELIVERY artifact | **LOAD-BEARING but lower-volume.** Only needs to clear the 2-VM eval bar, not scale. W0b's RFB verdict still gates it. |
| **W6 · portability-proof** | DELIVERY artifact (the headline) | **LOAD-BEARING.** This is the "one package, both substrates, identical grade" claim that makes the OCI-index design real. Use `fc7d32bd`. |
| **W8 · lab-delivery** | DELIVERY artifact | **LOAD-BEARING** for the sale; small scope (one Mac, `dispatch_yutori`). |

**Flagged as LOWER-PRIORITY by the hybrid decision:**

1. **High-density / RAM-floor / zram tuning *on VZ*** — pointless. VZ is capped at 2 VMs; never optimize VZ density. All density work (W7) belongs to KVM only.
2. **Byte-level portability between substrates** — already known impossible (boot lock); RFC §6-Q1 should be closed as "no, by design — only the manifest index ports." Don't spend W6 effort chasing byte round-trip; W6 proves *grade* portability, not *byte* portability.
3. **OCI split-base / >10 GB zstd partitioning (W3) for the VZ chain** — lower urgency: the VZ artifact ships to *one* lab on *their* hardware (or they `tart pull` a public base), not fanned out from a registry at scale. Prioritize W3's KVM-base distribution; treat the VZ-base distribution as "thin +apps layer, base pulled from Tart/out-of-band."
4. **Tart strategic-dependency hardening** — the OpenAI-acquisition risk only matters for the *delivery* leg; it does not block the training engine. Keep it async (Gate L style), not on the W1–W7 critical path.

---

## Appendix — method

- Tasks read from `infra/cli/tasks/mining/*.json` (20) + `infra/cli/tasks/apple_suite/*.json` (6).
- Each `grading_command` flattened and signature-scanned for tool usage (`sqlite3`, `defaults`, `sw_vers`, `system_profiler`, `sips`, `grep`, file tests) and for any screenshot/OCR/pixel grading (**none found**).
- "pixel" in the raw `ee0751c6` task text is the *image-export* task; its grade uses `sips -g pixelWidth` (shell metadata read of a saved file) — fully portable, not screenshot grading. Corrected in the classification.
- Throughput/footprint figures use measured 0.86 s clone, 17 MB delta, ram_gb=4, RFC 46/128 GB, and an assumed ~8 min/rollout agent loop. W7 supersedes the density estimate with a measured guests-per-GB on the 31 GB box.
