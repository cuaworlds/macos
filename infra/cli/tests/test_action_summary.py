"""summarize_action: greppable one-line action summaries for the unified log."""

from __future__ import annotations

from benchmark.runner import summarize_action


def test_ok_action_with_coords():
    s = summarize_action({"action": "left_click", "input": {"coordinate": [434, 294]}, "ok": True, "msg": "ok"})
    assert s == 'left_click {"coordinate":[434,294]} -> ok'


def test_failed_action_surfaces_msg():
    s = summarize_action(
        {"action": "scroll", "input": {"coordinate": [1, 2]}, "ok": False, "msg": "dispatch error: boom"}
    )
    assert s.startswith("scroll ") and "FAIL: dispatch error: boom" in s


def test_long_input_is_truncated():
    s = summarize_action({"action": "type", "input": {"text": "x" * 500}, "ok": True})
    assert "…" in s and len(s) < 220


def test_missing_fields_dont_crash():
    assert summarize_action({}).startswith("? ")  # no action name, no input, defaults to ok
