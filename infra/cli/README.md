# infra/cli — `mw`

Click-based CLI for the CUA Worlds macOS benchmark harness. Installed as the `mw` script when the workspace is synced.

## Quickstart

From the repo root:

```bash
uv sync
export USE_COMPUTER_API_KEY=...
export ANTHROPIC_API_KEY=...

uv run mw --help
```

## Commands

```
mw auth    login   <username> [--api-url <url>]       # log in, mint+save a cua_ API key
           whoami                                      # show the current backend user
           key                                         # rotate the API key
           logout                                      # remove saved credentials

mw bench   run     --model <id> [--tasks smoke|all|<csv ids>] [--run-id <name>] [--no-push]
           list                                       # list runs in outputs/runs/
           show    <run-id>                           # print summary.json for a run
           push    <run-id> [--keep]                  # push (or re-push) a local run

mw tasks   list    [--category <name>]                # list task IDs grouped by category
           show    <task-id>                          # print one task's JSON definition
           push    [--category <name>]                # register tasks in the backend

mw sandbox open    [--sandbox-id <id>]                # boot or reconnect, open noVNC
```

Models: `claude-haiku-4-5`, `claude-sonnet-4-6`, `claude-opus-4-7` (see `benchmark/config.py`).

## Backend (runs, rollouts, tasks)

`mw bench run` pushes results to the hosted backend by default. Authenticate once:

```bash
uv run mw auth login <username>          # saves a cua_ key to ~/.mw/credentials.json
```

In CI/headless, skip the login and set the env directly:

```bash
export CUA_API_KEY=cua_...               # long-lived API key
export CUA_API_URL=https://api.cuaworld.vibrantlabs.com/api/v1   # optional override
```

With no credentials, `run` simply records nothing and runs **local-only** (it
prints a notice and writes to `outputs/runs/` as usual) — so the benchmark works
fully without a backend. Force local with `--no-push` (or `CUA_PUSH=0`), and
re-push a local run later with `mw bench push <run-id>`. When a push succeeds the
local run dir is removed (staging only) unless `--keep` is passed.

## Outputs

Run results land in `<repo-root>/outputs/runs/<run-id>/`. Override with:

```bash
export MACOSWORLD_OUTPUTS_DIR=/absolute/path/to/outputs
```

## Layout

- `mw/` — Click umbrella (`mw.cli:cli`)
- `benchmark/` — agent loop, runner, env bridge, task/model config
- `tasks/` — task definitions (JSON, organized by category)
- `smoke_tasks.txt` — task IDs used by `--tasks smoke`
