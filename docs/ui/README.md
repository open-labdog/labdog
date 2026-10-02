# LabDog UI Guide

A walkthrough of every page in the LabDog web interface.

## Navigation

Navigation is an icon rail of four zones with a pane of destinations beside
it. The rail never grows: new destinations go into a zone's pane, or into
the command palette (`⌘K` / `Ctrl+K`), which indexes every destination,
every host, every group, every module × group pair, and a handful of verbs.
Configuration has no zone of its own: a module's desired state is edited on
the page of the group that declares it, and a host's effective state on the
host's page.

| Zone | Pane |
|------|------|
| **Overview** | Summary · Pending · Fleet state · Activity · Upcoming |
| **Fleet** | Hosts · Groups · Discovery |
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
| [Hosts](hosts.md) | Add and manage hosts; filter by status, group, firewall backend, drift check, overrides |
| [Discovery](hosts.md#discovery) | One screen: pending approval · scan schedules · scan now (`/discovery`) |
| [Operations](operations.md) | Plans (preview → apply with a URL), Drift findings, Actions library, Runs stream, Audit |
| [SSH Terminal](hosts.md#terminal) | Browser-based SSH terminal into any managed host |
| [Groups](groups.md) | Host groups in priority order; each group's own page — Overview · Config · Members · Activity — is where it is edited |
| [Firewall Rules](groups.md#firewall-rules) | Inbound/outbound TCP/UDP/ICMP rules per group |
| [Services](groups.md#services) | Systemd service desired state (running/stopped, enabled/disabled) |
| [Packages](groups.md#packages) | System package install/remove/pin and custom repositories |
| [Hosts File](groups.md#hosts-file) | /etc/hosts entries managed by LabDog |
| [Cron Jobs](groups.md#cron-jobs) | Scheduled tasks deployed via Ansible |
| [Linux Users](groups.md#linux-users) | User accounts, SSH keys, sudo rules |
| [DNS Resolver](groups.md#dns-resolver) | Nameservers and search domains (resolv.conf / systemd-resolved / NetworkManager) |
| [CA Certificates](groups.md#ca-certificates) | Deploy trusted CA certificates into hosts' system trust store (per group or per host) |
| [Syncing changes](groups.md#syncing-changes) | Preview-then-apply syncs on the Plans screen — per module or all modules, per host or per group — with live progress in the global sync tray; every sync goes through one per-host orchestrator, so two never race on a host |
| [Schedules](scheduled-actions.md) | Cron-driven runs of any action — pack-supplied or built-in — against hosts, groups, or the entire fleet, with snapshot/rollback for destructive actions (a tab of Operations · Actions) |
| [Alerts](alerts.md) | Alerts received from Grafana and Alertmanager, with the AI investigation each one did or did not get |
| [Email](notifications.md) | `/notifications` — the mail server, and which alerts, approvals and automatic fixes each person is emailed about (Settings · Integrations) |
| [Assistant](assistant.md) | Hand an investigation to a connected LLM; it works through LabDog's tools with every command classified, bounded, and audited |
| [Actions](actions.md) | Ad-hoc playbook runs on hosts or groups; includes snapshot-wrapped destructive actions |
| [Action Packs](actions.md#action-packs) | Configure the pack sources that supply actions (bundled, git, local) — a tab of Operations · Actions |
| [SSH Keys](admin.md#ssh-keys) | Manage SSH private keys used to connect to hosts |
| [Git Repos](gitops-ui.md) | Connect Git repositories for GitOps-driven configuration |
| [Proxmox](settings.md#proxmox-settings) | `/hypervisors` — connect Proxmox VE nodes (TLS verification, per-node CA certificate) and discover host↔VM mappings for snapshot/rollback |
| [Drift detection](drift-detection.md) | What a drift check does, why it is off by default on every host, and the two independent `drift_check_enabled` flags — host-level (firewall only) versus per-module (the other six) |
| [Grafana](host-metrics.md) | Two directions on one page. **Metrics in:** register a Mimir/Loki (Prometheus-compatible) backend to show instant CPU/memory/disk on the host page; ties into the bundled Alloy install action. **Metrics out:** the [Prometheus scrape endpoint](../metrics-export.md) — status, scrape URL and config snippet |
| [AI Providers](assistant.md#ai-providers) | Connect a local or hosted LLM, set per-token pricing, and cap spend with daily/monthly budgets |
| [Audit Log](operations.md#audit) | Append-only record of every change, with SSH session transcripts |
| [Users](admin.md#users) | LabDog user accounts (superuser only) |
| [Settings](settings.md) | Five sections — Integrations (a registry of what is connected), AI, Access, Fleet defaults, System |
| [About](settings.md#about) | Build metadata — version, commit SHA, build date, license, repo URL (Settings · System) |

---

## Screenshots

The screenshots in this directory show the current UI in the dark theme,
taken against a development build with sample data (a 24-host homelab), not
a live fleet — hostnames, addresses and counts are illustrative.

| Screenshot | Shows |
|------------|-------|
| [`login.png`](screenshots/login.png) | Sign-in |
| [`overview.png`](screenshots/overview.png) | Overview — status bar, Pending, activity, drift trend |
| [`hosts.png`](screenshots/hosts.png) | Hosts list |
| [`host-detail.png`](screenshots/host-detail.png) | A host's Config tab — effective firewall with where each rule comes from |
| [`discovery.png`](screenshots/discovery.png) | Discovery — scan schedules |
| [`hosts-discover.png`](screenshots/hosts-discover.png) | Discovery — scan now, with results |
| [`groups.png`](screenshots/groups.png) | Groups list, in priority order |
| [`group-detail.png`](screenshots/group-detail.png) | A group's Overview tab |
| [`group-rules.png`](screenshots/group-rules.png) | Group Config — firewall |
| [`group-services.png`](screenshots/group-services.png) | Group Config — services |
| [`group-packages.png`](screenshots/group-packages.png) | Group Config — packages and repositories |
| [`group-hosts-entries.png`](screenshots/group-hosts-entries.png) | Group Config — hosts file |
| [`group-cron-jobs.png`](screenshots/group-cron-jobs.png) | Group Config — cron |
| [`group-users.png`](screenshots/group-users.png) | Group Config — users and groups |
| [`group-resolver.png`](screenshots/group-resolver.png) | Group Config — DNS resolver |
| [`plan.png`](screenshots/plan.png) | Plan review before apply |
| [`assistant.png`](screenshots/assistant.png) | An assistant session paused at an approval gate |
