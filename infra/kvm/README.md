# infra/kvm

Assets for running the macOS-on-KVM eval fleet on a Linux box.

- **Setup runbook:** [`docs/runbooks/kvm-server-setup.md`](../../docs/runbooks/kvm-server-setup.md) — stand up a fresh Linux box end-to-end.
- **`scripts/export-base-bundle.sh`** — pack the gold base volume + SSH key into a distributable `*.tar.zst` (run on the source box).
- **`scripts/import-base-bundle.sh`** — restore that bundle into the default paths on a new eval box.
- **`dist/`** — the LFS-tracked base bundle lives here (see `dist/README.md`).

The harness code that uses all this is in [`infra/cli/benchmark/env/kvm/`](../cli/benchmark/env/kvm/)
(`fleet.py`, `host.py`, `rfb.py`, `ssh.py`, `config.py`, `prep_base.sh`).
