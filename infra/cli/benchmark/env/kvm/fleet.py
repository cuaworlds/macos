"""KvmFleet: a pre-warmed pool of macOS guests shared across rollouts.

Owned by `mw bench run` (not by the env). Boot once before the task loop, hand out
slots via acquire()/release(), tear down once after. Mirrors the spike's ramp_v2.sh
but in-process and with a thread-safe checkout queue.
"""
from __future__ import annotations

import queue
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from benchmark.env.kvm.config import KvmConfig
from benchmark.env.kvm.host import Host
from benchmark.env.kvm.rfb import RfbClient
from benchmark.env.kvm.ssh import GuestSsh
from benchmark.log import get_logger

log = get_logger()

# Best-effort in-guest reset between rollouts. Non-fatal; the next task's
# pre_command is the real state-setup. Quit common apps + clear the dirs tasks write.
_RESET_APPS = [
    "TextEdit", "Notes", "Reminders", "Calendar", "Mail", "Safari",
    "System Settings", "Preview", "Music", "Finder",
]
_RESET_CMD = (
    "; ".join(f'osascript -e \'tell application "{a}" to quit\' 2>/dev/null' for a in _RESET_APPS)
    + "; rm -rf ~/Desktop/* ~/Documents/* 2>/dev/null; true"
)


@dataclass
class FleetSlot:
    index: int
    container_name: str
    cfg: KvmConfig
    ssh_port: int
    vnc_port: int
    web_port: int
    volume_path: str
    fleet: "KvmFleet | None" = field(default=None, repr=False, compare=False)
    # Live `ssh -R` reverse-tunnel process (app_tunnel_ports), opened at boot and
    # killed at teardown. None when the feature is off. Not part of equality/repr.
    tunnel_proc: "subprocess.Popen | None" = field(default=None, repr=False, compare=False)

    @property
    def host(self) -> str:
        return self.cfg.host

    def ssh(self) -> GuestSsh:
        return GuestSsh(self.host, self.ssh_port, self.cfg.ssh_user, str(self.cfg.ssh_key))

    def connect_rfb(self) -> RfbClient:
        return RfbClient(self.host, self.vnc_port)

    def release(self) -> None:
        """Return this warm slot to its fleet (does not destroy the VM)."""
        if self.fleet is not None:
            self.fleet.release(self)


