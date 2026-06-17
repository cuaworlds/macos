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
mw bench   run     --model <id> [--tasks smoke|all|<csv ids>] [--run-id <name>]
           list                                       # list runs in outputs/runs/
           show    <run-id>                           # print summary.json for a run

mw tasks   list    [--category <name>]                # list task IDs grouped by category
           show    <task-id>                          # print one task's JSON definition

mw sandbox open    [--sandbox-id <id>]                # boot or reconnect, open noVNC
```

Models: `claude-haiku-4-5`, `claude-sonnet-4-6`, `claude-opus-4-7` (see `benchmark/config.py`).

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
