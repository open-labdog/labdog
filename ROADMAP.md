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

The canonical Ansible action pack. Nothing from it is built into
LabDog: a fresh install registers
[`labdog-playbooks`](https://github.com/open-labdog/labdog-playbooks)
as an ordinary git pack tracking `main` (**Operations → Actions →
Packs**), which syncs on startup. Sites without GitHub access point it
at a mirror or use a local-directory pack.

Currently in it:
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

### VM layer (planned, name open)

A separate project, not started, that would create and destroy
short-lived VMs for LabDog and on its own: backup restore tests and
leased VMs such as a `k8s-upgrade` surge node. See
[Hypervisor context](#hypervisor-context--a-companion-vm-layer).

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

**Later.** A second evidence producer for verify — today there is only
`evidence_from_state` — and with it, storing each verify step's evidence
pack structurally on its session so "which verifications ran without a
disk reading" is a query rather than a transcript grep. Not before: with
one producer there is nothing to compare across. The note for whoever
writes it sits at the seam, in `backend/app/workflows/steps/ai_verify.py`.

**The honest limit.** A verify step judges evidence LabDog collected in a
ten-minute window that includes the action's own work, so it can mistake
a change's own log noise for a fault. Prompt wording mitigates it;
narrowing the window to before the action started is the real fix and is
not built. Nothing here is a substitute for reading what it did.

---

## Hypervisor context — a companion VM layer

**Status.** Explored, nothing built. The direction below replaces the
earlier plan to pass hypervisor credentials to packs.

**Goal.** Two things LabDog cannot do today because it never creates
or destroys a VM:

- **Automatic backup testing.** Restore a VM's newest backup as a
  throwaway copy, boot it with networking cut off, check it, destroy
  it, and keep a pass/fail history per VM. A backup nobody has
  restored is a hope, not a backup.
- **Short-lived VMs for actions.** For example a surge node that
  `k8s-upgrade` leases for one run instead of a spare host someone
  built by hand.

**Why not in packs.** An action runs over SSH against a managed host,
and LabDog's Proxmox credentials never reach a playbook. Driving
`qmrestore` over SSH means making the Proxmox node a managed host,
which hands LabDog a root key to every VM on it and puts the
hypervisor one group membership away from a config sync. Passing an
API token to packs instead (the earlier `hypervisor_context` /
`labdog_hv_*` idea) needs a guard-rail system of its own, and a
killed ansible-runner still skips the playbook's `always:` cleanup.

**Direction: a separate project under `open-labdog`** (name to be
decided) that sits between LabDog and the hypervisor and owns every VM
whose life is short and planned. It must be useful without LabDog,
with its own UI, API tokens and notifications, and LabDog is one of
its clients.

What it owns:

- **Connections** to Proxmox clusters, several in the data model from
  the start (as LabDog's `ProxmoxNode` already allows), with a token
  scoped to one resource pool. A bug can only destroy VMs in that pool.
- **Restore tests.** Policies (which VMs, how often, which checks)
  and run history. Pure Proxmox API calls: restore with `archive=`
  into the pool with `unique=1`, rewrite every NIC to an isolated
  sandbox bridge before first boot, start, check through the QEMU
  guest agent (`agent/exec`, which needs no network), destroy in a
  `finally`. Both Proxmox Backup Server and vzdump file storage go
  through the same restore call; only listing and free-space checks
  differ. A backup is always restored on the cluster it came from.
- **Leases.** A VM made from a named blueprint (an OpenTofu module
  using the `bpg/proxmox` provider) with an owner, a purpose and an
  expiry. OpenTofu rather than Terraform for the licence; state in
  Postgres through the `pg` backend. OpenTofu fits leases, not restore
  tests, which are a sequence of API calls with nothing to declare.
- **A sweeper** that lists the pool on Proxmox and destroys anything
  expired or without a lease. It trusts Proxmox tags, not OpenTofu
  state, so a killed worker, a lost state file or a crashed LabDog
  all end the same way. This is the "cleanup after timeout or
  cancel" the old plan listed as a prerequisite.

Out of scope for it: managing long-lived VMs (Proxmox's UI and
Proxmox Datacenter Manager do that), making backups or modelling
backup schedules, and building templates.

**Boundary with LabDog.** REST calls from LabDog with an API token,
signed webhooks back. Neither reads the other's database, and a VM is
named by `(cluster, vmid)` on both sides, which `VMMapping` already
stores.

- `POST /api/leases` with blueprint, TTL, callback URL and the LabDog
  run id as idempotency key; `lease.ready` carries IP and SSH host key.
  Extend, release, and list by owner after a restart.
- `POST /api/restore-tests/runs` and `GET /api/restore-tests?vm=…`
  for a "last restore test" badge on the host page and an optional
  schedulable `_builtin.restore_test`.
- Every integration is optional in LabDog and appears only when a
  connection to the VM layer is configured.

**What stays in LabDog.** Snapshot, verify and rollback stay direct
against Proxmox. That path runs inside every destructive action,
already retries on locked VMs, and a second service there adds a
failure point without a new capability. It may move behind a
hypervisor provider interface later, once the VM layer has proved
reliable. The stateful-host rollback rules are LabDog's too: they
decide when to revert, not how.

**Surge nodes for `k8s-upgrade`** need two things in LabDog: a
manifest parameter type (say `lease`) that LabDog resolves to a host
before it builds the inventory, and temporary hosts that are left out
of drift and dashboards. One rule matters more than the rest: a failed
run leaves the surge node in the cluster carrying pods, and a re-run
reuses it, so on failure LabDog must extend the lease and mark it
held. A held lease never expires without a person releasing it.

**Other hypervisors** go in the same project, behind a narrow
provider interface (restore, set network, start, run in guest,
destroy, list pool), added only when someone needs one. What justifies
the project — leases, restore policies and history, the sweeper, the
UI, the API LabDog calls — does not depend on the hypervisor, and a
project per hypervisor would copy all of it. Backup sources (PBS,
vzdump, Veeam, …) are a second interface, separate from the
hypervisor one.

**Existing tools.** Proxmox Datacenter Manager covers multi-cluster
management, not restore tests or expiring VMs. PBS verify jobs check
chunk checksums, not that a backup boots. Veeam supports Proxmox VE
through a plug-in, but its SureBackup restore check is not documented
for it. Community restore-test scripts exist and are early. Cluster
API with the Proxmox provider has surge built in, but only for
clusters it created, not an existing kubeadm cluster.

**Phasing.** Each step ships something usable; LabDog changes only in
step 4.

1. Spike: one restore test end to end against a real cluster.
2. The VM layer with restore tests, sweeper, UI and API tokens. No
   OpenTofu yet.
3. Leases: blueprints, the OpenTofu runner, the sandbox network as
   code, webhooks.
4. LabDog integration: connection settings, the host-page badge, the
   `lease` parameter type, temporary hosts, the held-lease rule.
5. Later, only if it has proved reliable: a provider behind LabDog's
   snapshot path.

**Prerequisite in LabDog either way:** action-failure notifications.
A weekly restore test or a held surge lease that fails silently is
worse than none. Listed under the email follow-ups in
[TODO.md](TODO.md).

---

## Scaling out — workers on more than one box

LabDog's load is Ansible on the controller: at a few hundred hosts with
frequent bulk syncs, the CPU of the box running the workers is the limit,
while Postgres, Redis, the API and the scheduler stay lightly loaded
(load tests in PR #270).

**Shipped (PR #270).** One image, run as roles (`api`, `beat`,
`orchestrator`, `worker`, `worker-drift`) with `--role` / `LABDOG_ROLE`;
see [`docs/scaling-out.md`](docs/scaling-out.md). The single-container
`all` install stays the default. Drift checks run on their own queue,
fanned out per host.

**Next.** An actions queue and worker role, so the per-host part of an
action run stops waiting behind bulk syncs ([TODO.md](TODO.md)).

**Later.** Run `worker` containers (and their drift and actions
counterparts) on more than one box against the same Postgres and Redis,
so worker capacity grows with hardware. What it needs:

- SSH reach from every worker box to the managed hosts, and network
  access to Postgres and Redis.
- The same secrets and image version on every box.
- Pack checkouts that reach every box. In one compose project they share
  a volume; across boxes that means a shared filesystem, or workers that
  re-sync a pack when its commit in the database moves ahead of their
  checkout.
- A connection pooler (PgBouncer) once the total slot count grows, at
  ~6 Postgres connections per slot.
- A functional check (a worker on a second box runs syncs and actions)
  and a deployment doc. No further load tests are planned: the
  per-box limit is already understood.

`beat`, the API and the orchestrator stay single instances.

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
