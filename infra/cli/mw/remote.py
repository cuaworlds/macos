"""Run benchmarks on a remote host via rsync + tmux.

Ported from the sibling `helix` CLI (`helix_cli/remote.py`) — a deliberately lean
design: no git on the remote (rsync the working tree into an isolated per-session
snapshot), a detached tmux session that survives SSH disconnect / laptop sleep, and
secrets forwarded from the local environment rather than pre-placed on the host.

The remote process runs `mw bench run --backend kvm --kvm-host localhost ...`, so the
heavy data plane (RFB framebuffer + guest SSH) is loopback on the host instead of
crossing the WAN. Only rsync (code in, results out) and log text travel the network.

Config lives in `remote.toml` at the repo root. Host is assumed pre-provisioned
(rsync, uv, tmux on PATH).
"""

from __future__ import annotations

import os
import shlex
import subprocess
import tomllib
from pathlib import Path

import click

SESSION_PREFIX = "mw-"


def find_repo_root() -> Path:
    """Walk up from CWD to the directory containing remote.toml."""
    p = Path.cwd()
    while p != p.parent:
        if (p / "remote.toml").exists():
            return p
        p = p.parent
    raise click.ClickException("Could not find remote.toml in any parent directory")


def load_config(repo_root: Path, server_name: str | None = None, *, resolve_remote_dir: bool = True) -> dict:
    """Load remote.toml and return the merged server + settings config.

    `remote_dir` is eagerly resolved to an absolute path on the host (one
    `ssh cd && pwd`) so `~`/relative dirs work and downstream paths are stable.
    """
    with open(repo_root / "remote.toml", "rb") as f:
        raw = tomllib.load(f)

    settings = raw.get("settings", {})
    servers = raw.get("servers", {})
    name = server_name or settings.get("default_server")
    if not name or name not in servers:
        available = ", ".join(servers) or "(none)"
        raise click.ClickException(f"Server {name!r} not in remote.toml. Available: {available}")

    server = servers[name]
    host = server["host"]
    remote_dir = server["remote_dir"]
    if resolve_remote_dir:
        res = subprocess.run(["ssh", host, f"cd {remote_dir} && pwd"], capture_output=True, text=True)
        if res.returncode != 0:
            raise click.ClickException(
                f"Failed to resolve remote_dir {remote_dir!r} on {host}:\n{res.stderr.strip()}\n"
                f"(create it: ssh {host} 'mkdir -p {remote_dir}')"
            )
        remote_dir = res.stdout.strip()

    return {
        "server_name": name,
        "host": host,
        "remote_dir": remote_dir,
        "shell_prefix": server.get("shell_prefix", ""),
        "env_file": server.get("env_file", ""),
        # Static env exported verbatim on the remote (e.g. host paths the harness
        # needs). Distinct from forward_env, which copies the local process's values.
        "env": server.get("env", {}),
        "poll_interval": settings.get("poll_interval", 30),
        "rsync_excludes": settings.get(
            "rsync_excludes",
            [".git", ".venv", "node_modules", "__pycache__", "outputs"],
        ),
        "forward_env": settings.get("forward_env", []),
    }


# --- low-level helpers -------------------------------------------------------


