"""env.toml resolver — RFC 0002 Phase-A/B/C composer.

Parses a Harbor-shaped env package and walks its layer chain into the concrete
on-host paths a substrate fleet needs to boot the chain `os-base ← +apps ← instance`.

Two things live here now (E1 of the substrate program generalized W1's KVM-only
resolver):

1. **Host-target detection + multi-platform dispatch.** One logical env name can
   declare BOTH per-platform chains (Docker's multi-arch model). `[target]` is the
   dispatch key `(os, arch, runtime)`. The resolver detects the HOST's target and
   picks the chain whose `[target]` matches it. A mismatch fails LOUD — it never
   silently picks a chain.

2. **OCI index resolution (local oci-layout).** A logical env can point at an OCI
   image-index (`runtime = "oci-index"` with `index = "<oci-layout-dir>:<tag>"`).
   The resolver reads the index, selects the entry matching the host target via the
   entry's `platform` + `cua.target.*` annotations, fetches that chain manifest, and
   maps its `cua.layer.*` annotations back into a `ResolvedEnv`. This proves the
   same-name → two-chains mechanism end-to-end with NO registry credentials (`oras
   --oci-layout`). GHCR is the same flow with a real ref instead of a layout dir.

Two env.toml shapes are accepted:

  - **Single-platform (legacy, W1):** top-level `[target]` + `[[layers]]`. A layer
    `ref` is a LOCAL path in freeze-layer.sh's content-addressed cache shape
    (`<layer-dir>/<ver>/data.qcow2` + `layer.json`). Byte-for-byte the W1 behaviour.
  - **Multi-platform (E1):** top-level `[env]` + repeated `[[platform]]` blocks, each
    with its own `[platform.target]` + `[[platform.layers]]`. The resolver selects one
    `[[platform]]` by host target, then resolves it exactly like the single-platform
    body. A platform whose `target.runtime == "oci-index"` resolves via OCI instead of
    local paths.

The resolver returns a `ResolvedEnv` carrying exactly what a fleet `*Config` needs:
  - `base_volume_dir`  — the os-base layer dir (its <ver>/data.qcow2 is the shared base)
  - `apps_layer_dir`   — the top immutable +apps layer dir, or None for a bare-base env
  - `macos_version`    — from [env]/[target] (drives the <ver>/ subdir + dockur VERSION)
  - `runtime`          — the resolved chain's runtime (kvm-qcow2 | vz-clonefile)
"""
from __future__ import annotations

import json
import os
import platform as _platform
import subprocess
import tomllib
from dataclasses import dataclass
from pathlib import Path

# Roles we understand. The chain is ordered base-first; the instance overlay (L3) is
# created by the fleet at boot and is never a declared layer.
ROLE_OS_BASE = "os-base"
ROLE_APPS = "apps"
_KNOWN_ROLES = (ROLE_OS_BASE, ROLE_APPS)

# Substrate runtimes a resolved chain may target. `oci-index` is NOT a substrate — it
# is a platform indirection: such a platform points at an OCI index whose entries carry
# the real substrate runtimes below.
RUNTIME_KVM = "kvm-qcow2"
RUNTIME_VZ = "vz-clonefile"
RUNTIME_OCI_INDEX = "oci-index"
_SUBSTRATE_RUNTIMES = (RUNTIME_KVM, RUNTIME_VZ)

# qcow2 ← KVM, asif ← VZ. We accept the format that matches the resolved runtime.
_RUNTIME_FORMATS = {RUNTIME_KVM: ("qcow2",), RUNTIME_VZ: ("asif", "clonefile")}

# OCI media/annotation vocabulary (RFC 0002 §7.5).
OCI_CHAIN_ARTIFACT_TYPE = "application/vnd.cua.chain.v1+json"
ANN_TARGET_OS = "cua.target.os"
ANN_TARGET_RUNTIME = "cua.target.runtime"
ANN_LAYER_ROLE = "cua.layer.role"
ANN_LAYER_OS = "cua.layer.os"
ANN_LAYER_ARCH = "cua.layer.arch"
ANN_LAYER_PARENT = "cua.layer.parent"
ANN_LAYER_FORMAT = "cua.layer.format"

