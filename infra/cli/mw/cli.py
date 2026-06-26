from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
import time
import webbrowser
from dataclasses import asdict
from pathlib import Path

import click

from benchmark.config import MODEL_CONFIG
from benchmark.log import setup_logging
from benchmark.runner import aggregate_trials, build_work_items, run_task
from benchmark.task import TASKS_ROOT, load_tasks
from mw import auth, push
from mw import remote as rmt


def _outputs_root() -> Path:
    return Path(
        os.environ.get(
            "MACOSWORLD_OUTPUTS_DIR",
            Path(__file__).resolve().parents[3] / "outputs",
        )
    ) / "runs"


def _push_enabled(no_push: bool) -> bool:
    """Push by default; disabled by --no-push or CUA_PUSH=0."""
    if no_push:
        return False
    return os.getenv("CUA_PUSH", "1").strip().lower() not in ("0", "false", "no")


def _print_summary(model: str, results: list[dict]) -> None:
    click.echo("\n" + "=" * 80)
    click.echo(f"SUMMARY — {model} — {len(results)} tasks")
    click.echo("=" * 80)
    click.echo(
        f"{'category':<20} {'task':<10} {'score':>5} {'steps':>5} "
        f"{'in_tok':>8} {'out_tok':>8} {'cost$':>7} {'status':<10}"
    )
    for r in results:
        click.echo(
            f"{r['category']:<20} {r['task_id'][:8]:<10} {r['score']:>5} {r['n_steps']:>5} "
            f"{r['input_tokens']:>8} {r['output_tokens']:>8} {r['cost_usd']:>7.4f} {r['status']:<10}"
        )
    total_score = sum(r["score"] for r in results)
    total_in = sum(r["input_tokens"] for r in results)
    total_out = sum(r["output_tokens"] for r in results)
    total_cost = sum(r["cost_usd"] for r in results)
    click.echo("-" * 80)
    click.echo(
        f"{'TOTAL':<20} {'':<10} {total_score:>5} {'':<5} "
        f"{total_in:>8} {total_out:>8} {total_cost:>7.4f}"
    )


def _print_trials_summary(agg: dict) -> None:
    """Per-task pass@k aggregate block printed after the flat per-trial summary.

    Flags tasks landing in the 1/5 or 2/5 'target band' (the interesting, neither
    trivially-solved nor never-solved tasks) for rejection-sampling triage.
    """
    n = agg["n_trials"]
    click.echo("\n" + "=" * 80)
    click.echo(f"PASS@{n} — {agg['model']} — threshold {agg['pass_threshold']:g}")
    click.echo("=" * 80)
    click.echo(
        f"{'base_task':<24} {'passes':>8} {'pass_rate':>9} {'mean_score':>10} {'mean_steps':>10}"
    )
    for base_id, t in agg["tasks"].items():
        trials = t["trials"]
        n_t = t["n_trials"]
        mean_score = sum(x["score"] for x in trials) / n_t if n_t else 0.0
        mean_steps = sum(x["n_steps"] for x in trials) / n_t if n_t else 0.0
        flag = "  <- TARGET BAND" if t["passes"] in (1, 2) else ""
        click.echo(
            f"{base_id[:24]:<24} {f'{t['passes']}/{n_t}':>8} {t['pass_rate']:>9.2f} "
            f"{mean_score:>10.1f} {mean_steps:>10.1f}{flag}"
        )


@click.group()
def cli():
    """CUA Worlds — macOS benchmark CLI."""
    # Stream timestamped, per-task-tagged progress in real time (and line-buffer
    # stdout) so long/concurrent runs are debuggable and never *look* deadlocked.
    setup_logging()


# ---------- auth ----------


@cli.group("auth")
def auth_group():
    """Authenticate with the hosted backend."""


