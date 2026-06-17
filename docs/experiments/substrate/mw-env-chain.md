# mw env-chain — RFC 0002 Phase A→B, productized (W1)

Turn the prototyped `freeze-layer.sh` + the existing `KvmFleet` into a working
`mw bench run --env <pkg>` that boots an `os-base ← +apps ← instance` qcow2 backing
chain. **Proven end-to-end on the real KVM box:** a fresh `+apps` layer with a marker
file, a 3-deep runtime chain that resolves inside the boot container, the marker
visible in-guest through the chain, and an existing task graded **100/100** on the
chain-booted guest. `pytest` green (70 passed). This is the spine W3/W6/W7 build on.

---

## 1. env.toml schema

Harbor-shaped, RFC 0002 §7.3, **local-cache-only** (no OCI yet — that's W3). The
package is a dir holding `env.toml`:

```toml
[env]
name = "macos-cua-marker"
description = "..."
macos_version = "14"        # drives the <ver>/ subdir + dockur VERSION
guest_user = "user"

[target]
os      = "macos"
arch    = "x86_64"
runtime = "kvm-qcow2"       # the dispatch key; only kvm-qcow2 supported today

# L0 — os-base: the fleet's RAW source (data.img), exactly like --kvm-base-volume.
[[layers]]
ref    = "/.../macos-world/base"          # local path (abs / ~ / rel-to-env.toml)
format = "qcow2"
role   = "os-base"

# L1 — +apps: a frozen content-addressed layer (freeze-layer.sh shape).
[[layers]]
ref    = "/.../macos-world/layers/a22c98a5d55e"
digest = "sha256:a22c98a5d55e0e…"          # verified against the layer's layer.json
format = "qcow2"
role   = "apps"
```

Design decisions:
- **`ref` is a local path**, not an OCI ref (W3 swaps this for `oras pull` → cache dir;
  the rest of the resolver is unchanged). Accepts a registered tag dir, an abs path, a
  `~`-path, or a path relative to the env.toml's dir.
- **Roles ordered base-first.** Exactly one `os-base` (must be first); at most one
  `apps` today (the KVM host path mounts `/base` + `/apps` — 2-deep). Schema accepts the
  full chain so it's forward-compatible with `+task-state` etc.
- **os-base is the raw source dir** (`<ver>/data.img`), so a bare-base env.toml is
  byte-for-byte equivalent to the legacy `--kvm-base-volume` path. `+apps` layers are
  already-frozen qcow2 overlays (`<ver>/data.qcow2`, `backing_file=/base/data.qcow2`).
- **digest** (optional) is cross-checked against the layer's `layer.json` digest written
  by `freeze-layer.sh`. `macos_version` is taken from `[env]`/`[target]`, else inferred
  from `layer.json`.

The actual env package used to prove this lives at
`explorations/substrate/envs/macos-cua-marker/env.toml`.

## 2. Resolver design — `infra/cli/benchmark/env/pkg.py` (new)

`resolve_env_package(path, *, validate_paths=True) -> ResolvedEnv`:

1. Parse `env.toml` (stdlib `tomllib`).
2. Reject `target.runtime != "kvm-qcow2"`.
3. For each `[[layers]]`: known role, has `ref`, `format == qcow2`, resolve the dir;
   if `validate_paths`, assert the dir exists, read `layer.json`, cross-check declared
   vs manifest digest, infer `macos_version`.
4. Shape checks: exactly one os-base, os-base first, ≤1 apps.
5. If `validate_paths`, assert each layer carries its boot disk for the version
   (os-base: `data.img` **or** `data.qcow2`; apps: `data.qcow2`).
6. Return `ResolvedEnv(name, macos_version, runtime, base_volume_dir, apps_layer_dir,
   layers)` with `.top_layer_dir` = the immutable layer the instance overlay backs onto.

`ResolvedEnv` carries exactly what `KvmConfig` needs: `base_volume_dir` (→ `base_volume`),
`apps_layer_dir` (→ new `apps_layer_dir`, or `None` for bare base), `macos_version`.

**`validate_paths` is the remote-box gotcha fix** (see §6): the resolver runs where `mw`
runs; when the KVM box is remote the layer dirs are box-side and the laptop can't see
them, so `validate_paths=False` skips existence/manifest checks (the fleet's SSH host
commands check them instead — mirroring how `--kvm-base-volume` is a never-locally-checked
passthrough string today). The CLI passes `validate_paths=(kvm_host is local)`.

## 3. Exact code changes

| File | Change |
|---|---|
| `infra/cli/benchmark/env/pkg.py` | **New.** The resolver above (`ResolvedEnv`, `Layer`, `resolve_env_package`, `EnvPackageError`). |
| `infra/cli/benchmark/env/kvm/config.py` | Added `apps_layer_dir: Path \| None = None` field + `__post_init__` normalization + `has_apps_layer` property (`disk_mode=="overlay" and apps_layer_dir is not None`). |
| `infra/cli/benchmark/env/kvm/host.py` | `make_overlay_clone`: when `has_apps_layer`, mount **both** `/base` (so the apps layer's `backing_file=/base/data.qcow2` resolves) **and** the frozen `/apps` layer, and `qemu-img create -f qcow2 -F qcow2 -b /apps/data.qcow2`. Else unchanged (`-b /base/data.qcow2`). `run_container`: when `has_apps_layer`, additionally mount the frozen `+apps` layer read-only at `/apps` so the instance overlay's `backing_file=/apps/data.qcow2` (and that layer's own `/base` parent) both resolve inside the boot container. |
| `infra/cli/mw/cli.py` | New `--env <pkg>` option on `bench run` (mutually exclusive with `--kvm-base-volume`). Resolves the package (`validate_paths=` host-is-local), echoes the resolved chain, and threads `base_volume` + `macos_version` + `apps_layer_dir` into `KvmConfig` via `_run_kvm(..., resolved_env=...)`. `--kvm-base-volume` path unchanged (back-compat). |
| `infra/cli/tests/test_env_pkg.py` | **New.** 8 resolver unit tests (bare base, base+apps chain, version inference, digest mismatch, missing os-base, two-apps reject, unsupported runtime, missing dir). |
| `explorations/substrate/envs/macos-cua-marker/env.toml` | **New.** The hand-written 2-layer env package used for the proof. |

Back-compat: `--kvm-base-volume`, bare overlay mode, and `copy` mode are byte-for-byte
unchanged — the apps-layer branches are gated on `has_apps_layer`, which is only true
when an env package supplies an `apps_layer_dir`.

## 4. The +apps layer artifact (kept on box)

Built fresh against the **current** base (the round-1 layers under the old
`~/workspace/kvm-spike/layers/` were rebased on the old kvm-spike base and were NOT used).

- **Path:** `~/cua-world/layers/a22c98a5d55e/14/data.qcow2`
- **Digest:** `sha256:a22c98a5d55e0e78c3cd25e846ae3b0edb1c0ba7a0adc6b78bf7210ad21086f1`
- **Tag:** `~/cua-world/layers/by-name/cua-marker-1 -> a22c98a5d55e`
- **Size:** 145 MiB. `backing_file = /base/data.qcow2` (header-rebased by freeze-layer.sh).
- **Contents:** `/Users/user/.cua-apps-marker` =
  `CUA-LAYER-OK\nbuilt-by-W1-substrate-task 2026-06-12T06:09:58Z`.

How it was made: cloned a fresh overlay on the current base → booted it (`docker run`,
SSH up in ~20s) → wrote the marker over SSH → `docker stop` (clean exit 0) → froze with
`freeze-layer.sh --overlay … --base-qcow2 …/_base_qcow2 --cache …/layers --ver 14`.

## 5. Proofs

### 5a. `qemu-img info --backing-chain` — the 3-deep runtime chain resolves

Run against the **live** instance overlay while the chain guest was booted (mounting
`/base` + `/apps` + the instance `/out`, `-U` to read past the guest's write-lock):

```
image: /out/data.qcow2          virtual size: 40 GiB   disk size: 93.4 MiB
  backing file: /apps/data.qcow2
image: /apps/data.qcow2         virtual size: 40 GiB   disk size: 145 MiB
  backing file: /base/data.qcow2
image: /base/data.qcow2         virtual size: 40 GiB   disk size: 14.9 GiB
```

`instance (93 MiB) → +apps (145 MiB) → os-base (14.9 GiB)` — all three resolve inside
the container. This is the literal §7.6 step-3 chain.

### 5b. Marker visible in-guest through the chain

```
$ ssh -p 50201 user@localhost cat /Users/user/.cua-apps-marker
CUA-LAYER-OK
built-by-W1-substrate-task 2026-06-12T06:09:58Z
```

The marker lives only in the `+apps` layer; seeing it in a **fresh instance** (whose own
overlay was empty at boot) proves reads fall through instance → +apps → base correctly.

### 5c. Chain boots + task grades

`mw bench run --env explorations/substrate/envs/macos-cua-marker --tasks
746f816a-… --backend kvm --kvm-host localhost --kvm-fleet-size 1` (via `mw remote run`
on the box):

```
Env    : macos-cua-marker (base=…/base, +apps=…/layers/a22c98a5d55e)
[fleet] overlay clone -> …/mw1
[fleet] mw1 SSH up        (ready 1/1 in ~16s)
[mw1 746f816a] score: 100/100
[fleet] torn down 1 guest(s)
SUMMARY — claude-haiku-4-5 — 1 tasks
advanced  746f816a  100  …  done
```

Task `746f816a` (write `~/Documents/today.sh`) is marker-independent — it exercises the
chain-booted guest, not the marker app — and graded **100/100**.

### 5d. pytest green

`cd infra/cli && uv run --group dev pytest` → **70 passed** (62 pre-existing + 8 new
resolver tests).

## 6. Gotchas

- **Remote-box path resolution (the big one).** The resolver runs where `mw` runs. The
  intended flow is `mw remote run` (rsync the tree, run in tmux on the box with
  `--kvm-host localhost`), so the resolver runs **on the box** and `validate_paths=True`
  works against box-local paths. If you instead point a **laptop** `mw` at a remote
  `--kvm-host`, the layer dirs are box-side and invisible locally — hence
  `validate_paths=(kvm_host is local)`. env.toml records **box-side absolute** layer
  paths either way (the fleet's host commands run over SSH on the box).
- **Mounting two read-only dirs works.** `-v …/_base_qcow2/14:/base:ro -v
  …/layers/<d>/14:/apps:ro` in the same `docker run` is fine; qemu-img inside the
  container walks `/apps/data.qcow2 → /base/data.qcow2` with no extra flags.
- **dockur `DISK_FMT=qcow2`** is still required in overlay mode (set in `run_container`)
  so dockur treats `/storage/<ver>/data.qcow2` as qcow2 rather than looking for a raw
  `data.img`. Unchanged by the apps layer.
- **qcow2 write-lock.** Inspecting a *running* guest's overlay needs `qemu-img info -U`
  (the booted QEMU holds a shared-write lock); freeze still requires the guest
  `docker stop`-ped first (freeze-layer.sh enforces this).
- **os-base = raw, not qcow2.** The os-base layer points at the raw base dir
  (`data.img`); the fleet's existing `ensure_qcow2_base` converts+caches it to the
  shared `_base_qcow2/<ver>/data.qcow2` once. The resolver accepts `data.img` OR
  `data.qcow2` for the os-base role so a pre-converted base also works.

## 7. Box hygiene

- Test guest `mw1` + its overlay: removed by the fleet's own teardown.
- Build overlay `runs/apps-build`, rsync'd `runs/mw-w1chain/`, `/tmp/freeze-layer.sh`:
  removed.
- **Kept:** the frozen `+apps` layer `layers/a22c98a5d55e` (digest in §4) + its
  `by-name/cua-marker-1` tag — a useful artifact for W3/W6/W7.
- Base `~/cua-world/base/14/` untouched.
- **Orphans:** two pre-existing `Exited (255)` containers from 3 weeks ago
  (`odoo-review-analytic_cost_allocation`, `fix-git__pzkdyqs-main-1`) — **not mine**,
  left as-is.