# OCI uses Go's GOARCH names; our [target] uses uname-style. Normalize both ways.
_ARCH_ALIASES = {
    "x86_64": "amd64",
    "amd64": "amd64",
    "x64": "amd64",
    "arm64": "arm64",
    "aarch64": "arm64",
}


class EnvPackageError(RuntimeError):
    pass


@dataclass(frozen=True)
class HostTarget:
    """The (os, arch, runtime) a host resolves an env for — the dispatch key.

    `arch` is normalized to OCI/GOARCH spelling (amd64 | arm64) so it compares equal
    against both an env.toml `[target].arch` ("x86_64") and an OCI index entry's
    `platform.architecture` ("amd64").
    """

    os: str
    arch: str  # normalized: amd64 | arm64
    runtime: str  # substrate runtime: kvm-qcow2 | vz-clonefile

    def __str__(self) -> str:
        return f"(os={self.os}, arch={self.arch}, runtime={self.runtime})"


@dataclass(frozen=True)
class Layer:
    role: str
    dir: Path | None  # resolved local dir (None for an OCI-resolved layer)
    digest: str | None  # declared/annotated digest
    fmt: str
    ref: str | None = None  # OCI blob digest or local ref, for diagnostics


@dataclass(frozen=True)
class ResolvedEnv:
    name: str
    macos_version: str
    runtime: str  # the resolved chain's substrate runtime
    base_volume_dir: Path | None  # os-base layer dir (None when OCI-resolved)
    apps_layer_dir: Path | None  # top +apps layer dir, or None for bare base
    layers: tuple[Layer, ...]  # full resolved chain, base-first (for diagnostics)
    host_target: HostTarget | None = None  # the target this chain was selected for
    source: str = "local"  # "local" | "oci-index"

    @property
    def top_layer_dir(self) -> Path | None:
        """The top immutable layer the instance overlay must back onto."""
        return self.apps_layer_dir or self.base_volume_dir


# --------------------------------------------------------------------------- host target


def _normalize_arch(arch: str) -> str:
    a = arch.strip().lower()
    if a not in _ARCH_ALIASES:
        raise EnvPackageError(
            f"unknown arch {arch!r} (known: {sorted(set(_ARCH_ALIASES))})"
        )
    return _ARCH_ALIASES[a]


def detect_host_target(
    *,
    os_name: str | None = None,
    arch: str | None = None,
    runtime: str | None = None,
) -> HostTarget:
    """Return the local host's `(os, arch, runtime)` dispatch key.

    Detection order for each field: explicit arg → env var → autodetect.
      - os:      MW_HOST_OS      → "macos" (we only target macOS guests today)
      - arch:    MW_HOST_ARCH    → platform.machine()  (Darwin arm64 here)
      - runtime: MW_HOST_RUNTIME → arm64⇒vz-clonefile, else kvm-qcow2

    Overridable end-to-end so tests can simulate an x86_64/kvm-qcow2 host on this
    arm64 box. The runtime default follows the hybrid routing: Apple-Silicon hosts run
    the VZ substrate; x86 hosts run the KVM substrate.
    """
    resolved_os = os_name or os.environ.get("MW_HOST_OS") or "macos"
    resolved_arch = _normalize_arch(
        arch or os.environ.get("MW_HOST_ARCH") or _platform.machine()
    )
    resolved_runtime = runtime or os.environ.get("MW_HOST_RUNTIME")
    if not resolved_runtime:
        resolved_runtime = RUNTIME_VZ if resolved_arch == "arm64" else RUNTIME_KVM
    if resolved_runtime not in _SUBSTRATE_RUNTIMES:
        raise EnvPackageError(
            f"host runtime {resolved_runtime!r} is not a substrate runtime "
            f"(expected one of {_SUBSTRATE_RUNTIMES})"
        )
    return HostTarget(os=resolved_os, arch=resolved_arch, runtime=resolved_runtime)