@auth_group.command("login")
@click.argument("username")
@click.option("--api-url", default=None, help="Backend base URL (default: env CUA_API_URL or built-in).")
@click.password_option(confirmation_prompt=False, help="Password (prompted if omitted).")
def auth_login(username: str, api_url: str | None, password: str) -> None:
    """Log in, mint an API key, and save it to ~/.mw/credentials.json."""
    from benchmark import backend as be

    api_url = api_url or auth.resolve()[0]
    try:
        tokens = be.login(api_url, username, password)
        with be.BackendClient(api_url, tokens["access_token"]) as client:
            api_key = client.mint_key()
            user = client.whoami()
    except Exception as e:
        raise click.ClickException(str(e)) from e
    auth.save_credentials(api_url, api_key, user)
    click.echo(f"Logged in as {user.get('username', username)}; key saved to {auth.CREDENTIALS_PATH}")


@auth_group.command("whoami")
def auth_whoami() -> None:
    """Show the current backend user."""
    from benchmark.backend import BackendError

    try:
        client = auth.make_client(require=True)
    except PermissionError as e:
        raise click.UsageError(str(e)) from e
    try:
        with client:
            user = client.whoami()
    except BackendError as e:
        raise click.ClickException(
            f"Could not fetch your profile: {e}\n"
            "Your API key may be invalid or expired — run `mw auth login <username>` to refresh it."
        ) from e
    click.echo(json.dumps(user, indent=2))


@auth_group.command("key")
def auth_key() -> None:
    """Mint a new API key for the current user (rotates the old one)."""
    from benchmark.backend import BackendError

    try:
        client = auth.make_client(require=True)
    except PermissionError as e:
        raise click.UsageError(str(e)) from e
    try:
        with client:
            api_key = client.mint_key()
            user = client.whoami()
    except BackendError as e:
        raise click.ClickException(
            f"Could not rotate your API key: {e}\n"
            "Your API key may be invalid or expired — run `mw auth login <username>` to refresh it."
        ) from e
    auth.save_credentials(auth.resolve()[0], api_key, user)
    click.echo(f"New API key saved to {auth.CREDENTIALS_PATH}")
    click.echo(api_key)


@auth_group.command("logout")
def auth_logout() -> None:
    """Remove saved credentials."""
    if auth.clear_credentials():
        click.echo(f"Removed {auth.CREDENTIALS_PATH}")
    else:
        click.echo("No saved credentials.")


# ---------- bench ----------


@cli.group()
def bench():
    """Run and inspect benchmark runs."""


