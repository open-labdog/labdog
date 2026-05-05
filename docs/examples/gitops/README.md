# LabDog GitOps — Complete Guide

LabDog can pull a Linux host group's entire configuration from a git
repository. On every push to the repo (via webhook), LabDog clones the
repo, reads the YAML file associated with each gitops-enabled group, imports
the changes into its database under an advisory lock, and triggers the
downstream Ansible sync to the hosts. The UI refuses mutations on
gitops-managed groups so git remains the single source of truth.

Every configuration module LabDog supports can be declared in one YAML
file per group:

| Module | YAML section | Shape |
|---|---|---|
| Firewall rules | `firewall` | nested `rules: []` list |
| Systemd services | `services` | list |
| Packages | `packages` + `package_repositories` | two lists |
| /etc/hosts entries | `hosts_entries` | list |
| Cron jobs | `cron_jobs` | list |
| DNS resolver | `resolver` | singleton object |
| Linux users + groups | `users` + `linux_groups` | two lists |
| Scheduled actions | `scheduled_actions` | list |

Plus, in the optional global file `_global.yaml` at the repo root:

| Module | YAML section | Shape |
|---|---|---|
| Drift-check interval | `drift` | singleton object (global setting) |
| Discovery scan configs | `discovery` | list |

This directory contains:

- **[minimal.yaml](./minimal.yaml)** — smallest valid file: just `group:` and one tiny section.
- **[web-servers.yaml](./web-servers.yaml)** — realistic web tier covering all seven modules.
- **[database.yaml](./database.yaml)** — realistic database tier, different shape.
- **[_global.yaml](./_global.yaml)** — install-wide settings (drift interval + scan configs) that don't fit a single group. Optional; place at the repo root.
- **[modules/](./modules/)** — one file per module, focused examples showing every field and corner case.

---

## Quick start

1. Create a git repository. Any LabDog-reachable provider works (GitHub,
   GitLab, Gitea, a bare repo over SSH).
2. In that repo, create a YAML file per group you want LabDog to manage.
   File layout is arbitrary — `groups/web-servers.yaml`, `prod.yaml`,
   whatever fits your conventions. The file path is configured per group in
   LabDog.
3. In LabDog:
   a. Navigate to **Integrations → Git Repos**, add the repo, choose SSH key
   or HTTPS token auth.
   b. Navigate to **Manage → Groups → `<your group>`**.
   c. Click **Enable GitOps**, pick the repo and the YAML file path.
   d. Copy the webhook secret shown, then add a webhook on the git provider:
      - URL: `https://<your-labdog-host>/webhooks/github` (or
        `/webhooks/gitlab`, `/webhooks/gitea`)
      - Content type: `application/json`
      - Secret: the one LabDog showed you
      - Events: `push` only

   The Git Repos page renders all three URLs ready-to-copy under the
   "Webhook URLs" section.
4. Push a commit. LabDog imports, diffs, and syncs.

Manual imports are also available — a **Sync from Git** button on the Git
Repos page bypasses the webhook (handy during setup).

---

## Global YAML (`_global.yaml`)

State that's install-wide rather than per-group lives in an optional
file at the repo root, named exactly `_global.yaml`. As of v0.1 it
covers two sections:

- **`drift:`** — sets `drift.check_interval_minutes` (the same setting
  reachable from Settings → Drift Detection in the UI).
- **`discovery:`** — list of `ScanConfig` rows that periodically scan a
  CIDR for SSH-reachable hosts.

The file is optional — install behaviour with no `_global.yaml`
matches Phase 1 (only per-group YAML imports). The convention is path
plus name; operators who want a different layout can symlink to it.

See [`_global.yaml`](./_global.yaml) for a fully-commented example,
including how `ssh_key:` and `default_groups:` resolve to existing
`SSHKey` / `HostGroup` rows by name.

The global file imports under its own per-repo advisory lock,
independent of the per-group locks. A typo in `_global.yaml` does NOT
abort the per-group loop — the global error is logged and the
remaining files import normally.

---

## YAML reference

Every YAML file has a required `group:` key at the top. Everything else is
optional:

```yaml
group: web-servers          # required; the group's display name
priority: 100               # optional; informational only — real priority
                            # is set in LabDog's UI
```

Unknown top-level keys are **silently ignored** (`extra="allow"` in the
Pydantic model), so you can safely add new keys for upcoming modules without
breaking older LabDog versions.

### Missing-section semantics

