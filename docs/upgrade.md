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

The alembic migration chain is forward-only and covers every schema
change since v0.1.0, so upgrading from any earlier release to the
latest follows the single procedure below. The current released line
is whatever the [latest GitHub Release](https://github.com/open-labdog/labdog/releases/latest)
says — this page deliberately does not name a version, because a
hardcoded one goes stale the moment it ships.

Each release notes in `CHANGELOG.md` whether it carries breaking
schema changes, deprecated config fields, or non-reversible
migrations. Read that section before upgrading.

### Upgrading to the next release

**The bundled action pack is gone.** The image and the `.deb` / `.rpm` /
`.tar.gz` packages no longer carry a copy of `labdog-playbooks`. Actions
come only from the packs under **Operations → Actions → Packs**, so:

- If you still have the seeded `labdog-playbooks` pack, nothing changes:
  it has been supplying the same actions all along, and the keys it
  shared with the bundled copy stop being contested. The **pinned** and
  **frozen** badges on them go away.
- A pin that chose **bundled** is deleted by the upgrade. If another pack
  declares that key, it now wins on its own; if two or more do, the key
  needs a winner again and its actions can't run until you pick one.
- If you deleted `labdog-playbooks`, or LabDog cannot reach GitHub, its
  actions disappear until a pack provides them. Re-add it (repository
  `https://github.com/open-labdog/labdog-playbooks`, branch `main`, then a
  pack on that repository), point the repository at a mirror, or add a
  local-directory pack.

### Upgrading to 0.11.0

Nothing is required. Every new thing that can change a host or send mail
is off until you turn it on: alert investigations stay read-only, no
alert is on the full-auto list, no mail server is configured and nobody
is subscribed, and schedules keep reading their cron expressions in UTC.
Four things are worth checking.

**1. The AI assistant refuses more at read-only.** A command now counts
as read-only only in a form listed in `backend/app/ai/command_policy.yaml`;
a subcommand or option the list does not name counts as a change. Before,
it was the other way round, so most of `qm`, `pct` and `pvesh`, container
runtimes, systemd tools, `ip`, package managers and storage tools ran in a
read-only session whatever the subcommand. `wget`, `man`, `xxd`, `initctl`
and `at` now need approval too. A read-only session, which is what
scheduled checks and alert investigations run as by default, will be
refused commands it used to run. The refusal names the read-only forms of
the command, so the assistant usually finds one; if a scheduled check
keeps failing on one, look at its refused calls in the session. See
[Autonomy levels](ui/assistant.md#autonomy-levels).

**2. Retention keys in `labdog.toml` now log a warning at startup.**
`audit_retention_days`, `run_retention_days` and `drift_retention_days`
under `[logging]`, or as `LABDOG_LOGGING__…` variables, never did
anything: retention is read only from **Settings › System**. Check that
the values there are what you meant to keep, then delete the keys. The
packaged `labdog.toml` no longer has them, so a `.deb` or `.rpm` upgrade
of a modified config may ask which version to keep.

**3. A pack playbook that imports a file next to it is no longer loaded.**
Only the playbook's own text and the pack's roles reach a run, so a
playbook using `import_tasks`, `include_tasks`, `vars_files` or
`import_playbook` with a relative path never got past its first task. It
is now refused when the pack loads: the action is missing from the
library, and the log says `pack '<name>': failed to load manifest …`,
naming the files. Move them into a role. No `labdog-playbooks` action is affected.

**4. If you rotated the encryption key on 0.10.0 or earlier**, re-enter
each Git repository's webhook secret. The rotation script skipped that
column, so signed push webhooks have answered 500 since, and GitOps has
not imported a push. See
[encryption-key-rotation.md](encryption-key-rotation.md).

Smaller things to know:

- **The `security.allowed_origins` workaround for the web terminal** from
  the 0.10.0 notes can go: the terminal accepts its own origin again. Keep
  the setting only for a frontend served from another host.
- **Email** needs `notifications.public_url` set before it sends anything
  with a link; LabDog will not build links from a request's `Host`
  header. See [Notifications](ui/notifications.md).
- **Full auto for named alerts** needs Grafana's contact point to send
  resolved notifications, or every fix is judged as not working, and it is
  refused on the machine LabDog itself runs on. See
  [Alerts](ui/alerts.md#full-auto-for-named-alerts).
- **Setting `scheduling.timezone`** keeps every schedule's clock time and
  moves it to the new zone: `0 3 * * *` then runs at 03:00 there. Check
  existing schedules after changing it.
- **The navigation was rebuilt.** Old URLs (`/dashboard`, `/schedules`,
  `/action-packs`, `/hosts/discover`, `/groups/{id}/rules` and the other
  module pages) redirect, so bookmarks keep working.
- **Docker:** no compose change is needed.

Eight migrations (`0039`–`0046`) apply with the normal step below. All
are forward-safe. Three repair data and leave it repaired on downgrade:
`0039` advances id sequences, `0040` empties host-run output that was
two quote characters, `0041` fills in each host's last sync time.
Downgrading past `0045` and `0046` drops the mail settings, outbox and
subscriptions, and the rollback history.

### Upgrading to 0.10.0

This release is mostly the fixes from a full security and correctness
audit. Three things can bite an existing install; check them **before**
restarting, not after.

**1. LabDog now refuses to start on a weak `security.secret_key`.** It
must be at least 32 characters. `openssl rand -base64 32` — what the
install docs already told you to run — gives 44. If your key is shorter,
or is still the `CHANGE_ME` placeholder from the packaged
`labdog.toml`, the service will not come up until you replace it. Two
consequences of replacing it: every session is invalidated (see 2), and
if the old key was a placeholder, treat every account as having been
exposed — that key is published in this repository.

`security.allowed_origins = ["*"]` is refused for the same reason; list
your real origins. Plain HTTP on a trusted LAN still works and now logs
a warning about `cookie_secure` rather than failing.

**2. Everyone is logged out once.** Sessions now carry a generation
number so that logging out or changing a password actually revokes the
cookie. Existing cookies have no generation and are rejected. From here
on, logging out or changing your password signs you out everywhere,
including the browser you did it in.

**3. Old history is deleted on the first scheduler tick.** Two things
combine here:

- Finished action runs and sync jobs are pruned for the first time ever,
  using the new `logging.run_retention_days` (default **90**; `0` keeps
  everything).
- The daily maintenance jobs — audit-log, SSH-transcript and AI-snapshot
  pruning — were registered in a way that meant they **never ran** on a
  deployment that restarts more than once a day. They now do, so the
  first audit-log prune may delete a lot at once, per
  `logging.audit_retention_days` (default 90).

Set both values first if you want to keep more. Both are in
**Settings** only: the same keys in `labdog.toml` have no effect.

Smaller things to know:

- **Settings that never took effect now do**, on the next restart:
  `ansible.playbook_timeout`, `ssh.connect_timeout`,
  `ssh.idle_timeout_seconds`, `discovery.max_concurrent`,
  `logging.audit_retention_days`, `actions.preflight_enabled` and
  `ai.wall_clock_seconds`. A value someone set long ago and forgot will
  now be honoured — lowering `ansible.playbook_timeout` below a slow
  playbook's real runtime will now actually kill it.
  `SELECT key, value FROM app_settings` shows what is stored.
- **`/docs`, `/redoc` and `/openapi.json` are no longer served** unless
  `server.expose_docs = true`.
- **Ansible runs and git syncs now verify SSH host keys** against the key
  LabDog recorded on first contact. A host or git server that is
  legitimately re-keyed will start failing with a mismatch; clear it with
  `POST /api/hosts/{id}/trust-host-key` or
  `POST /api/git-repos/{id}/trust-host-key`.
- **Action packs that ship controller-side code** (`action_plugins/`,
  `library/`, `connection: local`, …) are now refused unless the pack is
  marked trusted. Packs that exist at upgrade time are marked trusted by
  the migration; packs added afterwards are not.
- Webhook secrets are encrypted in place; nothing to re-enter. The API
  reports `has_webhook_secret` instead of the value.
- Duplicate `host_groups.priority` values are renumbered by the
  migration (the older group keeps its number); every change is logged.
- `firewall_rules.is_system` and `hosts_entries.is_system` are dropped.
  Nothing ever wrote them; if you set one by hand the migration logs the
  row count before dropping it.
- **Docker:** Celery beat is now a third supervised subprocess, and
  `/health/ready` fails if it dies. No compose change is needed.
- **The web terminal fails with "Connection failed: Closed (1006)"**
  on 0.10.0 unless the UI's origin is listed in
  `security.allowed_origins`, which a deployment serving the UI and API
  from one host never needed before. Set it, for example
  `LABDOG_SECURITY__ALLOWED_ORIGINS='["https://labdog.example.com"]'`.
  From the release after 0.10.0 the terminal accepts its own origin
  without it (BUG-88), and the list is only needed for a frontend served
  from elsewhere.

Fourteen migrations (`0025`–`0038`) apply with the normal step below.
All are forward-safe; `0037` and `0038` are documented as lossy on
downgrade.

### Upgrading to 0.9.0

Nothing is required — the AI assistant is off by default and stays off
until you set `ai.enabled` and configure a provider. The release adds
schema for it, applied by the normal migration step below.

**If you intend to enable it on Docker**, add one volume before you do:

```yaml
volumes:
  - labdog_claude:/var/lib/labdog/claude-cli
```

This holds Claude Code's own session files, and only matters for the
Claude CLI / Agent SDK backends. Sessions that pause for approval resume
by their CLI session id, so without the volume an approval granted after
a container restart has nothing left to resume — and approvals can wait
up to `ai.approval_expiry_hours` (default 24). See
[production-deploy.md](production-deploy.md).

**If LabDog connects to your hosts as a non-root user**, check that user
has passwordless sudo, or the verify step cannot read the system journal.
It will report the reading as `UNAVAILABLE` rather than pretending the
host was quiet, so nothing breaks silently — but you lose a signal.

**Releases are cut from `main`.** Merging a release PR (`dev` →
`main`) triggers `release-artifacts`, which auto-tags `vX.Y.Z` from
the repo-root [`VERSION`](https://github.com/open-labdog/labdog/blob/main/VERSION) file and publishes the
`.tar.gz` / `.deb` / `.rpm` / `SHA256SUMS` set as a GitHub Release.
There is no separate `git tag` step — the release artifacts on the
GitHub Releases page are what you install from. See
[CONTRIBUTING.md → Release process](pathname:///../CONTRIBUTING.md#release-process).

No action pack ships with a release. Actions come from packs synced
at runtime, the seeded `labdog-playbooks` pack among them, so new
playbooks arrive with a pack sync rather than an upgrade.

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
docker pull ghcr.io/open-labdog/labdog:0.6.1
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
    image: ghcr.io/open-labdog/labdog:0.6.1
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
# (Also visible in the UI at Settings › System, in the About panel.)

# 2. Health endpoint returns 200.
curl -fsS http://127.0.0.1:8000/health
# → {"status":"ok"}

# 3. End-to-end: trigger one sync against a known-good test host.
#    Use the UI (Operations → Plans, pick the host, apply) or the API.
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