@bench.command("run")
@click.option(
    "--model",
    required=True,
    type=click.Choice(sorted(MODEL_CONFIG.keys())),
    help="Model to evaluate.",
)
@click.option(
    "--tasks",
    "tasks_spec",
    default="smoke",
    show_default=True,
    help="'smoke', 'all', or comma-separated task IDs.",
)
@click.option(
    "--run-id",
    default=None,
    help="Override the run directory name (default: <model>-<ts>).",
)
@click.option(
    "--backend",
    type=click.Choice(["use-computer", "kvm"]),
    default="use-computer",
    show_default=True,
    help="Sandbox backend: managed Use Computer SDK, or a local QEMU/KVM fleet.",
)
@click.option(
    "--trials",
    type=click.IntRange(min=1),
    default=1,
    show_default=True,
    help="Rollouts per task (pass@k). >1 writes trials.json and a per-task aggregate.",
)
@click.option(
    "--pass-threshold",
    type=float,
    default=0.99,
    show_default=True,
    help="Fraction of max_score a trial must reach to count as a pass.",
)
@click.option(
    "--no-push",
    is_flag=True,
    default=False,
    help="Don't push results to the backend; keep them local only.",
)
@click.option(
    "--kvm-fleet-size",
    type=int,
    default=lambda: int(os.getenv("MACOSWORLD_KVM_FLEET_SIZE", "4")),
    help="KVM only: number of guests to pre-warm and run rollouts across.",
)
@click.option(
    "--kvm-host",
    default=lambda: os.getenv("MACOSWORLD_KVM_HOST", "localhost"),
    help="KVM only: host where Docker runs and ports are reachable.",
)
@click.option(
    "--kvm-base-volume",
    default=None,
    help="KVM only: path to the gold base volume to clone (defaults to env/spike path).",
)
@click.option(
    "--env",
    "env_pkg",
    default=None,
    help="KVM only: path to an env.toml (or its dir) describing an os-base ← +apps "
    "layer chain (RFC 0002). Resolves the base + top layer instead of --kvm-base-volume.",
)
@click.option("--kvm-ram-gb", type=int, default=4, show_default=True, help="KVM only: RAM per guest.")
@click.option("--kvm-vcpu", type=int, default=4, show_default=True, help="KVM only: vCPUs per guest.")
@click.option(
    "--kvm-ssh-key",
    default=None,
    help="KVM only: path (on this machine) to the id_kvm private key for guest SSH.",
)
@click.option(
    "--kvm-ssh-login",
    default=None,
    help="KVM only: host login used to run docker over SSH when --kvm-host is remote.",
)
@click.option(
    "--kvm-disk-mode",
    type=click.Choice(["overlay", "copy"]),
    default="overlay",
    show_default=True,
    help="KVM only: 'overlay' (default) = thin qcow2 overlay over a shared read-only "
    "base (near-instant clones, ~MBs/guest, FS-agnostic); 'copy' = full per-guest copy (fallback).",
)
@click.option(
    "--kvm-app-tunnel",
    default=None,
    help="KVM only: reverse-tunnel host ports into each guest's localhost (for a sidecar "
    "like the MyPCBench apps container). Accepts a range '3001-3017' or a list '3001,3016'. "
    "Requires the sidecar to be listening on those host ports (e.g. run-apps.sh up).",
)
def bench_run(
    model: str,
    tasks_spec: str,
    run_id: str | None,
    backend: str,
    trials: int,
    pass_threshold: float,
    no_push: bool,
    kvm_fleet_size: int,
    kvm_host: str,
    kvm_base_volume: str | None,
    env_pkg: str | None,
    kvm_ram_gb: int,
    kvm_vcpu: int,
    kvm_ssh_key: str | None,
    kvm_ssh_login: str | None,
    kvm_disk_mode: str,
    kvm_app_tunnel: str | None,
) -> None:
    """Run benchmark tasks against a model."""
    provider = MODEL_CONFIG[model].provider
    if provider == "anthropic" and not os.getenv("ANTHROPIC_API_KEY"):
        raise click.UsageError("ANTHROPIC_API_KEY not set (required for claude-* models)")
    if provider == "yutori" and not os.getenv("YUTORI_API_KEY"):
        raise click.UsageError("YUTORI_API_KEY not set (required for n1.5-* models)")
    if backend == "use-computer" and not os.getenv("USE_COMPUTER_API_KEY"):
        raise click.UsageError("USE_COMPUTER_API_KEY not set (required for --backend use-computer)")

    # Resolve backend auth before the (expensive) run. No credential -> run
    # local-only (no abort, so the benchmark is still usable without a key); a
    # present-but-broken credential warns and continues rather than losing a run.
    push_client = None
    if not _push_enabled(no_push):
        click.echo("Push   : disabled (local only)")
    elif not auth.resolve()[1]:
        click.echo("Push   : no credentials — running local only (run `mw auth login` to record runs)")
    else:
        try:
            push_client = auth.make_client(require=True)
            user = push_client.whoami()
            click.echo(f"Push   : enabled as {user.get('username', '?')}")
        except Exception as e:
            push_client = None
            click.echo(f"Push   : backend unavailable ({e}) — running local only", err=True)

    if tasks_spec == "smoke":
        tasks = load_tasks(smoke=True)
    elif tasks_spec == "all":
        tasks = load_tasks()
    else:
        tasks = load_tasks(ids=[t.strip() for t in tasks_spec.split(",") if t.strip()])

    run_id = run_id or f"{model}-{backend}-{int(time.time())}"
    run_dir = _outputs_root() / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    # (task, trial) work items, trial-major. trial=None when trials==1 so the
    # output layout is byte-identical to single-trial runs.
    work_items = build_work_items(tasks, trials)
    click.echo(f"Run dir: {run_dir}")
    click.echo(f"Backend: {backend}")
    click.echo(f"Tasks  : {len(tasks)} ({', '.join(t.id[:8] for t in tasks)})")
    if trials > 1:
        click.echo(f"Trials : {trials} per task ({len(work_items)} rollouts), pass>={pass_threshold:g}")

    if backend == "kvm":
        app_tunnel_ports = _parse_tunnel_ports(kvm_app_tunnel)
        if app_tunnel_ports:
            _preflight_app_tunnel(kvm_host, app_tunnel_ports)
        resolved_env = None
        if env_pkg:
            if kvm_base_volume:
                raise click.UsageError("--env and --kvm-base-volume are mutually exclusive")
            from benchmark.env.pkg import EnvPackageError, resolve_env_package

            # On a remote box the layer dirs are box-side (the SSH host commands check
            # them); locally we validate they exist. Mirror KvmConfig.is_remote.
            local = kvm_host in ("localhost", "127.0.0.1", "")
            try:
                resolved_env = resolve_env_package(env_pkg, validate_paths=local)
            except EnvPackageError as e:
                raise click.UsageError(f"env package error: {e}") from e
            click.echo(
                f"Env    : {resolved_env.name} "
                f"(base={resolved_env.base_volume_dir}"
                + (f", +apps={resolved_env.apps_layer_dir}" if resolved_env.apps_layer_dir else ", bare base")
                + ")"
            )
        results = _run_kvm(
            model, work_items, run_dir,
            fleet_size=kvm_fleet_size, host=kvm_host,
            base_volume=kvm_base_volume, ram_gb=kvm_ram_gb, vcpu=kvm_vcpu,
            ssh_key=kvm_ssh_key, ssh_login=kvm_ssh_login, disk_mode=kvm_disk_mode,
            resolved_env=resolved_env, app_tunnel_ports=app_tunnel_ports,
        )
    else:
        # Managed SDK: sequential, one fresh sandbox per (task, trial).
        results = [run_task(model, t, run_dir, trial=k) for t, k in work_items]

    results_dicts = [asdict(r) for r in results]
    if trials > 1:
        # summary.json stays a FLAT list (one row per trial) for the dashboard;
        # fill the per-row `passed` flag (threshold lives in the CLI layer) and
        # emit the grouped pass@k report to trials.json.
        agg = aggregate_trials(results, trials, pass_threshold)
        passed_by_dir = {
            t["dir"]: t["passed"] for task in agg["tasks"].values() for t in task["trials"]
        }
        for r in results_dicts:
            r["passed"] = passed_by_dir.get(r["task_id"])
        (run_dir / "trials.json").write_text(json.dumps(agg, indent=2))
    (run_dir / "summary.json").write_text(json.dumps(results_dicts, indent=2))
    _print_summary(model, results_dicts)
    if trials > 1:
        _print_trials_summary(agg)

    if push_client is not None:
        click.echo("\nPushing results to backend…")
        try:
            info = push.push_run_dir(push_client, run_dir, delete_after=True, echo=click.echo)
            if info:
                click.echo(f"Pushed run #{info['run_id']}: {info['passed']}/{info['rollouts']} passed")
        except Exception as e:
            click.echo(
                f"WARNING: push failed ({e}). Kept local run at {run_dir}; "
                f"re-push with `mw bench push {run_id}`.",
                err=True,
            )
        finally:
            push_client.close()


