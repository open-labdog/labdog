# Update Workflows

**Path:** `/update-workflows` (sidebar) and `/groups/{id}/workflow` (per-group tab)

Update Workflows are scheduled, per-group runs of an
[Action](actions.md). Each group can have at most one workflow; the
workflow picks an action (e.g. `linux-upgrade`, `linux-os-upgrade`,
`k8s-upgrade`, or any pack-supplied action) and runs it across the
group's hosts on a cron schedule with optional Proxmox snapshot,
verify, and rollback.

> **Update Workflows vs. [Actions](actions.md):** Workflows are the
> *scheduled* surface — they pick an action and run it on a group on
> a cadence. Actions are the *primitive* — a playbook + manifest you
> can run ad-hoc against a host (or via a workflow). Same Ansible
> pipeline, same snapshot/verify/rollback safety net.

---

## Workflows list

**Path:** `/update-workflows`

Shows every group that has a workflow configured, with status,
schedule, and last-run summary. Click any row to jump to the
group's Workflow tab.

| Column | Description |
|--------|-------------|
| Group | Group name + category |
| Status | `enabled` or `disabled` |
| Schedule | Cron expression (or empty for manual-only) |
| Hosts | Number of hosts in the group |
| Batch | Hosts updated per batch |
| Options | Snapshot / rollback / reboot toggles |
| Last Run | Status + timestamp of the latest run |

A group with no workflow row simply doesn't appear here. Configure
one from the group's **Workflow** tab.

---

## Group Workflow tab

**Path:** `/groups/{id}/workflow`

The form has the live action picker, parameter inputs, schedule,
batching, snapshot/rollback/reboot toggles, and the recent-runs
table. Configuration changes save on **Save**; **Run Now** triggers
an immediate execution and is enabled only when **Enabled** is on
and no run is currently active.

### Configuration

| Field | Description |
|-------|-------------|
| Batch Size | Number of hosts to update simultaneously (default 1 = sequential) |
| Schedule (Cron) | Cron expression for scheduled runs. Leave blank for manual-only. Validated by `croniter` at save time. |
| Action | Dropdown of every registered action — bundled (`linux-upgrade`, `linux-os-upgrade`, `k8s-upgrade`) plus any DB-configured packs. Selection updates the parameter inputs below. |
| Action parameters | Per-action inputs derived from the manifest. Required parameters get a red `*`; e.g. `linux-os-upgrade` requires `current_version` + `next_version`. |
| Pre-update Snapshot | Take a Proxmox snapshot before each host's run (requires a VM mapping for the host). |
| Auto Rollback | Restore from snapshot on failure. |
| Auto Reboot | Reboot hosts as part of the playbook when needed (e.g. kernel update). |
| Enabled | Allows scheduled and manual runs. Off by default — turn on when you're ready for the workflow to fire. |
| Verification Prompt | Optional free-text shown alongside the run output for human review. |

### Run history

Below the configuration form, **Recent Runs** lists the workflow's
runs with status, started/completed timestamps, and triggered-by
("Manual" vs "Scheduled"). Click any row for the per-host detail
view.

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
