"""Unit tests for the env.toml resolver (benchmark.env.pkg).

Exercise the schema + chain resolution with on-disk fixtures — no VM, no SSH. The
disk files are touched empty (the resolver only checks existence/shape). Run:
`uv run pytest infra/cli/tests/test_env_pkg.py`.

Three groups:
  - single-platform (W1, legacy `[target]` + `[[layers]]`) — unchanged behaviour.
  - host-target detection + multi-platform dispatch (E1) — one env name → the right
    per-platform chain, fail-loud on a host the package doesn't target.
  - OCI image-index round-trip (E1) — same name → KVM/qcow2 vs VZ/asif chain via a
    credential-free local oci-layout, gated on `oras` being installed.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from benchmark.env.pkg import (
    EnvPackageError,
    HostTarget,
    detect_host_target,
    resolve_env_package,
)

X86_KVM = HostTarget(os="macos", arch="amd64", runtime="kvm-qcow2")
ARM_VZ = HostTarget(os="macos", arch="arm64", runtime="vz-clonefile")
ARM_KVM = HostTarget(os="macos", arch="arm64", runtime="kvm-qcow2")  # unmatched combo


def _make_layer(root, digest: str, *, role: str, raw: bool = False, ver: str = "14"):
    """Create a content-addressed layer dir with a boot disk + layer.json."""
    d = root / digest
    (d / ver).mkdir(parents=True)
    disk = "data.img" if raw else "data.qcow2"
    (d / ver / disk).write_bytes(b"")
    (d / "layer.json").write_text(
        json.dumps({"digest": f"sha256:{digest}", "role": role, "macos_version": ver})
    )
    return d


def _write_env(tmp_path, body: str):
    p = tmp_path / "env.toml"
    p.write_text(body)
    return p


def test_bare_base_resolves(tmp_path):
    base = _make_layer(tmp_path / "cache", "base000000000", role="os-base", raw=True)
    _write_env(
        tmp_path,
        f"""
[env]
name = "bare"
macos_version = "14"
[target]
runtime = "kvm-qcow2"
[[layers]]
ref = "{base}"
role = "os-base"
format = "qcow2"
""",
    )
    env = resolve_env_package(tmp_path)
    assert env.base_volume_dir == base
    assert env.apps_layer_dir is None
    assert env.top_layer_dir == base
    assert env.macos_version == "14"


def test_base_plus_apps_chain(tmp_path):
    base = _make_layer(tmp_path / "cache", "base000000000", role="os-base", raw=True)
    apps = _make_layer(tmp_path / "cache", "apps000000000", role="apps")
    _write_env(
        tmp_path,
        f"""
[env]
name = "withapps"
macos_version = "14"
[target]
runtime = "kvm-qcow2"
[[layers]]
ref = "{base}"
role = "os-base"
[[layers]]
ref = "{apps}"
role = "apps"
""",
    )
    env = resolve_env_package(tmp_path)
    assert env.base_volume_dir == base
    assert env.apps_layer_dir == apps
    assert env.top_layer_dir == apps
    assert [l.role for l in env.layers] == ["os-base", "apps"]


def test_macos_version_inferred_from_manifest(tmp_path):
    base = _make_layer(tmp_path / "cache", "base000000000", role="os-base", raw=True)
    _write_env(
        tmp_path,
        f"""
[env]
name = "infer"
[[layers]]
ref = "{base}"
role = "os-base"
""",
    )
    env = resolve_env_package(tmp_path)
    assert env.macos_version == "14"


def test_digest_mismatch_rejected(tmp_path):
    base = _make_layer(tmp_path / "cache", "base000000000", role="os-base", raw=True)
    _write_env(
        tmp_path,
        f"""
[env]
name = "mismatch"
macos_version = "14"
[[layers]]
ref = "{base}"
role = "os-base"
digest = "sha256:deadbeef"
""",
    )
    with pytest.raises(EnvPackageError, match="digest mismatch"):
        resolve_env_package(tmp_path)


def test_missing_os_base_rejected(tmp_path):
    apps = _make_layer(tmp_path / "cache", "apps000000000", role="apps")
    _write_env(
        tmp_path,
        f"""
[env]
name = "nobase"
macos_version = "14"
[[layers]]
ref = "{apps}"
role = "apps"
""",
    )
    with pytest.raises(EnvPackageError, match="os-base"):
        resolve_env_package(tmp_path)


def test_two_apps_layers_rejected(tmp_path):
    base = _make_layer(tmp_path / "cache", "base000000000", role="os-base", raw=True)
    a1 = _make_layer(tmp_path / "cache", "apps100000000", role="apps")
    a2 = _make_layer(tmp_path / "cache", "apps200000000", role="apps")
    _write_env(
        tmp_path,
        f"""
