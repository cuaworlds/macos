"""Unit tests for the Yutori n1.5 adapter (dispatch_yutori + agent_yutori).

Hermetic: no network, no API key needed for the pure paths. A dummy
YUTORI_API_KEY is injected only where a NavigatorAgent is constructed (OpenAI
client is monkeypatched so nothing leaves the process).

Covers two audited handicaps:
  1. key remapping (browser ctrl-hotkeys -> macOS cmd-hotkeys) in dispatch.
  2. system-prompt removal + desktop preamble seeded into the first user message.
"""

from __future__ import annotations

import pytest

from benchmark.dispatch_yutori import KEY_REMAP, dispatch_yutori, normalize_key


class FakeEnv:
    """Records every action_input dict passed to dispatch(); echoes (True, 'ok')."""

    def __init__(self):
        self.dispatched: list[dict] = []

    def dispatch(self, action_input: dict) -> tuple[bool, str]:
        self.dispatched.append(action_input)
        return True, "ok"


# --- normalize_key -----------------------------------------------------------


def test_ctrl_c_remaps_to_cmd_c():
    assert normalize_key("ctrl+c") == "cmd+c"


def test_all_table_keys_remap():
    for base in KEY_REMAP:
        assert normalize_key(f"ctrl+{base}") == f"cmd+{base}"


def test_ctrl_shift_t_remaps_with_extra_modifier():
    # Documented decision: remap ctrl->cmd when the base key is in the table,
    # even with extra modifiers present.
    assert normalize_key("ctrl+shift+t") == "cmd+shift+t"


def test_bare_ctrl_unchanged():
    assert normalize_key("ctrl") == "ctrl"


def test_ctrl_with_non_table_base_unchanged():
    # ctrl+left (word-jump on macOS) is NOT a browser cmd-shortcut: pass through.
    assert normalize_key("ctrl+left") == "ctrl+left"
    assert normalize_key("ctrl+b") == "ctrl+b"


def test_cmd_combo_unchanged():
    assert normalize_key("cmd+c") == "cmd+c"
    assert normalize_key("cmd+space") == "cmd+space"


def test_case_insensitive():
    assert normalize_key("CTRL+C") == "cmd+c"
    assert normalize_key("Ctrl+Shift+T") == "cmd+shift+t"


def test_enter_alias_to_return():
    assert normalize_key("enter") == "return"


def test_esc_alias_to_escape():
    assert normalize_key("esc") == "escape"


def test_canonical_names_unchanged():
    # return/escape are already canonical (env accepts both spellings; we emit these).
    assert normalize_key("return") == "return"
    assert normalize_key("escape") == "escape"


def test_empty_key_unchanged():
    assert normalize_key("") == ""


# --- dispatch routing --------------------------------------------------------


def test_key_press_routes_through_normalize_key():
    env = FakeEnv()
    ok, msg = dispatch_yutori(env, "key_press", {"key": "ctrl+s"})
    assert ok and msg == "ok"
    assert env.dispatched == [{"action": "key", "text": "cmd+s"}]


def test_hold_key_routes_through_normalize_key():
    env = FakeEnv()
    dispatch_yutori(env, "hold_key", {"key": "ctrl+a", "duration": 2})
    assert env.dispatched[0]["action"] == "hold_key"
    assert env.dispatched[0]["text"] == "cmd+a"
    assert env.dispatched[0]["duration"] == 2.0


def test_key_press_non_table_passthrough():
    env = FakeEnv()
    dispatch_yutori(env, "key_press", {"key": "ctrl+left"})
    assert env.dispatched == [{"action": "key", "text": "ctrl+left"}]


def test_left_click_coords_denormalized():
    # n1.5 1000-grid -> 1024x768 display: 500 -> 512 (x), 500 -> 384 (y).
    env = FakeEnv()
    dispatch_yutori(env, "left_click", {"coordinates": [500, 500]})
    assert env.dispatched == [{"action": "left_click", "coordinate": [512, 384]}]


def test_browser_only_refusal_unchanged():
    env = FakeEnv()
    ok, msg = dispatch_yutori(env, "goto_url", {"url": "https://x"})
    assert ok is False
    assert "browser-only" in msg
    assert env.dispatched == []  # nothing dispatched on refusal


# --- agent: preamble + first-user-message composition ------------------------


def test_desktop_preamble_non_empty():
    from benchmark.agent_yutori import DESKTOP_PREAMBLE

    assert isinstance(DESKTOP_PREAMBLE, str)
    assert DESKTOP_PREAMBLE.strip()
    # Re-grounds n1.5 in macOS, not a browser.
    assert "macOS" in DESKTOP_PREAMBLE
    assert "browser" in DESKTOP_PREAMBLE


def test_first_user_text_composes_instruction_and_preamble():
    from benchmark.agent_yutori import DESKTOP_PREAMBLE, _first_user_text

    out = _first_user_text("Open Notes and write hello")
    assert out == f"Open Notes and write hello\n\n{DESKTOP_PREAMBLE}"
    assert out.startswith("Open Notes and write hello")


def test_agent_has_no_system_message(tmp_path, monkeypatch):
    # Constructing the agent must not seed a system message (Yutori docs: a custom
    # system prompt degrades n1.5). Monkeypatch OpenAI so no client is created.
    monkeypatch.setenv("YUTORI_API_KEY", "dummy-key")
    import benchmark.agent_yutori as ay

    monkeypatch.setattr(ay, "OpenAI", lambda **kw: object())
    agent = ay.NavigatorAgent("n1.5-latest", env=FakeEnv(), save_dir=tmp_path)
    assert agent.messages == []
    assert all(m.get("role") != "system" for m in agent.messages)


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
