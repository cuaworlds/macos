default:
    @just --list

# Sync git submodules: gym-anything (agent-env toolkit) + MacOSWorld datasets
sync:
    git submodule update --init --recursive
    @echo "✓ submodules ready: gym-anything/, macosworld-aws/, macosworld-vmware/"

# Pull the latest gym-anything (collaborator repo) and stage the new pin to commit
gym-update:
    git submodule update --remote --init gym-anything
    @git -C gym-anything log --oneline -1
    @echo "↑ gym-anything moved to the commit above; review & commit: git add gym-anything && git commit -m 'chore: bump gym-anything pin'"

# Run the benchmark CLI (model defaults to claude-haiku-4-5, tasks to smoke)
bench model="claude-haiku-4-5" tasks="smoke":
    uv run mw bench run --model {{model}} --tasks {{tasks}}

# Run the benchmark against a local KVM fleet (concurrent across `size` guests)
bench-kvm model="claude-haiku-4-5" tasks="smoke" size="4":
    uv run mw bench run --backend kvm --model {{model}} --tasks {{tasks}} --kvm-fleet-size {{size}}

# Open a macOS sandbox in the browser
sandbox sandbox_id="":
    uv run mw sandbox open {{ if sandbox_id != "" { "--sandbox-id " + sandbox_id } else { "" } }}

# Open a single KVM macOS guest in the browser (dockur web VNC)
sandbox-kvm host="localhost":
    uv run mw sandbox open --backend kvm --kvm-host {{host}}

# Start the dashboard dev server
dashboard:
    cd infra/dashboard && npm run dev

# Install dashboard deps
dashboard-install:
    cd infra/dashboard && npm install
