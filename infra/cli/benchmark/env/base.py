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

    def grade(self, task: Task) -> tuple[float, float, list[dict]]:
        """Run the task's grading_command checkpoints.

        Returns (score, max_score, per_checkpoint_log). For a legacy single
        [cmd, 100] task this is (100, 100, log) or (0, 100, log) — unchanged
        binary behaviour. Weighted-checkpoint tasks return a fractional sum.
        """
        ...

    def guest_conn(self) -> dict | None:
        """Backend-agnostic SSH coordinates for host-side grading scripts.

        Returns a dict with keys ``host``, ``port``, ``user``, ``key_path`` so a
        host-run verifier can scp/ssh into the guest to pull artifacts, or
        ``None`` if the backend can't expose direct guest SSH (e.g. a managed
        sandbox). Optional — backends may return None.
        """
        ...

    def close(self) -> None: ...