| Module | `section: null` or key absent | `section: []` |
|---|---|---|
| `firewall` | wipe all non-system rules | wipe |
| `services` | wipe | wipe |
| `packages` | wipe | wipe |
| `package_repositories` | wipe | wipe |
| `hosts_entries` | wipe all non-system entries | wipe |
| `cron_jobs` | wipe | wipe |
| `users` | wipe | wipe |
| `linux_groups` | wipe | wipe |
| `resolver` | **leave alone** (singleton exception) | n/a |
| `scheduled_actions` | **leave alone** (list-shape exception) | wipe |
| `drift` *(global)* | **leave alone** (singleton exception) | n/a |
| `discovery` *(global)* | wipe | wipe |

Rule of thumb: omitting a list-shaped section means "I have no opinion and I
want LabDog to have none either" — so it empties the group's rows. The
resolver section is the singleton exception: omitting it leaves the current
DB state untouched, because a partially-populated resolver silently blanked
on every YAML push is almost never what you want.

### Per-module references

See [modules/firewall.yaml](./modules/firewall.yaml),
[modules/services.yaml](./modules/services.yaml),
[modules/packages.yaml](./modules/packages.yaml),
[modules/hosts-entries.yaml](./modules/hosts-entries.yaml),
[modules/cron-jobs.yaml](./modules/cron-jobs.yaml),
[modules/resolver.yaml](./modules/resolver.yaml),
[modules/users.yaml](./modules/users.yaml),
and [modules/scheduled-actions.yaml](./modules/scheduled-actions.yaml) for every field with
inline comments.

### System-owned rows

Two modules (`firewall`, `hosts_entries`) have a concept of "system" rows
— entries LabDog injects automatically (e.g. the SSH-lockout-prevention
firewall rule, or `127.0.0.1 localhost`). GitOps imports **never touch
system rows**. They are filtered out before the diff and preserved across
every import.

### Protected names

Some modules reject entries with reserved names, even from YAML:

- **Services:** `sshd`, `ssh`, `networking`, `NetworkManager`,
  `systemd-journald`, `systemd-logind`, `systemd-udevd`,
  `systemd-resolved`, `dbus` — rejected at parse time.
- **Packages:** `openssh-server`, `openssh-client`, `sshd`, `systemd`,
  `linux-image*`, `linux-headers*`, `bash`, `glibc`, `libc6`,
  `coreutils`, `grub`, `grub2`, and similar system-critical packages
  — rejected at parse time.
- **Users:** `root`, `daemon`, `bin`, `www-data`, `sshd`, etc. —
  rejected at parse time.
- **Linux groups:** `root`, `sudo`, `wheel`, `shadow`, `sshd`, etc. —
  rejected at parse time.

A YAML containing a protected name fails the import with a clear error
message and the group's status flips to `error`. The DB is not modified.

---

## What happens on a push

```
git push
   │
   ▼
Webhook receiver verifies the signature (HMAC, per provider).
   │
   ▼
Celery task `gitops.process_webhook` picks up:
  • Clones the repo at the pushed commit SHA.
  • For every group linked to this repo and gitops-enabled:
      ├── Reads the group's YAML file at that SHA.
      ├── Acquires a per-group PostgreSQL advisory lock.
      ├── Sets `gitops_status = importing`.
      ├── Parses YAML → validates → each per-module handler diffs
      │   the YAML-declared state against the DB and writes changes
      │   in one transaction.  Each handler emits a per-module
      │   audit event (`gitops.import.firewall`,
      │   `gitops.import.services`, …).
      ├── On success: `gitops_status = synced`, `last_import_at` updated.
      ├── On any handler error: rolls back the whole transaction,
      │   `gitops_status = error`, error message stored.
      └── If any module reported changes, a SyncJob is enqueued for
          every host in the group.
   │
   ▼
Ansible runs the merged desired state against each host.
```

All modules for a given group import under the **same transaction**. A
failure in the services handler rolls back the firewall handler's changes
from the same push. This matches the behaviour of the pre-existing firewall
importer and is almost always what you want.

---

## Error handling

When an import fails the group's status becomes `error` and the offending
message appears on the group's overview page. Common causes:

