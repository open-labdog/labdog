# Upgrade and Rollback

How to move a running LabDog install from one release to the next,
and how to back out cleanly when something goes wrong.

> **Read this first:** an upgrade is a destructive operation on the
> database — alembic migrations rewrite tables, and not every
> migration has a clean downgrade path. The pre-upgrade backup is
> non-negotiable in production. Without it, the only rollback story
> is "restore from yesterday's snapshot, lose a day of audit log".

- [Compatibility](#compatibility)
  - [Versioning](#versioning)
- [Pre-upgrade](#pre-upgrade)
  - [Docker: check the environment variables](#docker-check-the-environment-variables)
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
is whatever the [latest GitHub Release](https://github.com/open-unbork/unbork/releases/latest)
says — this page deliberately does not name a version, because a
hardcoded one goes stale the moment it ships.

Each release notes in `CHANGELOG.md` whether it carries breaking
schema changes, deprecated config fields, or non-reversible
migrations. Read that section before upgrading.

### Versioning

From 1.0.0, LabDog follows [Semantic Versioning](https://semver.org/).
A change that breaks existing use of anything below comes only in a new
major version (2.0.0). A minor release (1.1.0) only adds to them, and a
patch release (1.0.1) only fixes bugs.

- **Configuration**: the keys in `labdog.toml`, the `LABDOG_*`
  environment variables, and the setting keys under **Settings**.
- **The pack format**: the manifest a pack declares its actions in, and
  the action keys and parameters it can use (see
  [Actions](ui/actions.md)).
- **The GitOps file format** (see [the GitOps guide](examples/gitops/README.md)).
- **The webhook endpoints** under `/api/webhooks/` (`github`, `gitlab`,
  `gitea`, `grafana-alerts`) and the payloads they accept, including the
  alert labels LabDog reads: `labdog_action`, `labdog_ai`,
  `labdog_ai_automate`, `labdog_host_id` and `labdog_hostname` (see
  [Alerts](ui/alerts.md#labels)).
- **The Prometheus metrics**: names and labels in
  [Metrics export](metrics-export.md).
- **The REST calls these docs tell you to make**, such as
  `POST /api/hosts/{id}/trust-host-key`.
- **The upgrade path**: every earlier release upgrades to every 1.x
  release by the procedure on this page.

Not covered, and free to change in a minor release: the web UI's layout,
the rest of `/api` (it is the UI's backend), the database schema (only
ever changed by migrations), log messages, and what the AI assistant is
told and allowed to run.

One exception: a fix that closes a security hole may refuse something
that used to work, in any release. Its upgrade notes say so.

### Upgrading to 1.0.0

Nothing is required unless you deleted the seeded `labdog-playbooks`
pack or LabDog cannot reach GitHub (see 1), or alerts are investigated
automatically today (see 6). Every new thing that can change a host or
send mail is off until you turn it on: alert investigations stay
read-only, no alert runs at full auto unless its Grafana rule is labelled
`labdog_ai_automate=true`, no alert runs an action until you allow that
action under **Operations → Alerts → Handling**, no mail server is
configured and nobody is subscribed, and schedules keep reading their
cron expressions in UTC. Six things are worth checking.

**1. The bundled action pack is gone.** The image and the `.deb` / `.rpm` /
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
  `https://github.com/open-unbork/unbork-playbooks`, branch `main`, then a
  pack on that repository), point the repository at a mirror, or add a
  local-directory pack.

**2. The AI assistant refuses more at read-only.** A command now counts
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

**3. Retention keys in `labdog.toml` now log a warning at startup.**
`audit_retention_days`, `run_retention_days` and `drift_retention_days`
under `[logging]`, or as `LABDOG_LOGGING__…` variables, never did
anything: retention is read only from **Settings › System**. Check that
the values there are what you meant to keep, then delete the keys. The
packaged `labdog.toml` no longer has them, so a `.deb` or `.rpm` upgrade
of a modified config may ask which version to keep.

**4. A pack playbook that imports a file next to it is no longer loaded.**
Only the playbook's own text and the pack's roles reach a run, so a
playbook using `import_tasks`, `include_tasks`, `vars_files` or
`import_playbook` with a relative path never got past its first task. It
is now refused when the pack loads: the action is missing from the
library, and the log says `pack '<name>': failed to load manifest …`,
naming the files. Move them into a role. No `labdog-playbooks` action is affected.

**5. If you rotated the encryption key on 0.10.0 or earlier**, re-enter
each Git repository's webhook secret. The rotation script skipped that
column, so signed push webhooks have answered 500 since, and GitOps has
not imported a push. See
[encryption-key-rotation.md](encryption-key-rotation.md).

**6. Alerts are investigated only when their labels ask for it.** With
`ai.auto_investigate_enabled` on, 0.10.0 investigated every alert at or
above `ai.auto_investigate_min_severity`. Now an alert is investigated
only when its Grafana rule labels it `labdog_ai=true`, or
`labdog_ai_automate=true` to let the AI fix what it finds. **Until you
add a label, alerts are recorded and nothing else happens.** Add
`labdog_ai=true` to the alert rules you want investigated.
`ai.auto_investigate_min_severity` is gone, and the upgrade deletes its
stored value. See [Alerts](ui/alerts.md#labels).

An alert labelled `labdog_ai_automate=true` runs at full auto, behind the
safeguards in [Alerts](ui/alerts.md#full-auto-safeguards). Nobody adds
that label by accident. But anyone who can post to the alert webhook with
its token could send an alert carrying it, and the webhook token of at
least 32 characters is the only lock. To keep that decision in LabDog,
turn on `ai.alert_full_auto_rules_enabled` and list in
`ai.alert_full_auto_rules` what may act. An empty list allows nothing.

Smaller things to know:

- **Two alert settings were renamed**: `ai.alert_intake_enabled` is now
  `alerts.intake_enabled`, and `ai.alertmanager_poll_minutes` is
  `alerts.poll_minutes`, under Settings › Fleet defaults › Alert intake.
  The upgrade carries their values over; only scripts that set them
  through `/api/settings` need the new names.
- **The `security.allowed_origins` workaround for the web terminal** from
  the 0.10.0 notes can go: the terminal accepts its own origin again. Keep
  the setting only for a frontend served from another host.
- **Email** needs `notifications.public_url` set before it sends anything
  with a link; LabDog will not build links from a request's `Host`
  header. See [Notifications](ui/notifications.md).
- **Alerts moved** from the Assistant to **Operations → Alerts**, and
  their API from `/api/ai/alerts` to `/api/alerts`. The old paths still
  answer, deprecated, until 2.0.
- **Full auto** is for alerts whose Grafana rule is labelled
  `labdog_ai_automate=true`, or, with `ai.alert_full_auto_rules_enabled` on,
  those matching `ai.alert_full_auto_rules`. It needs Grafana's contact
  point to send resolved notifications, or every fix is judged as not
  working, and it is refused on the machine LabDog itself runs on. See
  [Alerts](ui/alerts.md#which-alerts-may-run-at-full-auto).
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

On Docker the same rules apply to the environment variables:
`LABDOG_SECURITY__SECRET_KEY` must be at least 32 characters and not
`CHANGE_ME`, and `LABDOG_SECURITY__ALLOWED_ORIGINS` must not contain
`"*"`. A variable overrides the same key in a `labdog.toml`, so on a
Docker install the variable is the one to check. See
[Docker: check the environment variables](#docker-check-the-environment-variables)
for a check that runs the new release's own validation before you
switch over.

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
the repo-root [`VERSION`](https://github.com/open-unbork/unbork/blob/main/VERSION) file and publishes the
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

On Docker the keys are not in `labdog.toml`. They live in `LABDOG_*`
variables, usually in the `.env` file next to `compose.yaml` (see
[production-deploy.md](production-deploy.md#secrets-via-env)). Back
that file up too, along with any `labdog.toml` you mount:

```bash
sudo install -m 0600 /etc/labdog/.env \
  /var/backups/labdog/labdog.env-pre-upgrade-$(date +%Y%m%d)
```

If the variables are written straight into `compose.yaml` or come from
a secrets manager, back up wherever they actually live.

A backup set without the encryption key is unrecoverable ciphertext.
Both files together, in the same directory, on a host that's not the
one you're about to upgrade.

### Docker: check the environment variables

The upgrade notes above name `labdog.toml` keys. A Docker install sets
the same settings as `LABDOG_` variables, with the section and key
joined by `__`: `[security] secret_key` is
`LABDOG_SECURITY__SECRET_KEY`. A variable wins over the same key in a
`labdog.toml`, so check the variables. The ones the upgrade notes
mention:

| `labdog.toml` key | Variable | What to check |
|---|---|---|
| `[security] secret_key` | `LABDOG_SECURITY__SECRET_KEY` | At least 32 characters, and not `CHANGE_ME`, `change-me-in-production` or `change-me-32-bytes-base64-encoded`. Otherwise LabDog does not start (0.10.0). |
| `[security] encryption_key` | `LABDOG_SECURITY__ENCRYPTION_KEY` | Set, not a placeholder, and the same key as before. A different key cannot decrypt the stored SSH keys. |
| `[security] allowed_origins` | `LABDOG_SECURITY__ALLOWED_ORIGINS` | A JSON list of your real origins, without `"*"`, which is refused (0.10.0). From 1.0.0 it is only needed for a frontend served from another host. |
| `[security] cookie_secure` | `LABDOG_SECURITY__COOKIE_SECURE` | `true` once LabDog is behind TLS. With `true`, a `localhost` origin in the list is refused. |
| `[security] labdog_server_ip` | `LABDOG_SECURITY__LABDOG_SERVER_IP` | The address your hosts see LabDog connect from. The SSH lockout rule allows SSH from it when LabDog has not recorded a host's own view of that address. Unset, it is `127.0.0.1`, which no remote host's firewall will match. |
| `[logging] audit_retention_days`, `run_retention_days`, `drift_retention_days` | `LABDOG_LOGGING__AUDIT_RETENTION_DAYS` and so on | Never had an effect, and log a warning from 1.0.0. Set retention under **Settings › System** and delete the variables. |

To list which `LABDOG_` variables the running container has, without
printing their values:

```bash
docker compose -f /etc/labdog/compose.yaml exec labdog \
  sh -c 'env | cut -d= -f1 | grep ^LABDOG_ | sort'
```

To check the secret key's length without printing it:

```bash
docker compose -f /etc/labdog/compose.yaml exec labdog \
  sh -c 'printf %s "$LABDOG_SECURITY__SECRET_KEY" | wc -c'
```

The thorough check is to let the new release validate your
configuration before you switch over. Set the new image tag in
`compose.yaml` and pull it as in [Docker](#docker) below, then, before
`up -d`, start a throwaway container from it that only loads the
settings:

```bash
docker compose -f /etc/labdog/compose.yaml run --rm --no-deps labdog \
  python -c 'import app.config'
```

It gets the same variables as the real container and touches neither
the database nor the running container. When the configuration is
accepted it exits 0, printing at most a warning (such as the one about
`cookie_secure` on plain HTTP). When it is not, it exits 1 with
`FATAL: LabDog cannot start:` and one line per problem, the same
message the real container would log in a restart loop. Fix those, run
it again, and then go on with `up -d`.

---

## Upgrading

Alembic migrations run automatically on container start (Docker) or
on service start (`labdog.service` → `python -m app`). You do not
run `alembic upgrade head` manually for a normal release upgrade.

### Docker

Pin the tag to a specific version — never run `:latest` in
production. Each release is tagged on Docker Hub as `X.Y.Z`, `X.Y` and
`X`: `unbork/unbork:1.0.0` never changes, while `1.0` follows the
patch releases of 1.0 and `1` every 1.x release. `latest` follows every
release, and the `dev-*` and `test-*` tags are unreleased builds.

```bash
# 1. Note the version you're on, in case rollback is needed.
docker inspect labdog | grep -i 'image\|labdog/labdog'

# 2. Set the new tag in compose.yaml (see below) and pull it.
docker compose -f /etc/labdog/compose.yaml pull labdog

# 3. Check the new release accepts your LABDOG_* variables
#    (see "Docker: check the environment variables" above).
docker compose -f /etc/labdog/compose.yaml run --rm --no-deps labdog \
  python -c 'import app.config'

# 4. Recreate the container.
docker compose -f /etc/labdog/compose.yaml up -d labdog

# 5. Watch the logs as alembic runs.
docker compose -f /etc/labdog/compose.yaml logs -f labdog
```

Update the image tag in `compose.yaml` to the new version so the
next `up -d` doesn't accidentally roll forward to whatever `latest`
points at:

```yaml
services:
  labdog:
    image: unbork/unbork:1.0.0
```

### Debian / Ubuntu (.deb)

```bash
VERSION=1.0.0
curl -LO https://github.com/open-unbork/unbork/releases/download/v${VERSION}/labdog_${VERSION}-1_amd64.deb
sudo apt install ./labdog_${VERSION}-1_amd64.deb

# systemd will restart labdog.service automatically; alembic runs
# at startup. Watch the logs:
sudo journalctl -u labdog -f
```

### RHEL / Fedora / Rocky (.rpm)

```bash
VERSION=1.0.0
curl -LO https://github.com/open-unbork/unbork/releases/download/v${VERSION}/labdog-${VERSION}-1.x86_64.rpm
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
#    "repo_url":"https://github.com/open-unbork/unbork"}
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