def _run(cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
    res = subprocess.run(cmd, **kwargs)
    if res.returncode != 0 and kwargs.get("check") is not False:
        raise click.ClickException(f"Command failed: {' '.join(cmd)}")
    return res


def _ssh(host: str, remote_cmd: str) -> list[str]:
    return ["ssh", host, remote_cmd]


def snapshot_dir(remote_dir: str, session: str) -> str:
    return f"{remote_dir}/runs/{session}"


def session_log(remote_dir: str, session: str) -> str:
    return f"{snapshot_dir(remote_dir, session)}/session.log"


def normalize_session(name: str) -> str:
    """`mw-`-prefixed, filesystem/tmux-safe session name (exactly one prefix)."""
    base = name[len(SESSION_PREFIX):] if name.startswith(SESSION_PREFIX) else name
    safe = "".join(c if c.isalnum() or c in "-_" else "-" for c in base).strip("-") or "run"
    return f"{SESSION_PREFIX}{safe}"


def _env_exports(forward_env: list[str], static_env: dict | None = None) -> str:
    """`&& export VAR=...` lines (shlex-quoted).

    `forward_env`: names copied from the *local* process env (e.g. secrets).
    `static_env`: literal name->value pairs from config (e.g. host paths).
    """
    out = ""
    for var in forward_env:
        val = os.environ.get(var, "")
        if val:
            out += f" && export {var}={shlex.quote(val)}"
    for var, val in (static_env or {}).items():
        out += f" && export {var}={shlex.quote(str(val))}"
    return out


# --- rsync + tmux primitives -------------------------------------------------


def rsync_to_remote(host: str, remote_dir: str, repo_root: Path, excludes: list[str], session: str) -> None:
    """Sync the working tree into an isolated per-session snapshot (no git)."""
    snap = snapshot_dir(remote_dir, session)
    click.echo(f"==> Syncing code -> {host}:{snap}/ ...")
    _run(_ssh(host, f"mkdir -p {snap}"))
    cmd = ["rsync", "-az", "--delete"]
    for ex in excludes:
        cmd += ["--exclude", ex]
    cmd += [f"{repo_root}/", f"{host}:{snap}/"]
    _run(cmd)
    click.echo("==> Sync complete.")


def has_session(host: str, session: str) -> bool:
    res = subprocess.run(_ssh(host, f"tmux has-session -t {session} 2>/dev/null"), capture_output=True)
    return res.returncode == 0


def session_is_active(host: str, session: str) -> bool:
    """True only if the session exists AND has a live (non-dead) pane."""
    check = (
        f"tmux has-session -t {session} 2>/dev/null && "
        f"tmux list-panes -t {session} -F '#{{pane_dead}}' 2>/dev/null | grep -q '^0$'"
    )
    return subprocess.run(_ssh(host, check), capture_output=True).returncode == 0


def kill_session(host: str, session: str) -> None:
    subprocess.run(_ssh(host, f"tmux kill-session -t {session} 2>/dev/null || true"), capture_output=True)


def start_session(cfg: dict, session: str, inner_cmd: str) -> None:
    """Launch `inner_cmd` in a detached tmux session inside the snapshot dir.

    Runs under `bash -lc` so the host's login PATH (uv, etc.) is available, sources
    an optional env_file, forwards local secrets, then execs the command. A
    `tmux pipe-pane` copies all pane output to session.log for `mw remote logs`.
    """
    host, remote_dir = cfg["host"], cfg["remote_dir"]
    snap = snapshot_dir(remote_dir, session)
    log = session_log(remote_dir, session)

    source_env = f"source {shlex.quote(cfg['env_file'])} 2>/dev/null; " if cfg["env_file"] else ""
    exports = _env_exports(cfg["forward_env"], cfg.get("env"))
    # The benchmark command, run from the snapshot with login PATH + forwarded env.
    inner = f"bash -lc {shlex.quote(f'cd {snap} && {source_env}true{exports} && {inner_cmd}')}"
    if cfg["shell_prefix"]:
        inner = f"{cfg['shell_prefix']} {inner}"

    new_session = f"tmux new-session -d -s {session} {shlex.quote(inner)}"
    pipe = f"tmux pipe-pane -t {session} -o {shlex.quote(f'cat >> {log}')}"
    _run(_ssh(host, f"mkdir -p {snap} && {new_session} && {pipe}"))


def pull_results(host: str, remote_dir: str, session: str, local_outputs_runs: Path) -> None:
    """Rsync the run's outputs/runs/ back to the laptop for the dashboard."""
    snap = snapshot_dir(remote_dir, session)
    local_outputs_runs.mkdir(parents=True, exist_ok=True)
    click.echo(f"==> Pulling {host}:{snap}/outputs/runs/ -> {local_outputs_runs}/ ...")
    # check=False: a run that produced no outputs yet shouldn't hard-fail the pull.
    _run(
        ["rsync", "-az", f"{host}:{snap}/outputs/runs/", f"{local_outputs_runs}/"],
        check=False,
    )
    click.echo("==> Results pulled.")
