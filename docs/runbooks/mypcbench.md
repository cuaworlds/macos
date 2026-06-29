# Runbook: run MyPCBench's real apps on the macOS KVM benchmark

This walks you through running the real [MyPCBench](https://github.com/ljang0/MyPCBench) seeded persona apps against our macOS KVM guests. The 17 web apps run as a lightweight native container; a macOS guest drives them in Safari over an SSH reverse tunnel; tasks are graded deterministically against the live app DB. No nested QEMU, no root, no `--privileged`.

The control tool is `infra/mypcbench/run-apps.sh` (wrapped by `just mypcbench-apps`). Ported tasks and the grader live in `infra/cli/tasks/mypcbench/`. The `--kvm-app-tunnel` plumbing lives in `infra/cli/benchmark/env/kvm/{config,fleet}.py` and `infra/cli/mw/cli.py`.

## What it is

`mypcbench-desktop.tar.zst` (HF dataset `ljang0/mypcbench-qemu-baseline`) is an OCI container image — the Ubuntu 24.04 desktop+apps appliance the published `michael_scott.qcow2` is built from. The 17 apps are plain Next.js Node servers started by supervisord (not systemd). `run-apps.sh up` strips the desktop programs (gnome/vnc/electron/control-api/libreoffice — they need an X display) and serves only the web apps + mail stack on `127.0.0.1:3001-3017`.

- Login is server-side auto-login (`GET / → 307 /?_autologin=1`) from a baked persona, so a fresh browser with no cookies gets the Michael Scott session.
- The entrypoint date-rebases seed dates to "today" on boot.
- Footprint is ~656 MiB for all 17 apps + mail. The ~3 GB image is not committed; `run-apps.sh pull` fetches and `docker load`s it into `~/.cache/mypcbench` (resumable).

## Prerequisites

This reuses the existing macOS KVM benchmark, so that must work first — see [`kvm-server-setup.md`](./kvm-server-setup.md). In short: Docker + `/dev/kvm`, the macOS base volume and SSH key (`~/workspace/kvm-spike/...`, or `MACOSWORLD_KVM_*` overrides), `uv` + `just`, and an API key via direnv (`YUTORI_API_KEY` for `n1.5-latest`, or `ANTHROPIC_API_KEY` for `claude-*`). Sanity-check the base path independently before adding MyPCBench: `direnv exec . uv run mw bench run --backend kvm --model n1.5-latest --tasks smoke --kvm-fleet-size 1`.

The apps container and `mw bench` must run on the same host — the reverse tunnel forwards in-guest ports to that host's localhost. On a ~7 GB box keep `--kvm-fleet-size 1` (a 4 GB guest plus the ~656 MiB apps container is near the ceiling).

## One-time setup

```bash
just mypcbench-apps pull     # fetch + docker load the OCI image (~3 GB, resumable)
just mypcbench-apps up       # start the 17-app sidecar on 127.0.0.1:3001-3017
curl -s -o /dev/null -w '%{http_code}\n' http://localhost:3001/   # expect 307 (auto-login)
```

## Each run

```bash
just mypcbench-apps reset    # restore pristine app DBs before a scored run
just bench-mypcbench         # 5-task seed set: n1.5, 1 guest, tunnel 3001-3017
```

Or run a custom set / model directly (drop `--no-push` to upload to cuaworld):

```bash
direnv exec . uv run mw bench run --backend kvm --model n1.5-latest \
  --kvm-fleet-size 1 --kvm-app-tunnel 3001-3017 --no-push \
  --tasks mypc-mail-star-tax,mypc-calendar-improv,mypc-shop-cart
```

To push the run to the dashboard, first register the catalog with `mw tasks push --category mypcbench`, then run without `--no-push`. Results land in `outputs/runs/<run-id>/` (per-task `result.json` + trajectory + screenshots); a successful push uploads them and removes the local staging dir.

## How the guest reaches the apps

`--kvm-app-tunnel 3001-3017` makes the fleet open `ssh -N -R <port>:localhost:<port>` into each guest at boot (and close it at teardown). `-R p:localhost:p` binds port `p` inside the guest and forwards connections back over SSH to the host's `localhost:p`, where the apps container listens — so in-guest Safari hitting `http://localhost:3001` reaches the real seeded app, keeping the task text's literal `localhost:PORT` valid. The flag accepts a range (`3001-3017`) or a list (`3001,3016`); `mw bench` preflights the first port and warns if the sidecar isn't up. With no `--kvm-app-tunnel`, the KVM backend behaves exactly as before.

## Port → app

| Port | App | Port | App | Port | App |
|---|---|---|---|---|---|
| 3001 | Gringotts | 3007 | HangryDash | 3013 | SprintBoard |
| 3002 | BatBucks | 3008 | TableFind | 3014 | LockedIn |
| 3003 | OddsMarket | 3009 | Kwik-E-Mart | 3015 | SpeedTax |
| 3004 | HooliChat | 3010 | HooliShop | 3016 | HooliMail |
| 3005 | HooliWork | 3011 | Dinoco | 3017 | HooliCalendar |
| 3006 | eTaxi | 3012 | Cheskepdia | | |

## Tasks and grading (deterministic, no LLM judge)

Ported tasks (14 so far) are action tasks graded deterministically against the live app DB via `infra/cli/tasks/mypcbench/grade_container.py` (`docker exec mypc-apps sqlite3 …`). Each task asserts an absolute post-condition — a new row carrying a unique marker, or a flag/status flipped on a specific seeded row — so graders are baseline-free and re-run-safe. This is a deliberate divergence from MyPCBench's offline LLM judge: bit-exact, zero cost, no drift, matching this repo's deterministic-checkpoint philosophy. The tradeoff is that only state-changing action tasks are covered; MyPCBench's ~72 analysis/read tasks (which grade an answer) need an LLM judge and are out of scope for this path. The upstream task+rubric catalog is vendored at `infra/mypcbench/all_tasks_with_grading.json` as the porting source.

To add a task: drop a `*.json` into `infra/cli/tasks/mypcbench/` (instruction + a `pre_command` that opens Safari to the app + `grading_script: grade_container.py`) and append one entry to the `SPECS` registry in `grade_container.py`. Self-test with no boot: `echo '{"task_id":"<id>"}' | python3 infra/cli/tasks/mypcbench/grade_container.py`.

## Follow-ups

- Analysis/read tasks (~72) need an answer-judge (Claude); deliberately skipped for now.
- LibreOffice/Files tasks need a home-dir seed and an office suite inside the guest.
- The per-VM app DB persists across runs (app writes are not auto-cleaned), so `reset` before a scored run; on a 7 GB box keep the fleet at size 1. Note: `reset` recreates the container from the pristine image — that is the only true wipe. An in-place generator reseed is an idempotent upsert and leaves prior run rows behind, which would falsely pass create/flag graders.
