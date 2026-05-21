default:
    @just --list

# Sync submodules
sync:
    git submodule update --init --recursive

# Run the benchmark CLI (model defaults to claude-haiku-4-5, tasks to smoke)
bench model="claude-haiku-4-5" tasks="smoke":
    uv run mw bench run --model {{model}} --tasks {{tasks}}

# Open a macOS sandbox in the browser
sandbox sandbox_id="":
    uv run mw sandbox open {{ if sandbox_id != "" { "--sandbox-id " + sandbox_id } else { "" } }}

# Start the dashboard dev server
dashboard:
    cd infra/dashboard && npm run dev

# Install dashboard deps
dashboard-install:
    cd infra/dashboard && npm install
