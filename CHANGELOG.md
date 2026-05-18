# Changelog

All notable changes to LabDog are documented in this file.

The format follows [Keep a Changelog]; LabDog follows
[Semantic Versioning].

## [Unreleased]

### Changed

#### Action pack precedence: drop positional ordering, pure per-key pinning

**Breaking change to the action-pack precedence model.** The
`ActionPack.position` column and the drag-to-reorder UI on
`/action-packs` are gone. Each action key now has at most one
source pack chosen by an explicit operator pin (the
`action_resolution` table). Contested keys without a pin are
*unresolved* — the action is unrunnable until the operator picks a
winner. Uncontested keys still win automatically.

- **Schema migration `0004_drop_pack_position`** drops the
  `action_packs.position` column. Before dropping, the migration
  backfills `action_resolution` rows from the current
  `action_registry_snapshot` so existing positional defaults
  become explicit pins — operators see no behavioural change at
  upgrade time. Pins they don't want can be edited or deleted in
  the UI later. Downgrade re-adds the column with default 0
  (best-effort; perfect roundtrip impossible because pins may
  have been edited between up/down).
- **`POST /api/action-packs/reorder` deleted.** The endpoint and
  its `ActionPackReorderRequest` schema are gone.
- **`POST /api/action-packs/{id}/claim-all-keys` added.** Bulk-pin
  every key a pack contributes via this pack; returns
  `{created, updated, skipped}` counts. Idempotent.
  Confirmation dialog on `/action-packs` shows the diff before
  commit. Overwrites pins on other packs for the same keys.
- **`POST /api/actions/runs` rejects unresolved actions** with
  HTTP 409 and a clear message directing the operator to
  `/action-packs` to pick a winner.
- **`GET /api/actions/`** gains `winning_pack_id: int | None` and
  `unresolved: bool` on every `ActionDefinitionOut`. The frontend
  reads these to disable the Run button + show an Unresolved
  badge on action cards.
- **`GET /api/action-resolutions`** gains `is_unresolved: bool`
  on each row; `current_winner` is `null` when unresolved.
