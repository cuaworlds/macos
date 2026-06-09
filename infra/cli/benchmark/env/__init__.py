from __future__ import annotations

from typing import TYPE_CHECKING

from benchmark.env.base import Env, Screenshot
from benchmark.env.use_computer import MacOSWorldEnv

if TYPE_CHECKING:
    from benchmark.env.kvm import KvmMacOSEnv
    from benchmark.env.kvm.fleet import FleetSlot

__all__ = ["Env", "Screenshot", "MacOSWorldEnv", "make_env"]


def make_env(backend: str = "use-computer", *, slot: "FleetSlot | None" = None) -> Env:
    """Construct the env for the chosen backend.

    - "use-computer": boots a fresh managed sandbox (existing behaviour).
    - "kvm": wraps an already-warm fleet slot. `slot` is required and is acquired
      by the runner from a KvmFleet before this is called.
    """
    if backend == "use-computer":
        return MacOSWorldEnv()
    if backend == "kvm":
        if slot is None:
            raise ValueError("kvm backend requires a fleet slot")
        # Imported lazily so the use-computer path never pulls in kvm modules.
        from benchmark.env.kvm import KvmMacOSEnv

        return KvmMacOSEnv(slot)
    raise ValueError(f"unknown backend: {backend!r}")
