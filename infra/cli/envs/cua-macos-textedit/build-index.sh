#!/usr/bin/env bash
# Build the cua/macos:textedit-v1 OCI image-index in a LOCAL oci-layout dir.
# Credential-free: `oras ... --oci-layout <dir>` — no registry, no login. This proves
# the same-name → two-per-platform-chains mechanism end-to-end (E1 of the substrate
# program). GHCR is the identical flow with `ghcr.io/<org>/cua/macos:textedit-v1`
# instead of the layout dir (see E1-index.md "registry caveat").
#
# Index layout:
#   cua/macos:textedit-v1  (oci.image.index)
#     ├─ platform{os=darwin, arch=amd64} + cua.target.runtime=kvm-qcow2  -> KVM chain
#     └─ platform{os=darwin, arch=arm64} + cua.target.runtime=vz-clonefile -> VZ chain
# Each chain is a cua.chain.v1 artifact manifest holding 2 placeholder blobs
# (os-base + apps) with cua.layer.* annotations (RFC 0002 §7.5).
#
# Re-runnable: wipes and rebuilds ./oci-layout. Blobs are tiny placeholders — the
# index plumbing is independent of payload size; real layers come later (E2/E6).
set -euo pipefail
cd "$(dirname "$0")"

LAYOUT="./oci-layout"
rm -rf "$LAYOUT"
mkdir -p _build
cd _build

# --- tiny placeholder blobs (a few bytes each; real layers replace these later) ----
printf 'cua os-base macos-14 qcow2 (KVM placeholder)\n'        > base-kvm.bin
printf 'cua +apps  textedit  qcow2 (KVM placeholder)\n'        > apps-kvm.bin
printf 'cua os-base macos-14 asif  (VZ placeholder)\n'         > base-vz.bin
printf 'cua +apps  textedit  asif  (VZ placeholder)\n'         > apps-vz.bin

# --- annotation files map layer.json fields -> cua.layer.* (RFC 0002 §7.5) ----------
# parent of +apps is filled after we know the os-base blob digest (two-pass).
cat > anno-kvm.json <<'JSON'
{
  "$manifest": { "cua.chain.os": "macos", "cua.chain.arch": "amd64", "cua.chain.runtime": "kvm-qcow2" },
  "base-kvm.bin": { "cua.layer.role": "os-base", "cua.layer.os": "macos", "cua.layer.arch": "amd64", "cua.layer.format": "qcow2" },
  "apps-kvm.bin": { "cua.layer.role": "apps",    "cua.layer.os": "macos", "cua.layer.arch": "amd64", "cua.layer.format": "qcow2" }
}
JSON
cat > anno-vz.json <<'JSON'
{
  "$manifest": { "cua.chain.os": "macos", "cua.chain.arch": "arm64", "cua.chain.runtime": "vz-clonefile" },
  "base-vz.bin": { "cua.layer.role": "os-base", "cua.layer.os": "macos", "cua.layer.arch": "arm64", "cua.layer.format": "asif" },
  "apps-vz.bin": { "cua.layer.role": "apps",    "cua.layer.os": "macos", "cua.layer.arch": "arm64", "cua.layer.format": "asif" }
}
JSON

L="../$LAYOUT"

# --- push the two per-platform chain manifests --------------------------------------
oras push --oci-layout "$L:chain-kvm-x86_64" \
  --artifact-type application/vnd.cua.chain.v1+json \
  --annotation-file anno-kvm.json \
  base-kvm.bin:application/x-qemu-qcow2+zstd \
  apps-kvm.bin:application/x-qemu-qcow2+zstd >/dev/null

oras push --oci-layout "$L:chain-vz-arm64" \
  --artifact-type application/vnd.cua.chain.v1+json \
  --annotation-file anno-vz.json \
  base-vz.bin:application/x-apple-asif+zstd \
  apps-vz.bin:application/x-apple-asif+zstd >/dev/null

# --- fill cua.layer.parent on each +apps blob = its os-base blob digest --------------
# (re-push with parent now that the os-base digest is known)
parent_of() { oras manifest fetch --oci-layout "$1" 2>/dev/null | jq -r '.layers[0].digest'; }
KP=$(parent_of "$L:chain-kvm-x86_64"); VP=$(parent_of "$L:chain-vz-arm64")
jq --arg p "$KP" '.["apps-kvm.bin"]["cua.layer.parent"]=$p' anno-kvm.json > anno-kvm.json.tmp && mv anno-kvm.json.tmp anno-kvm.json
jq --arg p "$VP" '.["apps-vz.bin"]["cua.layer.parent"]=$p' anno-vz.json > anno-vz.json.tmp && mv anno-vz.json.tmp anno-vz.json
oras push --oci-layout "$L:chain-kvm-x86_64" \
  --artifact-type application/vnd.cua.chain.v1+json --annotation-file anno-kvm.json \
  base-kvm.bin:application/x-qemu-qcow2+zstd apps-kvm.bin:application/x-qemu-qcow2+zstd >/dev/null
oras push --oci-layout "$L:chain-vz-arm64" \
  --artifact-type application/vnd.cua.chain.v1+json --annotation-file anno-vz.json \
  base-vz.bin:application/x-apple-asif+zstd apps-vz.bin:application/x-apple-asif+zstd >/dev/null

# --- hand-author the index with platform + cua.target.* annotations -----------------
# `oras manifest index create` won't synthesize a `platform` for artifact manifests
# (no image-config to infer from) and drops our annotations — so we author the index
# descriptor explicitly. This is the only place E1 deviates from `oras manifest index`.
desc() { oras manifest fetch --oci-layout "$1" --descriptor 2>/dev/null; }
KVM_D=$(desc "$L:chain-kvm-x86_64"); VZ_D=$(desc "$L:chain-vz-arm64")
KVM_DGST=$(echo "$KVM_D" | jq -r .digest); KVM_SZ=$(echo "$KVM_D" | jq -r .size)
VZ_DGST=$(echo "$VZ_D" | jq -r .digest);   VZ_SZ=$(echo "$VZ_D" | jq -r .size)

cat > index.json <<JSON
{
  "schemaVersion": 2,
  "mediaType": "application/vnd.oci.image.index.v1+json",
  "manifests": [
    {
      "mediaType": "application/vnd.oci.image.manifest.v1+json",
      "artifactType": "application/vnd.cua.chain.v1+json",
      "digest": "$KVM_DGST", "size": $KVM_SZ,
      "platform": { "os": "darwin", "architecture": "amd64" },
      "annotations": { "cua.target.os": "macos", "cua.target.runtime": "kvm-qcow2" }
    },
    {
      "mediaType": "application/vnd.oci.image.manifest.v1+json",
      "artifactType": "application/vnd.cua.chain.v1+json",
      "digest": "$VZ_DGST", "size": $VZ_SZ,
      "platform": { "os": "darwin", "architecture": "arm64" },
      "annotations": { "cua.target.os": "macos", "cua.target.runtime": "vz-clonefile" }
    }
  ]
}
JSON
oras manifest push --oci-layout "$L:textedit-v1" \
  --media-type application/vnd.oci.image.index.v1+json index.json >/dev/null

cd ..
rm -rf _build
echo "built $LAYOUT  (tags: textedit-v1 [index], chain-kvm-x86_64, chain-vz-arm64)"
oras manifest fetch --oci-layout "$LAYOUT:textedit-v1" | jq -c '.manifests[] | {arch: .platform.architecture, runtime: .annotations["cua.target.runtime"], digest}'
