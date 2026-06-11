"""drive_agent: per-step emission + terminal-status control flow.

Guards the observability property at the logic level — `on_step` fires once per
step (so progress is reported incrementally, never batched at the end) — and the
loop's stop conditions.
"""

from __future__ import annotations

from benchmark.runner import drive_agent

from conftest import FakeAgent


def test_emits_once_per_step_and_stops_on_terminal():
    agent = FakeAgent(["unfinished", "unfinished", "done"])
    emitted: list[int] = []

    status = drive_agent(agent, "task", max_steps=10, on_step=lambda s, r: emitted.append(s))

    assert status == "done"
    assert emitted == [1, 2, 3]  # one emit per step, in order, stops at terminal
    assert agent.calls == 3  # did not keep stepping after 'done'


def test_returns_max_steps_when_never_terminal():
    agent = FakeAgent(["unfinished", "unfinished"])
    emitted: list[int] = []

    status = drive_agent(agent, "task", max_steps=2, on_step=lambda s, r: emitted.append(s))

    assert status == "max_steps"
    assert emitted == [1, 2]
    assert agent.calls == 2


def test_propagates_fail_status():
    agent = FakeAgent(["fail"])
    status = drive_agent(agent, "task", max_steps=5)
    assert status == "fail"
    assert agent.calls == 1
