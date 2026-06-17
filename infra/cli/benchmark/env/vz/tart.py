"""Tart/VZ host-control: clone, boot, scrape VNC, resolve SSH, regen ECID, delete.

This is the VZ analogue of `env/kvm/host.py` — every host-side shell-out that
stands a Tart-launched macOS guest up and tears it down. `VzMacOSEnv`
(env/vz/__init__.py) sits ABOVE this and only ever calls these methods; it never
shells out to `tart` itself, so the Env-protocol surface stays substrate-blind.

Key facts this module encapsulates (all proven in W2 / vz-feasibility.md):
  * `tart clone <src> <dst>` is an APFS clonefile (CoW, ~0.07s, 4-8 KiB) AND
    regenerates the guest MAC — the right primitive for instance creation + reset.
  * `tart run --vnc-experimental --no-graphics <vm>` prints exactly one line
    `vnc://:<password>@127.0.0.1:<port>` on stdout once the VZ VNC server is up;
    we scrape host/port/password from it for RfbClient(vnc_password=...).
  * `tart ip <vm>` returns the guest's 192.168.64.x VZ-NAT address for SSH.
  * The guest's Hardware-UUID/serial derive from the ECID in config.json
    (`{"ECID": <uint64>}` as a base64 binary plist). `tart clone` keeps the ECID,
    so concurrent siblings collide (the W2 FLAG). Regenerating it per clone — a
    deterministic config.json edit done BEFORE first boot — fixes it.
"""
from __future__ import annotations

import base64
import plistlib
import re
import secrets
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from benchmark.env.vz.config import TART_HOME, VzConfig
from benchmark.log import get_logger

log = get_logger()

# `tart run --vnc-experimental` prints this once the VZ VNC server is listening.
_VNC_LINE = re.compile(r"vnc://:(?P<pw>[^@]+)@(?P<host>[^:]+):(?P<port>\d+)")


class TartError(RuntimeError):
    pass


@dataclass
class VncEndpoint:
    host: str
    port: int
    password: str


def _run(argv: list[str], *, timeout: int = 120) -> subprocess.CompletedProcess:
    return subprocess.run(argv, capture_output=True, text=True, timeout=timeout)


def tart_available() -> bool:
    return shutil.which("tart") is not None


def vm_exists(name: str) -> bool:
    """True iff a local Tart VM by this name is registered (`tart list`)."""
    res = _run(["tart", "list", "--source", "local", "--quiet"])
    if res.returncode != 0:
        # Older tart lacks --quiet; fall back to a name scan of plain `tart list`.
        res = _run(["tart", "list"])
        return any(name == line.split()[-1] for line in res.stdout.splitlines() if line.strip())
    return name in res.stdout.split()


def clone(src: str, dst: str) -> None:
    """`tart clone src dst` — APFS clonefile (CoW) + MAC regen. Idempotent-ish:
    raises if dst already exists (caller deletes first for reset)."""
    res = _run(["tart", "clone", src, dst], timeout=600)
    if res.returncode != 0:
        raise TartError(f"tart clone {src} -> {dst} failed: {res.stderr.strip()}")


def delete(name: str) -> None:
    """`tart delete name` — best-effort; a missing VM is not an error for cleanup."""
    res = _run(["tart", "delete", name], timeout=120)
    if res.returncode != 0 and "does not exist" not in (res.stderr or "").lower():
        log.warning(f"[vz] tart delete {name} warning: {res.stderr.strip()}")


def stop(name: str, *, timeout: int = 60) -> None:
    """`tart stop name` — graceful guest shutdown (best-effort)."""
    res = _run(["tart", "stop", name], timeout=timeout)
    if res.returncode != 0 and "not running" not in (res.stderr or "").lower():
        log.info(f"[vz] tart stop {name}: {res.stderr.strip()}")


