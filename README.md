# CUA Worlds — macOS

**CUA Worlds** is an open-source research effort building high-quality benchmarks for
**computer-use agents (CUA)** — agents that see a screen and drive a real computer with
mouse and keyboard. We're starting with **macOS** and the apps that ship with it.

> Think *AOSP, but for computer-use datasets*: open environments, open tasks, open
> verifiers — built in the open so researchers and labs can measure (and push) the
> frontier of what agents can actually do on a real desktop.

See [`docs/vision.md`](docs/vision.md) for the mission and roadmap.

## What's here

This repository holds the **macOS** benchmark: a set of native-app, personal-assistant
tasks with **execution-based verifiers** (the agent's work is graded by inspecting real
system state over SSH — files, app databases, settings — not by eyeballing pixels), plus
the harness that runs agents against them and a dashboard to inspect trajectories.

- **Default-apps only.** Tasks use the apps that come with macOS (Finder, Notes, Calendar,
  Reminders, Contacts, Mail, System Settings, …) so there's nothing to install and the
  environment is reproducible.
- **Transparent, weighted grading.** Each task scores against independent checkpoints, so
  partial progress is visible and every point is auditable.
- **Portable substrate.** The same task package runs on x86 Linux/KVM and Apple-Silicon
  Virtualization.framework and grades identically — see [RFC 0002](docs/rfcs/0002-cua-env-spec-layered-vm-images.md).

**Status:** early. The first milestone is a set of *frontier-hard* macOS personal-assistant
tasks + verifiers (see the roadmap). Contributions welcome.

## Quickstart

```bash
git clone --recurse-submodules git@github.com:cuaworlds/macos.git
cd macos

# Python toolchain (uv workspace) — install uv: https://docs.astral.sh/uv/
uv sync

# Drive an agent against the task catalog
export ANTHROPIC_API_KEY=...        # for claude-* models
export YUTORI_API_KEY=...           # for n1.5-* models (optional)
uv run mw bench run --model claude-haiku-4-5 --tasks smoke
uv run mw bench list                # see all runs
uv run mw tasks list                # browse the task catalog

# Inspect runs in the dashboard
just dashboard
```

The clone pulls the submodules too, including [`gym-anything/`](https://github.com/cmu-l3/gym-anything) (a collaborator's agent-environment toolkit we track and build on). If you forgot `--recurse-submodules` at clone time, run `just sync`. To pull the latest `gym-anything` and stage the new pin, run `just gym-update`.

## Layout

| Path | What |
| ---- | ---- |
| `infra/cli/` | the `mw` benchmark harness (uv workspace member) — `mw bench`, `mw tasks`, `mw sandbox` |
| `infra/cli/tasks/` | task definitions (JSON) + their execution-based verifiers, by category |
| `infra/dashboard/` | React + Vite + TS UI that visualizes runs from `outputs/` |
| `docs/` | the vision, RFCs, experiment notes, and runbooks |
| `outputs/` | run results (gitignored except for `.gitkeep`) |
| `gym-anything/` | submodule → [cmu-l3/gym-anything](https://github.com/cmu-l3/gym-anything) — "turn any software into an agent environment" toolkit from a collaborator; tracked here so we stay in sync and coding agents can leverage it |
| `macosworld-aws/`, `macosworld-vmware/` | submodules → upstream MacOSWorld datasets (read-only) |

## Contributing

CUA Worlds is meant to be built in the open. The most valuable contribution right now is
**new tasks with solid verifiers**. A task is a JSON file under `infra/cli/tasks/<category>/`
with an instruction, a `pre_command` to set up clean state, and a `grading_command` list of
`[shell-check, weight]` checkpoints that grade the result over SSH. Run `uv run mw tasks show
<task-id>` to see a real example, add yours, and `uv run pytest infra/cli` before opening a PR.

## License

[Apache-2.0](LICENSE).
