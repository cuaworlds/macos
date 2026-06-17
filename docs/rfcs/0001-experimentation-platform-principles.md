# RFC 0001 — Experimentation platform: principles from inspect_ai + wandb

- **Status:** Draft — for discussion
- **Author:** jjmachan
- **Created:** 2026-05-29
- **Affects:** `infra/cli` (harness), `infra/dashboard`
- **Decisions so far:** keep our own harness + dashboard (adopt principles, not the tools); sequence **score depth & trust** first, then provenance, then comparison.

---

## Summary

We have a working LLM-agent eval platform — the `mw` harness (tasks → rollouts → `result.json` / `trajectory.jsonl` / screenshots) and a React dashboard that plays back trajectories. The basics work. This RFC is **not** about adding features. It distills *why* `inspect_ai` (what people reach for to **evaluate** LLMs) and `wandb` (what people reach for to **manage experiments**) are good, and proposes how to evolve our framework + dashboard along those principles.

It is a working doc: the principles are the durable part; the proposed changes are a starting point for discussion, not a committed plan.

---

## 1. Motivation

As we run more rollouts (now cheap and concurrent via the KVM backend), three things hurt:

1. **You can't trust or decompose the score.** Grading is first-true-wins → a flat `0` or `100`, with a truncated command log and no stderr/exit code. When a grade looks wrong, you can't see *why*.
2. **Old runs aren't reproducible.** A `run_id` string is the only record of "what was this run." No model config, system prompt, git commit, package versions, or timestamps are captured.
3. **You can't compare runs.** The dashboard is a browser, not a comparator — no run-level aggregate, no filter/sort, no side-by-side, no model-vs-model, no by-category accuracy.

Both `inspect_ai` and `wandb` solve exactly these problems, in opposite-but-complementary ways. Worth understanding why before we build.

---

## 2. The principles (the study)

### 2.1 The meta-principle — one record, three layers

Both tools are built on **a single, structured, self-describing record as the atomic unit** — wandb's **Run**, inspect's **EvalLog** — separated into **three layers**:

1. **Inputs / provenance (immutable):** wandb `config`; inspect `EvalSpec` + `plan` (model, params, task/dataset version, seed, git, packages). Set once.
2. **Trajectory (append-only):** wandb `history` (time-series); inspect's per-sample **transcript** of typed `events` (model call, tool call, state change, score). The full story.
3. **Outcome (aggregated):** wandb `summary` (final/best, one row per run); inspect `results` (scorer **metrics**: accuracy, stderr, grouped-by-metadata).

**Everything else — comparison tables, triage viewers, sweeps, reports, eval-sets — is built on top of that record.** The record is the contract. Get it right and the surfaces follow.

The two tools then specialize on **opposite axes**:

- **wandb → breadth.** *Compare hundreds of runs to find what mattered.* The product is comparison, not logging. The config↔metric separation is what makes "filter by config, group by hyperparameter, read the metric" work. Philosophy: *log everything, compare anything later.*
- **inspect → depth.** *Trust and debug a single score.* The per-sample event transcript answers "why did this fail — model hallucinated, tool errored, or scorer mis-extracted?" Provenance + a typed/versioned log make any eval re-runnable and auditable.

**A good agent-eval platform needs both axes on the same backbone.**

### 2.2 inspect_ai — principles worth stealing

- **P1 — Typed, versioned, self-describing log.** One `EvalLog` = `eval`(spec) + `plan` + `results` + `stats` + `samples[]`. Not loose files. (Binary `.eval` is an efficiency detail; the *single structured record* is the principle.)
- **P2 — Event-level transcript per sample.** Every model/tool/state/score action is a typed event → debugging-at-scale.
- **P3 — Solver ≠ Scorer; scoring is first-class & composable.** "How we attempt" is separate from "how we measure." A `Score` carries `value` + `answer` + `explanation` + `metadata`; **metrics** aggregate across samples (accuracy, **stderr via CLT**, `grouped()` by metadata, partial credit, model-graded). You trust the number because its derivation is explicit.
- **P4 — Provenance by default → re-runnable.** Config/params/version/seed/git/packages live in the log; `eval-retry` resumes from it.
- **P5 — Resilience at scale.** eval-set, retries, resumption, incremental writes — never lose partial work.
- **P6 — Triage-first viewer.** eval list → sample grid (filter/sort by score) → sample detail (messages / events / scoring / metadata).

### 2.3 wandb — principles worth stealing

