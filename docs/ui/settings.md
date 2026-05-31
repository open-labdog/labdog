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

### Schedules

| Setting | Key | Default | Range | Description |
|---------|-----|---------|-------|-------------|
| Snapshot Max Age | `workflow.snapshot_max_age_hours` | `24` | 1 – 168 hours | Proxmox VM snapshots taken before a destructive scheduled action are automatically cleaned up after this many hours when the action completes successfully. |

---

## Resetting a Setting

Every setting shows its current value alongside the default. Click **Reset to default** on any row to revert that setting to its built-in default value.

---

## Proxmox Settings

**Path:** `/settings/proxmox`

Manages the connection to one or more Proxmox hypervisor nodes. See [Proxmox integration](../README.md) for setup details.

Beyond the per-node connection settings, the page exposes a **Discover VM Mappings** action that scans every configured node and links each LabDog host to its backing Proxmox VM/CT in one pass. Individual host↔VM mappings can also be discovered from a [host's detail page](hosts.md#proxmox-vm-mapping).

### TLS verification

Each node has two TLS-related fields that together decide how LabDog
validates the node's HTTPS certificate when it calls the Proxmox API:

- **Verify SSL certificate** — when unchecked, LabDog performs **no**
  certificate validation at all. This is the last-resort escape hatch
  for a node with a certificate LabDog can't otherwise trust; prefer a
  CA certificate (below) instead.
- **CA certificate (PEM)** — paste a PEM-encoded certificate to verify
  the node against it, instead of the operating system's trust store.
  Use this for nodes with a **private-CA** or **self-signed**
  certificate so verification stays on. The field is shown only while
  *Verify SSL certificate* is checked.

| Verify SSL | CA certificate | Result |
|---|---|---|
| Off | (ignored) | No verification — accepts any certificate. |
| On | Set | Verify against the pasted certificate only. |
| On | Empty | Verify against the system trust store (default). |

Notes:

- The field accepts either a real CA certificate **or** a self-signed
  node (leaf) certificate — paste whichever the node presents, and it
  becomes the trust anchor for that node.
- Hostname checking stays on. The certificate's Subject Alternative
  Name (SAN) must match the host in the node's **API URL**, or
  verification fails even with the right certificate uploaded.
- The certificate is **not** a secret and is stored as-is (unencrypted)
  — CA certificates are public. The page never displays the pasted PEM
  back; it shows only whether a certificate is configured and its
  SHA-256 fingerprint.
- To replace a configured certificate, paste a new PEM and save. To
  remove it (returning the node to system-trust-store verification),
  use **Clear CA**.

---

## About

**Path:** `/settings/about`

Build metadata for the running LabDog instance. The page reads
from `GET /api/version` (a public endpoint — no authentication
required) and renders:

| Field | Source |
|---|---|
| Version | `VERSION` file at the repo root, baked into `backend/pyproject.toml` and `frontend/package.json` by the release pipeline. Read at runtime via `importlib.metadata.version("labdog-backend")`. |
| Commit SHA | The git commit the image / package was built from. Set via the `GIT_SHA` Docker build arg or the `bake-build-info` Makefile target (writes `app/_build_info.py`). Falls back to the `LABDOG_COMMIT_SHA` env var. `null` on a dev install with neither source populated. |
| Build date | ISO 8601 timestamp of the build. Set via `BUILD_DATE` build arg / Makefile target / env var. `null` when not provided. |
| License | `AGPL-3.0-or-later` (constant). |
| Repository URL | Upstream project URL (constant). |

The page also exposes a one-click copy of a "support line" suitable
for pasting into bug reports — `version` + short SHA + build date
in one string. Use this when filing issues so maintainers know
exactly what build you're on.

For operators automating health checks, hit `/api/version` directly
rather than scraping this page — it returns the same data as JSON.