def _parse_tunnel_ports(spec: str | None) -> tuple[int, ...]:
    """Parse '3001-3017' or '3001,3016' into a sorted unique port tuple. () if unset."""
    if not spec:
        return ()
    ports: set[int] = set()
    for chunk in spec.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        if "-" in chunk:
            lo, hi = chunk.split("-", 1)
            ports.update(range(int(lo), int(hi) + 1))
        else:
            ports.add(int(chunk))
    return tuple(sorted(ports))


def _preflight_app_tunnel(host: str, ports: tuple[int, ...]) -> None:
    """Warn (don't fail) if the host sidecar isn't listening on the first tunnel port."""
    import socket

    probe_host = "127.0.0.1" if host in ("localhost", "127.0.0.1", "") else host
    p = ports[0]
    try:
        with socket.create_connection((probe_host, p), timeout=2):
            return
    except OSError:
        click.echo(
            f"WARNING: nothing is listening on {probe_host}:{p} — the app reverse-tunnel "
            f"will have no backend. Start the MyPCBench apps first "
            f"(e.g. `just mypcbench-apps up`, or `infra/mypcbench/run-apps.sh up`).",
            err=True,
        )


def _run_kvm(model, work_items, run_dir, *, fleet_size, host, base_volume, ram_gb, vcpu,
             ssh_key=None, ssh_login=None, disk_mode="overlay", resolved_env=None,
             app_tunnel_ports=()):
    """Boot a KVM fleet, run all (task, trial) items concurrently across it, tear down."""
    from concurrent.futures import ThreadPoolExecutor

    from benchmark.env.kvm import KvmConfig, KvmFleet

    cfg_kwargs = dict(fleet_size=fleet_size, host=host, ram_gb=ram_gb, vcpu=vcpu,
                      disk_mode=disk_mode, app_tunnel_ports=app_tunnel_ports)
    if resolved_env is not None:
        # An env package supplies the os-base (as the base_volume source) and, optionally,
        # the top +apps layer the instance overlay backs onto. Takes the place of
        # --kvm-base-volume; the two are mutually exclusive (enforced in bench_run).
        cfg_kwargs["base_volume"] = str(resolved_env.base_volume_dir)
        cfg_kwargs["macos_version"] = resolved_env.macos_version
        if resolved_env.apps_layer_dir is not None:
            cfg_kwargs["apps_layer_dir"] = str(resolved_env.apps_layer_dir)
    if base_volume:
        cfg_kwargs["base_volume"] = base_volume
    if ssh_key:
        cfg_kwargs["ssh_key"] = ssh_key
    if ssh_login:
        cfg_kwargs["ssh_login"] = ssh_login
    fleet = KvmFleet(KvmConfig(**cfg_kwargs)).boot()
    parallelism = max(1, fleet.size)
    click.echo(f"Fleet  : {fleet.size} guest(s), running {parallelism}-way concurrent")
    try:
        with ThreadPoolExecutor(max_workers=parallelism) as pool:
            return list(
                pool.map(
                    lambda item: run_task(
                        model, item[0], run_dir, backend="kvm", fleet=fleet, trial=item[1]
                    ),
                    work_items,
                )
            )
    finally:
        fleet.teardown()