- **W1 — The Run is the atom; config vs history vs summary are separated on purpose.** Config = inputs (group/filter by these); history = append-only series (dynamics); summary = one number per run (scan in a table). Mixing them makes comparison nonsensical.
- **W2 — Comparison is the product.** Runs table → filter (isolate) → **group-by-config** (aggregate across seeds) → parallel-coordinates/scatter (which variable drove the win). The UI *is* the analysis tool.
- **W3 — Low-friction, append-only logging.** Cheap to instrument; immutable once written; rich media. Capture enough to answer questions you didn't know you'd ask.
- **W4 — Artifacts & lineage.** Versioned datasets/models linked to runs; "how was this made" is recorded.
- **W5 — Sweeps are first-class** and surface in the *same* comparison UI.
- **W6 — Reports** turn runs into shareable, **live-data** narratives.

---

## 3. Where we stand (principles → our gaps)

We already have a **respectable depth story**: `trajectory.jsonl` + per-step screenshots + `chat_log.json` ≈ inspect's transcript (P2); `result.json` ≈ a per-sample record; the dashboard's verifier card + click overlay surface it well. The gaps are in the **other two layers of the backbone**.

| Principle | Our state | Gap |
|---|---|---|
| Backbone: self-describing record | `result.json` per task; `summary.json` = `TaskResult[]` | No run-level record; the `run_id` string is the only "config." Not self-describing. |
| W1 config / P4 provenance | Nothing captured | No model cfg, **system prompt**, `max_steps`, backend/fleet, git commit, package versions, seed, ISO timestamps. **Old runs aren't reproducible or comparable-by-config.** |
| P3 outcome / metrics | `grade()` first-true-wins → **0 or 100**; `grade_log` truncates cmd, drops stderr/rc; returns early | No accuracy/mean/**stderr**, no by-category, no partial credit, no multi-dimensional score. **Can't trust or decompose the number.** |
| W2 comparison surface | RunsList = list by mtime; RunDetail = one table | No run aggregate, no filter/sort, **no side-by-side, no model-vs-model, no by-category.** A browser, not a comparator. |
| P5 resilience | Fleet concurrency exists; no resume | A crashed run loses partial work; no multi-model eval-set in one command. |
| W4/W6 lineage / reports | None | Lower priority for an internal tool. |

**Diagnosis:** trajectory depth is fine; the **outcome layer is thin and binary** and there is **no provenance layer** — which is exactly why comparison is impossible. The chosen sequence (score → provenance → comparison) follows the dependency order: a trustworthy, decomposable outcome must exist before cross-run comparison means anything.

---

## 4. Proposed changes

Direction: **keep our harness + dashboard; adopt the principles.** Three phases; Phase 1 detailed, 2–3 sketched.

### Phase 1 — Score depth & trust (first)

Apply P3 (composable scoring + metrics) and build the outcome layer of the backbone. Two sub-parts: **immediate trust** on today's binary tasks, and a score model that **supports depth** as richer tasks arrive. (Note: all 10 current tasks are single-check binary; the partial-credit machinery is a model investment, not an immediate scoreboard change.)

**Framework — new `infra/cli/benchmark/score.py`:**
- `CheckResult`: `cmd, value, stdout, stderr, rc, hit, points` — full grader transcript per check (no truncation; capture stderr + exit code). "You can audit the score."
- `Score`: `value: float`, `max_value: float`, `normalized: float`, `checks: list[CheckResult]`, `explanation: str`. Mirrors inspect's `Score`.
- `aggregate(scores) -> RunMetrics`: `n`, `accuracy` (mean normalized), `stderr` (CLT: `std(normalized, ddof=1)/sqrt(n)`), `by_category`, `total_cost`, `total_tokens`, `status_counts`. Mirrors inspect `results` / wandb `summary`.

**Framework — grader (`env/kvm/__init__.py` + `env/use_computer.py` `grade()`):**
- Evaluate **all** grading commands (not first-true-wins); each check captures stdout/stderr/rc/hit; `points = value if hit else 0`; `Score.value = sum(points)`, `max_value = sum(values)`. A single 100-point task stays exactly 0 or 100 — no regression — but now with a complete transcript.
- `grade()` returns a `Score`; `runner.py` adapts. `run_pre_command` stays best-effort.

**Framework — wiring (`runner.py`, `mw/cli.py`):**
- `TaskResult` gains `normalized_score` + `checks` (keep `grade_log` as a back-compat alias).
- After the task loop, compute `aggregate(...)` and write a sibling **`run.json`** (a `results` block for now) alongside `summary.json`. First brick of the run record that Phase 2 completes.

**Dashboard:**
- **RunDetail**: header shows `accuracy ± stderr`, total cost, a **by-category** breakdown; per-task row shows partial score + status pill.
- **TrajectoryView verifier card** (already full-width): render each `CheckResult` with `+points`, pass/fail, expandable stdout/stderr/rc.
- Aggregate client-side from `summary.json` when `run.json` is absent (old runs still render).

### Phase 2 — Provenance / the run record (sketch)

Write a **`run.json` manifest** at run start (wandb `config` + inspect `EvalSpec`): model cfg (incl. **system-prompt text + hash**, tool/beta version, thinking budget), `max_steps`, display dims, backend + KVM fleet params, **git commit + dirty flag**, **package versions** (`anthropic`, `use_computer`), seed, ISO `started_at`/`ended_at`, task-set spec + count. Fold Phase-1 `RunMetrics` in as the `results`/`summary` block. Result: a **self-describing, re-runnable record** (P1, P4, W1). Dashboard shows config on RunDetail; enables compare-by-config. *Stretch:* `mw bench resume <run-id>` skipping completed tasks (P5).

### Phase 3 — Comparison & triage dashboard (sketch)

Make the dashboard a **comparator** (W2, P6): RunsList gains aggregate-score / model / backend / date columns (from `run.json`) + filter/sort; a **Compare view** (select 2+ runs → side-by-side per-task scores, model-vs-model on the same task, by-category diff); cross-run drill into the *same task* to triage regressions. *Stretch:* a saved/shareable compare view as a lightweight "report" (W6).

---

## 5. Files in scope (when we build)

- **New:** `infra/cli/benchmark/score.py` (`CheckResult`, `Score`, `RunMetrics`, `aggregate`).
- `infra/cli/benchmark/env/kvm/__init__.py`, `infra/cli/benchmark/env/use_computer.py` — `grade()` → `Score`, all checks, stderr/rc.
- `infra/cli/benchmark/runner.py` — `TaskResult` gains `normalized_score`/`checks`.
- `infra/cli/mw/cli.py` — `bench_run` computes `aggregate()`, writes `run.json` (Phase 1: results; Phase 2: full manifest).
- `infra/cli/benchmark/config.py` — source of model cfg / system prompt for the manifest (Phase 2).
- Dashboard: `src/pages/RunDetail.tsx`, `src/pages/TrajectoryView.tsx`, `src/lib/trajectory.ts`, `vite-plugins/runs-api.ts`; Phase 3 adds `RunsList.tsx` + a `Compare` route in `App.tsx`.
- Outputs contract (CLAUDE.md) unchanged: everything under `outputs/runs/<run-id>/`, dashboard reads the same place, contents gitignored.

---

## 6. Open questions / for discussion

1. **Score model shape.** Is a scalar `value` + `checks[]` enough, or do we want inspect-style **multi-dimensional** scores (a dict of named sub-scores) from day one? Today's tasks don't need it; future ones might.
2. **stderr semantics.** CLT `std/sqrt(n)` over normalized scores is simple and matches inspect. Do we also want binomial CI for the pure-binary case, or clustered stderr (inspect's `grouped()`) by category?
3. **Where the run record lives.** Sibling `run.json` next to `summary.json` (minimal change, keeps the contract) vs. folding `summary.json` into `run.json` (cleaner, one record — closer to EvalLog, but a contract change touching both sides).
4. **Reproducibility ambition.** Is "captured well enough to compare and understand" sufficient, or do we want *true* re-runnability (pinned package versions, seed-deterministic) — which is harder given model API nondeterminism?
5. **Did we pick the right first axis?** We chose score-depth first. Comparison gives the most visible payoff; provenance is the true backbone. Confirm score-first still feels right.
6. **Tasks dataset.** Partial-credit only matters once tasks carry multi-check weighted grading. Do we plan to author richer tasks (or pull the upstream MacOSWorld grading) to exercise it?

---

## 7. References

- inspect_ai — https://inspect.aisi.org.uk/ (tasks, solvers, scorers, eval-logs, log-viewer, eval-sets); repo https://github.com/UKGovernmentBEIS/inspect_ai
- wandb — https://docs.wandb.ai/ (track/config, log/summary, runs filtering, parallel-coordinates, artifacts, sweeps, reports)
- Our current data model: `infra/cli/benchmark/{runner,agent,task,config}.py`, `infra/dashboard/src/`, and the outputs contract in `CLAUDE.md`.
