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

The canonical Ansible action pack. Every build (image, `.deb` /
`.rpm` / `.tar.gz`, dev) clones
[`labdog-playbooks`](https://github.com/open-labdog/labdog-playbooks)
into `backend/app/ansible/` at the SHA pinned in
[`LABDOG_PLAYBOOKS_REF`](LABDOG_PLAYBOOKS_REF), as the always-present
bundled pack. A fresh install also registers it as a DB-backed pack
tracking `main` (**Operations → Actions → Packs**), so deployed
instances pick up newer playbooks than the image carries.

Currently bundled:
- `linux-upgrade` — apt/dnf system package upgrade with optional reboot
- `linux-os-upgrade` — Debian major-version upgrade (e.g. 12 → 13)
  with NIC-rename safety
- `example` / `example-ai-verify` — harmless probes that exercise the
  full snapshot → run → verify → rollback lifecycle without changing
  anything. The first decides success with a verify playbook, the
  second with LabDog's AI verify step, so the two demonstrate the
  alternatives side by side.
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
  loop with the **Settings › Integrations › Grafana** page — registered Mimir/Loki
  instances auto-populate the Alloy remote-write / Loki push URLs, so
  live host metrics appear on the host Overview tab with no free-text
  endpoint entry. Remaining follow-ups (per-host metrics backend
  routing, Loki log surfacing) are tracked in [TODO.md](TODO.md).

---

## Agentic administration

The largest thing LabDog has shipped, and the one most worth stating
plainly: an LLM can now investigate hosts, and — when you allow it —
change them.

**Shipped.** Provider connections (OpenAI-compatible, Anthropic, Claude
CLI, Claude Agent SDK), the agent loop with a default-deny command
classifier, read-only investigation from a chat page, scheduled
unattended checks, approval-gated changes that park without holding a
worker or a host lock, snapshot-before-change, full-auto for operators
who want it, cost accounting with enforced budgets, and AI verification
of destructive actions. See [`docs/ui/assistant.md`](docs/ui/assistant.md).

Alert intake shipped alongside it: a Grafana contact-point webhook and an
Alertmanager poller, deduplicated on (fingerprint, start time), spawning
a read-only session under a severity policy — with whichever gate stopped
an investigation recorded on the alert rather than left to be guessed.

**Next.** `propose_action`, which would let the assistant ask for a
named, vetted actionpack rather than a raw shell command. Two design
problems block it, both recorded in [TODO.md](TODO.md).

**The honest limit.** A verify step judges evidence LabDog collected in a
ten-minute window that includes the action's own work, so it can mistake
a change's own log noise for a fault. Prompt wording mitigates it;
narrowing the window to before the action started is the real fix and is
not built. Nothing here is a substitute for reading what it did.

---

## Hypervisor context — automatic backup testing

**Goal.** A scheduled action that proves a VM's backups restore: pick
the VM (or a group of them), and LabDog restores its newest backup as a
throwaway copy, boots it with networking cut off, runs checks
inside it, and destroys it — reporting a pass/fail per VM in the run
history. A backup nobody has restored is a hope, not a backup.

**Why packs can't do this today.** An action runs over SSH against a
managed host, and LabDog's Proxmox credentials never reach a playbook.
The only route is to make the Proxmox node itself a managed host and
drive `qmrestore` over SSH — which hands LabDog a root key to every VM
on the node, loses the link to the VM being tested, puts the hypervisor
one group membership away from a config sync, and does not extend to
hypervisors that are only reachable through an API (ESXi, Hyper-V,
Nutanix, cloud).

**Direction.** Give actions the *hypervisor* as context instead of
making it a target:

- **Core: a hypervisor abstraction.** Generalise `ProxmoxNode` /
  `VMMapping` into a connection with a `kind` (Proxmox first; libvirt,
  XCP-ng, VMware later) and an opaque VM reference, with a provider
  interface for what LabDog itself does — snapshot, rollback, status,
  power, VM discovery. The snapshot/verify/rollback envelope then works
  on any supported hypervisor. (The `/hypervisors` page already exists;
  today it is the Proxmox screen.)
- **Packs: context, not logic.** A manifest declares
  `hypervisor_context: required`; LabDog injects the target's VM mapping
  and connection as `labdog_hv_*` vars, and can skip SSH to the target
  entirely (`host_access: none`), so a dead VM can still have its backup
  tested. API-based kinds get credentials; SSH-based kinds (libvirt) get
  the hypervisor as a delegate host. The pack branches per
  `labdog_hv_kind`. Backup systems (PBS, vzdump, Veeam, …) vary even
  more than hypervisors, so restore logic stays in packs.
- **Credential guard rails.** A separate, narrower credential per
  connection for pack use — on Proxmox, a token scoped to a resource
  pool, so even a broken playbook can only destroy throwaway VMs; an
  audited per-pack opt-in, so a new commit to a tracked pack cannot
  quietly acquire it; secrets redacted from run output and kept off disk.

**Prerequisites** that matter more for an unattended test than for
anything run by hand:
- **Action-failure notifications.** A weekly restore test that fails
  silently is worse than none. Listed under the email follow-ups in
  [TODO.md](TODO.md).
- **Cleanup after timeout or cancel.** Killing ansible-runner skips the
  playbook's `always:` block and leaves the restored VM behind; LabDog
  needs a cleanup hook that runs regardless, plus a sweep for tagged
  leftovers.

**Not planned:** modelling backup jobs or schedules in LabDog. It tests
that backups restore; the backup system stays the source of truth for
making them.

---

## Ideas — exploration welcome

Direction signals, not commitments. To pursue any of these, branch
from `dev`, scratch the design under `plans/` (see
[CONTRIBUTING.md](CONTRIBUTING.md)), and add a [TODO.md](TODO.md)
entry for the work itself.

| Idea | Notes |
|------|-------|
| **Notification system** | Email ships for alerts, approvals and automatic fixes ([Email notifications](docs/ui/notifications.md)). Open: the same for drift detection, sync failures and certificate expiry, and webhook/Slack channels — see TODO.md |
| **API tokens** | Non-cookie auth for CI/CD integration or scripting against the LabDog API |
| **Host tagging & filtering** | Tags beyond groups for flexible organisation (e.g. `region:eu`, `env:prod`) |
| **Import/export configuration** | Import shipped as GitOps: a group bound to a YAML file in a git repo takes it as desired state on every push ([GitOps UI](docs/ui/gitops-ui.md)). Open: the reverse direction — exporting a group's current config as that YAML — and backup/restore of LabDog's own settings |
| **CLI tool** | Command-line client for power users who prefer terminal over UI. `labdog-lint` ships today as a YAML validator; the open piece is a general API-driving CLI. |
| **Ansible playbook export** | Export LabDog's desired state as standalone Ansible playbooks (escape hatch) |
| **APT/YUM repository hosting** | First-class managed repository server alongside the package module |
| **Visualise rule calculation** | Partly shipped: every host config tab lists the effective entries with where each comes from (group and priority, host override, or system), and the firewall tab names the group that set each default policy. Open: showing the candidates that *lost* — which conflicts were resolved and what a host override replaced |
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