class KvmFleet:
    def __init__(self, cfg: KvmConfig):
        self.cfg = cfg
        self.host = Host(cfg)
        self.slots: list[FleetSlot] = []
        self._available: "queue.Queue[FleetSlot]" = queue.Queue()
        self._booted = False
        self._lock = threading.Lock()

    @property
    def size(self) -> int:
        return len(self.slots)

    def _make_slot(self, i: int) -> FleetSlot:
        cfg = self.cfg
        return FleetSlot(
            index=i,
            container_name=f"{cfg.container_prefix}{i}",
            cfg=cfg,
            ssh_port=cfg.ssh_port_base + i,
            vnc_port=cfg.vnc_port_base + i,
            web_port=cfg.web_port_base + i,
            volume_path=f"{cfg.volumes_dir}/{cfg.container_prefix}{i}",
            fleet=self,
        )

    def boot(self) -> "KvmFleet":
        cfg = self.cfg
        n = cfg.fleet_size
        log.info(f"[fleet] booting {n} guest(s) on {cfg.host} from {cfg.base_volume}")

        # 1. Build slots and clone the base volume for each.
        self.slots = [self._make_slot(i) for i in range(1, n + 1)]
        # Clean any stale containers from a previous run before re-cloning their volumes.
        for slot in self.slots:
            self.host.remove_container(slot.container_name)
        if cfg.disk_mode == "overlay":
            # Convert the raw base to a shared read-only qcow2 once (cached), then give
            # each guest a thin overlay — near-instant, ~MBs per guest, FS-agnostic.
            log.info("[fleet] ensuring shared qcow2 base (overlay mode)...")
            self.host.ensure_qcow2_base()
            for slot in self.slots:
                log.info(f"[fleet] overlay clone -> {slot.volume_path}")
                self.host.make_overlay_clone(slot.volume_path)
        else:
            for slot in self.slots:
                log.info(f"[fleet] copy clone -> {slot.volume_path}")
                self.host.clone_volume(str(cfg.base_volume), slot.volume_path)

        # 2. Launch all containers, then wait for SSH on each — both in parallel.
        def launch(slot: FleetSlot) -> None:
            self.host.run_container(
                name=slot.container_name,
                volume=slot.volume_path,
                ssh_port=slot.ssh_port,
                vnc_port=slot.vnc_port,
                web_port=slot.web_port,
            )

        with ThreadPoolExecutor(max_workers=n) as pool:
            list(pool.map(launch, self.slots))
        log.info(f"[fleet] {n} container(s) launched; waiting for SSH...")

        def wait(slot: FleetSlot) -> tuple[FleetSlot, bool]:
            ready = slot.ssh().wait_until_ready(timeout_s=cfg.boot_timeout_s)
            status = "up" if ready else "TIMEOUT"
            log.info(f"[fleet] {slot.container_name} SSH {status}")
            return slot, ready

        with ThreadPoolExecutor(max_workers=n) as pool:
            results = list(pool.map(wait, self.slots))

        ready_slots = [s for s, ok in results if ok]
        if not ready_slots:
            self.teardown()
            raise RuntimeError("fleet boot failed: no guests reached SSH")
        # Optional app reverse-tunnels (e.g. MyPCBench apps): open one per ready guest
        # BEFORE the slots go on the available queue, so the first task already sees
        # localhost:<port> wired to the host sidecar.
        if cfg.app_tunnel_ports:
            for slot in ready_slots:
                self._open_app_tunnel(slot)
        for slot in ready_slots:
            self._available.put(slot)
        self.slots = ready_slots
        self._booted = True
        log.info(f"[fleet] ready: {len(ready_slots)}/{n} guest(s) usable")
        return self

    def _open_app_tunnel(self, slot: FleetSlot) -> None:
        """Open an `ssh -R` reverse tunnel: guest localhost:<port> -> host localhost:<port>.

        One `-R` per configured app port. The host side is wherever this process runs
        (the box hosting the apps sidecar). Best-effort: a failure is logged, not fatal —
        tasks that need the apps will simply fail to reach them and grade accordingly.
        """
        cfg = self.cfg
        ports = cfg.app_tunnel_ports
        argv = [
            "ssh", "-i", str(cfg.ssh_key), "-p", str(slot.ssh_port), "-N",
            "-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null",
            "-o", "BatchMode=yes", "-o", "ExitOnForwardFailure=yes",
            "-o", "ServerAliveInterval=15", "-o", "ServerAliveCountMax=4",
        ]
        for p in ports:
            argv += ["-R", f"{p}:localhost:{p}"]
        argv.append(f"{cfg.ssh_user}@{cfg.host}")
        try:
            slot.tunnel_proc = subprocess.Popen(
                argv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )
            time.sleep(1.0)  # let the forwards bind before the first task hits them
            if slot.tunnel_proc.poll() is not None:
                log.warning(
                    f"[fleet] {slot.container_name} app tunnel exited immediately "
                    f"(rc={slot.tunnel_proc.returncode}); is the apps sidecar up on the host?"
                )
            else:
                log.info(
                    f"[fleet] {slot.container_name} app tunnel up: guest "
                    f"localhost:{ports[0]}..{ports[-1]} -> host"
                )
        except Exception as e:  # noqa: BLE001 — tunnel is best-effort
            log.warning(f"[fleet] {slot.container_name} app tunnel failed: {e}")

    def _close_app_tunnel(self, slot: FleetSlot) -> None:
        proc = slot.tunnel_proc
        if proc is None:
            return
        try:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
        except Exception as e:  # noqa: BLE001 — cleanup, best-effort
            log.info(f"[fleet] {slot.container_name} tunnel close warning: {e}")
        finally:
            slot.tunnel_proc = None

    def acquire(self, timeout: float | None = None) -> FleetSlot:
        return self._available.get(timeout=timeout)

    def release(self, slot: FleetSlot) -> None:
        try:
            slot.ssh().exec_detached(_RESET_CMD, timeout=30)
        except Exception as e:  # noqa: BLE001 — reset is best-effort
            log.info(f"[fleet] {slot.container_name} reset warning: {e}")
        finally:
            self._available.put(slot)

    def teardown(self, *, remove_clones: bool = True) -> None:
        for slot in self.slots:
            self._close_app_tunnel(slot)
            self.host.remove_container(slot.container_name)
            if remove_clones:
                self.host.remove_volume(slot.volume_path)
        self._booted = False
        log.info(f"[fleet] torn down {len(self.slots)} guest(s)")