| Cause | Where it surfaces | Fix |
|---|---|---|
| Malformed YAML syntax | `YAML parse error: …` | Validate locally with `yq . file.yaml` |
| Unknown enum value | `YAML validation failed: …` | See the per-module examples |
| Protected name | `'root' is a protected system user …` | Remove the entry |
| Invalid cron schedule | `Invalid cron schedule for job '<name>': …` | `crontab -l` on any Linux box to sanity-check the expression |
| Missing host ref | `Referenced host id 42 does not exist` | The host was deleted in LabDog; update the YAML |
| SSH key prefix unknown | `Invalid SSH key: must start with ssh-rsa \| ssh-ed25519 \| …` | Only public-key formats are accepted |
| Too many nameservers | `Maximum 3 nameservers allowed …` | Resolver's `/etc/resolv.conf` limit |
| Unknown `action_key` in `scheduled_actions:` | `Unknown action_key: '<value>'` | The action isn't in the registry — fix the typo, or add the pack supplying it |
| Missing required parameters | `Invalid parameters for '<key>': …` | Add the keys the action's manifest requires under `parameters:` |
| Invalid cron in `schedule_cron` | `Invalid cron expression: …` | 5-field cron only; `crontab.guru` is your friend |
| Group-only target on host action | `Action '<key>' does not support group runs` | Use a different action or change the target — `linux-upgrade` is host-only |

The error message is also available via `GET /api/groups/{group_id}` →
`gitops_error_message` for scripted recovery.

### Mutation lock

While a group has `gitops_enabled=true` every group-scoped mutation API
endpoint returns **HTTP 403** with body:

```json
{"detail": "This group is managed by GitOps. Changes must be made via Git."}
```

The UI wires this into disabled Add / Edit / Delete controls on every
module page so users don't even get to try. Host-level overrides (config
scoped to a single host, not the group) stay open — GitOps manages group
config only.

To break the glass in an emergency, toggle GitOps off on the group from
the UI — the lock lifts immediately.

---

## Idempotency and drift

Every handler compares the YAML-declared state against the DB before
writing. If the normalised tuples match exactly, **nothing is written**
and no audit event is emitted. A re-push of the same commit is effectively
a no-op. Two important normalisations:

- **`authorized_keys` and `supplementary_groups`** on linux users: the diff
  sorts a copy so reordering a user's keys in YAML is not treated as
  drift. User-provided order is preserved in the database for display.
- **`environment` dicts on cron jobs:** compared by sorted items for the
  same reason.
- **Service `unit_content`:** trailing whitespace is stripped and a final
  newline enforced before comparison — otherwise every whitespace tweak
  would look like drift.

Conversely, these must round-trip **byte-identically** to stay stable:

- **Cron `schedule` strings** are never normalised. `*/5` and `0/5` are
  semantically equivalent but textually different — LabDog treats
  them as different schedules to avoid silent behaviour changes on
  re-import.
- **Resolver `nameservers` and `search_domains` list order** is
  significant (fallback order in `/etc/resolv.conf`), so it is preserved
  and compared in order.

---

## Multi-group repositories

Each group points at its own YAML file in the repo. Two common layouts:

### File per group

```
my-infra-repo/
├── groups/
│   ├── base-security.yaml
│   ├── web-servers.yaml
│   ├── database.yaml
│   └── edge.yaml
└── README.md
```

In LabDog, each group's **GitOps file path** points at its file
(`groups/web-servers.yaml`, etc.). A push that touches any file triggers
the webhook once; LabDog re-reads the file for every group linked to
the repo and only imports the ones whose file actually changed.

### Single repo, single group

```
labdog-prod-repo/
└── config.yaml
```

Simplest. One group per repo. Whole repo serves one config file.

Either layout works. Larger homelabs lean toward the file-per-group
variant because branch protection on a single repo is easier to reason
about than juggling multiple repos.

---

## Recovery

If you need to break out of the GitOps lock (e.g. emergency rule push and
your git provider is down):

1. **From the UI:** group → **Disable GitOps** toggle. Lock lifts. You can
   edit via the UI. Re-enable when done; the next push will reconcile any
   drift from git.
2. **From a DB shell:** `UPDATE host_groups SET gitops_enabled = FALSE
   WHERE id = <id>;` — same effect, for when the UI is unreachable.

Either way, the next successful import re-synchronises the group with the
git-declared state. Ad-hoc DB edits made during a break-glass window will
be overwritten.

---

## See also

- [../precedence/README.md](../precedence/README.md) — how multiple groups
  merge when a host belongs to several.
- `backend/app/gitops/schema.py` — authoritative Pydantic schemas for
  everything documented here.
- `backend/app/gitops/importers/` — per-module handler sources.
