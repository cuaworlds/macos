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
import time

from PIL import Image

from benchmark.config import DISPLAY_HEIGHT, DISPLAY_WIDTH
from benchmark.env.base import Screenshot
from benchmark.env.kvm.config import KvmConfig
from benchmark.env.kvm.fleet import FleetSlot, KvmFleet
from benchmark.task import Task

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
        img = self.rfb.screenshot().convert("RGB")
        if img.size != (DISPLAY_WIDTH, DISPLAY_HEIGHT):
            img = img.resize((DISPLAY_WIDTH, DISPLAY_HEIGHT), Image.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return Screenshot(image=img, png=buf.getvalue(), scale_x=self.scale_x, scale_y=self.scale_y)

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
                print(f"    [warn] pre_command rc={res.rc} (continuing): {res.stderr.strip()[:120]}")

    def grade(self, task: Task) -> tuple[int, list[dict]]:
        """Run grading_command list; first command worth 100 returning 'true' wins."""
        log: list[dict] = []
        for cmd, value in task.grading_command:
            if value != 100:
                continue
            res = self._ssh.exec(cmd, timeout=60)
            if res.rc == 124:
                log.append({"cmd": cmd[:200], "value": value, "error": "ssh timeout"})
                continue
            output = (res.stdout or "").strip().lower()
            hit = "true" in output
            log.append({"cmd": cmd[:200], "value": value, "stdout": output[:200], "hit": hit})
            if hit:
                return value, log
        return 0, log

    # --- cleanup ---

    def close(self) -> None:
        try:
            self.rfb.close()
        finally:
            # Hand the warm VM back to the pool instead of destroying it.
            self.slot.release()
