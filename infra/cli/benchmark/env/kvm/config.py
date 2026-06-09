from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

# Port bases on the host. VM i (1-indexed) gets base+i. Chosen to avoid the
# ports dockur reserves internally (5700/5900/7100/8006/8004) once mapped out.
SSH_PORT_BASE = 50200
VNC_PORT_BASE = 50300
WEB_PORT_BASE = 50400

# dockur identity files that must be stripped per-clone so each guest regenerates
# a unique MAC + serial. Relative to the version dir inside the volume (e.g. "14").
IDENTITY_FILES = ("macos.id", "macos.mac", "macos.mlb", "macos.sn")


def _default_base_volume() -> Path:
    return Path(
        os.path.expanduser(
            os.getenv("MACOSWORLD_KVM_BASE_VOLUME", "~/workspace/kvm-spike/volumes/base")
        )
    )


def _default_ssh_key() -> Path:
    return Path(
        os.path.expanduser(
            os.getenv("MACOSWORLD_KVM_SSH_KEY", "~/workspace/kvm-spike/ssh/id_kvm")
        )
    )


@dataclass
class KvmConfig:
    """Everything the fleet needs to clone, boot, and reach a set of guests.

    `host` is where Docker lives and where the mapped ports are reachable. When it
    is "localhost" the fleet shells out to docker directly; otherwise every docker
    command is run over `ssh <ssh_login>@<host>` while the guest SSH/VNC ports are
    reached directly at <host>:<port> (Tailscale-routed in our setup).
    """

    fleet_size: int = 4
    host: str = "localhost"
    ssh_login: str = "jjmachan"  # the *host* login (for remote docker control)
    base_volume: Path = field(default_factory=_default_base_volume)
    volumes_dir: Path | None = None  # defaults to base_volume.parent
    # How per-guest disks are made from the base:
    #   "overlay" (default) — convert the base to a shared read-only qcow2 once, then give
    #               each guest a thin qcow2 overlay (backing-file CoW). Near-instant clones,
    #               ~MBs per guest, filesystem-agnostic. (Validated experiment.)
    #   "copy"    — full `cp` of the 16 GiB base per guest (slow on ext4); kept as fallback.
    disk_mode: str = "overlay"
    qcow2_base_dir: Path | None = None  # overlay mode: where the shared base.qcow2 lives
    image: str = "dockurr/macos:latest"
    macos_version: str = "14"
    ram_gb: int = 4
    vcpu: int = 4
    disk_size: str = "40G"
    ssh_key: Path = field(default_factory=_default_ssh_key)
    # The base volume's macOS account is `user`; /Users/ec2-user is symlinked to its
    # home so the tasks' absolute /Users/ec2-user/... paths resolve to the real home
    # (the managed backend uses a real ec2-user account — the symlink bridges the gap).
    ssh_user: str = "user"
    ssh_port_base: int = SSH_PORT_BASE
    vnc_port_base: int = VNC_PORT_BASE
    web_port_base: int = WEB_PORT_BASE
    container_prefix: str = "mw"
    boot_timeout_s: int = 900  # 15 min ceiling per guest to reach SSH

    def __post_init__(self) -> None:
        self.base_volume = Path(self.base_volume)
        self.ssh_key = Path(self.ssh_key)
        if self.volumes_dir is None:
            self.volumes_dir = self.base_volume.parent
        else:
            self.volumes_dir = Path(self.volumes_dir)
        if self.qcow2_base_dir is None:
            self.qcow2_base_dir = self.volumes_dir / "_base_qcow2"
        else:
            self.qcow2_base_dir = Path(self.qcow2_base_dir)

    @property
    def is_remote(self) -> bool:
        return self.host not in ("localhost", "127.0.0.1", "")
