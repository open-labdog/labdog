# Settings

**Path:** `/settings`

The Settings page controls operational behaviour that can be tuned without restarting LabDog. Values are stored in the database and take effect immediately — no config file edit or service restart required.

> **Note:** Infrastructure settings (database URL, TLS, secrets, rate limits) are set via environment variables or `dev/labdog.toml`. Only the settings below are managed through this page.

---

## Settings Reference

### Logging

| Setting | Key | Default | Range | Description |
|---------|-----|---------|-------|-------------|
| Log Level | `logging.level` | `info` | `debug` `info` `warning` `error` `critical` | Application log verbosity. Use `debug` to trace API calls and task execution. Use `warning` or higher in production to reduce noise. |
| Audit Retention | `logging.audit_retention_days` | `90` | 1 – 3650 days | How many days to keep audit log entries. Entries older than this are purged automatically. Set to `3650` (10 years) to keep effectively forever. |

---

### Drift Detection

| Setting | Key | Default | Range | Description |
|---------|-----|---------|-------|-------------|
| Check Interval | `drift.check_interval_minutes` | `30` | 1 – 1440 min | How often the scheduled drift check runs across all hosts that have drift detection enabled. Lower values catch drift faster but increase SSH and CPU load. |

---

### SSH

| Setting | Key | Default | Range | Description |
|---------|-----|---------|-------|-------------|
| Connect Timeout | `ssh.connect_timeout` | `10` | 1 – 120 sec | How long to wait when opening an SSH connection to a host (used for sync, drift checks, and state collection). Increase if managed hosts are on high-latency links. |
| Idle Timeout | `ssh.idle_timeout_seconds` | `1800` | 60 – 86400 sec | How long a web terminal session can be idle before it is automatically disconnected. 1800 = 30 minutes. |

---

### Ansible

| Setting | Key | Default | Range | Description |
|---------|-----|---------|-------|-------------|
| Playbook Timeout | `ansible.playbook_timeout` | `300` | 30 – 3600 sec | Maximum time allowed for a single Ansible playbook run. If a sync exceeds this, the run is killed and marked as failed. Increase for large host fleets or slow package installs. |

---

### Discovery

| Setting | Key | Default | Range | Description |
|---------|-----|---------|-------|-------------|
| Scan Timeout | `discovery.scan_timeout` | `1.0` | 0.1 – 30.0 sec | Per-host TCP connect timeout during a network scan. Lower values speed up scans but miss hosts on slow links. |
| Max Concurrent | `discovery.max_concurrent` | `100` | 1 – 1000 | Maximum simultaneous TCP probes during a network scan. Reduce if your network drops packets under high connection rates. |

---

### Update Workflows

| Setting | Key | Default | Range | Description |
|---------|-----|---------|-------|-------------|
| Schedule Check Interval | `workflow.schedule_check_interval_seconds` | `60` | 10 – 300 sec | How often the scheduler checks whether any workflow is due to run. Lower = more responsive scheduling; higher = less DB load. |
| Snapshot Max Age | `workflow.snapshot_max_age_hours` | `24` | 1 – 168 hours | Proxmox VM snapshots taken before an update are automatically cleaned up after this many hours if the workflow completes successfully. |

---

## Resetting a Setting

Every setting shows its current value alongside the default. Click **Reset to default** on any row to revert that setting to its built-in default value.

---

## Proxmox Settings

**Path:** `/settings/proxmox`

Manages the connection to one or more Proxmox hypervisor nodes. See [Proxmox integration](../README.md) for setup details.
