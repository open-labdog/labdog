# Upgrade and Rollback

How to move a running LabDog install from one release to the next,
and how to back out cleanly when something goes wrong.

> **Read this first:** an upgrade is a destructive operation on the
> database — alembic migrations rewrite tables, and not every
> migration has a clean downgrade path. The pre-upgrade backup is
> non-negotiable in production. Without it, the only rollback story
> is "restore from yesterday's snapshot, lose a day of audit log".

- [Compatibility](#compatibility)
- [Pre-upgrade](#pre-upgrade)
- [Upgrading](#upgrading)
  - [Docker](#docker)
  - [Debian / Ubuntu (.deb)](#debian--ubuntu-deb)
  - [RHEL / Fedora / Rocky (.rpm)](#rhel--fedora--rocky-rpm)
- [Verifying the upgrade](#verifying-the-upgrade)
- [Rolling back](#rolling-back)
  - [Pin the previous Docker tag](#pin-the-previous-docker-tag)
  - [Reinstall the previous package](#reinstall-the-previous-package)
  - [When alembic downgrade is not enough](#when-alembic-downgrade-is-not-enough)
- [References](#references)

---

## Compatibility

v0.3.0 is the current released line. The alembic migration chain is
forward-only and covers every schema change since v0.1.0, so
upgrading from any earlier release to the latest follows the single
procedure below.

Each release notes in `CHANGELOG.md` whether it carries breaking
schema changes, deprecated config fields, or non-reversible
migrations. Read that section before upgrading.

**Releases are cut from `main`.** Merging a release PR (`dev` →
`main`) triggers `release-artifacts`, which auto-tags `vX.Y.Z` from
the repo-root [`VERSION`](https://github.com/open-labdog/labdog/blob/main/VERSION) file and publishes the
`.tar.gz` / `.deb` / `.rpm` / `SHA256SUMS` set as a GitHub Release.
There is no separate `git tag` step — the release artifacts on the
GitHub Releases page are what you install from. See
[CONTRIBUTING.md → Release process](pathname:///../CONTRIBUTING.md#release-process).

The bundled action pack is fetched from `labdog-playbooks` at the
SHA pinned in the repo-root [`LABDOG_PLAYBOOKS_REF`](https://github.com/open-labdog/labdog/blob/main/LABDOG_PLAYBOOKS_REF)
file at build time, so the bundled pack content shipped with a
LabDog release corresponds exactly to one `labdog-playbooks`
commit. To ship newer playbook content, bump that file's SHA in
the release PR; CI re-fetches as part of the image / artefact
build.

---

## Pre-upgrade

Take a complete backup *before touching anything*. See
[backup-restore.md](./backup-restore.md) for the full procedure; the
short form:

```bash
# 1. Database
pg_dump --dbname=labdog --format=custom \
  --file=/var/backups/labdog/labdog-pre-upgrade-$(date +%Y%m%d-%H%M%S).dump

# 2. Encryption key + config
sudo install -m 0600 /etc/labdog/labdog.toml \
  /var/backups/labdog/labdog.toml-pre-upgrade-$(date +%Y%m%d)
```

A backup set without the encryption key is unrecoverable ciphertext.
Both files together, in the same directory, on a host that's not the
one you're about to upgrade.

---

## Upgrading

Alembic migrations run automatically on container start (Docker) or
on service start (`labdog.service` → `python -m app`). You do not
run `alembic upgrade head` manually for a normal release upgrade.

### Docker

Pin the tag to a specific version — never run `:latest` in
production. The `dev` and `latest` floating tags exist for testing,
not for stable installs.

```bash
# 1. Note the version you're on, in case rollback is needed.
docker inspect labdog | grep -i 'image\|labdog/labdog'

# 2. Pull the new image and recreate the container.
docker pull ghcr.io/open-labdog/labdog:0.2.0
docker compose -f /etc/labdog/compose.yaml up -d labdog

# 3. Watch the logs as alembic runs.
docker compose -f /etc/labdog/compose.yaml logs -f labdog
```

Update the image tag in `compose.yaml` to the new version so the
next `up -d` doesn't accidentally roll forward to whatever `latest`
points at:

```yaml
services:
  labdog:
    image: ghcr.io/open-labdog/labdog:0.2.0
```

### Debian / Ubuntu (.deb)

```bash
VERSION=0.3.0
curl -LO https://github.com/open-labdog/labdog/releases/download/v${VERSION}/labdog_${VERSION}-1_amd64.deb
sudo apt install ./labdog_${VERSION}-1_amd64.deb

# systemd will restart labdog.service automatically; alembic runs
# at startup. Watch the logs:
sudo journalctl -u labdog -f
```

### RHEL / Fedora / Rocky (.rpm)

```bash
VERSION=0.3.0
curl -LO https://github.com/open-labdog/labdog/releases/download/v${VERSION}/labdog-${VERSION}-1.x86_64.rpm
sudo dnf install ./labdog-${VERSION}-1.x86_64.rpm

sudo systemctl restart labdog.service
sudo journalctl -u labdog -f
```

---

## Verifying the upgrade

Three checks, in order:

```bash
# 1. Version reported by the running process matches the tag.
curl -fsS http://127.0.0.1:8000/api/version
# → {"version":"0.3.0","commit_sha":"…","commit_sha_short":"…",
#    "build_date":"2026-05-12T09:14:37Z",
#    "license":"AGPL-3.0-or-later",
#    "repo_url":"https://github.com/open-labdog/labdog"}
# (Also visible in the UI at Settings → About.)

# 2. Health endpoint returns 200.
curl -fsS http://127.0.0.1:8000/health
# → {"status":"ok"}

# 3. End-to-end: trigger one sync against a known-good test host.
#    Use the UI (Hosts → pick a host → Plan → Sync) or the API.
#    A successful sync confirms SSH keys decrypt, packs load, and
#    the celery worker is healthy after the restart.
```

If `/api/version` reports an older `version` than the tag, the
package install succeeded but the service didn't restart. `sudo systemctl restart
labdog.service`. If `/health` returns 5xx, check
`journalctl -u labdog -f` — the most common cause is an alembic
failure on a column type change, which leaves the schema half-
migrated. Stop the service and restore from the pre-upgrade backup
before doing anything else.

---

## Rolling back

The canonical rollback is **restore the pre-upgrade backup**. It
covers every case, including migrations that are not cleanly
reversible.

If the migration was trivial and reversible (additive columns, new
tables, no data rewrites), pinning the previous binary version is
faster — but verify against the new release's `CHANGELOG.md` first.

### Pin the previous Docker tag

```bash
# Update compose.yaml to the previous version, then:
docker compose -f /etc/labdog/compose.yaml up -d labdog
```

If alembic on the older binary refuses to start because the schema
is at a newer revision than it knows about, you must restore from
backup — see [When alembic downgrade is not enough](#when-alembic-downgrade-is-not-enough).

### Reinstall the previous package

```bash
# Debian / Ubuntu
sudo apt install ./labdog_0.1.0-1_amd64.deb

# RHEL / Fedora / Rocky
sudo dnf downgrade ./labdog-0.1.0-1.x86_64.rpm

sudo systemctl restart labdog.service
```

### When alembic downgrade is not enough

Alembic downgrades are written best-effort, but data-rewriting
migrations (column type changes, splits, merges, reseeds) are not
always reversible without data loss. **Treat the pre-upgrade
backup as the canonical rollback** and stop trying to be clever:

```bash
sudo systemctl stop labdog.service
sudo -u postgres dropdb labdog
sudo -u postgres createdb labdog
pg_restore --dbname=labdog --clean --if-exists --no-owner --no-acl \
  /var/backups/labdog/labdog-pre-upgrade-YYYYMMDD-HHMMSS.dump

# Reinstall the previous binary version (see above), then:
sudo systemctl start labdog.service
```

The encryption key in `/etc/labdog/labdog.toml` did not change, so
encrypted columns decrypt correctly against the restored DB.

---

## References

- [backup-restore.md](./backup-restore.md) — pre-upgrade backup
  procedure and full restore steps.
- [Release process in CONTRIBUTING.md](pathname:///../CONTRIBUTING.md#release-process)
  — how a new version gets cut, what's in the artifact set.
- [CHANGELOG.md](pathname:///../CHANGELOG.md) — per-release
  breaking-change notes; read before every upgrade.
