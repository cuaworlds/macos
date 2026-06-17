"""KVM macOS backend: drives a pre-warmed QEMU/KVM guest from a fleet slot.

Implements the same surface as MacOSWorldEnv (benchmark/env/use_computer.py) so the
agent loop is backend-agnostic:
  - screenshot()  -> RFB framebuffer read (rfb.py)
  - dispatch()    -> RFB keyboard/pointer (rfb.py)
  - run_pre_command()/grade() -> SSH into the guest (ssh.py)
  - close()       -> release the slot back to the fleet (does NOT destroy the VM)
"""
from __future__ import annotations

import io
import socket
import time

from PIL import Image

from benchmark.config import DISPLAY_HEIGHT, DISPLAY_WIDTH
from benchmark.env.base import Screenshot
from benchmark.grading import grade_checkpoints
from benchmark.env.kvm.config import KvmConfig
from benchmark.env.kvm.fleet import FleetSlot, KvmFleet
from benchmark.env.kvm.rfb import RfbError
from benchmark.log import get_logger
from benchmark.task import Task

log = get_logger()

# Errors that mean "this RFB socket is wedged/broken" — recoverable by reconnecting
# a fresh socket to the same guest's VNC port.
_RFB_BROKEN = (socket.timeout, ConnectionError, OSError, RfbError)

__all__ = ["KvmMacOSEnv", "KvmFleet", "KvmConfig", "FleetSlot"]


