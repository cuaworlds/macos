from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from PIL import Image

from benchmark.task import Task


@dataclass
class Screenshot:
    image: Image.Image
    png: bytes
    scale_x: float
    scale_y: float


@runtime_checkable
class Env(Protocol):
    """The surface the agent loop (benchmark/agent.py) drives.

    Both MacOSWorldEnv (managed Use Computer SDK) and KvmMacOSEnv (local QEMU/KVM
    fleet) implement this. The agent only ever calls screenshot() + dispatch();
    the runner additionally uses sandbox_id, run_pre_command(), grade(), close().
    """

    @property
    def sandbox_id(self) -> str: ...

    def screenshot(self) -> Screenshot: ...

    def dispatch(self, action_input: dict) -> tuple[bool, str]: ...

    def run_pre_command(self, task: Task) -> None: ...

    def grade(self, task: Task) -> tuple[int, list[dict]]: ...

    def close(self) -> None: ...