@bench.command("list")
def bench_list() -> None:
    """List run directories under outputs/runs/."""
    root = _outputs_root()
    if not root.exists():
        click.echo(f"(no runs — {root} does not exist)")
        return
    runs = sorted([p for p in root.iterdir() if p.is_dir()], key=lambda p: p.stat().st_mtime)
    if not runs:
        click.echo(f"(no runs in {root})")
        return
    click.echo(f"{'run_id':<40} {'tasks':>6} {'summary':<8}")
    for run in runs:
        summary = run / "summary.json"
        n_tasks = "?"
        has_summary = "yes" if summary.exists() else "no"
        if summary.exists():
            try:
                data = json.loads(summary.read_text())
                n_tasks = str(len(data))
            except Exception:
                n_tasks = "err"
        click.echo(f"{run.name:<40} {n_tasks:>6} {has_summary:<8}")


@bench.command("show")
@click.argument("run_id")
def bench_show(run_id: str) -> None:
    """Show summary.json for a run."""
    run_dir = _outputs_root() / run_id
    summary = run_dir / "summary.json"
    if not summary.exists():
        raise click.ClickException(f"No summary.json at {summary}")
    results = json.loads(summary.read_text())
    model = results[0]["model"] if results else "?"
    _print_summary(model, results)


@bench.command("push")
@click.argument("run_id")
@click.option("--keep", is_flag=True, default=False, help="Keep the local run dir after a successful push.")
def bench_push(run_id: str, keep: bool) -> None:
    """Push (or re-push) a local run to the backend."""
    run_dir = _outputs_root() / run_id
    if not (run_dir / "summary.json").exists():
        raise click.ClickException(f"No summary.json at {run_dir}")
    try:
        client = auth.make_client(require=True)
    except PermissionError as e:
        raise click.UsageError(str(e)) from e
    with client:
        info = push.push_run_dir(client, run_dir, delete_after=not keep, echo=click.echo)
    if info:
        click.echo(f"Pushed run #{info['run_id']}: {info['passed']}/{info['rollouts']} passed")


