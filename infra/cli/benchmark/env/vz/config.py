"""Configuration for the VZ (Apple Virtualization.framework / Tart) backend.

Mirrors env/kvm/config.py's role: everything VzMacOSEnv needs to clone, boot,
reach (SSH+VNC), and reset a Tart-launched macOS guest. Unlike KVM there are no
host-mapped ports — Tart's VZ NAT gives every guest its own 192.168.64.x IP
(reached over `tart ip`), and `tart run --vnc-experimental` allocates a random
loopback VNC port + passphrase per boot (scraped at runtime, see tart.py).
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

# The frozen base bundle a clone/instance is made from. Defaults to the E3
# `e3-base` (a `tart clone ghcr.io/cirruslabs/macos-sequoia-base:latest`). When an
# +apps layer is folded in (E2 VZ-leg), point this at the frozen +apps bundle name.
DEFAULT_BASE_VM = "e3-base"

# The cirruslabs macos-sequoia-base image ships an `admin` account with password
# `admin` and passwordless sudo. We provision a key-based login into it during
# env-build (so the harness's key-based GuestSsh works) — see VzInstance.boot.
DEFAULT_SSH_USER = "admin"
DEFAULT_SSH_PASSWORD = "admin"

# Where Tart keeps its VM bundles. Each bundle is a dir holding
# { disk.img, config.json, nvram.bin } — the unit we content-address + freeze.
TART_HOME = Path(os.path.expanduser(os.getenv("TART_HOME", "~/.tart")))


def _default_ssh_key() -> Path:
    """Dedicated ed25519 key the harness uses to reach VZ guests.

    Generated on first boot if absent (VzInstance.boot seeds the public half into
    the guest's authorized_keys via `tart exec`/sshpass). Kept apart from the KVM
    key so the two backends never share credentials.
    """
    return Path(
        os.path.expanduser(os.getenv("MACOSWORLD_VZ_SSH_KEY", "~/.tart/_e3/id_vz"))
    )


@dataclass
class VzConfig:
    """Clone/boot/reach/reset parameters for the VZ backend.

    A single instance is launched per VzMacOSEnv (the analogue of one KVM fleet
    slot). `base_vm` is the frozen bundle every instance is `tart clone`d from;
    reset-by-discard deletes the instance and re-clones from `base_vm`.
    """

    # The frozen bundle to clone instances from (a Tart VM name under ~/.tart/vms).
    base_vm: str = DEFAULT_BASE_VM
    # Name of the per-env instance clone. Unique-per-env so concurrent siblings
    # (≤2 on this host) don't collide.
    instance_name: str = "e3-inst"
    ssh_user: str = DEFAULT_SSH_USER
    ssh_password: str = DEFAULT_SSH_PASSWORD
    ssh_key: Path = field(default_factory=_default_ssh_key)
    # VZ guests answer SSH at their own NAT IP (resolved by `tart ip`), port 22.
    ssh_port: int = 22
    # How long to wait for (a) `tart ip` to return an address and (b) SSH to answer.
    boot_timeout_s: int = 300
    ip_timeout_s: int = 120
    # Regenerate the guest's VZMacMachineIdentifier (ECID) before first boot so
    # concurrent siblings report distinct Hardware UUID / serial (W2 FLAG fix).
    regen_identity: bool = True
    # Keep the VZ virtual display awake so framebuffer reads aren't black under an
    # idle guest (W2 gotcha #1). Applied over SSH once the guest is up.
    keep_display_awake: bool = True

    def __post_init__(self) -> None:
        self.ssh_key = Path(self.ssh_key)
