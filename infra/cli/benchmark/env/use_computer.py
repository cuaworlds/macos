from __future__ import annotations

import io
import time

from PIL import Image
from use_computer import Computer, SandboxType

from benchmark.config import BASE_URL, DISPLAY_HEIGHT, DISPLAY_WIDTH
from benchmark.env.base import Screenshot
from benchmark.task import Task


class MacOSWorldEnv:
    """Thin wrapper around use.computer's MacOSSandbox sized for MacOSWorld tasks.

    - Boots a fresh sandbox per instance.
    - Caps screenshots to (DISPLAY_WIDTH, DISPLAY_HEIGHT) and tracks scale factors so
      coordinates returned by the model (which thinks the display is that size) can be
      upscaled back to the sandbox's real pixel space before dispatch.
    - Exposes dispatch() for Claude's `computer` tool action dicts.
    - Exposes grade() that runs the task's grading_command list via exec_ssh.
    """

    def __init__(self):
        self.client = Computer(base_url=BASE_URL)
        self.sandbox = self.client.create(type=SandboxType.MACOS)
        self.sandbox.start_keepalive()
        # display.get_info() can return 0x0 immediately after boot. Pull a screenshot
        # instead — it's authoritative for the dims Claude's clicks need to land on.
        png = self.sandbox.screenshot.take_full_screen()
        img = Image.open(io.BytesIO(png))
        self._real_w, self._real_h = img.size
        if self._real_w == 0 or self._real_h == 0:
            # Fall back to display info if even screenshot is empty (shouldn't happen).
            info = self.sandbox.display.get_info()
            self._real_w, self._real_h = info.width or DISPLAY_WIDTH, info.height or DISPLAY_HEIGHT
        self.scale_x = self._real_w / DISPLAY_WIDTH
        self.scale_y = self._real_h / DISPLAY_HEIGHT

    @property
    def sandbox_id(self) -> str:
        return self.sandbox.sandbox_id

    # --- screen ---

    def screenshot(self) -> Screenshot:
        png = self.sandbox.screenshot.take_full_screen()
        img = Image.open(io.BytesIO(png)).convert("RGB")
        if img.size != (DISPLAY_WIDTH, DISPLAY_HEIGHT):
            img = img.resize((DISPLAY_WIDTH, DISPLAY_HEIGHT), Image.LANCZOS)
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            png = buf.getvalue()
        return Screenshot(image=img, png=png, scale_x=self.scale_x, scale_y=self.scale_y)

    # --- action dispatch (Claude computer tool → use.computer SDK) ---

    def _upscale(self, x: int, y: int) -> tuple[int, int]:
        return int(round(x * self.scale_x)), int(round(y * self.scale_y))

    def dispatch(self, action_input: dict) -> tuple[bool, str]:
        """Translate a Claude `computer` tool_use input dict to SDK calls.

        Returns (success, message). Screenshot is taken separately by the agent loop —
        most actions don't return one; the agent always takes a fresh screenshot after.
        """
        action = action_input.get("action")
        try:
            if action == "screenshot":
                return True, "ok"

            if action == "wait":
                time.sleep(float(action_input.get("duration", 1)))
                return True, "ok"

            if action == "key":
                self.sandbox.keyboard.hotkey(action_input.get("text", ""))
                return True, "ok"

            if action == "type":
                self.sandbox.keyboard.type(action_input.get("text", ""))
                return True, "ok"

            if action == "mouse_move":
                x, y = self._upscale(*action_input["coordinate"])
                self.sandbox.mouse.move(x, y)
                return True, "ok"

            if action == "cursor_position":
                pos = self.sandbox.mouse.get_position()
                return True, f"x={pos.x}, y={pos.y}"

            if action == "left_click":
                x, y = self._upscale(*action_input["coordinate"])
                # Modifier-while-clicking: Claude passes `text` like "shift" or "cmd".
                # SDK has no held-modifier-click; emulate by pressing the modifier as a
                # hotkey with the click — best effort.
                modifier = action_input.get("text")
                if modifier:
                    # Press modifier+click as a single hotkey isn't supported; click then warn.
                    self.sandbox.mouse.click(x, y, button="left")
                    return True, f"clicked (note: modifier '{modifier}' ignored)"
                self.sandbox.mouse.click(x, y, button="left")
                return True, "ok"

            if action == "right_click":
                x, y = self._upscale(*action_input["coordinate"])
                self.sandbox.mouse.click(x, y, button="right")
                return True, "ok"

            if action == "middle_click":
                x, y = self._upscale(*action_input["coordinate"])
                self.sandbox.mouse.click(x, y, button="middle")
                return True, "ok"

            if action == "double_click":
                x, y = self._upscale(*action_input["coordinate"])
                self.sandbox.mouse.click(x, y, button="left", double=True)
                return True, "ok"

            if action == "triple_click":
                x, y = self._upscale(*action_input["coordinate"])
                for _ in range(3):
                    self.sandbox.mouse.click(x, y, button="left")
                return True, "ok"

            if action == "left_click_drag":
                sx, sy = self._upscale(*action_input["start_coordinate"])
                ex, ey = self._upscale(*action_input["coordinate"])
                self.sandbox.mouse.drag(sx, sy, ex, ey, button="left")
                return True, "ok"

            if action == "scroll":
                coord = action_input.get("coordinate") or [DISPLAY_WIDTH // 2, DISPLAY_HEIGHT // 2]
                x, y = self._upscale(*coord)
                direction = action_input.get("scroll_direction", "down")
                amount = int(action_input.get("scroll_amount", 3))
                self.sandbox.mouse.scroll(x, y, direction=direction, amount=amount)
                return True, "ok"

            if action == "hold_key":
                # No native equivalent — fall back to a single press.
                self.sandbox.keyboard.hotkey(action_input.get("text", ""))
                return True, "hold_key not supported; pressed once instead"

            if action in ("left_mouse_down", "left_mouse_up"):
                # No native equivalent. Skip silently.
                return True, f"{action} not supported by SDK; skipped"

            if action == "zoom":
                # 2025-11-24 zoom action: just return a region screenshot reference.
                # Implementation deferred — we ignore for v0.
                return True, "zoom not supported in v0; ignored"

            return False, f"unknown action: {action}"
        except Exception as e:
            return False, f"dispatch error: {type(e).__name__}: {e}"

    # --- task lifecycle ---

    def run_pre_command(self, task: Task) -> None:
        if task.pre_command:
            self.sandbox.exec_ssh(task.pre_command, timeout=60)

    def grade(self, task: Task) -> tuple[int, list[dict]]:
        """Run grading_command list and return (best_score, per_check_log).

        Mirrors macosworld-vmware/master/utils/evaluator.py:19-31 — only checks worth 100
        are evaluated; first one returning "true" wins.
        """
        log = []
        for cmd, value in task.grading_command:
            if value != 100:
                continue
            try:
                result = self.sandbox.exec_ssh(cmd, timeout=60)
                output = (result.stdout or "").strip().lower()
                hit = "true" in output
                log.append({"cmd": cmd[:200], "value": value, "stdout": output[:200], "hit": hit})
                if hit:
                    return value, log
            except Exception as e:
                log.append({"cmd": cmd[:200], "value": value, "error": f"{type(e).__name__}: {e}"})
        return 0, log

    # --- cleanup ---

    def close(self) -> None:
        try:
            self.sandbox.stop_keepalive()
        finally:
            try:
                self.sandbox.close()
            finally:
                self.client.close()
