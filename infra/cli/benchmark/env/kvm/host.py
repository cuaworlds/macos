from __future__ import annotations

import shlex
import subprocess

from benchmark.env.kvm.config import KvmConfig


class HostError(RuntimeError):
    pass


class Host:
    """Runs control-plane commands where Docker lives.

    Local host  -> commands run directly via the shell.
    Remote host -> commands are wrapped in `ssh <login>@<host> ...`.

    Only fleet lifecycle (docker run/rm, volume clone) goes through here. The guest
    data-plane (SSH into the guest, VNC) talks straight to host:port and lives in
    ssh.py / rfb.py.
    """

    def __init__(self, cfg: KvmConfig):
        self.cfg = cfg

    # --- command execution ---

    def run(self, cmd: str, *, timeout: int = 120, check: bool = True) -> str:
        """Run a shell command on the host, return stdout (raises on non-zero if check)."""
        if self.cfg.is_remote:
            argv = [
                "ssh",
                "-o", "BatchMode=yes",
                "-o", "StrictHostKeyChecking=no",
                "-o", "ConnectTimeout=10",
                f"{self.cfg.ssh_login}@{self.cfg.host}",
                cmd,
            ]
        else:
            argv = ["bash", "-lc", cmd]
        proc = subprocess.run(
            argv, capture_output=True, text=True, timeout=timeout
        )
        if check and proc.returncode != 0:
            raise HostError(
                f"host command failed (rc={proc.returncode}): {cmd}\n"
                f"stdout: {proc.stdout}\nstderr: {proc.stderr}"
            )
        return proc.stdout

    def docker(self, args: str, *, timeout: int = 120, check: bool = True) -> str:
        return self.run(f"docker {args}", timeout=timeout, check=check)

    # --- volume management ---

    def clone_volume(self, src: str, dst: str) -> None:
        """Full copy of the base volume, then strip per-VM identity files."""
        version_dir = f"{dst}/{self.cfg.macos_version}"
        ident = " ".join(
            f"{shlex.quote(version_dir + '/' + f)}" for f in self._identity_files()
        )
        script = (
            f"rm -rf {shlex.quote(dst)} && "
            f"cp -a --sparse=always {shlex.quote(src)} {shlex.quote(dst)} && "
            f"rm -f {ident} 2>/dev/null || true"
        )
        # Cloning a 16 GiB volume on ext4 is ~20s; give it generous headroom.
        self.run(script, timeout=600)

    def _identity_files(self) -> tuple[str, ...]:
        from benchmark.env.kvm.config import IDENTITY_FILES

        return IDENTITY_FILES

    def remove_volume(self, path: str) -> None:
        self.run(f"rm -rf {shlex.quote(path)}", check=False)

    # --- overlay (qcow2 backing-chain) clones ---

    def ensure_qcow2_base(self) -> str:
        """Ensure a shared read-only base.qcow2 exists (convert the raw base once).

        Cached across runs — the convert only happens the first time. Returns the
        in-host dir holding `<version>/data.qcow2`.
        """
        cfg = self.cfg
        ver = cfg.macos_version
        qbase_dir = f"{cfg.qcow2_base_dir}/{ver}"
        qbase = f"{qbase_dir}/data.qcow2"
        present = self.run(
            f"test -f {shlex.quote(qbase)} && echo yes || echo no"
        ).strip().endswith("yes")
        if present:
            return qbase_dir
        self.run(f"mkdir -p {shlex.quote(qbase_dir)}")
        src_dir = f"{cfg.base_volume}/{ver}"
        # qemu-img ships inside the dockur image; run it there (no qemu needed on host).
        self.run(
            f"docker run --rm --entrypoint qemu-img "
            f"-v {shlex.quote(src_dir)}:/src:ro -v {shlex.quote(qbase_dir)}:/out "
            f"{shlex.quote(cfg.image)} convert -f raw -O qcow2 /src/data.img /out/data.qcow2",
            timeout=900,
        )
        return qbase_dir

    def make_overlay_clone(self, dst: str) -> None:
        """Create a per-guest clone as a thin qcow2 overlay over the top immutable layer.

        Copies only the small companion files (base.dmg / firmware / NVRAM) and a
        KB-sized overlay whose backing file is the shared base (mounted read-only at
        /base inside the container at boot). Identity files are omitted so dockur
        regenerates a unique MAC/serial per guest.

        Bare base (no +apps layer): the instance overlay backs onto /base/data.qcow2 —
        chain is instance -> os-base.

        With a +apps layer (RFC 0002 §7.1 L1): we mount BOTH the shared base at /base
        (so the +apps layer's own backing_file=/base/data.qcow2 resolves) AND the
        frozen +apps layer at /apps, and parent the instance overlay onto
        /apps/data.qcow2 — chain is instance -> +apps -> os-base. This is the literal
        2-deep recipe documented at the tail of freeze-layer.sh.
        """
        cfg = self.cfg
        ver = cfg.macos_version
        dstv = f"{dst}/{ver}"
        qbase_dir = f"{cfg.qcow2_base_dir}/{ver}"
        raw = f"{cfg.base_volume}/{ver}"
        companions = " ".join(
            shlex.quote(f"{raw}/{f}") for f in ("base.dmg", "macos.rom", "macos.vars")
        )
        if cfg.has_apps_layer:
            apps_dir = f"{cfg.apps_layer_dir}/{ver}"
            # Parent the instance overlay on /apps; mount /base too so the apps layer's
            # own backing_file=/base/data.qcow2 resolves when qemu-img validates the chain.
            mounts = (
                f"-v {shlex.quote(qbase_dir)}:/base:ro "
                f"-v {shlex.quote(apps_dir)}:/apps:ro "
                f"-v {shlex.quote(dstv)}:/out"
            )
            backing = "/apps/data.qcow2"
        else:
            mounts = f"-v {shlex.quote(qbase_dir)}:/base:ro -v {shlex.quote(dstv)}:/out"
            backing = "/base/data.qcow2"
        script = (
            f"rm -rf {shlex.quote(dst)} && mkdir -p {shlex.quote(dstv)} && "
            f"cp -a {companions} {shlex.quote(dstv)}/ && "
            f"docker run --rm --entrypoint qemu-img {mounts} "
            f"{shlex.quote(cfg.image)} create -f qcow2 -F qcow2 -b {backing} /out/data.qcow2"
        )
        self.run(script, timeout=120)

    # --- container lifecycle ---

    def run_container(
        self,
        *,
        name: str,
        volume: str,
        ssh_port: int,
        vnc_port: int,
        web_port: int,
    ) -> None:
        cfg = self.cfg
        # Overlay mode: mount the shared base.qcow2 read-only at /base (the path the
        # overlay's backing_file records) and tell dockur the disk is qcow2.
        overlay = ""
        if cfg.disk_mode == "overlay":
            qbase_dir = f"{cfg.qcow2_base_dir}/{cfg.macos_version}"
            overlay = f"-v {shlex.quote(qbase_dir)}:/base:ro -e DISK_FMT=qcow2 "
            # With a +apps layer the instance overlay's backing_file=/apps/data.qcow2
            # (and that layer's own backing_file=/base/data.qcow2) must both resolve
            # inside the boot container — so mount the frozen +apps layer read-only at
            # /apps as well. Chain at runtime: instance -> /apps -> /base.
            if cfg.has_apps_layer:
                apps_dir = f"{cfg.apps_layer_dir}/{cfg.macos_version}"
                overlay += f"-v {shlex.quote(apps_dir)}:/apps:ro "
        argv = (
            f"run -d --name {shlex.quote(name)} "
            f"--device /dev/kvm --device /dev/net/tun --cap-add NET_ADMIN "
            f"-e VERSION={shlex.quote(cfg.macos_version)} "
            f"-e RAM_SIZE={cfg.ram_gb}G -e CPU_CORES={cfg.vcpu} "
            f"-e DISK_SIZE={shlex.quote(cfg.disk_size)} "
            f"{overlay}"
            f"-p {ssh_port}:22 -p {vnc_port}:5900 -p {web_port}:8006 "
            f"-v {shlex.quote(volume)}:/storage "
            f"--stop-timeout 60 "
            f"{shlex.quote(cfg.image)}"
        )
        self.docker(argv, timeout=120)

    def stop_container(self, name: str) -> None:
        self.docker(f"stop {shlex.quote(name)}", timeout=90, check=False)

    def remove_container(self, name: str) -> None:
        self.docker(f"rm -f {shlex.quote(name)}", timeout=90, check=False)
