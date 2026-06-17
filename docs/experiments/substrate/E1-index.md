# E1 — interop link #1: one env name → the right per-platform chain (OCI index)

**Verdict: PROVEN.** A single logical env (`cua/macos:textedit-v1`) resolves — via a
standard OCI image-index in a credential-free local oci-layout — to the **KVM/qcow2**
chain on an `(x86_64, kvm-qcow2)` host and the **VZ/clonefile** chain on an
`(arm64, vz-clonefile)` host. An unmatched host target **fails LOUD**
(`no matching platform for target=…`) and never silently picks a chain. Same resolver
call, two byte-chains, mismatch rejected. **Decision: use the INDEX** (one tag), not
sibling tags — it round-trips cleanly in oci-layout and on GHCR (one cosmetic caveat,
§7). `cd infra/cli && uv run --group dev pytest` → **86 passed** (8 W1 + 13 new E1).

Built on W1's `pkg.py` (the KVM-only resolver). E1 generalizes it: host-target
detection, a multi-platform env.toml shape, and OCI-index resolution.

---

## 1. What changed in `infra/cli/benchmark/env/pkg.py`

Three additions, all backward-compatible (the 8 W1 tests pass byte-for-byte):

### 1a. Host-target detection — `detect_host_target() -> HostTarget`

The dispatch key is `(os, arch, runtime)`. `HostTarget.arch` is **normalized to
GOARCH spelling** (`amd64` | `arm64`) so it compares equal against both an env.toml
`arch = "x86_64"` and an OCI index entry's `platform.architecture = "amd64"`.

Detection order per field: **explicit arg → env var → autodetect**, so tests (and
operators) can simulate any host on this arm64 box:

| field   | arg        | env var           | autodetect                                  |
|---------|------------|-------------------|---------------------------------------------|
| os      | `os_name=` | `MW_HOST_OS`      | `"macos"` (we only target macOS guests)     |
| arch    | `arch=`    | `MW_HOST_ARCH`    | `platform.machine()` → normalized           |
| runtime | `runtime=` | `MW_HOST_RUNTIME` | arm64 ⇒ `vz-clonefile`, else `kvm-qcow2`    |

The runtime default encodes the **hybrid routing**: Apple-Silicon hosts run the VZ
substrate; x86 hosts run KVM. On this worktree machine (Darwin/arm64) the autodetected
target is `(macos, arm64, vz-clonefile)`.

### 1b. Multi-platform env.toml shape

Two shapes are accepted; `resolve_env_package(path, *, validate_paths=True,
host_target=None)` dispatches on presence of `[[platform]]`:

- **Single-platform (legacy W1):** top-level `[target]` + `[[layers]]`. Unchanged. If a
  `host_target` is passed, the resolver now **enforces** it matches the package's
  `[target]` (mismatch → fail loud), so a laptop can't silently boot an x86 package on
  an arm64 host.
- **Multi-platform (E1):** top-level `[env]` + repeated `[[platform]]` blocks, each with
  its own `[platform.target]` + `[[platform.layers]]`. The resolver selects the one
  block whose `[target]` matches the host, then resolves its body exactly like the
  single-platform path. A `[[platform]]` whose `target.runtime == "oci-index"` resolves
  via OCI instead of local paths (§1c). Both inline-chains and the oci-index indirection
  are supported and tested.

`_target_matches_host` requires `os` (optional, defaults to host os), `arch`, and
`runtime` to all agree. **No match → `EnvPackageError("…no matching platform for
target=(os=…, arch=…, runtime=…); package offers: …")`** listing every declared
platform — loud, with the available set.

### 1c. OCI-index resolution — credential-free, via `oras --oci-layout`

`runtime = "oci-index"` + `index = "<oci-layout-dir>:<tag>"` (abs / `~` / rel-to-env.toml,
so a package ships its own relocatable oci-layout sibling). Resolution:

1. `oras manifest fetch --oci-layout <dir>:<tag>` → the index doc; assert it's an
   `oci.image.index`.
2. `_select_index_entry`: pick the entry where `platform.architecture == host.arch`
   **and** annotation `cua.target.runtime == host.runtime` (and `cua.target.os` /
   `platform.os` ∈ {host.os, darwin}). No match → fail loud, listing the index's
   `(os, arch, runtime)` offers.
3. `oras manifest fetch --oci-layout <dir>@<chain-digest>` → the selected chain
   manifest. Its blobs' `cua.layer.*` annotations (§5) become the `ResolvedEnv.layers`
   (role + format + digest + parent).