def _target_matches_host(target: dict, host: HostTarget) -> bool:
    """A `[target]` table matches the host iff os, arch, runtime all agree.

    `os` is optional in the target (defaults to the host os) for forward-compat with
    non-macOS targets; arch + runtime must be present and must match.
    """
    t_os = target.get("os", host.os)
    t_arch = target.get("arch")
    t_runtime = target.get("runtime")
    if t_arch is None or t_runtime is None:
        return False
    return (
        t_os == host.os
        and _normalize_arch(t_arch) == host.arch
        and t_runtime == host.runtime
    )


# --------------------------------------------------------------------------- local paths


def _resolve_layer_dir(ref: str, env_dir: Path) -> Path:
    """A local layer `ref` is a path: absolute, ~-expanded, or relative to env.toml."""
    p = Path(ref).expanduser()
    if not p.is_absolute():
        p = (env_dir / p).resolve()
    return p


def _read_layer_manifest(layer_dir: Path) -> dict | None:
    """Read freeze-layer.sh's layer.json if present (optional but used to verify)."""
    mf = layer_dir / "layer.json"
    if not mf.exists():
        return None
    try:
        return json.loads(mf.read_text())
    except (OSError, ValueError) as e:
        raise EnvPackageError(f"unreadable layer manifest {mf}: {e}") from e


def _resolve_local_chain(
    *,
    name: str,
    runtime: str,
    env_tbl: dict,
    target: dict,
    raw_layers: list,
    env_dir: Path,
    validate_paths: bool,
    host_target: HostTarget | None,
    source: str,
) -> ResolvedEnv:
    """Resolve a single-platform body (local-path layers) → ResolvedEnv.

    Shared by the legacy single-platform shape and a selected multi-platform
    `[[platform]]`. Validates roles/shape/digest exactly as W1 did.
    """
    declared_ver = env_tbl.get("macos_version") or target.get("macos_version")
    ok_formats = _RUNTIME_FORMATS.get(runtime, ("qcow2",))

    if not raw_layers:
        raise EnvPackageError(f"{name}: no [[layers]] declared")

    resolved: list[Layer] = []
    inferred_ver: str | None = None
    for i, lyr in enumerate(raw_layers):
        role = lyr.get("role")
        if role not in _KNOWN_ROLES:
            raise EnvPackageError(
                f"{name}: layer[{i}] role={role!r} unknown (known: {_KNOWN_ROLES})"
            )
        ref = lyr.get("ref") or lyr.get("path")
        if not ref:
            raise EnvPackageError(f"{name}: layer[{i}] ({role}) has no 'ref'/'path'")
        fmt = lyr.get("format", ok_formats[0])
        if fmt not in ok_formats:
            raise EnvPackageError(
                f"{name}: layer[{i}] format={fmt!r} unsupported for runtime "
                f"{runtime!r} (expected one of {ok_formats})"
            )
        ldir = _resolve_layer_dir(ref, env_dir)
        if validate_paths and not ldir.is_dir():
            raise EnvPackageError(
                f"{name}: layer[{i}] ({role}) dir does not exist: {ldir}"
            )

        manifest = _read_layer_manifest(ldir) if validate_paths else None
        declared_digest = lyr.get("digest")
        if manifest:
            mver = manifest.get("macos_version")
            if mver:
                inferred_ver = inferred_ver or mver
            mdigest = manifest.get("digest")
            if declared_digest and mdigest and declared_digest != mdigest:
                raise EnvPackageError(
                    f"{name}: layer[{i}] ({role}) digest mismatch: env.toml says "
                    f"{declared_digest} but layer.json says {mdigest}"
                )
        resolved.append(
            Layer(role=role, dir=ldir, digest=declared_digest, fmt=fmt, ref=ref)
        )

    _check_chain_shape(name, resolved)

    macos_version = declared_ver or inferred_ver
    if not macos_version:
        raise EnvPackageError(
            f"{name}: macos_version not set in [env]/[target]"
            + ("" if validate_paths else " (required when validate_paths=False)")
            + (" and no layer.json to infer it" if validate_paths else "")
        )

    if validate_paths:
        for l in resolved:
            vdir = l.dir / macos_version
            if l.role == ROLE_OS_BASE:
                if not (vdir / "data.img").exists() and not (vdir / "data.qcow2").exists():
                    raise EnvPackageError(
                        f"{name}: os-base layer missing boot disk {vdir}/data.{{img,qcow2}}"
                    )
            else:
                disk = vdir / "data.qcow2"
                if not disk.exists():
                    raise EnvPackageError(
                        f"{name}: layer ({l.role}) missing boot disk {disk}"
                    )

    base = next(l for l in resolved if l.role == ROLE_OS_BASE)
    apps = next((l for l in resolved if l.role == ROLE_APPS), None)
    return ResolvedEnv(
        name=name,
        macos_version=macos_version,
        runtime=runtime,
        base_volume_dir=base.dir,
        apps_layer_dir=(apps.dir if apps else None),
        layers=tuple(resolved),
        host_target=host_target,
        source=source,
    )


