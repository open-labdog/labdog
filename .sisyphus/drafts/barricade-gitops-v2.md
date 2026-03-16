# Draft: Barricade GitOps v2

## Requirements (confirmed)

- **Mode**: Mode B only — Git is the authoritative source of truth (NOT a mirror)
- **Git providers**: All three — GitHub, GitLab, Gitea (webhooks for all)
- **CI/CD linter**: YES — `barricade-lint` CLI tool for pipeline validation
- **YAML format**: Multi-module from the start — extensible container format supporting firewall + future modules (services, users, packages, etc.)

## Technical Decisions

- **Mode B replaces Mode A**: No mirroring. Git is truth, Barricade reconciles.
- **Webhook-first, polling-fallback**: Same as original plan
- **gitpython**: Same as original plan
- **SSH key reuse**: Same as original plan (SSHKey model for repo auth)

## Design Decisions (confirmed round 2)

- **UI vs GitOps**: Mutually exclusive per group. When GitOps is enabled on a group, UI rule editor is disabled (read-only viewer). When GitOps is off, UI works as normal. Clean separation.
- **Initial state**: Manual YAML creation. User creates YAML files in Git. Barricade only reads from Git, never writes. Purest GitOps approach.
- **Auto-sync**: Full pipeline — Git push → webhook → import to DB → auto-sync to all hosts. No human gate.

## Design Decisions (confirmed round 3)

- **YAML format**: Top-level module keys (firewall, services, etc.). Keep syntax close to Ansible instructions for familiarity. Unknown modules ignored by parser. Group metadata at top.
- **Branch strategy**: Single configurable branch per repo (default: main). No multi-branch workflows.
- **Plan on auto-sync**: YES — compute diff and log in audit trail before every auto-sync. Audit trail without human gate.
- **Error handling**: Reject invalid YAML/rules. Don't update DB. Set group's gitops_status to 'error' with descriptive message. Visible on dashboard.

## Design Decisions (confirmed round 4)

- **Git auth**: SSH + HTTPS token. HTTPS tokens (PAT/GitLab token) stored encrypted same as SSH keys.
- **Core dependency**: Fix T18/T19 stub (`fetch_current_state`) as prerequisite in GitOps plan. Ensures logged diffs are real, not placeholder.
- **Linter distribution**: Standalone pip package (`barricade-lint`). Installable in CI without cloning Barricade. Proper entry point.
- **Test strategy**: Tests after implementation (same pattern as core plan). pytest.

## Design Decisions (confirmed round 5 — Metis gaps)

- **File mapping**: Explicit `gitops_file_path` on HostGroup model. User specifies path when enabling GitOps.
- **Multi-group repos**: Yes (one repo → many groups via FK).
- **Polling fallback**: Removed. Webhook only for v1.
- **File deleted from repo**: Set gitops_status to 'error'. Don't remove rules.
- **YAML port naming**: `port: 443` (single), `port: "443-445"` (range). Ansible-familiar.
- **YAML field names**: `source`/`dest` not `source_cidr`/`destination_cidr`.
- **All 14 Metis edge cases**: Accepted recommended handling.

## Metis Review Summary

Key risks identified:
- R1: Auto-sync could brick hosts via bad YAML → mitigated by validation + SSH lockout auto-inject
- R2: Concurrent webhook processing → mitigated by per-group advisory lock
- R3: gitpython file locking → mitigated by temp-dir-per-import pattern
- R4: HTTPS token leakage in .git/config → mitigated by temp dirs
- R5: GitLab commit cap at 20 → mitigated by reading all files at SHA, not relying on changed list
- R6: Lint validation drift → accepted risk for v1

Key directives incorporated:
- DRY up FirewallRule → FirewallRuleSpec conversion (duplicated 4x)
- Use hmac.compare_digest() for webhook verification
- Gate rule mutations on GitOps-enabled groups
- Strip `system: true` from YAML imports
- Use existing run_sync_playbook task for sync triggering
- Per-group lock for import operations
- Respond 200 immediately on webhook, process async

## Open Questions

(none remaining — all decisions captured)

## Scope Boundaries

- INCLUDE: Mode B (Git authoritative), webhooks (3 providers), linter CLI, multi-module YAML, auto-sync
- EXCLUDE: Mode A (mirror), UI-to-Git commits, one-time export, UI rule editing for GitOps groups
