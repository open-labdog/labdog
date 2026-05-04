# LabDog

## *A homelabber's best friend.*

Centralized Linux configuration management via Ansible. Manage firewall rules, systemd services, `/etc/hosts`, and more from a web UI — preview changes before applying, detect drift, and sync to hosts over SSH.

## Features

- **Firewall management**: nftables and iptables — same rules, either backend
- **Service management**: Declare systemd service states (running/stopped, enabled/disabled), manage unit files (full deploy or drop-in overrides via `systemctl edit`), sync via Ansible, detect drift, orphan cleanup
- **/etc/hosts management**: Manage host file entries with full-file rendering, system entry protection, and file preview
- **Package management**: Declare package states (installed/absent) via apt/dnf/yum (auto-detected)
- **Linux user management**: System users, SSH authorized_keys, sudo rules, and group membership
- **Cron job management**: Declarative cron scheduling with diff-based sync
- **DNS resolver management**: Configure resolv.conf, systemd-resolved, or NetworkManager DNS settings
- **Host discovery**: Scan network ranges for SSH-reachable hosts, bulk-add with SSH verification
- **Proxmox integration**: Discover and manage VMs/containers from Proxmox VE hypervisors
- **GitOps**: Import configuration from Git repositories with webhook-driven sync
- **Web shell**: Browser-based SSH terminal via xterm.js + WebSocket + asyncssh
- **Plan-before-apply**: Preview exact changes before syncing to remote hosts
- **Coalesced sync**: One Ansible playbook per host covering every requested module — bulk-sync a host or per-tab single-module sync, both run through the same per-host orchestrator with PostgreSQL-backed serialisation. No SSH races between concurrent module syncs.
- **SSH lockout prevention**: Auto-injected system rule ensures SSH access is never accidentally blocked
- **Drift detection**: Periodic and manual checks for out-of-sync hosts across all modules
- **Audit trail**: Append-only log of all actions with before/after state
- **Priority-based merge**: Groups with higher priority override lower ones on shared hosts; host-level overrides replace group rules
- **Protected service deny-list**: Critical services (sshd, systemd-*) blocked from accidental management
- **Action packs (BYO playbooks)**: Extend the built-in action catalog by pointing LabDog at a git repo or a local directory; credentials encrypted at rest, overrides resolved by semantic role (default / override / local), provenance surfaced in the UI
- **Update Workflows**: Schedule per-group action runs (linux-upgrade, linux-os-upgrade, k8s-upgrade, or any pack-supplied action) on a cron cadence with optional Proxmox snapshot, verify, and auto-rollback

## Documentation

Everything technical — installation, configuration, architecture, API surface, local development, testing, and the GitOps / precedence guides — lives under [`docs/`](docs/).

Start with the documentation index:

**→ [docs/README.md](docs/README.md)**

Direct entry points:

- [How configuration is applied](docs/README.md#how-configuration-is-applied) — groups, priorities, host overrides, sync semantics
- [Installation](docs/README.md#installation) — .deb / .rpm / tarball / Docker
- [Local development](docs/README.md#local-development) — `dev.sh`, manual setup, Celery
- [API endpoints](docs/README.md#api-endpoints) — full REST surface
- [GitOps guide](docs/examples/gitops/README.md) — webhook setup + YAML schema for every module
- [Precedence](docs/examples/precedence/README.md) — worked examples for multi-group merges
- [Actions & Action Packs](docs/ui/actions.md) — ad-hoc playbook runs and bring-your-own pack guide
- [Action-pack examples](docs/examples/action-packs/README.md) — three working starter packs
- [Update Workflows](docs/ui/workflows.md) — scheduling Linux/Kubernetes/custom upgrades with snapshot + rollback

## License

LabDog is licensed under the GNU Affero General Public License v3.0 or
later (**AGPL-3.0-or-later**). See [LICENSE](LICENSE) for the full
text.

The AGPL's network-use clause means anyone running a modified version
of LabDog as a service must make the source of their modifications
available to its users. Private internal deployments without
modifications carry no extra obligations beyond those of any
AGPL-licensed program.

Copyright © 2026 Dennis Tyresson and contributors.