def bundle_dir(name: str) -> Path:
    """Path to a local VM bundle dir ({disk.img, config.json, nvram.bin})."""
    return TART_HOME / "vms" / name


def disk_and_nvram(name: str) -> tuple[Path, Path]:
    d = bundle_dir(name)
    return d / "disk.img", d / "nvram.bin"


# --- ECID regen (the W2 FLAG fix) -------------------------------------------

def read_ecid(name: str) -> int:
    """Decode the integer ECID from a VM bundle's config.json."""
    cfg = bundle_dir(name) / "config.json"
    import json

    data = json.loads(cfg.read_text())
    raw = base64.b64decode(data["ecid"])
    return plistlib.loads(raw)["ECID"]


def regen_ecid(name: str) -> tuple[int, int]:
    """Write a fresh random 64-bit ECID into the VM bundle's config.json.

    The guest derives its IOPlatformUUID + serial from this ECID, so a distinct
    ECID per clone gives concurrent siblings distinct Hardware-UUID/serial — the
    W2 FLAG fix. Must run on a STOPPED bundle, BEFORE first boot, so the guest
    builds its identity from the new value. Returns (old_ecid, new_ecid).

    The ECID field is a base64-encoded binary plist {"ECID": <uint64>}; we keep
    that exact shape (binary plist, like Tart writes it) so Tart re-reads it.
    """
    import json

    cfg = bundle_dir(name) / "config.json"
    data = json.loads(cfg.read_text())
    old = plistlib.loads(base64.b64decode(data["ecid"]))["ECID"]
    # 64-bit unsigned, non-zero. Apple ECIDs are 64-bit; any distinct value de-collides.
    new = secrets.randbits(64) or 1
    blob = plistlib.dumps({"ECID": new}, fmt=plistlib.FMT_BINARY)
    data["ecid"] = base64.b64encode(blob).decode("ascii")
    cfg.write_text(json.dumps(data))
    log.info(f"[vz] regen ECID for {name}: {old} -> {new}")
    return old, new


# --- boot + VNC scrape ------------------------------------------------------

