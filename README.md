# macos-world

Internal benchmarking platform built on top of the public MacOSWorld datasets.

## Layout

| Path | What |
| ---- | ---- |
| `macosworld-aws/` | submodule → [showlab/macosworld](https://github.com/showlab/macosworld) (read-only upstream) |
| `macosworld-vmware/` | submodule → [yangpei-comp/macosworld_vmware](https://github.com/yangpei-comp/macosworld_vmware) (read-only upstream) |
| `infra/cli/` | benchmark harness — runs eval tasks via the Use Computer API |
| `infra/dashboard/` | React + Vite + TS UI that visualizes runs from `outputs/` |
| `outputs/` | run results (gitignored except for `.gitkeep`) |
| `docs/` | internal documentation |

## Quickstart

```bash
git clone --recurse-submodules <repo-url> macos-world
cd macos-world

# Python toolchain (uv workspace)
uv sync

# Run a smoke benchmark
export USE_COMPUTER_API_KEY=...     # required for --backend use-computer
export ANTHROPIC_API_KEY=...        # required for claude-* models
export YUTORI_API_KEY=...           # required for n1.5-* models
uv run mw bench run --model claude-haiku-4-5 --tasks smoke
uv run mw bench run --model n1.5-latest --tasks smoke   # cross-provider
uv run mw bench list             # see all runs
uv run mw tasks list             # browse task catalog

# View results in the dashboard
just dashboard
```

If you forgot `--recurse-submodules` at clone time:

```bash
just sync   # or: git submodule update --init --recursive
```

## Conventions

- Python is managed by `uv` as a workspace; always invoke via `uv run`.
- Benchmark results land in `outputs/runs/<run-id>/`. Override with `MACOSWORLD_OUTPUTS_DIR`.
- The two submodules are read-only references — don't edit their contents.