- **`/action-packs` page rewritten.** Top: Action Registry table
  (every action key, winner, inline radio picker when contested).
  Bottom: Pack Sources table (add/sync/edit/delete + "Make
  winner for all keys" button per row). The bundled pack appears
  as a read-only built-in row. No drag handles, no position
  column. Conflict resolution flows inline — the standalone
  `ConflictResolutionDialog` component is gone.
- **Run dialog and action cards.** When an action's
  `winning_pack_id` is null (and it's not a built-in), the action
  card shows an "Unresolved" badge with a link to `/action-packs`
  and the Run button is disabled. The Run dialog refuses to
  submit with the same message inline.

Migration is one-way safe — existing installs become explicit pins
that mirror the prior positional winner. The freeze-on-fresh-
conflict behaviour is unchanged (auto-pin previous winner; UI
surfaces "Frozen" until the operator confirms).

## [0.2.0] — 2026-05-13

### Added

#### Group-dispatch actions and one-click Kubernetes upgrade

Actions that declare `supports_host: false` are now dispatched as a
single `ansible-playbook` invocation against the whole group's flat
inventory, instead of fanning out per-host. Multi-node coordination
(`serial:`, `add_host`, `delegate_to`, `run_once`) lives entirely
inside the pack's own playbook — labdog has no notion of per-member
"roles" or cluster topology. The existing per-host fan-out is
unchanged for everything else.

- **`app.tasks.action_group.run_action_group`** — new Celery task
  for the group-dispatch path. Builds a flat `all` Ansible inventory
  of every member host, runs `ansible-playbook` once, routes per-host
  events back to per-host `ActionHostRun` rows by inventory hostname.
  The per-host advisory locks, audit log shape, and SSE streaming
  channel work the same as the per-host path; only the dispatch
  shape changes.
- **`POST /api/actions/runs`** — actions with `supports_host: false`
  reject host-target submissions with HTTP 400 and a clear message.
- **Production `k8s-upgrade` playbook** — the bundled pack ships
  the canonical kubeadm upgrade flow at
  `backend/app/ansible/actions/k8s-upgrade/`. Self-discovers
  control-plane vs worker nodes by probing every member for
  `/etc/kubernetes/manifests/kube-apiserver.yaml` in a setup play,
  then `serial: 1` upgrades control-plane nodes (first runs
  `kubeadm upgrade plan + apply`; subsequent run `kubeadm upgrade
  node`), then workers serially. `kubectl`-driven tasks (drain /
  uncordon / Ready-wait) delegate to the first control-plane node
  so no kubeconfig has to ship elsewhere. Apt-only for now;
  RHEL/Rocky/Alma support tracked in `TODO.md`.
- **Destructive group-dispatched actions get the same per-host
  snapshot/verify/rollback envelope as per-host actions.** Before
  the single ansible-playbook invocation, labdog snapshots every
  member with a Proxmox VM mapping. After: per-host verify
  (`verify_playbook` if declared, else built-in SSH/services/packages
  checks). Per-host rollback policy on failure — only hosts whose
  action OR verify failed get their snapshot reverted; successfully-
  upgraded hosts keep their state and their snapshots get cleaned up.
  The operator inspects the partial outcome and re-runs the action;
  pack idempotency carries the resumption.

### Changed

#### Action pack precedence: drop role, add position + per-key resolutions

The `default` / `override` role concept is gone. Pack precedence is now
a single linear `ActionPack.position` integer (higher wins; bundled
implicit at 0); operators reorder packs by drag-and-drop on the
**Action Packs** page, matching the firewall-rules UX. Per-key
conflicts have a dedicated resolution path so adding or syncing a
pack never silently flips behaviour.

- **`ActionPack.role` column dropped, `position` added.** Migration
  `e7b2c4f9a3d1` backfills positions in stable, behaviour-preserving
  order (today's local > override > default precedence). Local packs
  lose their implicit "always wins" status — operators can now demote
  a local pack below other packs.
- **`POST /api/action-packs/reorder`** — atomic full-list rewrite of
  pack positions. Submitted ids must match the current set exactly;
  the UI builds the body from its full sorted list.
- **`action_resolution` table + endpoints.** `GET/PUT/DELETE
  /api/action-resolutions[/{action_key}]` lets operators inspect
  contested keys and pin which pack wins each one. `pack_id NULL`
  pins bundled. Pack delete cascades — pinned-to-deleted-pack rows go
  away automatically.
- **`action_registry_snapshot` table + freeze-on-fresh-conflict.** The
  registry rebuild reads the snapshot of last-known winners; when a
  sync introduces a new manifest that turns a previously-uncontested
  key into a contested one, LabDog auto-pins the previous winner via
  an `action_resolution` row. Behaviour does not silently flip — the
  conflict banner on **Action Packs** flags frozen rows for operator
  review. Resetting a resolution clears the snapshot row so the next
  rebuild treats the key fresh.
- **Wizard now requires per-key picks.** When activating a repo whose
  packs collide with existing keys, the review step shows a
  per-key winner radio (one row per contested key). Activation
  rejects 409 if any contested key has no decision. The old
  pre-checked `role=override` semantics are gone — every contested
  key is an explicit operator choice.
- **`/action-packs` page rewrite.** Drag-to-reorder, info banner
  explaining priority, conflict banner that links to a
  per-key resolution dialog, no role radio in the Add/Edit form.
  Bundled is implicit (no row) — the info banner explains the
  ordering convention.

Drop the role concept outright (no deprecation shim). Existing
installs lose pack-level role configuration but keep the same
effective ordering on first boot via the migration backfill.

### Added

#### Coalesced per-host sync (option-c)

Replaces the seven independent per-module Celery sync tasks with one
orchestrator task per host that produces a single unified Ansible
playbook. Eliminates the per-host SSH race between concurrent module
syncs and unblocks bulk-sync UX.

- **New `POST /api/sync/hosts/{host_id}/bulk` endpoint** — sync any
  subset of modules (or all of them) for a host in one call.
  Validates `module_filter` (rejects empty list, unknown module names),
  is idempotent on the in-flight job (HTTP 200 with the existing
  `job_id` if a bulk sync is already pending or running for the host),
  and requires superuser auth (matches per-tab convention).
- **`run_host_sync` Celery task** — drives the full lifecycle:
  per-host serialisation via PostgreSQL advisory lock, atomic
  per-module status writes (`HostModuleStatus`), per-(sync_job)
  audit log emission with composite `module_outcomes` payload,
  tmpfs `/dev/shm` lifecycle, exception compensation, and
  dispatch-next-pending on completion.
- **Per-tab delegation** — the seven existing per-module sync tasks
  (`run_sync_playbook`, `service_sync.run_sync`, etc.) are now
  one-line delegators to `run_host_sync`. Same task names preserved
  for any external Celery clients; same per-module audit + status
  semantics. Sync triggered from any single-tab API call now goes
  through the unified orchestrator.
- **Pending-job queue** — sync requests against a host that already
  has a running sync are queued (status `pending`); the running
  task dispatches the oldest pending one when it finishes. UI sees
  the queued state immediately.
- **Stale-job sweeper** — periodic Celery beat task
  (`app.tasks.sync_sweeper.sweep_stale_syncs`, every 5 minutes)
  that finds `SyncJob` rows stuck in `running` for longer than
  30 minutes (2× the worst-case orchestrator timeout), flips
  them to `failed`, marks every seeded `HostModuleStatus` as
  `error`, emits a `sync_failed` audit row, and dispatches the
  queued successor. Closes the crash-recovery hole left open by
  the option-c chain: a worker dying mid-task no longer blocks
  the host's queue indefinitely.
- **`sync_triggered` audit events** — bulk and per-tab sync API
  endpoints now emit an audit row at the moment of trigger
  (separate from the existing `sync_completed` row at finish).

#### Schedulable actions

Folds the legacy `UpdateWorkflow` model into a unified `ScheduledAction`
that can schedule any registered action — pack-supplied or built-in —
against a host, a group, or the entire fleet.

- **New `ScheduledAction` model** at
  `app/models/scheduled_action.py` with `target_kind` (`host` /
  `group` / `fleet`), `target_id`, `action_key`, `parameters`,
  `schedule_cron`, plus the universal destructive-flow toggles
  (`snapshot_enabled`, `verify_enabled`, `auto_rollback`,
  `batch_size`). `action_runs` gets a nullable `scheduled_action_id`
  FK and mirrors of the three toggles so per-host executors see
  immutable run-time intent.
- **Three built-in pseudo-actions** (`_builtin.sync`,
  `_builtin.drift_check`, `_builtin.collect_state`) registered
  alongside pack-supplied actions in
  `app/actions/builtins.py`. The `_builtin.` namespace is reserved —
  pack manifests with underscore-prefixed keys are rejected at
  validation time. New `supports_fleet` capability flag on
  `ActionDefinition` and `ActionManifest`; opt-in only.
- **Unified scheduler** at
  `app/tasks/scheduled_action_schedule.py:check_due` (replaces
  `workflow_schedule.check_scheduled_workflows`). RedBeat ticks every
  60 s; `last_dispatched_at` is the cron walk's reference, so a
  missed tick doesn't fire-twice. Schedules with a non-terminal
  `ActionRun` are skipped — no duplicate dispatch.
- **Per-host built-in dispatchers** in
  `app/tasks/builtin_dispatchers.py` — thin wrappers that delegate
  to existing engines (`run_host_sync` for sync, the new
  `_check_drift_for_one_host` helper for drift, `collect_host_facts`
  for state) and write back `ActionHostRun.status`. `_builtin.sync`
  creates the SyncJob row option-c expects.
- **`POST /api/scheduled-actions/*` API** — CRUD plus run-now and
  run-history-list endpoints. Superuser-only. Cross-cutting
  validation enforces target compatibility (`supports_fleet/group/
  host`), cron syntax via `croniter.is_valid`, and parameter shape
  via the new `app.actions.validation.build_param_model` Pydantic
  dynamic-model builder shared with `POST /actions/runs`.
- **GitOps `scheduled_actions:` block** (replaces the legacy
  singleton `workflow:`). List-shaped, leave-alone-on-absence
  semantics: section absent ⇒ DB rows untouched; section present
  ⇒ delete-and-replace by `(target_kind='group', target_id, action_key)`.
- **Frontend** — rebuilt `/schedules` page with filter strip
  (Built-in / Pack / Target / enabled-only / search) and a kebab
  menu (Edit, Run now, View runs, Delete); shared
  `<ScheduleActionDialog>` 4-step wizard reachable from `/schedules`
  "+ New", action cards on host/group detail (preselects action),
  and a new "Schedules" tab on host & group detail (preselects
  target); the `<CronInput>` component posts to
  `/api/scheduled-actions/validate-cron` for live next-fire-times
  preview; new generic `/actions/runs/[runId]` route for fleet runs.

**Migration:** alembic backfills `update_workflows` rows into
`scheduled_actions` (target_kind=`group`, mapping
`pre_update_snapshot`→`snapshot_enabled`) and drops the legacy
`workflow_runs`, `workflow_host_runs`, `update_workflows` tables
plus the three Postgres enums. **Breaking:** the legacy
`/api/groups/{id}/workflow/*` endpoints are gone, the legacy YAML
`workflow:` block is dropped (re-shape on next push), the
`qemu-guest-agent` PackageRule auto-add side-effect is removed
(footgun), and `verification_prompt` / `auto_reboot` columns are
dropped (nothing read them). The dead
`workflow.schedule_check_interval_seconds` setting is gone — the
scheduler ticks at a hardcoded 60 s.

#### Documentation & process

- New top-level `ROADMAP.md` — high-altitude in-design / ideas /
  out-of-scope view, distinct from `TODO.md` (open near-term tasks).
- `CONTRIBUTING.md` documents the branch-scoped `plans/` workflow:
  drop plan files into `plans/` on a work branch, capture decisions
  in commit messages, delete `plans/` before merge. `dev` and
  `main` never carry it.

### Fixed

- Pre-existing bug in `app.rules.desired_state.get_desired_state`:
  short-circuited to `[]` when a host had no groups AND no host-level
  rules, skipping the auto-injected SSH lockout rule. Now always runs
  `merge_group_rules` so the lockout rule is unconditionally present
  on every code path (firewall sync, drift check, orchestrator).

### Security

- **SEC-03**: `POST /api/sync/hosts/{host_id}/bulk` now requires
  superuser (was authenticated-user). Matches the existing per-tab
  endpoint policy.
- **SEC-04**: SSH key tmpfs file is opened with `O_NOFOLLOW` to
  foreclose symlink-attack regressions.

### Internal

- Composer + 7 fragment adapters at `app.ansible_runtime.composer`:
  pure library code that wraps each per-module generator into a
  uniform `PlaybookFragment` and concatenates them in canonical
  order with module-tagged tasks and a `hosts` sentinel.
- `app.ansible_runtime.outcomes`: per-module outcome aggregator that
  resolves module identity from `event_data.play` on ansible-runner
  events.
- Firewall `get_effective_rules` / `get_effective_policies` moved
  from `app/api/rules.py` to `app/rules/merge.py` for consistency
  with the per-module pattern (cron, services, packages, …).

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
  see [`TODO.md`](TODO.md).
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
[Unreleased]: https://github.com/open-labdog/labdog/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/open-labdog/labdog/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/open-labdog/labdog/releases/tag/v0.1.0