class TartRun:
    """A backgrounded `tart run --vnc-experimental --no-graphics <vm>` process.

    Owns the subprocess + the scraped VNC endpoint. `stop()`/context-exit
    terminates the run process (which shuts the guest down); the bundle remains
    on disk until `tart delete` (done by VzMacOSEnv.close / reset)."""

    def __init__(self, name: str):
        self.name = name
        self.proc: subprocess.Popen | None = None
        self.vnc: VncEndpoint | None = None
        self._stdout_lines: list[str] = []

    def start(self, *, vnc_scrape_timeout: int = 120) -> VncEndpoint:
        """Launch the guest and scrape its `vnc://:<pw>@host:<port>` line."""
        argv = ["tart", "run", "--vnc-experimental", "--no-graphics", self.name]
        log.info(f"[vz] launching: {' '.join(argv)}")
        self.proc = subprocess.Popen(
            argv,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        deadline = time.time() + vnc_scrape_timeout
        assert self.proc.stdout is not None
        while time.time() < deadline:
            line = self.proc.stdout.readline()
            if not line:
                if self.proc.poll() is not None:
                    out = "".join(self._stdout_lines)
                    raise TartError(
                        f"tart run {self.name} exited before printing a vnc:// line "
                        f"(rc={self.proc.returncode}):\n{out[-500:]}"
                    )
                time.sleep(0.1)
                continue
            self._stdout_lines.append(line)
            m = _VNC_LINE.search(line)
            if m:
                self.vnc = VncEndpoint(
                    host=m.group("host"),
                    port=int(m.group("port")),
                    password=m.group("pw"),
                )
                log.info(f"[vz] {self.name} VNC up at {self.vnc.host}:{self.vnc.port}")
                return self.vnc
        raise TartError(
            f"tart run {self.name}: no vnc:// line within {vnc_scrape_timeout}s\n"
            + "".join(self._stdout_lines[-20:])
        )

    def is_alive(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def stop(self) -> None:
        """Terminate the run process (shuts the guest down). Best-effort."""
        # Ask Tart for a graceful stop first so the guest filesystem quiesces, then
        # terminate the run subprocess if it lingers.
        try:
            stop(self.name, timeout=45)
        except Exception as e:  # noqa: BLE001
            log.info(f"[vz] graceful stop {self.name} warning: {e}")
        if self.proc is None:
            return
        if self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=20)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                try:
                    self.proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    log.warning(f"[vz] tart run {self.name} would not die")
        self.proc = None


def get_ip(name: str, *, timeout_s: int = 120, poll_s: float = 3.0) -> str:
    """Poll `tart ip <name>` until the guest gets its VZ-NAT address."""
    deadline = time.time() + timeout_s
    last = ""
    while time.time() < deadline:
        res = _run(["tart", "ip", name], timeout=15)
        ip = (res.stdout or "").strip()
        if res.returncode == 0 and ip and ip.count(".") == 3:
            return ip
        last = (res.stderr or res.stdout or "").strip()
        time.sleep(poll_s)
    raise TartError(f"tart ip {name}: no address within {timeout_s}s (last: {last!r})")


# --- SSH-key provisioning (one-time, into a running guest) ------------------

def provision_ssh_key(
    cfg: VzConfig, ip: str, pub_key_text: str, *, timeout: int = 60
) -> bool:
    """Append our public key to the guest's authorized_keys using password SSH.

    The cirruslabs base ships `admin/admin`; we use `sshpass` if available, else a
    one-shot `ssh` with the password piped (BatchMode off). Idempotent: re-adding
    the same key is harmless. Returns True on success.

    Provisioning the key is the env-build step W2 documented — once it's in the
    frozen base/+apps bundle, every clone inherits it and the harness's key-based
    GuestSsh works with no password. We keep the runtime fallback here so a base
    that was NOT pre-provisioned still works on first boot.
    """
    remote = (
        "mkdir -p ~/.ssh && chmod 700 ~/.ssh && "
        f"grep -qF '{pub_key_text.strip()}' ~/.ssh/authorized_keys 2>/dev/null || "
        f"echo '{pub_key_text.strip()}' >> ~/.ssh/authorized_keys; "
        "chmod 600 ~/.ssh/authorized_keys"
    )
    ssh_opts = [
        "-o", "StrictHostKeyChecking=no",
        "-o", "UserKnownHostsFile=/dev/null",
        "-o", "ConnectTimeout=10",
        f"{cfg.ssh_user}@{ip}",
        remote,
    ]
    if shutil.which("sshpass"):
        argv = ["sshpass", "-p", cfg.ssh_password, "ssh"] + ssh_opts
    else:
        # No sshpass: rely on SSH_ASKPASS-free password prompt being unavailable in
        # BatchMode; instead try `tart exec` if present (newer Tart can exec into a
        # running guest with its baked-in creds), else surface a clear hint.
        return _provision_via_tart_exec(cfg, remote)
    res = _run(argv, timeout=timeout)
    if res.returncode != 0:
        log.warning(f"[vz] ssh-key provision failed: {res.stderr.strip()[:200]}")
        return _provision_via_tart_exec(cfg, remote)
    return True


def _provision_via_tart_exec(cfg: VzConfig, remote_cmd: str) -> bool:
    """Fallback: provision via `tart exec` (uses Tart's baked-in guest creds)."""
    res = _run(
        ["tart", "exec", cfg.instance_name, "bash", "-lc", remote_cmd], timeout=60
    )
    if res.returncode != 0:
        log.warning(
            f"[vz] tart exec key-provision failed (rc={res.returncode}): "
            f"{(res.stderr or res.stdout).strip()[:200]} — "
            "pre-provision the key into the base bundle during env-build instead."
        )
        return False
    return True
