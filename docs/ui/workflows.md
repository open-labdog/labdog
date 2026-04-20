# Update Workflows

**Path:** `/update-workflows` (sidebar) and `/groups/{id}/workflow` (per-group tab)

Update Workflows automate Linux system updates and Kubernetes cluster upgrades on managed hosts. Workflows run Ansible playbooks with optional Proxmox VM snapshotting before each run.

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