class KvmMacOSEnv:
    """Wraps one already-warm fleet slot for the duration of a single task."""

    def __init__(self, slot: FleetSlot):
        self.slot = slot
        self._ssh = slot.ssh()
        self.rfb = slot.connect_rfb()
        # The RFB ServerInit gives us the guest's real framebuffer size — authoritative
        # for the pixel space Claude's (1024x768) clicks must be scaled into.
        self._real_w = self.rfb.w or DISPLAY_WIDTH
        self._real_h = self.rfb.h or DISPLAY_HEIGHT
        self.scale_x = self._real_w / DISPLAY_WIDTH
        self.scale_y = self._real_h / DISPLAY_HEIGHT

    @property
    def sandbox_id(self) -> str:
        return self.slot.container_name

    # --- screen ---

    def screenshot(self) -> Screenshot:
        img = self._grab_with_reconnect().convert("RGB")
        if img.size != (DISPLAY_WIDTH, DISPLAY_HEIGHT):
            img = img.resize((DISPLAY_WIDTH, DISPLAY_HEIGHT), Image.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return Screenshot(image=img, png=buf.getvalue(), scale_x=self.scale_x, scale_y=self.scale_y)

    def _grab_with_reconnect(self) -> Image.Image:
        """Grab one framebuffer; if the socket is wedged, reconnect once and retry.

        A timed-out/broken RFB socket would otherwise poison the env — every later
        screenshot reuses the same dead socket. Reconnecting a fresh socket to the
        guest's VNC port lets a transiently-stalled guest recover. If the retry also
        fails we raise, so run_task records the task as errored and releases the slot
        (rather than the whole run hanging).
        """
        try:
            return self.rfb.screenshot()
        except _RFB_BROKEN as e:
            log.warning(f"[{self.sandbox_id}] RFB screenshot failed ({type(e).__name__}: {e}); reconnecting")
            try:
                self.rfb.close()
            except Exception:
                pass
            self.rfb = self.slot.connect_rfb()
            return self.rfb.screenshot()

    # --- action dispatch (Claude computer tool → RFB) ---

    def _upscale(self, x: int, y: int) -> tuple[int, int]:
        return int(round(x * self.scale_x)), int(round(y * self.scale_y))

    def dispatch(self, action_input: dict) -> tuple[bool, str]:
        action = action_input.get("action")
        try:
            if action == "screenshot":
                return True, "ok"

            if action == "wait":
                time.sleep(float(action_input.get("duration", 1)))
                return True, "ok"

            if action == "key":
                self.rfb.key_combo(action_input.get("text", ""))
                return True, "ok"

            if action == "type":
                self.rfb.type_text(action_input.get("text", ""))
                return True, "ok"

            if action == "mouse_move":
                x, y = self._upscale(*action_input["coordinate"])
                self.rfb.move(x, y)
                return True, "ok"

            if action == "cursor_position":
                return True, f"x={self.rfb._x}, y={self.rfb._y}"

            if action == "left_click":
                x, y = self._upscale(*action_input["coordinate"])
                modifier = action_input.get("text")
                if modifier:
                    self.rfb.click_with_modifiers(x, y, "left", modifier)
                else:
                    self.rfb.click(x, y, "left")
                return True, "ok"

            if action == "right_click":
                x, y = self._upscale(*action_input["coordinate"])
                self.rfb.click(x, y, "right")
                return True, "ok"

            if action == "middle_click":
                x, y = self._upscale(*action_input["coordinate"])
                self.rfb.click(x, y, "middle")
                return True, "ok"

            if action == "double_click":
                x, y = self._upscale(*action_input["coordinate"])
                self.rfb.click(x, y, "left", double=True)
                return True, "ok"

            if action == "triple_click":
                x, y = self._upscale(*action_input["coordinate"])
                self.rfb.move(x, y)
                for _ in range(3):
                    self.rfb.button_down("left")
                    self.rfb.button_up("left")
                return True, "ok"

            if action == "left_click_drag":
                sx, sy = self._upscale(*action_input["start_coordinate"])
                ex, ey = self._upscale(*action_input["coordinate"])
                self.rfb.drag(sx, sy, ex, ey, "left")
                return True, "ok"

            if action == "scroll":
                coord = action_input.get("coordinate") or [DISPLAY_WIDTH // 2, DISPLAY_HEIGHT // 2]
                x, y = self._upscale(*coord)
                direction = action_input.get("scroll_direction", "down")
                amount = int(action_input.get("scroll_amount", 3))
                self.rfb.scroll(x, y, direction=direction, amount=amount)
                return True, "ok"

            if action == "hold_key":
                self.rfb.hold_key(action_input.get("text", ""), float(action_input.get("duration", 1)))
                return True, "ok"

            if action == "left_mouse_down":
                if action_input.get("coordinate"):
                    self.rfb.move(*self._upscale(*action_input["coordinate"]))
                self.rfb.button_down("left")
                return True, "ok"

            if action == "left_mouse_up":
                if action_input.get("coordinate"):
                    self.rfb.move(*self._upscale(*action_input["coordinate"]))
                self.rfb.button_up("left")
                return True, "ok"

            if action == "zoom":
                return True, "zoom not supported in v0; ignored"

            return False, f"unknown action: {action}"
        except Exception as e:  # noqa: BLE001 — surface to the agent as a failed action
            return False, f"dispatch error: {type(e).__name__}: {e}"

    # --- task lifecycle ---

    def run_pre_command(self, task: Task) -> None:
        if task.pre_command:
            # Best-effort, matching MacOSWorldEnv: the managed backend ignores the
            # pre_command result, and some osascript setup commands legitimately exit
            # non-zero or stall on a cold app. A hiccup here must not error the task —
            # the agent still runs and grading reflects the real end state.
            res = self._ssh.exec_detached(task.pre_command, timeout=60)
            if res.rc != 0:
                log.warning(
                    f"[{self.sandbox_id}] pre_command rc={res.rc} (continuing): "
                    f"{res.stderr.strip()[:120]}"
                )

    def grade(self, task: Task) -> tuple[float, float, list[dict]]:
        """Evaluate weighted grading checkpoints over SSH (see benchmark.grading)."""

        def _exec(cmd: str) -> tuple[int, str, str]:
            res = self._ssh.exec(cmd, timeout=60)
            return res.rc, res.stdout or "", res.stderr or ""

        return grade_checkpoints(task.grading_command, _exec)

    def guest_conn(self) -> dict | None:
        """SSH coordinates so a host-side grading_script can reach this guest."""
        cfg = self.slot.cfg
        return {
            "host": self.slot.host,
            "port": self.slot.ssh_port,
            "user": cfg.ssh_user,
            "key_path": str(cfg.ssh_key),
        }

    # --- cleanup ---

    def close(self) -> None:
        # Releasing the slot is the only thing that must happen here; a failure to
        # tear down an already-broken RFB socket must not propagate (or it would mask
        # the release in callers' eyes and could leak the guest from the pool).
        try:
            self.rfb.close()
        except Exception as e:  # noqa: BLE001 — cleanup, best-effort
            log.warning(f"[{self.sandbox_id}] rfb.close failed (ignored): {e}")
        finally:
            self.slot.release()
