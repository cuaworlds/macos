# CLAUDE.md

Repo conventions for Claude Code working in CUA Worlds (the macOS benchmark).

## Layout

- `macosworld-aws/`, `macosworld-vmware/` — git submodules pointing at the upstream MacOSWorld datasets. **Read-only.** Don't edit anything inside these; if you need to fork upstream, surface that as a separate task.
- `infra/cli/` — Python uv-workspace member named `macosworld-usecomputer`. The benchmark harness. Entry: `mw` (Click umbrella) — `mw bench`, `mw tasks`, `mw sandbox`.
- `infra/dashboard/` — Vite + React + TS frontend. Reads from `<repo-root>/outputs/`.
- `outputs/` — run results. Track `.gitkeep` only; contents are gitignored.
- `docs/` — the vision, RFCs, experiment notes, and runbooks.

## Tooling

- Python: always use `uv` from the repo root. Workspace members are listed in root `pyproject.toml`.
  - Run the CLI: `uv run mw ...` (e.g. `uv run mw bench run --model claude-haiku-4-5 --tasks smoke`).
  - Or via the justfile: `just bench <model> <tasks>`, `just sandbox [<id>]`.
- Node: `infra/dashboard/` is self-contained. `cd infra/dashboard && npm install && npm run dev`.
- Recipes: see `justfile` at the repo root for canonical invocations.

## Outputs contract

Benchmark runs write to `<repo-root>/outputs/runs/<run-id>/`. The dashboard reads from the same place. Override the location with `MACOSWORLD_OUTPUTS_DIR`. Don't break this contract without updating both sides.

## Submodules

- `git clone --recurse-submodules <url>` to clone properly.
- `just sync` (or `git submodule update --init --recursive`) to pick up submodules in an existing checkout.
- Bumping a submodule is a deliberate action: `cd <submodule> && git fetch && git checkout <ref> && cd .. && git add <submodule> && git commit`. Don't do this casually.