The resolver proves **selection** (same name → the right chain identity: digests +
roles + formats). Pulling blobs to a content-addressed cache + rebasing is the runtime
composition step (W3 / E6); the `oras pull --oci-layout` round-trip is shown in §4 to
prove the artifacts are real and retrievable credential-free.

`ResolvedEnv` gained `host_target` and `source ∈ {"local", "oci-index"}`;
`base_volume_dir`/`apps_layer_dir` are `None` on the OCI path (no local dirs yet).

---

## 2. The env package — `explorations/substrate/envs/cua-macos-textedit/`

```
cua-macos-textedit/
├── env.toml            # one [[platform]] of runtime "oci-index" → ./oci-layout:textedit-v1
├── build-index.sh      # re-runnable: builds the oci-layout from tiny placeholder blobs
└── oci-layout/         # the committed OCI image-index (48 KiB; credential-free)
    ├── oci-layout
    ├── index.json      # tag store: textedit-v1, chain-kvm-x86_64, chain-vz-arm64
    └── blobs/sha256/…  # 4 placeholder layer blobs + 3 manifests + empty config
```

`env.toml` (the logical name, pointing at the index):

```toml
[env]
name = "cua-macos-textedit"
macos_version = "14"

[[platform]]
  [platform.target]
  os      = "macos"
  runtime = "oci-index"
  index   = "./oci-layout:textedit-v1"
```

The index fans out to two substrate chains; the index itself dispatches on
arch + runtime, so the env.toml stays a single declaration. Rebuild with
`./build-index.sh` (tiny placeholder blobs — index plumbing is independent of payload
size; the real layers come from E2/E6).

---

## 3. The exact oras oci-layout commands (same name → two chains)

All credential-free (`--oci-layout`, no registry, no `oras login`). Full author script:
`envs/cua-macos-textedit/build-index.sh`. The load-bearing steps:

### 3a. Push each per-platform chain (one artifact manifest, 2 placeholder blobs)

```bash
oras push --oci-layout ./oci-layout:chain-kvm-x86_64 \
  --artifact-type application/vnd.cua.chain.v1+json \
  --annotation-file anno-kvm.json \
  base-kvm.bin:application/x-qemu-qcow2+zstd \
  apps-kvm.bin:application/x-qemu-qcow2+zstd

oras push --oci-layout ./oci-layout:chain-vz-arm64 \
  --artifact-type application/vnd.cua.chain.v1+json \
  --annotation-file anno-vz.json \
  base-vz.bin:application/x-apple-asif+zstd \
  apps-vz.bin:application/x-apple-asif+zstd
```

`--annotation-file` maps `layer.json` fields → per-blob `cua.layer.*` and per-manifest
`cua.chain.*` (§5). `oras push <file>:<mediaType>` alone only sets
`org.opencontainers.image.title`, so the annotation file is required to carry our
vocabulary.

### 3b. Author the index with `platform` + `cua.target.*` per entry

> **Why hand-authored, not `oras manifest index create`:** `oras manifest index create`
> will not synthesize a `platform` for **artifact** manifests (there's no image-config
> to infer os/arch from), and it drops descriptor annotations. So it produced an index
> whose entries had **no `platform` and no `cua.target.*`** — nothing to dispatch on.
> We author the index descriptor explicitly and `oras manifest push` it. This is the one
> place E1 deviates from the `oras manifest index` convenience command.

```bash
# index.json: two manifests, each with platform{os,architecture} + cua.target.runtime
oras manifest push --oci-layout ./oci-layout:textedit-v1 \
  --media-type application/vnd.oci.image.index.v1+json index.json
```

### 3c. The index — two platform entries under one tag

```console
$ oras manifest fetch --oci-layout ./oci-layout:textedit-v1 | jq '...'
{
  "mediaType": "application/vnd.oci.image.index.v1+json",
  "manifests": [
    { "platform": {"os":"darwin","architecture":"amd64"},
      "runtime": "kvm-qcow2",   "digest": "sha256:2b77aeb0d7cdbf409" },
    { "platform": {"os":"darwin","architecture":"arm64"},
      "runtime": "vz-clonefile","digest": "sha256:833fc0579e32563d1" }
  ]
}
```

### 3d. Resolution proof (the resolver, three targets)

`resolve_env_package(env, host_target=…)` over the committed package:

