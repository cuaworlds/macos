"""VZ macOS backend: drives an Apple Virtualization.framework guest via Tart.

Peer to `env/kvm/KvmMacOSEnv` — implements the same substrate-blind `Env`
protocol (env/base.py) so the agent loop (benchmark/agent.py) and the runner are
backend-agnostic:
  - screenshot()  -> RFB framebuffer read over the VZ native VNC server (rfb.py,
                     W2-patched: DES type-2 auth + DesktopSize, gated on vnc_password)
  - dispatch()    -> RFB keyboard/pointer (same rfb.py)
  - run_pre_command()/grade() -> SSH into the guest over `tart ip` (ssh.py)
  - guest_conn()  -> SSH coordinates for a host-side grading_script
  - close()       -> stop + `tart delete` the instance (destroys the guest)

How it maps onto Tart (all proven in W2 / vz-feasibility.md):
  * instance = `tart clone <base_vm> <instance>` (APFS clonefile, CoW, MAC regen);
    before first boot we regen the ECID so siblings get distinct UUID/serial.
  * boot = `tart run --vnc-experimental --no-graphics <instance>` (backgrounded);
    scrape its `vnc://:<pw>@127.0.0.1:<port>` line -> RfbClient(vnc_password=pw).
  * SSH host = `tart ip <instance>`; the harness's key-based GuestSsh + the
    already-VZ-ready grade() then work unchanged.
  * reset-by-discard = `tart delete <instance>` then re-`tart clone <base_vm>`.

Everything ABOVE the Env protocol is unchanged. The KVM backend is untouched;
the only shared code is the W2-patched rfb.py (gated on vnc_password) and the
substrate-blind grading.py + GuestSsh, both already backend-agnostic.
"""
from __future__ import annotations

import io
import socket
import time

from PIL import Image

from benchmark.config import DISPLAY_HEIGHT, DISPLAY_WIDTH
from benchmark.env.base import Screenshot
from benchmark.env.kvm.rfb import RfbClient, RfbError
from benchmark.env.kvm.ssh import GuestSsh
from benchmark.env.vz import tart as tartctl
from benchmark.env.vz.config import VzConfig
from benchmark.grading import grade_checkpoints
from benchmark.log import get_logger
from benchmark.task import Task

log = get_logger()

# Same recoverable-RFB error set as KvmMacOSEnv: a wedged VNC socket reconnects.
_RFB_BROKEN = (socket.timeout, ConnectionError, OSError, RfbError)

__all__ = ["VzMacOSEnv", "VzConfig"]