# ---------- tasks ----------


@cli.group("tasks")
def tasks_group():
    """Inspect task definitions."""


@tasks_group.command("list")
@click.option("--category", default=None, help="Filter by category directory name.")
def tasks_list(category: str | None) -> None:
    """List task IDs grouped by category."""
    paths = sorted(TASKS_ROOT.glob("*/*.json"))
    by_cat: dict[str, list[str]] = {}
    for p in paths:
        by_cat.setdefault(p.parent.name, []).append(p.stem)
    for cat in sorted(by_cat):
        if category and cat != category:
            continue
        click.echo(f"\n{cat} ({len(by_cat[cat])})")
        for tid in by_cat[cat]:
            click.echo(f"  {tid}")


@tasks_group.command("show")
@click.argument("task_id")
def tasks_show(task_id: str) -> None:
    """Show the JSON definition of a single task."""
    matches = list(TASKS_ROOT.glob(f"*/{task_id}.json"))
    if not matches:
        raise click.ClickException(f"Task {task_id} not found under {TASKS_ROOT}")
    click.echo(matches[0].read_text())


@tasks_group.command("push")
@click.option("--category", default=None, help="Only register tasks in this category.")
def tasks_push(category: str | None) -> None:
    """Register local tasks in the backend (idempotent; keyed on local_task_id)."""
    tasks = load_tasks()
    if category:
        tasks = [t for t in tasks if t.category == category]
    if not tasks:
        raise click.ClickException("No tasks to register.")
    try:
        client = auth.make_client(require=True)
    except PermissionError as e:
        raise click.UsageError(str(e)) from e
    with client:
        mapping = push.ensure_tasks(client, tasks)
    click.echo(f"Synced {len(mapping)} task(s) to the backend.")
    for tid in sorted(mapping):
        click.echo(f"  {tid} -> #{mapping[tid]}")


# ---------- sandbox ----------


@cli.group()
def sandbox():
    """Manage Use Computer sandboxes."""