```console
=== (x86_64, kvm-qcow2) ===
  -> runtime=kvm-qcow2  source=oci-index
     os-base  fmt=qcow2  sha256:c8da398b1a0e…
     apps     fmt=qcow2  sha256:2aaa842acbf5…
=== (arm64, vz-clonefile) ===
  -> runtime=vz-clonefile  source=oci-index
     os-base  fmt=asif   sha256:040ea0bd73cc…
     apps     fmt=asif   sha256:8ccc757b94b7…
=== (arm64, kvm-qcow2) — UNMATCHED ===
  OK fail-loud: no matching platform for target=(os=macos, arch=arm64, runtime=kvm-qcow2);
  index offers: (os=macos, arch=amd64, runtime=kvm-qcow2), (os=macos, arch=arm64, runtime=vz-clonefile)
```

The two resolved chains share **zero** blobs (qcow2 vs asif, distinct digests) — proof
it's genuinely two byte-chains under one name, not one chain relabeled.

---

## 4. Pull round-trip (artifacts are real, credential-free)

Resolve the x86/kvm chain digest from the index, then pull it by digest:

```console
$ KVM_DGST=$(oras manifest fetch --oci-layout ./oci-layout:textedit-v1 \
    | jq -r '.manifests[] | select(.platform.architecture=="amd64"
             and .annotations["cua.target.runtime"]=="kvm-qcow2") | .digest')
$ oras pull --oci-layout "./oci-layout@${KVM_DGST}" -o /tmp/pull-kvm
Downloaded  c8da398b1a0e base-kvm.bin
Downloaded  2aaa842acbf5 apps-kvm.bin
Pulled [oci-layout] ./oci-layout@sha256:2b77aeb0d7cdbf40…
$ cat /tmp/pull-kvm/base-kvm.bin
cua os-base macos-14 qcow2 (KVM placeholder)
```

`push → manifest index → fetch (resolve) → pull` all complete with **no registry and no
credentials**. The eventual GHCR step swaps the `<dir>:<tag>` ref for
`ghcr.io/<org>/cua/macos:textedit-v1` and drops `--oci-layout`; everything else is
identical.

---

## 5. Schema — `[target]` + the OCI annotation map (RFC §7.5)

### env.toml (multi-platform)

```toml
[env]
name = "<logical-name>"
macos_version = "14"

# Either inline per-platform chains …
[[platform]]
  [platform.target]
  os = "macos"; arch = "x86_64"; runtime = "kvm-qcow2"   # dispatch key
  [[platform.layers]]
  ref = "<local-path>"; role = "os-base"; format = "qcow2"
  [[platform.layers]]
  ref = "<local-path>"; role = "apps";    format = "qcow2"; digest = "sha256:…"

# … or a single oci-index platform that fans out to both chains:
[[platform]]
  [platform.target]
  os = "macos"; runtime = "oci-index"; index = "<oci-layout-dir>:<tag>"
```

### OCI mapping — `layer.json` fields → annotations

| layer.json / chain field | OCI carrier                              | example                       |
|--------------------------|------------------------------------------|-------------------------------|
| layer `role`             | blob annotation `cua.layer.role`         | `os-base` / `apps`            |
| layer `os`               | blob annotation `cua.layer.os`           | `macos`                       |
| layer `arch`             | blob annotation `cua.layer.arch`         | `amd64` / `arm64`             |
| layer `parent` (digest)  | blob annotation `cua.layer.parent`       | `sha256:c8da398b…` (os-base)  |
| layer `format`           | blob annotation `cua.layer.format`       | `qcow2` / `asif`              |
| layer payload mediaType  | blob `mediaType`                         | `application/x-qemu-qcow2+zstd` / `application/x-apple-asif+zstd` |
| chain artifactType       | manifest `artifactType`                  | `application/vnd.cua.chain.v1+json` |
| chain target os/arch/rt  | manifest annotations `cua.chain.*`       | `cua.chain.runtime=kvm-qcow2` |
| **index dispatch key**   | entry `platform.{os,architecture}` + `cua.target.runtime` | the only field the resolver matches on |

`cua.layer.parent` is filled two-pass in `build-index.sh` (the os-base blob digest is
known only after its first push) — proven by
`test_oci_index_apps_parent_annotation_points_at_os_base` (apps.parent == os-base.digest).

**Why `cua.target.runtime` lives in an annotation, not `platform`:** OCI's `platform`
struct only has `os` / `architecture` / `variant` — there is **no slot for our runtime
dispatch key**. Two macOS-arm64 envs could differ only by substrate (kvm vs vz). So
`platform` carries `(os, arch)` (Docker-compatible, what registries index on) and a
`cua.target.runtime` annotation carries the third axis. The resolver matches on both.

---

## 6. Index vs sibling-tag decision (RFC open Q3) → **INDEX**

**Recommendation: INDEX (one tag, `cua/macos:textedit-v1`), implemented above.**