class VzMacOSEnv:
    """Wraps one live Tart/VZ macOS guest for the duration of a task.

    Construct with a VzConfig; `__init__` clones the instance from the frozen base
    (regen ECID first), boots it, scrapes VNC, waits for SSH, and (optionally)
    keeps the display awake. The instance is the analogue of one KVM fleet slot,
    but VZ owns its whole lifecycle (clone->boot->delete) because the 2-VM cap
    rules out a pre-warmed pool.
    """

    def __init__(self, cfg: VzConfig | None = None, *, _provisioned: bool = True):
        self.cfg = cfg or VzConfig()
        self._run: tartctl.TartRun | None = None
        self.rfb: RfbClient | None = None
        self._ssh: GuestSsh | None = None
        self.ip: str = ""
        self._provisioned = _provisioned  # base already has our SSH key baked in
        self._boot_instance()

    # --- lifecycle helpers ---

    def _boot_instance(self) -> None:
        cfg = self.cfg
        # Fresh instance from the frozen base (delete a stale one first).
        tartctl.delete(cfg.instance_name)
        tartctl.clone(cfg.base_vm, cfg.instance_name)
        if cfg.regen_identity:
            tartctl.regen_ecid(cfg.instance_name)

        # Boot + scrape VNC.
        self._run = tartctl.TartRun(cfg.instance_name)
        vnc = self._run.start(vnc_scrape_timeout=cfg.boot_timeout_s)

        # Resolve SSH host (VZ-NAT IP) and connect the harness's key-based GuestSsh.
        self.ip = tartctl.get_ip(cfg.instance_name, timeout_s=cfg.ip_timeout_s)
        self._ssh = GuestSsh(self.ip, cfg.ssh_port, cfg.ssh_user, str(cfg.ssh_key))

        if not self._provisioned:
            self._provision_key()

        if not self._ssh.wait_until_ready(timeout_s=cfg.boot_timeout_s):
            raise RuntimeError(
                f"VZ guest {cfg.instance_name} ({self.ip}) never answered key-based SSH"
            )

        if cfg.keep_display_awake:
            self._keep_display_awake()

        # Connect RFB last — the framebuffer is live once the GUI is up + driven.
        self.rfb = RfbClient(vnc.host, vnc.port, vnc_password=vnc.password)
        self._real_w = self.rfb.w or DISPLAY_WIDTH
        self._real_h = self.rfb.h or DISPLAY_HEIGHT
        self.scale_x = self._real_w / DISPLAY_WIDTH
        self.scale_y = self._real_h / DISPLAY_HEIGHT
        log.info(
            f"[vz] {cfg.instance_name} ready: ip={self.ip} "
            f"fb={self._real_w}x{self._real_h} scale=({self.scale_x:.2f},{self.scale_y:.2f})"
        )

    def _provision_key(self) -> None:
        """One-time: seed our public key into the guest (W2 env-build step)."""
        pub = self.cfg.ssh_key.with_suffix(self.cfg.ssh_key.suffix + ".pub")
        if not pub.exists():
            pub = self.cfg.ssh_key.parent / (self.cfg.ssh_key.name + ".pub")
        if not pub.exists():
            raise RuntimeError(
                f"VZ SSH public key not found next to {self.cfg.ssh_key}; "
                "generate it or pre-provision the base bundle."
            )
        tartctl.provision_ssh_key(self.cfg, self.ip, pub.read_text())

    def _keep_display_awake(self) -> None:
        """Stop the VZ virtual display blanking (W2 gotcha #1: idle -> black frames).

        `pmset noidle`-style backgrounding via `caffeinate`, plus disabling display
        sleep. Best-effort: failure just means we may need reconnect-per-shot, which
        screenshot() already does.
        """
        cmd = (
            "sudo pmset -a displaysleep 0 disksleep 0 sleep 0 2>/dev/null; "
            "(caffeinate -dimsu </dev/null >/dev/null 2>&1 &) ; true"
        )
        res = self._ssh.exec_detached(cmd, timeout=30) if self._ssh else None
        if res and res.rc != 0:
            log.info(f"[vz] keep-display-awake rc={res.rc} (continuing)")

    @property
    def sandbox_id(self) -> str:
        return self.cfg.instance_name

    # --- screen ---

    def screenshot(self) -> Screenshot:
        img = self._grab_with_reconnect().convert("RGB")
        if img.size != (DISPLAY_WIDTH, DISPLAY_HEIGHT):
            img = img.resize((DISPLAY_WIDTH, DISPLAY_HEIGHT), Image.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return Screenshot(image=img, png=buf.getvalue(), scale_x=self.scale_x, scale_y=self.scale_y)

    def _grab_with_reconnect(self) -> Image.Image:
        """Grab one framebuffer; reconnect a fresh VNC socket once on a wedged one.

        Mirrors KvmMacOSEnv._grab_with_reconnect, but for VZ a reconnect re-scrapes
        nothing — the same loopback VNC port/password from the live `tart run` is
        reused (the run process owns the listener for the guest's lifetime).
        """
        assert self.rfb is not None
        try:
            return self.rfb.screenshot()
        except _RFB_BROKEN as e:
            log.warning(
                f"[{self.sandbox_id}] RFB screenshot failed ({type(e).__name__}: {e}); reconnecting"
            )
            try:
                self.rfb.close()
            except Exception:
                pass
            vnc = self._run.vnc if self._run else None
            if vnc is None:
                raise
            self.rfb = RfbClient(vnc.host, vnc.port, vnc_password=vnc.password)
            return self.rfb.screenshot()

    # --- action dispatch (Claude computer tool -> RFB) ---

    def _upscale(self, x: int, y: int) -> tuple[int, int]:
        return int(round(x * self.scale_x)), int(round(y * self.scale_y))

    def dispatch(self, action_input: dict) -> tuple[bool, str]:
        action = action_input.get("action")
        rfb = self.rfb
        assert rfb is not None
        try:
            if action == "screenshot":
                return True, "ok"
            if action == "wait":
                time.sleep(float(action_input.get("duration", 1)))
                return True, "ok"
            if action == "key":
                rfb.key_combo(action_input.get("text", ""))
                return True, "ok"
            if action == "type":
                rfb.type_text(action_input.get("text", ""))
                return True, "ok"
            if action == "mouse_move":
                x, y = self._upscale(*action_input["coordinate"])
                rfb.move(x, y)
                return True, "ok"
            if action == "cursor_position":
                return True, f"x={rfb._x}, y={rfb._y}"
            if action == "left_click":
                x, y = self._upscale(*action_input["coordinate"])
                modifier = action_input.get("text")
                if modifier:
                    rfb.click_with_modifiers(x, y, "left", modifier)
                else:
                    rfb.click(x, y, "left")
                return True, "ok"
            if action == "right_click":
                x, y = self._upscale(*action_input["coordinate"])
                rfb.click(x, y, "right")
                return True, "ok"
            if action == "middle_click":
                x, y = self._upscale(*action_input["coordinate"])
                rfb.click(x, y, "middle")
                return True, "ok"
            if action == "double_click":
                x, y = self._upscale(*action_input["coordinate"])
                rfb.click(x, y, "left", double=True)
                return True, "ok"
            if action == "triple_click":
                x, y = self._upscale(*action_input["coordinate"])
                rfb.move(x, y)
                for _ in range(3):
                    rfb.button_down("left")
                    rfb.button_up("left")
                return True, "ok"
            if action == "left_click_drag":
                sx, sy = self._upscale(*action_input["start_coordinate"])
                ex, ey = self._upscale(*action_input["coordinate"])
                rfb.drag(sx, sy, ex, ey, "left")
                return True, "ok"
            if action == "scroll":
                coord = action_input.get("coordinate") or [DISPLAY_WIDTH // 2, DISPLAY_HEIGHT // 2]
                x, y = self._upscale(*coord)
                direction = action_input.get("scroll_direction", "down")
                amount = int(action_input.get("scroll_amount", 3))
                rfb.scroll(x, y, direction=direction, amount=amount)
                return True, "ok"
            if action == "hold_key":
                rfb.hold_key(action_input.get("text", ""), float(action_input.get("duration", 1)))
                return True, "ok"
            if action == "left_mouse_down":
                if action_input.get("coordinate"):
                    rfb.move(*self._upscale(*action_input["coordinate"]))
                rfb.button_down("left")
                return True, "ok"
            if action == "left_mouse_up":
                if action_input.get("coordinate"):
                    rfb.move(*self._upscale(*action_input["coordinate"]))
                rfb.button_up("left")
                return True, "ok"
            if action == "zoom":
                return True, "zoom not supported in v0; ignored"
            return False, f"unknown action: {action}"
        except Exception as e:  # noqa: BLE001 — surface to the agent as a failed action
            return False, f"dispatch error: {type(e).__name__}: {e}"

    # --- task lifecycle ---

    def run_pre_command(self, task: Task) -> None:
        if task.pre_command and self._ssh is not None:
            res = self._ssh.exec_detached(task.pre_command, timeout=60)
            if res.rc != 0:
                log.warning(
                    f"[{self.sandbox_id}] pre_command rc={res.rc} (continuing): "
                    f"{res.stderr.strip()[:120]}"
                )

    def grade(self, task: Task) -> tuple[float, float, list[dict]]:
        """Evaluate weighted grading checkpoints over SSH (benchmark.grading).

        Identical seam to KvmMacOSEnv.grade — the grader is substrate-blind, so this
        is the same code path E4 will measure for cross-substrate identity."""
        assert self._ssh is not None

        def _exec(cmd: str) -> tuple[int, str, str]:
            res = self._ssh.exec(cmd, timeout=60)
            return res.rc, res.stdout or "", res.stderr or ""

        return grade_checkpoints(task.grading_command, _exec)

    def guest_conn(self) -> dict | None:
        """SSH coordinates so a host-side grading_script can reach this guest."""
        return {
            "host": self.ip,
            "port": self.cfg.ssh_port,
            "user": self.cfg.ssh_user,
            "key_path": str(self.cfg.ssh_key),
        }

    # --- reset-by-discard ---

    def reset(self) -> None:
        """Discard the instance and re-clone from the frozen base (deterministic).

        The VZ analogue of KVM overlay-discard: `tart delete <instance>` then
        re-`tart clone <base_vm> <instance>` + fresh ECID + reboot. The frozen base
        is never written through (W2 proved clones never touch the parent), so the
        base disk.img+nvram.bin stay byte-identical across resets."""
        self._teardown_live()
        self._boot_instance()

    # --- cleanup ---

    def _teardown_live(self) -> None:
        """Close RFB + stop the run process (does NOT delete the bundle)."""
        if self.rfb is not None:
            try:
                self.rfb.close()
            except Exception as e:  # noqa: BLE001
                log.warning(f"[{self.sandbox_id}] rfb.close failed (ignored): {e}")
            self.rfb = None
        if self._run is not None:
            try:
                self._run.stop()
            except Exception as e:  # noqa: BLE001
                log.warning(f"[{self.sandbox_id}] tart run stop failed (ignored): {e}")

    def close(self) -> None:
        """Stop the guest and delete the instance bundle (full teardown)."""
        self._teardown_live()
        try:
            tartctl.delete(self.cfg.instance_name)
        except Exception as e:  # noqa: BLE001 — cleanup, best-effort
            log.warning(f"[{self.sandbox_id}] tart delete failed (ignored): {e}")
