# LabDog UI Guide

A walkthrough of every page in the LabDog web interface.

## Navigation

Navigation is an icon rail of five zones with a pane of destinations beside
it. The rail never grows: new destinations go into a zone's pane, or into
the command palette (`⌘K` / `Ctrl+K`), which indexes every destination,
every host, every group, every module × scope pair, and a handful of verbs.

| Zone | Pane |
|------|------|
| **Overview** | Summary · Pending · Fleet state · Activity · Upcoming |
| **Fleet** | Hosts · Groups · Discovery |
| **Config** | Firewall · Services · Hosts file · Packages · Users & SSH · Cron · DNS resolver · CA certificates — plus a scope switcher (fleet, a group) |
| **Operations** | Plans · Drift · Actions · Runs · Audit |
| **Assistant** | Sessions · Alerts |

**Settings** sits at the foot of the rail, with the theme toggle (dark is
the default; `t` switches), the palette button and your account — the avatar
opens a menu with your email, **Change password…**, **About LabDog** and
**Log out**. `[` collapses and reopens the pane.

On a tablet the pane is an overlay that closes when you navigate; on a phone
the rail becomes a bottom tab bar. Old links keep working: `/dashboard`,
`/hosts/discovery`, `/hosts/pending`, `/schedules`, `/action-packs` and the
rest redirect to their new homes.

---

## Pages

| Page | Description |
|------|-------------|
| [Overview](dashboard.md) | The landing page — fleet status bar, the Pending queue, activity, drift trend, stale hosts, upcoming schedules |
| [Hosts](hosts.md) | Add and manage hosts; filter by status, group, firewall backend, drift check, overrides; plan a sync for a selection |
| [Discovery](hosts.md#discovery) | One screen: pending approval · scan schedules · scan now (`/discovery`) |
| [Config](groups.md) | Module × scope: every module's fleet lens at `/config/<module>`, a group's editor at `?scope=group:<id>` |
| [Operations](operations.md) | Plans (preview → apply with a URL), Drift findings, Actions library, Runs stream, Audit |
| [SSH Terminal](hosts.md#terminal) | Browser-based SSH terminal into any managed host |
| [Groups](groups.md) | Host groups, priority ordering, and per-module configuration |
| [Firewall Rules](groups.md#firewall-rules) | Inbound/outbound TCP/UDP/ICMP rules per group |
| [Services](groups.md#services) | Systemd service desired state (running/stopped, enabled/disabled) |
| [Packages](groups.md#packages) | System package install/remove/pin and custom repositories |
| [Hosts File](groups.md#hosts-file) | /etc/hosts entries managed by LabDog |
| [Cron Jobs](groups.md#cron-jobs) | Scheduled tasks deployed via Ansible |
| [Linux Users](groups.md#linux-users) | User accounts, SSH keys, sudo rules |
| [DNS Resolver](groups.md#dns-resolver) | Nameservers and search domains (resolv.conf / systemd-resolved / NetworkManager) |
| [CA Certificates](groups.md#ca-certificates) | Deploy trusted CA certificates into hosts' system trust store (per group or per host) |
| [Syncing changes](groups.md#syncing-changes) | Preview-then-apply syncs — per module or all modules at once, per host or per group — with live progress in the global sync tray (v0.2.0+ all syncs route through one per-host orchestrator with PostgreSQL serialisation) |
| [Schedules](scheduled-actions.md) | Cron-driven runs of any action — pack-supplied or built-in — against hosts, groups, or the entire fleet, with snapshot/rollback for destructive actions (a tab of Operations · Actions) |
| [Alerts](alerts.md) | Alerts received from Grafana and Alertmanager, with the AI investigation each one did or did not get |
| [Assistant](assistant.md) | Hand an investigation to a connected LLM; it works through LabDog's tools with every command classified, bounded, and audited |
| [Actions](actions.md) | Ad-hoc playbook runs on hosts or groups; includes snapshot-wrapped destructive actions |
| [Action Packs](actions.md#action-packs) | Configure the pack sources that supply actions (bundled, git, local) — a tab of Operations · Actions |
| [SSH Keys](admin.md#ssh-keys) | Manage SSH private keys used to connect to hosts |
| [Git Repos](gitops-ui.md) | Connect Git repositories for GitOps-driven configuration |
| [Proxmox](settings.md#proxmox-settings) | Connect Proxmox VE nodes (TLS verification, per-node CA certificate) and discover host↔VM mappings for snapshot/rollback |
| [Drift detection](drift-detection.md) | What a drift check does, why it is off by default on every host, and the two independent `drift_check_enabled` flags — host-level (firewall only) versus per-module (the other six) |
| [Grafana](host-metrics.md) | Two directions on one page. **Metrics in:** register a Mimir/Loki (Prometheus-compatible) backend to show instant CPU/memory/disk on the host page; ties into the bundled Alloy install action. **Metrics out:** the [Prometheus scrape endpoint](../metrics-export.md) — status, scrape URL and config snippet |
| [AI Providers](assistant.md#ai-providers) | Connect a local or hosted LLM, set per-token pricing, and cap spend with daily/monthly budgets |
| [Audit Log](admin.md#audit-log) | Append-only record of every change with before/after state |
| [Users](admin.md#users) | LabDog user accounts (superuser only) |
| [Settings](settings.md) | Five sections — Integrations (a registry of what is connected), AI, Access, Fleet defaults, System |
| [About](settings.md#about) | Build metadata — version, commit SHA, build date, license, repo URL (Settings · System) |

---

## Screenshots

All screenshots in this directory were taken from a live development instance and show the actual UI.

- [`screenshots/login.png`](screenshots/login.png)
- [`screenshots/dashboard.png`](screenshots/dashboard.png)
- [`screenshots/hosts.png`](screenshots/hosts.png)
- [`screenshots/discovery.png`](screenshots/discovery.png)
- [`screenshots/groups.png`](screenshots/groups.png)
- [`screenshots/group-detail.png`](screenshots/group-detail.png)
- [`screenshots/group-rules.png`](screenshots/group-rules.png)
- [`screenshots/group-services.png`](screenshots/group-services.png)
- [`screenshots/group-packages.png`](screenshots/group-packages.png)
- [`screenshots/group-hosts-entries.png`](screenshots/group-hosts-entries.png)
- [`screenshots/group-cron-jobs.png`](screenshots/group-cron-jobs.png)
- [`screenshots/group-users.png`](screenshots/group-users.png)
- [`screenshots/group-resolver.png`](screenshots/group-resolver.png)
- [`screenshots/group-sync.png`](screenshots/group-sync.png)