def _check_chain_shape(name: str, resolved: list[Layer]) -> None:
    roles = [l.role for l in resolved]
    if roles.count(ROLE_OS_BASE) != 1:
        raise EnvPackageError(
            f"{name}: need exactly one os-base layer, got roles={roles}"
        )
    if roles[0] != ROLE_OS_BASE:
        raise EnvPackageError(
            f"{name}: os-base must be the first layer, got order={roles}"
        )
    n_apps = roles.count(ROLE_APPS)
    if n_apps > 1:
        raise EnvPackageError(
            f"{name}: at most one apps layer supported today (got {n_apps})"
        )


# ----------------------------------------------------------------------------- OCI index


def _oras(*args: str) -> str:
    """Run `oras <args>` and return stdout, raising EnvPackageError on failure."""
    try:
        proc = subprocess.run(
            ["oras", *args],
            check=True,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError as e:
        raise EnvPackageError(
            "oras not found on PATH (install with `brew install oras`)"
        ) from e
    except subprocess.CalledProcessError as e:
        raise EnvPackageError(
            f"oras {' '.join(args)} failed (exit {e.returncode}): {e.stderr.strip()}"
        ) from e
    return proc.stdout


def _oci_layout_ref(index: str, env_dir: Path) -> str:
    """Resolve an `index = "<dir>:<tag>"` to an absolute `<abs-dir>:<tag>` ref.

    The layout dir is local-path-resolved (abs / ~ / rel-to-env.toml) so an env
    package can ship its own oci-layout sibling and be relocatable.
    """
    if ":" not in index:
        raise EnvPackageError(
            f"oci-index 'index' must be '<oci-layout-dir>:<tag>', got {index!r}"
        )
    layout, _, tag = index.rpartition(":")
    p = Path(layout).expanduser()
    if not p.is_absolute():
        p = (env_dir / p).resolve()
    return f"{p}:{tag}"


def _select_index_entry(index_doc: dict, host: HostTarget) -> dict:
    """Pick the index entry whose platform+annotations match the host target.

    Matching key: `platform.architecture` (== host.arch) AND the entry's
    `cua.target.runtime` annotation (== host.runtime) AND, if present,
    `cua.target.os` / `platform.os`. Fail LOUD with the available entries when no
    entry matches — never silently pick one.
    """
    manifests = index_doc.get("manifests", [])
    candidates = []
    for m in manifests:
        plat = m.get("platform", {}) or {}
        ann = m.get("annotations", {}) or {}
        entry_arch = plat.get("architecture")
        entry_runtime = ann.get(ANN_TARGET_RUNTIME)
        entry_os = ann.get(ANN_TARGET_OS) or plat.get("os")
        candidates.append((entry_os, entry_arch, entry_runtime, m))
        arch_ok = entry_arch is not None and _normalize_arch(entry_arch) == host.arch
        runtime_ok = entry_runtime == host.runtime
        # os is advisory: darwin/macos both map to our macOS guest.
        os_ok = entry_os in (None, host.os, "darwin")
        if arch_ok and runtime_ok and os_ok:
            return m
    avail = ", ".join(
        f"(os={o}, arch={a}, runtime={r})" for (o, a, r, _) in candidates
    ) or "<none>"
    raise EnvPackageError(
        f"no matching platform for target={host}; index offers: {avail}"
    )


def _resolve_oci_chain(
    *,
    name: str,
    env_tbl: dict,
    target: dict,
    env_dir: Path,
    host_target: HostTarget,
) -> ResolvedEnv:
    """Resolve an `oci-index` platform → fetch index → select entry → fetch chain.

    No registry credentials: uses `oras ... --oci-layout <dir>`. The selected chain
    manifest's `cua.layer.*` annotations become the `ResolvedEnv` layers. Layers carry
    no local dir (pulling blobs to a cache is the runtime-composition step, W3/E6);
    this resolver proves *selection*, returning the chain identity (digests + roles).
    """
    index = target.get("index")
    if not index:
        raise EnvPackageError(
            f"{name}: target.runtime='oci-index' requires 'index = \"<dir>:<tag>\"'"
        )
    ref = _oci_layout_ref(index, env_dir)
    index_doc = json.loads(_oras("manifest", "fetch", "--oci-layout", ref))
    media = index_doc.get("mediaType", "")
    if "index" not in media:
        raise EnvPackageError(
            f"{name}: {ref} is not an OCI image-index (mediaType={media!r})"
        )

    entry = _select_index_entry(index_doc, host_target)
    chain_digest = entry["digest"]
    runtime = (entry.get("annotations", {}) or {}).get(ANN_TARGET_RUNTIME)

    layout_dir = ref.rpartition(":")[0]
    chain_doc = json.loads(
        _oras("manifest", "fetch", "--oci-layout", f"{layout_dir}@{chain_digest}")
    )

    layers: list[Layer] = []
    for blob in chain_doc.get("layers", []):
        ann = blob.get("annotations", {}) or {}
        role = ann.get(ANN_LAYER_ROLE)
        if role not in _KNOWN_ROLES:
            raise EnvPackageError(
                f"{name}: chain {chain_digest} blob has role={role!r} "
                f"(expected one of {_KNOWN_ROLES})"
            )
        layers.append(
            Layer(
                role=role,
                dir=None,
                digest=blob.get("digest"),
                fmt=ann.get(ANN_LAYER_FORMAT, ""),
                ref=blob.get("digest"),
            )
        )
    _check_chain_shape(name, layers)

    macos_version = env_tbl.get("macos_version") or target.get("macos_version")
    if not macos_version:
        raise EnvPackageError(
            f"{name}: macos_version not set in [env] (required for oci-index)"
        )
    return ResolvedEnv(
        name=name,
        macos_version=macos_version,
        runtime=runtime,
        base_volume_dir=None,
        apps_layer_dir=None,
        layers=tuple(layers),
        host_target=host_target,
        source="oci-index",
    )


# ----------------------------------------------------------------------------- entrypoint


def resolve_env_package(
    path: str | Path,
    *,
    validate_paths: bool = True,
    host_target: HostTarget | None = None,
) -> ResolvedEnv:
    """Parse an env.toml (file or its dir) and resolve its chain for the host target.

    Accepts both env.toml shapes (see module docstring):
      - single-platform (legacy): top-level `[target]` + `[[layers]]`.
      - multi-platform: top-level `[env]` + `[[platform]]` blocks; the resolver selects
        the block matching `host_target` (or `detect_host_target()`), failing LOUD on a
        mismatch. A selected block whose `target.runtime == "oci-index"` resolves via an
        OCI image-index in a local oci-layout dir (no registry creds).

    `validate_paths` (default True) checks each local layer dir + boot disk exists; set
    False for box-side paths the laptop can't see (mirrors W1). It does not apply to the
    OCI path (no local dirs to check).
    """
    p = Path(path).expanduser()
    if p.is_dir():
        p = p / "env.toml"
    if not p.exists():
        raise EnvPackageError(f"no env.toml at {p}")
    env_dir = p.parent

    try:
        doc = tomllib.loads(p.read_text())
    except (OSError, tomllib.TOMLDecodeError) as e:
        raise EnvPackageError(f"cannot parse {p}: {e}") from e

    env_tbl = doc.get("env", {})
    name = env_tbl.get("name") or p.parent.name
    platforms = doc.get("platform", [])

    if platforms:
        host = host_target or detect_host_target()
        return _resolve_multiplatform(
            name=name,
            env_tbl=env_tbl,
            platforms=platforms,
            env_dir=env_dir,
            validate_paths=validate_paths,
            host=host,
        )

    # --- single-platform (legacy W1) shape ---
    target = doc.get("target", {})
    runtime = target.get("runtime", RUNTIME_KVM)
    if runtime not in _SUBSTRATE_RUNTIMES:
        raise EnvPackageError(
            f"{name}: target.runtime={runtime!r} unsupported "
            f"(substrate runtimes: {_SUBSTRATE_RUNTIMES})"
        )
    # If a host target is supplied for a single-platform env, enforce it matches —
    # otherwise booting an x86/kvm package on an arm64/vz host would silently lie.
    if host_target is not None and not _target_matches_host(target, host_target):
        raise EnvPackageError(
            f"{name}: no matching platform for target={host_target}; "
            f"package targets (os={target.get('os', '?')}, "
            f"arch={target.get('arch', '?')}, runtime={runtime})"
        )
    return _resolve_local_chain(
        name=name,
        runtime=runtime,
        env_tbl=env_tbl,
        target=target,
        raw_layers=doc.get("layers", []),
        env_dir=env_dir,
        validate_paths=validate_paths,
        host_target=host_target,
        source="local",
    )


def _resolve_multiplatform(
    *,
    name: str,
    env_tbl: dict,
    platforms: list,
    env_dir: Path,
    validate_paths: bool,
    host: HostTarget,
) -> ResolvedEnv:
    """Select the `[[platform]]` matching the host target, then resolve its body."""
    available = []
    for plat in platforms:
        target = plat.get("target", {})
        available.append(
            f"(os={target.get('os', host.os)}, arch={target.get('arch', '?')}, "
            f"runtime={target.get('runtime', '?')})"
        )
        if not _target_matches_host(target, host):
            continue
        runtime = target["runtime"]
        # merge: platform [env] overrides (e.g. macos_version) fall back to top [env].
        merged_env = {**env_tbl, **plat.get("env", {})}
        return _resolve_local_chain(
            name=name,
            runtime=runtime,
            env_tbl=merged_env,
            target=target,
            raw_layers=plat.get("layers", []),
            env_dir=env_dir,
            validate_paths=validate_paths,
            host_target=host,
            source="local",
        )

    # No `[[platform]]` matched directly. One may be an oci-index indirection — try
    # those (they dispatch internally on the index entries).
    for plat in platforms:
        target = plat.get("target", {})
        if target.get("runtime") == RUNTIME_OCI_INDEX:
            t_os = target.get("os", host.os)
            if t_os != host.os:
                continue
            merged_env = {**env_tbl, **plat.get("env", {})}
            return _resolve_oci_chain(
                name=name,
                env_tbl=merged_env,
                target=target,
                env_dir=env_dir,
                host_target=host,
            )

    raise EnvPackageError(
        f"{name}: no matching platform for target={host}; "
        f"package offers: {', '.join(available)}"
    )
