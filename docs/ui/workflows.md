# Update Workflows

**Path:** `/update-workflows` (sidebar) and `/groups/{id}/workflow` (per-group tab)

Update Workflows automate Linux system updates and Kubernetes cluster upgrades on managed hosts. Workflows run Ansible playbooks with optional Proxmox VM snapshotting before each run.

> **Update Workflows vs. [Actions](actions.md):** Update Workflows are
> scheduled and opinionated about upgrade flows. Actions are the
> generic "run this playbook now" primitive — same Ansible pipeline
> and same snapshot-rollback safety net, but triggered manually and
> backed by [action packs](actions.md#action-packs) you can extend
> with your own playbooks.

---

## Workflow Types

| Type | What it does |
|------|-------------|
| **Linux Upgrade** | Runs `apt upgrade` / `dnf upgrade`, optionally takes a Proxmox snapshot first, and reboots if the kernel changed |
| **Kubernetes Upgrade** | Drains node, upgrades kubeadm/kubelet/kubectl to target version, uncordons |

---

## Workflows List

**Path:** `/update-workflows`

Shows all defined workflows. Each row shows:

| Column | Description |
|--------|-------------|
| Name | Workflow label |
| Type | Linux upgrade or Kubernetes upgrade |
| Schedule | Cron expression or interval for automatic runs |
| Last Run | When the workflow last executed |
| Status | `success`, `failed`, `running`, or `never` |

### Creating a Workflow

Click **New Workflow**. Fields vary by type:

**Common:**

| Field | Description |
|-------|-------------|
| Name | Display label |
| Target | Hosts or groups to apply to |
| SSH Key | Key used to connect |
| Snapshot before run | Take a Proxmox VM snapshot before applying (requires Proxmox configured) |
| Schedule | Leave empty for manual-only; or set a cron expression for automatic runs |

**Linux Upgrade specific:**

| Field | Description |
|-------|-------------|
| Reboot if kernel updated | Automatically reboot after a kernel upgrade |
| Reboot timeout | Wait time (seconds) for host to come back after reboot |

**Kubernetes Upgrade specific:**

| Field | Description |
|-------|-------------|
| Target version | Kubernetes version string (e.g. `1.30.1`) |
| Drain timeout | Time to wait for pods to evacuate before forcing |

---

## Group Workflow Tab

**Path:** `/groups/{id}/workflow`

Lists all workflows that target this group and shows their run history. Click **Run Now** on any workflow to trigger an immediate execution.

### Run Detail

**Path:** `/groups/{id}/workflow/runs/{runId}`

Shows live output streamed from the Ansible playbook execution. Each host's output is shown in a collapsible section. The run status updates in real time.

---

## Scheduling

Workflows use cron expressions (same syntax as the Cron Jobs module). The scheduler check interval is configurable in [Settings](settings.md) (`workflow.schedule_check_interval_seconds`, default 60 seconds).

Proxmox snapshots created before a run are cleaned up automatically after the configured max age (`workflow.snapshot_max_age_hours`, default 24 hours).

---

## GitOps

Workflows can be declared in the same per-group YAML as the rest of a
group's configuration. When a group has GitOps enabled, the
`workflow:` section becomes the source of truth and the UI mutation
controls on this page are read-only.

The section is **singleton**: at most one workflow per group, and
omitting (or `null`-ing) the section leaves the existing DB row
untouched. Explicit deletion is not supported via YAML — disable
GitOps on the group first, then delete via the UI.

See [`docs/examples/gitops/modules/workflow.yaml`](../examples/gitops/modules/workflow.yaml)
for a fully-commented example covering every field, including the
`linux-os-upgrade` parameter requirements.
