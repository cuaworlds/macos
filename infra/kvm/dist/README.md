# infra/kvm/dist

The distributable **macOS base-volume bundle** — `kvm-base-bundle.tar.zst` —
packs the gold base volume (`volumes/base`) **and** its matching SSH keypair
(`ssh/id_kvm`) into one sparse-aware, zstd-compressed artifact.

## Distribution: AWS S3 (the supported path)

The bundle is hosted at `s3://your-kvm-base-bucket/kvm-base-bundle.tar.zst`
(private bucket; access via a regenerable sigv4 presigned URL — see
[`docs/runbooks/kvm-server-setup.md` §4](../../../docs/runbooks/kvm-server-setup.md)
for the colleague-facing download flow).

### Produce a new bundle (on the box that has the prepped base)

```bash
bash infra/kvm/scripts/export-base-bundle.sh /tmp/kvm-base-bundle.tar.zst
sha256sum /tmp/kvm-base-bundle.tar.zst            # record for the runbook
# upload (from any machine with AWS creds to the bucket):
aws s3 cp /tmp/kvm-base-bundle.tar.zst \
  s3://your-kvm-base-bucket/kvm-base-bundle.tar.zst
# then regenerate the presigned URL and update the runbook's <PRESIGNED_URL>:
aws s3 presign s3://your-kvm-base-bucket/kvm-base-bundle.tar.zst --expires-in 604800
```

### Consume on a fresh box

Documented in the runbook §4 — `curl` the presigned URL, `sha256sum -c`, then
`bash infra/kvm/scripts/import-base-bundle.sh`.

## Why not git LFS?

We considered LFS (`.gitattributes` still routes `*.tar.zst` here for it). On a
private repo LFS would need a paid data pack for >1 GB, and LFS objects are hard
to purge from history if the bundle ever needs to be recalled. Object storage
gives us a regenerable URL, easy deletion, and no permanent repo coupling.

The LFS wiring stays in tree as inert scaffolding — fine to leave, easy to use
later if we want to flip back.

> Contains macOS (Apple EULA caveat) + a throwaway eval-VM SSH key and the guest
> password (`haime`). Keep the box internal; don't paste presigned URLs publicly.
