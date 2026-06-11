"""A wedged RFB socket must reconnect once, not poison the env.

Before the fix, a timed-out screenshot left the same dead socket in place and every
subsequent screenshot failed. Now screenshot() reconnects a fresh socket and retries;
if the retry also fails it raises (so run_task errors the task and releases the slot).
"""

from __future__ import annotations

import socket

import pytest

from benchmark.env.kvm import KvmMacOSEnv

from conftest import FakeRfb, FakeSlot


def test_screenshot_reconnects_after_socket_timeout():
    # connect #1 (env init) returns a healthy rfb; connect #2 (reconnect) is a fresh
    # rfb whose first screenshot fails, then... no — we want: the rfb held by the env
    # fails once, reconnect succeeds. So: init rfb fails-first, reconnect rfb is clean.
    rfbs = [FakeRfb(fail_first=True), FakeRfb(fail_first=False)]
    slot = FakeSlot(rfb_factory=lambda: rfbs.pop(0))

    env = KvmMacOSEnv(slot)  # consumes rfbs[0] (the fail-first one) at init
    assert slot.connect_calls == 1

    shot = env.screenshot()  # first grab raises -> reconnect -> second grab succeeds

    assert shot.png  # got real bytes
    assert slot.connect_calls == 2  # reconnected exactly once


def test_screenshot_raises_when_reconnect_also_fails():
    # Every rfb fails its first screenshot -> reconnect also fails -> propagate.
    slot = FakeSlot(rfb_factory=lambda: FakeRfb(fail_first=True))
    env = KvmMacOSEnv(slot)
    with pytest.raises(socket.timeout):
        env.screenshot()
    assert slot.connect_calls == 2  # tried once to reconnect, then gave up
