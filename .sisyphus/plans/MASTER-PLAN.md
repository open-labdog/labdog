# Barricade Extensions — Feature Expansion Plan

## TL;DR

> Extend Barricade from a firewall-only tool into a **Linux node configuration management platform**. Each extension follows the "Barricade pattern": DB as truth → Ansible enforcement → drift detection → audit log → React UI.

---

## Design Philosophy

Every extension shares the same architecture:
1. **DB model** — desired state stored in PostgreSQL
2. **Ansible renderer** — desired state → playbook tasks
3. **Drift detector** — actual state fetched from host, compared to desired
4. **Sync engine** — apply desired state via Celery + ansible-runner
5. **Audit log** — every change recorded with before/after state
6. **React UI** — manage desired state, preview diff, trigger sync

All extensions support **group-level defaults + per-host overrides** with priority-based merge.

---

## Implementation Plans

| # | Module | Plan | Effort | Status | Key Points |
|---|--------|------|--------|--------|------------|
| 1 | **Service Management** | [`ext-service-management.md`](ext-service-management.md) | S | ✅ Shipped | Systemd services (running/stopped, enabled/disabled). Created shared infrastructure: `host_module_status` table, `SyncJob.module_type`. |
| 1a | **Service Live Control** | [`service-live-control.md`](service-live-control.md) | S | ✅ Shipped | Live service inventory + ad-hoc start/stop/restart via SSH. Extends #1. |
| 2 | **Linux User Management** | [`ext-linux-user-management.md`](ext-linux-user-management.md) | M | ✅ Shipped | Users, SSH authorized_keys, sudo rules, supplementary groups. Depends on #1. |
| 3 | **/etc/hosts** | [`ext-etc-hosts.md`](ext-etc-hosts.md) | S | ✅ Shipped | Hostname-to-IP mappings. Full-file template rendering with localhost safety. |
| 4 | **Package Management** | [`ext-package-management.md`](ext-package-management.md) | M | 📋 Planned | System packages (apt/dnf/yum). Version pinning, repository management. Multi-distro drift. Depends on #1. |
| 5 | **Cron Jobs** | [`ext-cron-jobs.md`](ext-cron-jobs.md) | S | ✅ Shipped | Scheduled tasks via `ansible.builtin.cron`. 5-field cron validation. Depends on #1. |
| 6 | **DNS Resolver** | [`ext-dns-resolver.md`](ext-dns-resolver.md) | M | 📋 Planned | Nameservers, search domains. 3 backends (resolv.conf, systemd-resolved, NetworkManager). Singleton config per scope. Depends on #1. |
| 7 | **TLS Certificates** | _Not yet planned_ | XL | — | ACME, uploaded PEM, expiry monitoring, service reload. |
| 8 | **User Management & Auth** | [`user-management.md`](user-management.md) | M | ✅ Shipped | First-user bootstrap, registration gating, admin CRUD, RBAC removal. |
| 9 | **GitOps Frontend** | [`gitops-frontend.md`](gitops-frontend.md) | S | ✅ Shipped | Git Repos page, group GitOps settings, status badges. |
| 10 | **Host Discovery** | [`host-discovery.md`](host-discovery.md) | S | ✅ Shipped | Network CIDR scan for SSH hosts, bulk-add UI. |
| 11 | **Web Shell** | [`web-shell.md`](web-shell.md) | M | 📋 Planned | Browser-based SSH terminal via xterm.js + WebSocket + asyncssh PTY. |

