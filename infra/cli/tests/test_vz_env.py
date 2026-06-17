"""Tests for the VZ (Apple Virtualization.framework / Tart) backend.

Two tiers:

  * HERMETIC (always run): pure-logic units that need no VM — the ECID-regen
    config.json edit, the `vnc://` scrape regex, and the structural assertion
    that VzMacOSEnv satisfies the Env protocol. These keep CI green.

  * LIVE (gated behind MACOSWORLD_VZ_LIVE=1): boot a real Tart guest, assert
    isinstance(env, Env), screenshot+dispatch+grade end-to-end, distinct sibling
    identities, and deterministic reset-by-discard. Skipped by default so CI and
    non-Mac machines never try to launch a VM.

Run the live tier (on the locked Mac, with `e3-base` present):
    MACOSWORLD_VZ_LIVE=1 uv run --group dev pytest tests/test_vz_env.py -v -s
"""

from __future__ import annotations

import base64
import json
import os
import plistlib

import pytest

from benchmark.env.base import Env
from benchmark.env.vz import VzMacOSEnv
from benchmark.env.vz.config import VzConfig

LIVE = os.getenv("MACOSWORLD_VZ_LIVE") == "1"
live_only = pytest.mark.skipif(not LIVE, reason="set MACOSWORLD_VZ_LIVE=1 to run live-VZ tests")


# --- hermetic: structural Env conformance -----------------------------------


def test_vz_env_implements_env_surface():
    """VzMacOSEnv has every member of the @runtime_checkable Env protocol.

    The live isinstance(env, Env) check needs a booted instance (live tier); this
    keeps a fast structural guard in CI so a missing method is caught instantly.
    """
    required = [n for n in dir(Env) if not n.startswith("_")]
    missing = [m for m in required if not hasattr(VzMacOSEnv, m)]
    assert missing == [], f"VzMacOSEnv missing Env members: {missing}"
    # reset() is an extra VZ method (E5 uses it); it must exist too.
    assert hasattr(VzMacOSEnv, "reset")


# --- hermetic: ECID regen (the W2 FLAG fix) ---------------------------------


def _write_fake_bundle(tmp_path, name: str, ecid_int: int):
    """Create a minimal Tart bundle dir with a config.json carrying `ecid_int`."""
    vms = tmp_path / "vms"
    bundle = vms / name
    bundle.mkdir(parents=True)
    blob = plistlib.dumps({"ECID": ecid_int}, fmt=plistlib.FMT_BINARY)
    cfg = {
        "ecid": base64.b64encode(blob).decode("ascii"),
        "macAddress": "86:44:0f:12:49:26",
        "arch": "arm64",
    }
    (bundle / "config.json").write_text(json.dumps(cfg))
    return bundle


def test_regen_ecid_changes_the_identifier(tmp_path, monkeypatch):
    """regen_ecid writes a *different* valid binary-plist ECID into config.json."""
    import benchmark.env.vz.tart as tartctl

    monkeypatch.setattr(tartctl, "TART_HOME", tmp_path)
    name = "fake-vm"
    original = 13239895588774939608  # the real value W2 decoded from the base
    bundle = _write_fake_bundle(tmp_path, name, original)

    old, new = tartctl.regen_ecid(name)
    assert old == original
    assert new != original
    assert 0 < new < 2**64

    # config.json now decodes to the new ECID, in the same binary-plist shape.
    data = json.loads((bundle / "config.json").read_text())
    decoded = plistlib.loads(base64.b64decode(data["ecid"]))
    assert decoded == {"ECID": new}
    # read_ecid round-trips.
    assert tartctl.read_ecid(name) == new


def test_regen_ecid_is_distinct_across_two_clones(tmp_path, monkeypatch):
    """Two sibling clones get distinct ECIDs — the precondition for distinct UUIDs."""
    import benchmark.env.vz.tart as tartctl

    monkeypatch.setattr(tartctl, "TART_HOME", tmp_path)
    shared = 13239895588774939608
    _write_fake_bundle(tmp_path, "inst1", shared)
    _write_fake_bundle(tmp_path, "inst2", shared)

    _, n1 = tartctl.regen_ecid("inst1")
    _, n2 = tartctl.regen_ecid("inst2")
    assert n1 != n2, "sibling ECIDs collided — siblings would share Hardware UUID/serial"


# --- hermetic: VNC line scrape ----------------------------------------------


def test_vnc_line_regex_scrapes_host_port_password():
    """The `tart run --vnc-experimental` line parses into host/port/password."""
    from benchmark.env.vz.tart import _VNC_LINE

    line = "vnc://:dignity-essay-equip-fortune@127.0.0.1:54123\n"
    m = _VNC_LINE.search(line)
    assert m is not None
    assert m.group("host") == "127.0.0.1"
    assert int(m.group("port")) == 54123
    assert m.group("pw") == "dignity-essay-equip-fortune"


# --- live: end-to-end on a real Tart/VZ guest -------------------------------


@pytest.fixture
def live_cfg():
    return VzConfig(
        base_vm=os.getenv("MACOSWORLD_VZ_BASE", "e3-base"),
        instance_name=os.getenv("MACOSWORLD_VZ_INSTANCE", "e3-inst-test"),
    )


@live_only
def test_live_isinstance_and_grade_e2e(live_cfg):
    """Boot a real VZ guest: isinstance(Env), screenshot, dispatch, grade a task."""
    from benchmark.task import Task
    from pathlib import Path

    env = VzMacOSEnv(live_cfg)
    try:
        assert isinstance(env, Env), "VzMacOSEnv is not a structural Env at runtime"

        # screenshot must be a real, non-black frame (display-sleep handled).
        shot = env.screenshot()
        assert shot.png and len(shot.png) > 1000
        extrema = shot.image.convert("L").getextrema()
        assert extrema != (0, 0), "framebuffer read black — display-sleep not handled"

        # a dispatch must round-trip without error.
        ok, msg = env.dispatch({"action": "mouse_move", "coordinate": [500, 400]})
        assert ok, msg

        # grade a real clean-POSIX task: 0 before seeding -> full after seeding.
        task_path = (
            Path(__file__).resolve().parent.parent
            / "tasks/file_management/925bdc48-a331-48c2-85f9-dcf82199966d.json"
        )
        task = Task.from_json(task_path)
        env.run_pre_command(task)
        score0, maxs, _ = env.grade(task)
        assert score0 == 0.0 and maxs == 100.0

        # seed the expected artifact over SSH, then grade should be full.
        seed = (
            'mkdir -p "$HOME/Desktop/Reports/2026" && '
            "printf 'Q1 report TBD\\n' > \"$HOME/Desktop/Reports/2026/summary.txt\""
        )
        res = env._ssh.exec(seed, timeout=30)
        assert res.rc == 0, res.stderr
        score1, _, _ = env.grade(task)
        assert score1 == 100.0
    finally:
        env.close()


@live_only
def test_live_reset_keeps_base_immutable(live_cfg):
    """3× reset-by-discard; the frozen base disk.img+nvram.bin stay byte-identical."""
    import hashlib

    import benchmark.env.vz.tart as tartctl

    disk, nvram = tartctl.disk_and_nvram(live_cfg.base_vm)

    def sha(p):
        h = hashlib.sha256()
        with open(p, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest()

    base_disk0, base_nvram0 = sha(disk), sha(nvram)

    env = VzMacOSEnv(live_cfg)
    try:
        for _ in range(3):
            env.reset()
            assert sha(disk) == base_disk0, "base disk.img changed across reset"
            assert sha(nvram) == base_nvram0, "base nvram.bin changed across reset"
    finally:
        env.close()
