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

# Manage the MyPCBench apps sidecar (the 17 seeded web apps on :3001-3017).
# Actions: pull (one-time image fetch+load) | up | down | status | logs | reset
mypcbench-apps action="up":
    infra/mypcbench/run-apps.sh {{action}}

# Run the MyPCBench task batch on a 1-guest KVM fleet, reverse-tunnelling the apps in.
# Precondition: `just mypcbench-apps up`. Tasks default to the 5-task seed set.
bench-mypcbench model="n1.5-latest" tasks="mypc-calendar-improv,mypc-mail-star-tax,mypc-mail-read-eticket,mypc-shop-cart,mypc-calendar-dentist":
    uv run mw bench run --backend kvm --model {{model}} --tasks {{tasks}} \
        --kvm-fleet-size 1 --kvm-app-tunnel 3001-3017

# Open a macOS sandbox in the browser
sandbox sandbox_id="":
    uv run mw sandbox open {{ if sandbox_id != "" { "--sandbox-id " + sandbox_id } else { "" } }}

# Open a single KVM macOS guest in the browser (dockur web VNC)
sandbox-kvm host="localhost":
    uv run mw sandbox open --backend kvm --kvm-host {{host}}

# Start the dashboard dev server (talks to the backend at VITE_API_BASE_URL)
dashboard:
    cd infra/dashboard && npm run dev

# Start the dashboard in offline mode: reads local outputs/, no backend or login
dashboard-local:
    cd infra/dashboard && VITE_DATA_SOURCE=local npm run dev

# Install dashboard deps
dashboard-install:
    cd infra/dashboard && npm install
