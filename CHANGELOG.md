# Changelog

All notable changes to LabDog are documented in this file.

The format follows [Keep a Changelog]; LabDog follows
[Semantic Versioning].

## [Unreleased]

Nothing yet — this section gathers changes after the v0.1.0 tag.

## [0.1.0] — 2026-04-26

First public release. LabDog is a small-fleet Linux configuration
manager: declare desired state per host group, push changes over SSH
via Ansible, watch for drift, run scheduled OS upgrades with
automatic snapshot + rollback, and (optionally) keep the whole thing
in git. The pieces below describe the surface as it exists at v0.1.0.

### Added

#### Host management
- Add hosts manually or via network discovery scans.
  `auto_add: false` puts new hits in a Pending Approval queue;
  `auto_add: true` admits them straight into the fleet.
- Per-host SSH connection (asyncssh), terminal embed in the UI.
- Auto-collected facts on add: OS family, codename, kernel, default
  NIC, firewall backend (`nftables` / `iptables`).
- Group membership with priorities; configuration merges with
  higher-priority groups winning.

#### Configuration modules
Eight modules, each with desired-state ↔ collected-state diffing,
SSH-pushed Ansible reconciliation, and a per-host detail tab:

- **Firewall** — `nftables` and `iptables`. Chain policies
  (input / output) plus per-rule `allow` / `deny` / `reject`.
  System-injected SSH-lockout-prevention rule that stays put across
  imports.
- **Systemd services** — managed `running` / `stopped`, `enabled`
  flag, override-only or full unit-content deploys with templated
  units. Protected services (sshd, networking, NetworkManager…)
  rejected at parse time.
- **Packages** — apt / dnf / yum, version pinning + holds, optional
  package repositories with GPG keys.
- **/etc/hosts entries** — literal IP-hostname pairs or
  `host_ref_id` cross-references that resolve at apply time.
- **Cron jobs** — per-user, with optional environment dict and
  schedule-string preservation.
- **DNS resolver** — `resolv.conf` / `systemd-resolved` /
  `NetworkManager` backends, DNS-over-TLS, options validation.
- **Linux users + groups** — `authorized_keys`, `supplementary_groups`,
  `sudo_rule`, shell, uid/gid pinning.
- **CA certificates** — bundle deploy + per-host override.

#### Actions
- Bundled action pack ships three actions: `linux-upgrade`,
  `linux-os-upgrade` (with `current_version` / `next_version`
  parameters), `k8s-upgrade`.
- DB-backed action packs: configure additional packs from the UI at
  `/action-packs`, sourced from a git repository (public, SSH key,
  or HTTPS PAT) or a local filesystem path. Packs sync at FastAPI
  lifespan + Celery `worker_ready`.
- Action manifest schema (`*.manifest.yml`) declares parameters,
  destructive flag, `verify_playbook`, supported scope (host /
  group), and version. Override semantics — pack role
  (`default` / `override`) derives a priority tier, admins never
  enter integers.
- Snapshot + verify + rollback wrap destructive actions when a
  Proxmox VM mapping exists for the host: pre-action snapshot,
  optional `verify_playbook` post-action, automatic rollback on
  failure.

#### Update workflows
- Per-group scheduled action runs at `/groups/{id}/workflow/`.
  Cron schedule, batch size, snapshot / rollback / reboot toggles,
  optional verification prompt. Action picker exposes any action
  registered in the live registry — including pack-supplied ones —
  with parameter inputs derived from the manifest.

#### GitOps
- Per-group YAML imports the eight configuration modules plus the
  per-group update workflow, under per-group PostgreSQL advisory
  locks.
- Optional `_global.yaml` at the repo root imports the global
  drift-check interval and any number of `ScanConfig` rows for
  network discovery, with cross-references resolved by name
  (`ssh_key: <name>`, `default_groups: [<name>, …]`).
- Webhook receivers for GitHub, GitLab, and Gitea (HMAC-verified
  per provider). Each module emits its own `gitops.import.*`
  audit event with before/after state.
- The UI mutation lock: with `gitops_enabled=true` on a group, all
  group-scoped mutation endpoints return 403 — git is the source of
  truth.
- Worked examples at [`docs/examples/gitops/`](docs/examples/gitops/).

#### Discovery
- Recurring network scans by CIDR with per-config schedule
  (`interval_minutes` XOR `cron_expression`), default group
  assignment for auto-added hosts, optional review queue for the
  rest. Rate-limited at 100k IP-checks/min.

#### Drift detection
- Configurable interval (1–1440 min, default 30) collects current
  host state via SSH and diffs against desired. Per-module status
  on the host detail page; fleet-level rollup on the dashboard.

#### Audit log
- Append-only `audit_log` table records every mutation across hosts,
  groups, modules, action runs, workflow runs, GitOps imports,
  scan-config approvals, and authentication events. User email
  resolved at query time.

#### Authentication
- `fastapi-users` with cookie + JWT, bcrypt password hashing.
  First-user-becomes-superuser bootstrap; subsequent users created
  by superusers from the Users page.

#### Operations
- AES-256-GCM at-rest encryption for SSH private keys, Proxmox API
  tokens, and HTTPS git credentials. Encryption key required at
  startup; insecure defaults rejected.
- Backup + restore guide at
  [`docs/backup-restore.md`](docs/backup-restore.md): `pg_dump`
  invocation, encryption-key handling, systemd timer + script,
  fresh-host restore, point-in-time restore, and disaster scenarios.
- Release artifacts (`.tar.gz` + `.deb` + `.rpm` + `SHA256SUMS`)
  built and attached to GitHub Releases automatically on `v*` tag
  push by the `release-artifacts` job in
  [`.github/workflows/ci.yml`](.github/workflows/ci.yml).
- systemd service unit shipped in
  [`packaging/systemd/`](packaging/systemd/) for production deploys
  on Debian / Ubuntu / Fedora / RHEL.

### Stack
- Backend: Python 3.12+, FastAPI, SQLAlchemy 2.0 (async), asyncpg,
  Celery + Redis (RedBeat scheduler), ansible-runner, asyncssh.
- Frontend: Next.js 16 (App Router), shadcn/ui, TanStack Query.
- Database: PostgreSQL 16.

### Known limitations at v0.1.0

- Per-host action execution only — multi-host coordination
  (one ansible-runner invocation against a host group) is parked,
  see [`plans/TODO.md`](plans/TODO.md).
- `nftables` and `iptables` only; no Cisco / pfSense / opnsense /
  Mikrotik backends.
- No encryption-key rotation tooling — recovery from a leaked key
  is a documented truncate-and-re-enter procedure (see
  [`docs/backup-restore.md`](docs/backup-restore.md)). Build a
  proper rotation runbook when an install with non-trivial
  credential inventory needs it.
- No upgrade guide — first public version, nothing to upgrade from.

### Security
- AGPL-3.0-or-later licence (see [`LICENSE`](LICENSE)).
- Vulnerability reporting via GitHub Private Vulnerability
  Reporting; see [`SECURITY.md`](SECURITY.md). Contributions are
  inbound-equals-outbound under AGPL — no CLA.

[Keep a Changelog]: https://keepachangelog.com/en/1.1.0/
[Semantic Versioning]: https://semver.org/spec/v2.0.0.html
[Unreleased]: https://github.com/open-labdog/labdog/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/open-labdog/labdog/releases/tag/v0.1.0
