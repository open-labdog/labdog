# Roadmap

What LabDog is planning, considering, and deliberately not building.
Concrete near-term tasks live in [TODO.md](TODO.md). This file is the
high-altitude view.

---

## Companion repos

LabDog ships a small core. Pluggable behaviour lives in companion
repos so the application image stays slim and operators can mix and
match.

### `labdog-playbooks`

The canonical Ansible action pack — added through **Integrations →
Action Packs** with role `Default`. The directory at
`backend/app/ansible/` in this repo is a byte-identical mirror of
[`labdog-playbooks`](https://github.com/open-labdog/labdog-playbooks)
baked into the container image as an offline fallback.

Currently bundled:
- `linux-upgrade` — apt/dnf system package upgrade with optional reboot
- `linux-os-upgrade` — Debian major-version upgrade (e.g. 12 → 13)
  with NIC-rename safety
- `k8s-upgrade` — `kubeadm`-based drain-upgrade-uncordon runbook
  dispatched against a labdog group containing every cluster node.
  The playbook self-discovers control-plane vs worker by probing each
  node — no per-member roles in labdog. Currently apt-only;
  broadening to `dnf` is tracked in [TODO.md](TODO.md).
- `alloy-install` (**`grafana-alloy`**) — installs and configures
  [Grafana Alloy](https://grafana.com/oss/alloy/) on Debian/Ubuntu
  (and Windows) hosts: GPG/repo setup, package install (apt or local
  file), config templating with group-based overlays, optional service
  detection (docker, mysql, postgresql, custom), and systemd / Windows
  service management. Stamps the `labdog_host_id` label and closes the
  loop with the **Integrations → Grafana** page — registered Mimir/Loki
  instances auto-populate the Alloy remote-write / Loki push URLs, so
  live host metrics appear on the host Overview tab with no free-text
  endpoint entry. Remaining follow-ups (per-host metrics backend
  routing, Loki log surfacing) are tracked in [TODO.md](TODO.md).

---

## Ideas — exploration welcome

Direction signals, not commitments. To pursue any of these, branch
from `dev`, scratch the design under `plans/` (see
[CONTRIBUTING.md](CONTRIBUTING.md)), and add a [TODO.md](TODO.md)
entry for the work itself.

| Idea | Notes |
|------|-------|
| **Notification system** | Email/webhook/Slack alerts on drift detection, sync failures, certificate expiry |
| **API tokens** | Non-cookie auth for CI/CD integration or scripting against the LabDog API |
| **Host tagging & filtering** | Tags beyond groups for flexible organisation (e.g. `region:eu`, `env:prod`) |
| **Import/export configuration** | Backup and restore group configs, rules, service definitions |
| **CLI tool** | Command-line client for power users who prefer terminal over UI. `labdog-lint` ships today as a YAML validator; the open piece is a general API-driving CLI. |
| **Ansible playbook export** | Export LabDog's desired state as standalone Ansible playbooks (escape hatch) |
| **APT/YUM repository hosting** | First-class managed repository server alongside the package module |
| **Visualise rule calculation** | UI showing how the effective per-host rule list is derived (which group contributed each rule, which conflicts were resolved, which host override won) |
| **Module enable/disable per group** | `enabled_modules` list on `HostGroup` to control which modules apply — useful for groups that should only manage firewall, etc. |

---

## Out of scope

LabDog deliberately does not aim to be these things. Adjacent tools
do them better; we'd rather integrate than replace.

- **Puppet/Chef/Salt replacement** — LabDog is opinionated and UI-first.
- **Container orchestration** — Kubernetes/Nomad territory.
- **Application deployment** — use CI/CD (Argo, Flux, GitHub Actions).
- **Monitoring/alerting** — use Prometheus/Grafana/Loki.
- **Secret management** — use Vault/SOPS.
- **Sysctl / kernel parameter management** — too niche, low demand.
- **Arbitrary file/directory management** — too broad, overlaps with
  Ansible itself. If you need it, write a pack.