### Execution Order
Service Management (#1) was built first — it created `host_module_status` and `SyncJob.module_type` that all other modules reuse. /etc/hosts (#3), User Management (#8), GitOps Frontend (#9), and Host Discovery (#10) are also complete. Modules #1a, #2, #4–7, #11 can be built in any order.

### Supporting Files

| File | Purpose |
|------|---------|
| [`COMPROMISES.md`](COMPROMISES.md) | Registry of all deviations from stated guardrails, patterns, or policies across plans. Review before starting and after completing any plan. |

---

## Work Tracking Protocol

> **Purpose**: Ensure traceability across multiple developer environments (human or AI) working on plans concurrently.

### Rules

1. **Master plan status** — When work begins on any module, update its `Status` column in the table above to `🔨 Ongoing`. Only mark `✅ Shipped` when the plan is fully complete and verified.

2. **Individual plan updates** — Each plan file (e.g. `ext-cron-jobs.md`) **must be updated as work is carried out**. This includes:
   - **Checking off completed task boxes** (`- [ ]` → `- [x]`) immediately after each task is done
   - Recording implementation decisions made during development
   - Noting any deviations from the original plan and why
   - Updating estimates if scope changed

3. **Session-end checkpoint** — When a work session ends before the plan is complete (context limit, interruption, end of day), the developer **must** before stopping:
   - Update all checkboxes in the plan file to reflect current state
   - Add a brief status note at the top of the plan (e.g. "T1–T4 done, T5 in progress, router not yet registered in main.py")
   - Commit all work-in-progress so the next session can pick up cleanly
   - This prevents the next session from having to rediscover progress by reading every file

4. **Integration verification** — After all individual tasks in a plan are complete, verify end-to-end wiring before marking the plan as shipped:
   - Are all new modules/routers registered in entry points (e.g. `main.py`)?
   - Do all cross-file references resolve (types, imports, API calls)?
   - Does the full build pass and the feature actually work?
   - Individual tasks passing does not mean the feature is done — the glue between them must be verified.

5. **Bug tracking** — If bugs are discovered or fixed during any work, update [`.sisyphus/BUGS.md`](../BUGS.md) immediately:
   - New bugs: Add with next `BUG-XX` ID, file location, description, and severity
   - Fixed bugs: Mark `[x]`, note the fix and commit hash
   - Section headers group bugs by discovery context — add a new section if working on a new module/feature

6. **Status lifecycle**: `📋 Planned` → `🔨 Ongoing` → `✅ Shipped`

7. **Why this matters** — Multiple developer environments (different sessions, machines, or agents) may pick up or continue work on any plan. Without up-to-date plan files, work gets duplicated, conflicts arise, and context is lost. The plan files and BUGS.md are the **single source of truth** for what has been done, what remains, and what's broken.

---

## Cross-Cutting Concerns (Future)

### Multi-module Sync
Currently each module syncs independently. A future `full_sync` would apply all modules in one ordered playbook:
```
packages → users → /etc/hosts → resolver → cron → services → firewall
```

### Module Enable/Disable per Group
Future: `enabled_modules` list on HostGroup to control which modules apply.

---

## Ideas

> Raw ideas that need further exploration before becoming a plan. To promote an idea, create a plan file and add it to the Implementation Plans table above.

| Idea | Notes |
|------|-------|
| **Repository management** | — |
| **Change name and description of application** | — |
| **Dashboard metrics & charts** | Host count, drift status breakdown, sync success/failure rates, recent activity on the main dashboard |
| **Notification system** | Email/webhook/Slack alerts on drift detection, sync failures, or certificate expiry |
| **API tokens** | Non-cookie auth for CI/CD integration or scripting against the Barricade API |
| **Bulk sync / scheduled sync** | Sync all hosts in a group on a schedule, not just manual trigger |
| **Host tagging & filtering** | Tags beyond groups for flexible host organization (e.g. `region:eu`, `env:prod`) |
| **Import/export configuration** | Backup and restore group configs, rules, and service definitions |
| **CLI tool** | Command-line client for power users who prefer terminal over UI |
| **Ansible playbook export** | Export Barricade's desired state as standalone Ansible playbooks (escape hatch) |
| **Group hosts in hosts view** | Visual grouping/sorting of hosts by their group in the hosts list UI |

---

## What NOT to Build

- Puppet/Chef/Salt replacement — Barricade is opinionated and UI-first
- Container management — Kubernetes/Nomad territory
- Application deployment — use CI/CD
- Monitoring/alerting — use Prometheus/Grafana
- Secret management — use Vault/SOPS
- Sysctl / kernel parameter management — too niche, low demand
- Arbitrary file/directory management — too broad, overlaps with Ansible itself
