# Packaging smoke harness

Containerised install-path smoke tests for LabDog's release artifacts.
They turn the smoke procedure that used to be run by hand before a release
into something CI runs automatically (and that you can run locally).

## What it does

For each artifact built into `packaging/dist/`, a clean target-OS container
installs it the way an operator would, then runs shared assertions:

| Artifact | Image | Install |
|----------|-------|---------|
| `.deb` | `ubuntu:24.04` | `apt-get install ./labdog_*.deb` |
| `.rpm` | `rockylinux:9` | `dnf install ./labdog-*.rpm` |
| tarball | `ubuntu:24.04` | extract + `./install.sh` |

The assertions ([`smoke.sh`](smoke.sh)) check that the install:

- landed the app code + a working venv, the bundled action pack (4 actions),
  the systemd unit, tmpfiles.d config, and `/etc/labdog/labdog.toml`;
- created the `labdog` service account and data directories;
- produced a venv that imports the `app` package and every key runtime
  dependency (cross-distro — the venv is built on Ubuntu and must also run
  on Rocky);
- reports the **correct version** via `importlib.metadata` (guards the
  regression where packaged installs reported `0.0.0`).

It deliberately does **not** start the service — that needs PostgreSQL,
Redis, and secrets. This is an install-path smoke, not an integration test.

## Run it locally

```bash
# build the artifacts first (needs python3.12, node, nfpm)
./packaging/build.sh --version="$(cat VERSION)"

# then smoke them (needs Docker)
./packaging/tests/run-smoke.sh
```

Restrict targets or override images with env vars:

```bash
TARGETS="deb tarball" ./packaging/tests/run-smoke.sh
RPM_IMAGE=almalinux:9 ./packaging/tests/run-smoke.sh 0.6.1
```

## In CI

The `packaging-smoke` job in [`.github/workflows/ci.yml`](../../.github/workflows/ci.yml)
builds the artifacts and runs this harness on pushes to `main` (release
commits) and on pull requests that touch packaging, so a broken install
path fails a check instead of a customer's install.