[env]
name = "twoapps"
macos_version = "14"
[[layers]]
ref = "{base}"
role = "os-base"
[[layers]]
ref = "{a1}"
role = "apps"
[[layers]]
ref = "{a2}"
role = "apps"
""",
    )
    with pytest.raises(EnvPackageError, match="at most one apps"):
        resolve_env_package(tmp_path)


def test_unsupported_runtime_rejected(tmp_path):
    base = _make_layer(tmp_path / "cache", "base000000000", role="os-base", raw=True)
    _write_env(
        tmp_path,
        f"""
[env]
name = "vz"
macos_version = "14"
[target]
runtime = "vz-asif"
[[layers]]
ref = "{base}"
role = "os-base"
""",
    )
    with pytest.raises(EnvPackageError, match="unsupported"):
        resolve_env_package(tmp_path)


def test_missing_layer_dir_rejected(tmp_path):
    _write_env(
        tmp_path,
        """
[env]
name = "ghost"
macos_version = "14"
[[layers]]
ref = "/nonexistent/layer/dir"
role = "os-base"
""",
    )
    with pytest.raises(EnvPackageError, match="does not exist"):
        resolve_env_package(tmp_path)


# --------------------------------------------------------------------------- host target


def test_detect_host_target_explicit_args_normalize_arch():
    ht = detect_host_target(os_name="macos", arch="x86_64", runtime="kvm-qcow2")
    assert (ht.os, ht.arch, ht.runtime) == ("macos", "amd64", "kvm-qcow2")
    # aarch64 also normalizes to arm64.
    assert detect_host_target(arch="aarch64", runtime="vz-clonefile").arch == "arm64"


def test_detect_host_target_env_var_override(monkeypatch):
    monkeypatch.setenv("MW_HOST_OS", "macos")
    monkeypatch.setenv("MW_HOST_ARCH", "x86_64")
    monkeypatch.setenv("MW_HOST_RUNTIME", "kvm-qcow2")
    assert detect_host_target() == X86_KVM


def test_detect_host_target_runtime_defaults_to_substrate_by_arch(monkeypatch):
    monkeypatch.delenv("MW_HOST_RUNTIME", raising=False)
    # arm64 ⇒ VZ, x86 ⇒ KVM (the hybrid routing default).
    assert detect_host_target(arch="arm64").runtime == "vz-clonefile"
    assert detect_host_target(arch="x86_64").runtime == "kvm-qcow2"


def test_detect_host_target_rejects_non_substrate_runtime():
    with pytest.raises(EnvPackageError, match="not a substrate runtime"):
        detect_host_target(arch="x86_64", runtime="oci-index")


# ----------------------------------------------------- single-platform host enforcement


def test_single_platform_host_mismatch_fails_loud(tmp_path):
    """A legacy single-platform package booted on a host it doesn't target fails loud,
    never silently boots the wrong-arch chain."""
    base = _make_layer(tmp_path / "cache", "base000000000", role="os-base", raw=True)
    _write_env(
        tmp_path,
        f"""
[env]
name = "x86only"
macos_version = "14"
[target]
os = "macos"
arch = "x86_64"
runtime = "kvm-qcow2"
[[layers]]
ref = "{base}"
role = "os-base"
""",
    )
    # matching host: resolves fine.
    assert resolve_env_package(tmp_path, host_target=X86_KVM).runtime == "kvm-qcow2"
    # mismatched host: fail loud.
    with pytest.raises(EnvPackageError, match="no matching platform for target"):
        resolve_env_package(tmp_path, host_target=ARM_VZ)


# ------------------------------------------------- inline multi-platform dispatch (E1)


def _inline_multiplatform_env(tmp_path):
    """One logical name with BOTH platform chains declared inline (local paths)."""
    kbase = _make_layer(tmp_path / "cache", "kbase00000000", role="os-base", raw=True)
    kapps = _make_layer(tmp_path / "cache", "kapps00000000", role="apps")
    vbase = _make_layer(tmp_path / "cache", "vbase00000000", role="os-base", raw=True)
    _write_env(
        tmp_path,
        f"""
[env]
name = "dual"
macos_version = "14"

[[platform]]
  [platform.target]
  os = "macos"
  arch = "x86_64"
  runtime = "kvm-qcow2"
  [[platform.layers]]
  ref = "{kbase}"
  role = "os-base"
  [[platform.layers]]
  ref = "{kapps}"
  role = "apps"

