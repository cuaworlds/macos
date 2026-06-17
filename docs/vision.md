# CUA World — Vision

## Mission

Build, in the open, the benchmarks the computer-use-agent field needs to measure and push
its frontier — starting with macOS and expanding to every platform agents will have to
operate.

We want to be for **computer-use datasets** what the Android Open Source Project is for
mobile: a shared, open foundation — environments, tasks, and verifiers — that any
researcher or lab can build on, contribute to, and trust.

## Why

Computer-use agents are improving fast, but progress is bottlenecked by **evaluation**.
Good benchmarks for desktop agents are scarce, hard to reproduce, and easy to overfit. The
hard part isn't running an agent — it's defining tasks that are *meaningfully difficult* and
verifiers that are *correct and reproducible*. That's the work CUA World focuses on:

- **Real environments, not mocks.** Tasks run on actual operating systems with their real
  apps and data stores.
- **Execution-based verification.** Results are graded by inspecting real system state
  (files, application databases, settings) rather than by interpreting screenshots — so
  scores are objective and auditable.
- **Frontier-hard, not saturated.** Tasks are calibrated to be hard for current frontier
  models, so the benchmark stays informative as agents improve.
- **Open and collaborative.** Environments, tasks, and verifiers are open source; the
  benchmark is something the community builds together.

## Roadmap

**v1 — macOS (current).** A benchmark of frontier-hard, personal-assistant tasks on the
default macOS app suite, with execution-based verifiers. This is the MVP described below.

**Beyond macOS.** The substrate and task/verifier model are designed to generalize. The
intended path is to expand to other platforms agents must operate — desktop (Windows),
mobile (Android), and others — and to work with the platform ecosystems and model labs who
have the most at stake in good evaluation.

## The macOS MVP (v1)

A focused first release: an agent acting as a **personal assistant** on macOS, using only
the apps that ship with the OS.

**Scope & constraints**

- **Default macOS apps only** — Finder, Notes, Calendar, Reminders, Contacts, Mail, System
  Settings, TextEdit, Preview, and the like. No third-party installs, so the environment is
  reproducible and the benchmark is about *agent skill*, not setup.
- **Personal-assistant tasks** — realistic things a user would delegate: extract information
  from a document and record it elsewhere, create and cross-reference calendar/reminder/note
  entries, organize files, read back system state. Tasks often span multiple apps.
- **Execution-based, weighted verifiers** — each task grades against independent checkpoints
  (e.g. *event exists* / *date correct* / *time correct*), each worth a weight, run as shell
  checks over SSH. Partial credit is visible; every point is auditable. (See
  [RFC 0001](rfcs/0001-experimentation-platform-principles.md) for the scoring model.)
- **Frontier-hard calibration** — tasks are selected/tuned to sit at the difficulty frontier
  for current top models, where they carry the most signal.

**A first target:** a curated set of frontier-hard macOS personal-assistant tasks with
verifiers, with the corpus growing from there. The corpus already includes native-app and
multi-app tasks (`infra/cli/tasks/`) as the seed.

**Planned directions**

- **Model/VLM judges** for tasks whose success resists pure execution-based grading, used to
  complement (not replace) deterministic checkpoints.
- **Scaling the corpus** — both breadth (more apps, more workflows) and depth (harder,
  multi-step, cross-app tasks), with an eye toward professional/work workflows over time.

## Get involved

The most valuable contribution today is **new tasks with solid verifiers**. See the
[README](../README.md#contributing) for the task format and how to submit one. Environments,
harness improvements, and verifier review are all welcome.