Rationale:
- **It round-trips cleanly in oci-layout.** A standard `oci.image.index` carries our two
  **non-image artifact** chain manifests with full `platform` + annotation fidelity
  through `oras manifest push` / `fetch` / `pull` (§3, §4). The OCI image-index spec does
  not require children to be images — artifact manifests are valid index members. No
  blocker found.
- **One name is the whole point of interop link #1.** `mw bench run --env
  cua/macos:textedit-v1` means "the right thing here" on every host — exactly Docker's
  `linux/amd64` vs `linux/arm64`. Sibling tags
  (`:textedit-x86_64` / `:textedit-arm64`) push the host→tag dispatch back up into the
  caller, defeating the goal.
- **The only deviation is cosmetic and contained:** we hand-author the index descriptor
  instead of using `oras manifest index create` (§3b), because that command won't
  synthesize `platform` for artifact manifests. The hand-authored index is ~20 lines of
  JSON in `build-index.sh` and is itself a standard `oci.image.index`.

No real blocker surfaced, so the **sibling-tag fallback is NOT implemented**. It remains
trivially available if a registry ever rejects an artifact-manifest index: drop the
index, push `:textedit-x86_64` / `:textedit-arm64`, and resolve by constructing the tag
from the host target (`f"{name}-{host.arch}"`). The resolver's host-target detection is
the same either way; only step (1) of §1c changes.

---

## 7. Registry caveat for the eventual GHCR step

- **Artifact-manifest index children render as `unknown/unknown` in registry UIs.** GHCR
  (and Docker Hub) infer the per-arch label in their UI from each child's **image
  config**; our children are artifact manifests with the OCI **empty config**
  (`application/vnd.oci.empty.v1+json`), so the UI shows `unknown/unknown` for the two
  entries. **Cosmetic only** — the `platform` field and our `cua.target.*` annotations
  are intact in the manifest JSON, and `oras` (and our resolver) dispatch on them
  correctly. It does not affect resolution.
- **GHCR accepts OCI artifact manifests + indexes** (it's OCI-1.1-compliant and ORAS's
  primary tested target). The push is `oras push ghcr.io/<org>/cua/macos:chain-…` +
  `oras manifest push ghcr.io/<org>/cua/macos:textedit-v1 index.json` after
  `oras login ghcr.io` with a PAT (`write:packages`). Same commands, real ref, no
  `--oci-layout`.
- **Referrers/subject:** we use a plain index (no `subject`/referrers), so no dependence
  on GHCR's referrers-API maturity.
- **Layer-size caps (RFC §7.5) are orthogonal here:** placeholder blobs are bytes. The
  real ~15 GB base must be zstd-partitioned to stay under GHCR's ~10 GB layer cap — a
  packaging concern for E6, independent of the index mechanism proven here.

---

## 8. Tests — `infra/cli/tests/test_env_pkg.py`

`cd infra/cli && uv run --group dev pytest` → **86 passed** (was 70 at W1). The
resolver file alone: **21 passed** (8 W1 + 13 new):

- host-target detection: explicit-arg + arch normalization (`x86_64`/`aarch64`),
  `MW_HOST_*` env-var override, substrate-by-arch runtime default, non-substrate-runtime
  rejection.
- single-platform host enforcement: matching host resolves; mismatched host fails loud.
- inline multi-platform dispatch: x86 host → KVM chain; arm host → VZ chain; unmatched
  host fails loud.
- OCI index round-trip (gated on `oras` installed + layout built): x86→KVM, arm→VZ,
  same-name→two-disjoint-chains, unmatched fails loud, apps.parent == os-base.digest.

The OCI tests run against the committed `envs/cua-macos-textedit/oci-layout`; they skip
(not fail) if `oras` is absent or the layout isn't built.

---

## 9. Files touched

| File | Change |
|---|---|
| `infra/cli/benchmark/env/pkg.py` | Generalized: `HostTarget` + `detect_host_target`, multi-platform `[[platform]]` shape, `oci-index` resolution via `oras --oci-layout`, `_select_index_entry` (fail-loud), single-platform host enforcement. W1 single-platform path unchanged. |
| `infra/cli/tests/test_env_pkg.py` | +13 tests (host-target, multi-platform dispatch, OCI round-trip). 8 W1 tests unchanged. |
| `explorations/substrate/envs/cua-macos-textedit/env.toml` | New. The logical multi-platform env. |
| `explorations/substrate/envs/cua-macos-textedit/build-index.sh` | New. Re-runnable credential-free index builder. |
| `explorations/substrate/envs/cua-macos-textedit/oci-layout/` | New. The committed 48 KiB OCI image-index (tiny placeholder blobs). |
| `explorations/substrate/E1-index.md` | This doc. |
```