[[platform]]
  [platform.target]
  os = "macos"
  arch = "arm64"
  runtime = "vz-clonefile"
  [[platform.layers]]
  ref = "{vbase}"
  role = "os-base"
  format = "asif"
""",
    )
    return kbase, kapps, vbase


def test_multiplatform_selects_kvm_chain_for_x86_host(tmp_path):
    kbase, kapps, _ = _inline_multiplatform_env(tmp_path)
    env = resolve_env_package(tmp_path, host_target=X86_KVM)
    assert env.runtime == "kvm-qcow2"
    assert env.base_volume_dir == kbase
    assert env.apps_layer_dir == kapps
    assert [l.role for l in env.layers] == ["os-base", "apps"]


def test_multiplatform_selects_vz_chain_for_arm_host(tmp_path):
    _, _, vbase = _inline_multiplatform_env(tmp_path)
    env = resolve_env_package(tmp_path, host_target=ARM_VZ)
    assert env.runtime == "vz-clonefile"
    assert env.base_volume_dir == vbase
    assert env.apps_layer_dir is None
    assert env.layers[0].fmt == "asif"


def test_multiplatform_unmatched_host_fails_loud(tmp_path):
    _inline_multiplatform_env(tmp_path)
    with pytest.raises(EnvPackageError, match="no matching platform for target"):
        resolve_env_package(tmp_path, host_target=ARM_KVM)


# --------------------------------------------------------- OCI image-index round-trip


_ORAS = shutil.which("oras")
# The committed cua-macos-textedit env package + its prebuilt oci-layout.
_TEXTEDIT_ENV = (
    Path(__file__).resolve().parents[1]  # infra/cli
    / "envs"
    / "cua-macos-textedit"
)
requires_oras = pytest.mark.skipif(_ORAS is None, reason="oras not installed")
requires_layout = pytest.mark.skipif(
    not (_TEXTEDIT_ENV / "oci-layout" / "index.json").exists(),
    reason="oci-layout not built (run envs/cua-macos-textedit/build-index.sh)",
)


@requires_oras
@requires_layout
def test_oci_index_resolves_kvm_chain_for_x86_host():
    env = resolve_env_package(_TEXTEDIT_ENV, host_target=X86_KVM)
    assert env.source == "oci-index"
    assert env.runtime == "kvm-qcow2"
    assert [l.role for l in env.layers] == ["os-base", "apps"]
    assert all(l.fmt == "qcow2" for l in env.layers)
    assert all(l.digest and l.digest.startswith("sha256:") for l in env.layers)


@requires_oras
@requires_layout
def test_oci_index_resolves_vz_chain_for_arm_host():
    env = resolve_env_package(_TEXTEDIT_ENV, host_target=ARM_VZ)
    assert env.source == "oci-index"
    assert env.runtime == "vz-clonefile"
    assert [l.role for l in env.layers] == ["os-base", "apps"]
    assert all(l.fmt == "asif" for l in env.layers)


@requires_oras
@requires_layout
def test_oci_index_same_name_yields_two_distinct_chains():
    """The headline: ONE env name → two different chain manifests by host target."""
    kvm = resolve_env_package(_TEXTEDIT_ENV, host_target=X86_KVM)
    vz = resolve_env_package(_TEXTEDIT_ENV, host_target=ARM_VZ)
    kvm_digests = {l.digest for l in kvm.layers}
    vz_digests = {l.digest for l in vz.layers}
    assert kvm_digests.isdisjoint(vz_digests)  # no shared blobs — genuinely two chains


@requires_oras
@requires_layout
def test_oci_index_unmatched_host_fails_loud():
    with pytest.raises(EnvPackageError, match="no matching platform for target"):
        resolve_env_package(_TEXTEDIT_ENV, host_target=ARM_KVM)


@requires_oras
@requires_layout
def test_oci_index_apps_parent_annotation_points_at_os_base():
    """cua.layer.parent on +apps == the os-base blob digest (layer.json → OCI map)."""
    env = resolve_env_package(_TEXTEDIT_ENV, host_target=X86_KVM)
    layout = _TEXTEDIT_ENV / "oci-layout"
    chain = subprocess.run(
        ["oras", "manifest", "fetch", "--oci-layout", f"{layout}:chain-kvm-x86_64"],
        check=True,
        capture_output=True,
        text=True,
    )
    doc = json.loads(chain.stdout)
    base_blob, apps_blob = doc["layers"]
    assert base_blob["annotations"]["cua.layer.role"] == "os-base"
    assert apps_blob["annotations"]["cua.layer.parent"] == base_blob["digest"]