@sandbox.command("open")
@click.option(
    "--sandbox-id",
    default=None,
    help="Connect to an existing sandbox instead of creating a new one.",
)
@click.option(
    "--backend",
    type=click.Choice(["use-computer", "kvm"]),
    default="use-computer",
    show_default=True,
    help="Sandbox backend to open.",
)
@click.option(
    "--kvm-host",
    default=lambda: os.getenv("MACOSWORLD_KVM_HOST", "localhost"),
    help="KVM only: host where Docker runs and ports are reachable.",
)
def sandbox_open(sandbox_id: str | None, backend: str, kvm_host: str) -> None:
    """Open (or reconnect to) a macOS sandbox and launch noVNC in the browser."""
    if backend == "kvm":
        _sandbox_open_kvm(kvm_host)
        return

    if not os.getenv("USE_COMPUTER_API_KEY"):
        raise click.UsageError("USE_COMPUTER_API_KEY not set")

    from use_computer import Computer, SandboxType

    base_url = os.environ.get("USE_COMPUTER_BASE_URL", "https://api.dev.use.computer")
    api_key = os.environ["USE_COMPUTER_API_KEY"]
    client = Computer(api_key=api_key, base_url=base_url)

    if sandbox_id:
        click.echo(f"Connecting to existing sandbox {sandbox_id}...")
        mac = client.get(sandbox_id)
        created = False
    else:
        click.echo("Booting macOS sandbox...")
        mac = client.create(type=SandboxType.MACOS)
        created = True

    url = f"{base_url}/vnc?sandbox={mac.sandbox_id}&token={api_key}"
    click.echo(f"sandbox_id : {mac.sandbox_id}")
    click.echo(f"ssh_url    : {getattr(mac, 'ssh_url', '')}")
    click.echo(f"vnc_url    : {url}")

    mac.start_keepalive()
    webbrowser.open(url)

    try:
        suffix = " and shut down the sandbox..." if created else "..."
        input(f"\nVNC opened in browser. Press Enter to exit{suffix}")
    finally:
        mac.stop_keepalive()
        if created:
            click.echo("Closing sandbox...")
            mac.close()
        client.close()


def _sandbox_open_kvm(host: str) -> None:
    """Boot a single KVM guest, print connection info, open dockur's web VNC."""
    from benchmark.env.kvm import KvmConfig, KvmFleet

    click.echo("Booting one KVM macOS guest...")
    fleet = KvmFleet(KvmConfig(fleet_size=1, host=host)).boot()
    try:
        slot = fleet.acquire()
        web_url = f"http://{host}:{slot.web_port}"
        ssh_cmd = (
            f"ssh -i {slot.cfg.ssh_key} -p {slot.ssh_port} "
            f"-o StrictHostKeyChecking=no {slot.cfg.ssh_user}@{host}"
        )
        click.echo(f"container : {slot.container_name}")
        click.echo(f"ssh       : {ssh_cmd}")
        click.echo(f"vnc (web) : {web_url}")
        click.echo(f"vnc (raw) : {host}:{slot.vnc_port}")
        webbrowser.open(web_url)
        input("\nWeb VNC opened in browser. Press Enter to tear down the guest...")
    finally:
        click.echo("Tearing down KVM guest...")
        fleet.teardown()


# ---------- remote ----------


@cli.group()
def remote():
    """Run benchmarks on a remote host (rsync + tmux); detach/attach friendly."""


