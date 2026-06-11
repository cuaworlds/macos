"""Slots must always return to the pool — else queued tasks block forever.

Two invariants:
  - KvmMacOSEnv.close() releases its slot even if tearing down the RFB socket raises.
  - KvmFleet.release() re-enqueues the slot even when the best-effort in-guest reset
    fails (no host reachable in tests).
"""

from __future__ import annotations

import queue

from benchmark.env.kvm import KvmMacOSEnv, KvmConfig, KvmFleet

from conftest import FakeRfb, FakeSlot


class _ClosolRfb(FakeRfb):
    def close(self):
        raise RuntimeError("socket already broken")


def test_env_close_releases_slot_even_if_rfb_close_raises():
    slot = FakeSlot(rfb_factory=lambda: _ClosolRfb())
    env = KvmMacOSEnv(slot)
    env.close()
    assert slot.released is True


def test_fleet_release_reenqueues_even_when_reset_fails():
    # Real fleet object, no boot. release() will try slot.ssh().exec_detached (which
    # our FakeSlot makes raise) — the best-effort reset must be swallowed and the slot
    # put back so it is acquirable again.
    fleet = KvmFleet(KvmConfig(fleet_size=1, host="localhost"))
    slot = FakeSlot()

    fleet.release(slot)

    # Slot is back in the pool and acquirable without blocking.
    assert fleet.acquire(timeout=1) is slot
    with_empty = queue.Empty
    try:
        fleet.acquire(timeout=0.05)
        raise AssertionError("pool should be empty after the one slot is taken")
    except with_empty:
        pass
