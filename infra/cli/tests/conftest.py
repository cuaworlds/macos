"""Shared fakes for the benchmark harness tests.

All tests are hermetic: no real VMs, no docker, no network, no API keys. The fakes
implement just enough of each collaborator's surface for the unit under test.
"""

from __future__ import annotations

import socket
from dataclasses import dataclass, field

from PIL import Image

from benchmark.env.base import Screenshot


def make_image(w: int = 1024, h: int = 768, color=(10, 20, 30)) -> Image.Image:
    return Image.new("RGB", (w, h), color)


# --- agent / step-loop fakes -------------------------------------------------


@dataclass
class FakeRec:
    """Mimics agent.StepRecord's fields that run_task / drive_agent read."""

    status: str
    actions: list = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    latency_s: float = 0.0


class FakeAgent:
    """Returns a scripted sequence of statuses, one per step()."""

    def __init__(self, statuses: list[str]):
        self._statuses = list(statuses)
        self.calls = 0

    def step(self, step_index: int, max_steps: int, instruction: str) -> FakeRec:
        self.calls += 1
        status = self._statuses[step_index - 1] if step_index <= len(self._statuses) else "unfinished"
        return FakeRec(status=status)


# --- RFB / fleet-slot fakes --------------------------------------------------


class FakeRfb:
    """One RFB connection. `fail_first` makes the first screenshot() raise, as a
    wedged-socket would; a freshly-constructed FakeRfb (the reconnect) succeeds."""

    def __init__(self, *, w: int = 1920, h: int = 1080, fail_first: bool = False):
        self.w = w
        self.h = h
        self._fail_next = fail_first
        self.closed = False

    def screenshot(self) -> Image.Image:
        if self._fail_next:
            self._fail_next = False
            raise socket.timeout("simulated wedged VNC socket")
        return make_image(self.w, self.h)

    def close(self) -> None:
        self.closed = True


class FakeSlot:
    """Stands in for a fleet FleetSlot for KvmMacOSEnv tests."""

    def __init__(self, *, rfb_factory=None, container_name: str = "mwX"):
        self.container_name = container_name
        self.released = False
        self.connect_calls = 0
        self._rfb_factory = rfb_factory or (lambda: FakeRfb())

    def ssh(self):
        return _NoopSsh()

    def connect_rfb(self) -> FakeRfb:
        self.connect_calls += 1
        return self._rfb_factory()

    def release(self) -> None:
        self.released = True


class _NoopSsh:
    def exec(self, *a, **k):
        raise AssertionError("tests must not touch SSH")

    def exec_detached(self, *a, **k):
        raise AssertionError("tests must not touch SSH")
