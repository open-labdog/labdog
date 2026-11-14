# C-033: Export: GitOps YAML, standalone playbooks, settings backup

| | |
|---|---|
| Edition | community |
| Source | roadmap, board item "Export: group config as GitOps YAML, standalone Ansible playbooks, LabDog settings backup" |
| Tier / score | P3 / 3.3 |
| Stage | parked |
| Effort | L |
| Autonomy | B |
| Depends on | none |

## Goal
An operator can take configuration out of the product in three forms: a group as GitOps YAML, a host's desired state as a standalone Ansible playbook, and the install's own settings as a backup that can be restored.

## Why it is parked
The item is three separate exports with different risks, and it must be split before any part is promoted. The board rates all three as P3 ideas.

## What would un-park it
Promote one part at a time, as its own item:
1. **GitOps YAML export** (smallest): most of the code exists.
2. **Standalone playbook export**: an escape hatch for an operator leaving the product.
3. **Settings backup and restore**: needs a decision on secrets first (see Assumptions).

## Context
- GitOps export: `backend/app/gitops/serializer.py` already has the reverse direction per module: `specs_to_yaml` (line 163), `package_specs_to_yaml` (207), `package_repo_specs_to_yaml` (240), `hosts_entry_specs_to_yaml` (272), `cron_job_specs_to_yaml` (308), `linux_group_specs_to_yaml` (342), `linux_user_specs_to_yaml` (368), `resolver_spec_to_yaml` (408), `service_specs_to_yaml` (434). Nothing calls them from an API route: the module is imported only by `gitops/importer.py`, `gitops/importers/firewall.py` and `tasks/__init__.py`. Missing: a loader from the database rows to the YAML models in `gitops/schema.py`, and a route.
- Playbook export: `backend/app/sync/orchestrator.py` `build_host_sync_plan` (line 169) returns a `HostSyncPlan` whose `playbook_yaml` is the unified playbook, built by `compose_playbook` in `backend/app/ansible_runtime/composer.py` (line 132). It is written for one run: the inventory, SSH key file and `hosts` sentinel are run-specific, and drift cleanup tasks depend on collected state.
- Settings backup: settings live in `backend/app/models/app_setting.py` through `backend/app/settings_service.py`. Secrets (SSH keys, SMTP password, pack credentials) are encrypted with `security.encryption_key` (`backend/app/crypto/`). `docs/backup-restore.md` documents the database dump as today's backup.

## Assumptions
- Assumption (not yet confirmed): each export route needs a permission. Reading group config suggests `hosts.view` with the group scope; a settings backup suggests `settings.manage`. Question for Dennis when promoted.
- Assumption (not yet confirmed): a settings backup leaves secrets out, or keeps them encrypted under the same key, and never writes them in plain text. Question for Dennis: is a backup without secrets useful, given `docs/backup-restore.md` already covers a full database dump?
- Assumption (not yet confirmed): the exported playbook is a snapshot with no promise that it stays in step with later product versions.
