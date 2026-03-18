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
| 1a | **Service Live Control** | [`service-live-control.md`](service-live-control.md) | S | 📋 Planned | Live service inventory + ad-hoc start/stop/restart via SSH. Extends #1. |
| 2 | **Linux User Management** | [`ext-linux-user-management.md`](ext-linux-user-management.md) | M | 📋 Planned | Users, SSH authorized_keys, sudo rules, supplementary groups. Depends on #1. |
| 3 | **/etc/hosts** | [`ext-etc-hosts.md`](ext-etc-hosts.md) | S | ✅ Shipped | Hostname-to-IP mappings. Full-file template rendering with localhost safety. |
| 4 | **Package Management** | [`ext-package-management.md`](ext-package-management.md) | M | 📋 Planned | System packages (apt/dnf/yum). Version pinning, repository management. Multi-distro drift. Depends on #1. |
| 5 | **Cron Jobs** | [`ext-cron-jobs.md`](ext-cron-jobs.md) | S | 📋 Planned | Scheduled tasks via `ansible.builtin.cron`. 5-field cron validation. Depends on #1. |
| 6 | **DNS Resolver** | [`ext-dns-resolver.md`](ext-dns-resolver.md) | M | 📋 Planned | Nameservers, search domains. 3 backends (resolv.conf, systemd-resolved, NetworkManager). Singleton config per scope. Depends on #1. |
| 7 | **TLS Certificates** | _Not yet planned_ | XL | — | ACME, uploaded PEM, expiry monitoring, service reload. |

### Execution Order
Service Management (#1) was built first — it created `host_module_status` and `SyncJob.module_type` that all other modules reuse. /etc/hosts (#3) is also complete. Modules #1a, #2, #4–6 can be built in any order.

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

## What NOT to Build

- Puppet/Chef/Salt replacement — Barricade is opinionated and UI-first
- Container management — Kubernetes/Nomad territory
- Application deployment — use CI/CD
- Monitoring/alerting — use Prometheus/Grafana
- Secret management — use Vault/SOPS
- Sysctl / kernel parameter management — too niche, low demand
- Arbitrary file/directory management — too broad, overlaps with Ansible itself
