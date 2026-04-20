# Barricade UI Guide

A walkthrough of every page in the Barricade web interface.

## Navigation

The sidebar is always visible on desktop (collapses on mobile). It has three sections:

| Section | Items |
|---------|-------|
| **Manage** | Dashboard, Hosts, Groups, Update Workflows |
| **Integrations** | SSH Keys, Git Repos, Proxmox |
| **Admin** | Users, Audit Log, Settings |

Your email, a **Change Password** link, and a **Log Out** button sit at the bottom of the sidebar.

---

## Pages

| Page | Description |
|------|-------------|
| [Dashboard](dashboard.md) | Fleet-wide health overview — host counts, drift/sync status, quick-check |
| [Hosts](hosts.md) | Add and manage hosts; view per-host sync status and firewall backend |
| [Host Discovery](hosts.md#discovery) | Scan a CIDR range to find SSH-reachable hosts |
| [SSH Terminal](hosts.md#terminal) | Browser-based SSH terminal into any managed host |
| [Groups](groups.md) | Host groups, priority ordering, and per-module configuration |
| [Firewall Rules](groups.md#firewall-rules) | Inbound/outbound TCP/UDP/ICMP rules per group |
| [Services](groups.md#services) | Systemd service desired state (running/stopped, enabled/disabled) |
| [Packages](groups.md#packages) | System package install/remove/pin and custom repositories |
| [Hosts File](groups.md#hosts-file) | /etc/hosts entries managed by Barricade |
| [Cron Jobs](groups.md#cron-jobs) | Scheduled tasks deployed via Ansible |
| [Linux Users](groups.md#linux-users) | User accounts, SSH keys, sudo rules |
| [DNS Resolver](groups.md#dns-resolver) | Nameservers and search domains (resolv.conf / systemd-resolved / NetworkManager) |
| [Sync](groups.md#sync) | Preview and apply desired state to hosts |
| [Update Workflows](workflows.md) | Scheduled Linux and Kubernetes upgrade automation |
| [SSH Keys](admin.md#ssh-keys) | Manage SSH private keys used to connect to hosts |
| [Git Repos](gitops-ui.md) | Connect Git repositories for GitOps-driven configuration |
| [Audit Log](admin.md#audit-log) | Append-only record of every change with before/after state |
| [Users](admin.md#users) | Barricade user accounts (superuser only) |
| [Settings](settings.md) | Operational tuning — log level, timeouts, drift interval |

---

## Screenshots

All screenshots in this directory were taken from a live development instance and show the actual UI.

- [`screenshots/login.png`](screenshots/login.png)
- [`screenshots/dashboard.png`](screenshots/dashboard.png)
- [`screenshots/hosts.png`](screenshots/hosts.png)
- [`screenshots/hosts-discover.png`](screenshots/hosts-discover.png)
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