@remote.command("run")
@click.option("--server", default=None, help="Server name from remote.toml (default: its default_server).")
@click.option("--name", default="run", help="Session name suffix -> mw-<name>.")
@click.option("--detach/--wait", "detach", default=True, help="Detach (default) or wait for completion then pull.")
@click.argument("bench_args", nargs=-1, type=click.UNPROCESSED)
def remote_run(server: str | None, name: str, detach: bool, bench_args: tuple[str, ...]) -> None:
    """Sync the working tree to the host and run `mw bench run` there in tmux.

    Everything after `--` is passed through to `mw bench run`. The backend is
    forced to kvm/localhost (loopback data plane). Example:

        mw remote run --name smoke -- --model n1.5-latest --tasks smoke --kvm-fleet-size 5
    """
    repo_root = rmt.find_repo_root()
    cfg = rmt.load_config(repo_root, server)
    session = rmt.normalize_session(name)
    if rmt.session_is_active(cfg["host"], session):
        raise click.UsageError(
            f"Session {session} is already running on {cfg['host']}. "
            f"`mw remote attach {name}` or `mw remote stop {name}` first."
        )
    rmt.rsync_to_remote(cfg["host"], cfg["remote_dir"], repo_root, cfg["rsync_excludes"], session)
    # `--package` is required: `mw` is the workspace *member* (macosworld-usecomputer)
    # script; a fresh-venv `uv run mw` at the workspace root won't install it.
    inner = "uv run --package macosworld-usecomputer mw bench run --backend kvm --kvm-host localhost " + " ".join(
        shlex.quote(a) for a in bench_args
    )
    rmt.start_session(cfg, session, inner)
    click.echo(f"==> Started {session} on {cfg['host']}")
    click.echo(f"    attach: uv run mw remote attach {name}")
    click.echo(f"    logs:   uv run mw remote logs {name}")
    click.echo(f"    pull:   uv run mw remote pull {name}")

    if not detach:
        click.echo(f"==> Waiting (poll {cfg['poll_interval']}s); Ctrl-C just detaches, run keeps going.")
        try:
            while rmt.session_is_active(cfg["host"], session):
                time.sleep(cfg["poll_interval"])
        except KeyboardInterrupt:
            click.echo("\n==> Detached; session still running on the host.")
            return
        rmt.kill_session(cfg["host"], session)
        _remote_pull(cfg, session)


@remote.command("attach")
@click.option("--server", default=None)
@click.argument("name", default="run")
def remote_attach(server: str | None, name: str) -> None:
    """Attach to the live tmux session (Ctrl-b d to detach; the run keeps going)."""
    cfg = rmt.load_config(rmt.find_repo_root(), server)
    session = rmt.normalize_session(name)
    # -t allocates a PTY so the local terminal is wired to tmux attach.
    os.execvp("ssh", ["ssh", "-t", cfg["host"], f"tmux attach -t {session}"])


@remote.command("logs")
@click.option("--server", default=None)
@click.option("-n", "--lines", default=40, help="Lines of history before following.")
@click.argument("name", default="run")
def remote_logs(server: str | None, lines: int, name: str) -> None:
    """Follow the unified session log (agent + harness) without taking the pane."""
    cfg = rmt.load_config(rmt.find_repo_root(), server)
    session = rmt.normalize_session(name)
    log = rmt.session_log(cfg["remote_dir"], session)
    os.execvp("ssh", ["ssh", cfg["host"], f"tail -n {lines} -f {shlex.quote(log)}"])


@remote.command("list")
@click.option("--server", default=None)
def remote_list(server: str | None) -> None:
    """List live mw-* sessions on the host."""
    cfg = rmt.load_config(rmt.find_repo_root(), server)
    res = subprocess.run(
        ["ssh", cfg["host"], "tmux ls 2>/dev/null | grep '^mw-' || true"],
        capture_output=True, text=True,
    )
    out = res.stdout.strip()
    click.echo(out if out else "(no mw-* sessions)")


@remote.command("pull")
@click.option("--server", default=None)
@click.argument("name", default="run")
def remote_pull(server: str | None, name: str) -> None:
    """Pull a run's outputs back to the laptop so the dashboard can show it."""
    cfg = rmt.load_config(rmt.find_repo_root(), server)
    _remote_pull(cfg, rmt.normalize_session(name))


@remote.command("stop")
@click.option("--server", default=None)
@click.argument("name", default="run")
def remote_stop(server: str | None, name: str) -> None:
    """Kill the tmux session (the bench's own teardown releases the guests)."""
    cfg = rmt.load_config(rmt.find_repo_root(), server)
    session = rmt.normalize_session(name)
    rmt.kill_session(cfg["host"], session)
    click.echo(f"==> Killed {session}")


def _remote_pull(cfg: dict, session: str) -> None:
    rmt.pull_results(cfg["host"], cfg["remote_dir"], session, _outputs_root())


if __name__ == "__main__":
    cli()
