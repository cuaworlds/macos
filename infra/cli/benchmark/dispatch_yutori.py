"""Yutori Navigator n1.5 → use.computer/KVM action dispatcher.

n1.5 emits action names from the `browser_tools_core-20260403` tool set with
coordinates in a normalized 1000x1000 grid. This module translates each n1.5
action + args into the **Anthropic `computer` tool action_input dict** that
`env.dispatch()` already understands, then calls `env.dispatch(action_input)`.

By routing through `Env.dispatch()` rather than reaching into env-specific
internals (e.g. `MacOSWorldEnv.sandbox`), this dispatcher works against BOTH
backends — the use.computer managed sandbox and the KVM/dockurr fleet — because
both implement the same Env protocol (`benchmark/env/base.py`).

Coordinate transform: n1.5 emits in 1000x1000 normalized space; env.dispatch()
expects coords in the 1024x768 display space (which the env then upscales to
real pixels). So `x_anth = x_n15 * 1024 / 1000`, `y_anth = y_n15 * 768 / 1000`.
"""

from __future__ import annotations

from benchmark.config import DISPLAY_HEIGHT, DISPLAY_WIDTH
from benchmark.env.base import Env


N15_GRID = 1000


def _coords(n15_xy: list[int] | tuple[int, int]) -> list[int]:
    x, y = n15_xy
    return [
        int(round(x * DISPLAY_WIDTH / N15_GRID)),
        int(round(y * DISPLAY_HEIGHT / N15_GRID)),
    ]


# Tools that are part of n1.5's core set but have no analogue in a macOS desktop
# context. If the model emits one despite `disable_tools`, we refuse cleanly so
# the agent loop can recover. The browser-expanded tools (extract_elements,
# find, set_element_value, execute_js) only appear under `browser_tools_expanded-*`
# which we don't select, but list them defensively.
_BROWSER_ONLY = {
    "goto_url",
    "refresh",
    "go_back",
    "go_forward",
    "extract_elements",
    "find",
    "set_element_value",
    "execute_js",
}


def dispatch_yutori(env: Env, action: str, args: dict) -> tuple[bool, str]:
    """Translate an n1.5 tool_call into an `env.dispatch(action_input)` call.

    Returns (ok, msg) — same contract as `env.dispatch()`.
    """
    if action in _BROWSER_ONLY:
        return False, f"{action} is a browser-only tool and is not available in this macOS environment"

    if action == "left_click":
        ai: dict = {"action": "left_click", "coordinate": _coords(args["coordinates"])}
        if args.get("modifier"):
            ai["text"] = args["modifier"]
        return env.dispatch(ai)

    if action == "right_click":
        return env.dispatch({"action": "right_click", "coordinate": _coords(args["coordinates"])})

    if action == "double_click":
        return env.dispatch({"action": "double_click", "coordinate": _coords(args["coordinates"])})

    if action == "triple_click":
        return env.dispatch({"action": "triple_click", "coordinate": _coords(args["coordinates"])})

    if action == "middle_click":
        return env.dispatch({"action": "middle_click", "coordinate": _coords(args["coordinates"])})

    if action == "scroll":
        return env.dispatch(
            {
                "action": "scroll",
                "coordinate": _coords(args["coordinates"]),
                "scroll_direction": args.get("direction", "down"),
                "scroll_amount": int(args.get("amount", 1)),
            }
        )

    if action == "type":
        return env.dispatch({"action": "type", "text": args.get("text", "")})

    if action == "key_press":
        # n1.5 uses `key` for the hotkey string; Anthropic's `computer` tool uses `text`.
        return env.dispatch({"action": "key", "text": args.get("key", "")})

    if action == "drag":
        return env.dispatch(
            {
                "action": "left_click_drag",
                "start_coordinate": _coords(args["start_coordinates"]),
                "coordinate": _coords(args["coordinates"]),
            }
        )

    if action == "mouse_move":
        return env.dispatch({"action": "mouse_move", "coordinate": _coords(args["coordinates"])})

    if action == "wait":
        return env.dispatch({"action": "wait", "duration": float(args.get("duration", 1))})

    if action == "hold_key":
        # Map to Anthropic's hold_key (env.dispatch falls back to a single press where unsupported).
        ai = {"action": "hold_key", "text": args.get("key", "")}
        if args.get("duration") is not None:
            ai["duration"] = float(args["duration"])
        return env.dispatch(ai)

    if action == "mouse_down":
        return env.dispatch({"action": "left_mouse_down", "coordinate": _coords(args["coordinates"])})

    if action == "mouse_up":
        return env.dispatch({"action": "left_mouse_up", "coordinate": _coords(args["coordinates"])})

    return False, f"unknown action: {action}"
