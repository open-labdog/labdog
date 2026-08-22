# Settings

**Path:** `/settings`

The Settings page controls operational behaviour that can be tuned without restarting LabDog. Values are stored in the database and take effect immediately — no config file edit or service restart required.

> **Note:** Infrastructure settings (database URL, TLS, secrets, rate limits) are set via environment variables or `dev/labdog.toml`. Only the settings below are managed through this page.

Each row shows a one-line description. Where a setting has caveats worth
knowing before you change it — a condition it only applies under, or a
consequence that is not obvious — an **ⓘ** button beside the description opens
them. The Description column below carries the same detail in full, so nothing
is only available behind the button.

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

### Actions

| Setting | Key | Default | Range | Description |
|---------|-----|---------|-------|-------------|
| Preflight reachability check | `actions.preflight_enabled` | `1` (on) | `0` / `1` | When enabled, every per-host action run first performs a bounded SSH liveness probe. A genuinely unreachable host fails in ~25 s with a clear `host unreachable (preflight)` error instead of tying up a worker for the full playbook timeout. Set to `0` to disable if the probe is too aggressive for flaky hosts. |

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

### AI

Every one of these defaults closed. LabDog does nothing with an LLM until
`ai.enabled` is turned on **and** a provider is configured on the
[AI Providers](assistant.md#ai-providers) page. See the
[Assistant guide](assistant.md) for what the feature actually does.

| Setting | Key | Default | Range | Description |
|---------|-----|---------|-------|-------------|
| AI Enabled | `ai.enabled` | `0` (off) | 0 – 1 | Master switch. While off, chat sessions, scheduled AI checks, and AI verification are all skipped. |
| Allow Cloud Providers | `ai.allow_cloud_providers` | `0` (off) | 0 – 1 | Permit providers that send host data outside your network. While off, only local endpoints and the Claude CLI may run. |
| Currency | `ai.currency` | `USD` | USD EUR GBP SEK NOK DKK CHF CAD AUD | Label for costs and budgets. **Formatting only** — LabDog never converts between currencies, so enter provider rates in the same unit you pick here. |

**Spend limits.** Checked before a session starts *and* between steps, so a
long run that crosses a limit stops rather than finishing on credit.

| Setting | Key | Default | Range | Description |
|---------|-----|---------|-------|-------------|
| Daily Budget | `ai.budget_daily` | `0` (unlimited) | 0 – 10000 | Maximum spend per day, in the `ai.currency` unit. |
| Monthly Budget | `ai.budget_monthly` | `0` (unlimited) | 0 – 100000 | Maximum spend per calendar month. |
| Budget Warning | `ai.budget_warn_pct` | `80` | 0 – 100 % | Warn in the UI once this share of any budget is spent (0 = never). |

A local model priced at zero is unaffected by the money budgets — the
per-session caps below still apply.

**Per-session caps.** Bound one session's blast radius and cost. Whichever is
reached first ends the run, and the assistant spends a final turn summarising
what it established, so the work is not wasted.

| Setting | Key | Default | Range | Description |
|---------|-----|---------|-------|-------------|
| Max Iterations | `ai.max_iterations` | `15` | 1 – 100 | Model turns in one session. |
| Max Commands | `ai.max_commands` | `20` | 1 – 200 | Shell commands across all hosts in one session. |
| Max Tokens | `ai.max_tokens_total` | `200000` | 1000 – 5000000 | Prompt + completion tokens in one session. |
| Wall Clock | `ai.wall_clock_seconds` | `900` | 30 – 21600 s | Run time for one session. Time spent waiting for an approval does not count. |

**Alert intake.** Recording alerts and investigating them are separate
switches — recording costs nothing, investigating spends money without
anyone asking. The webhook token lives in the config file rather than
here; see [Alerts](alerts.md).

| Setting | Key | Default | Range | Description |
|---------|-----|---------|-------|-------------|
| Alert Intake | `ai.alert_intake_enabled` | `0` (off) | 0 – 1 | Accept alerts from Grafana and Alertmanager. |
| Alertmanager Poll | `ai.alertmanager_poll_minutes` | `0` (never) | 0 – 1440 min | Poll the default Mimir instance's Alertmanager API, as a fallback for alerts that arrived while LabDog was unreachable. **Only useful when your alert rules live in Mimir's ruler** — Grafana-managed rules go to Grafana's own Alertmanager, which this cannot see, and the poll then records nothing while looking healthy. See [Alerts](alerts.md#alertmanager-poll-catch-up). |
| Auto Investigate | `ai.auto_investigate_enabled` | `0` (off) | 0 – 1 | Start a read-only investigation when an eligible alert arrives. |
| Minimum Severity | `ai.auto_investigate_min_severity` | `critical` | info / warning / critical | Lowest severity that triggers one. An alert whose severity is missing or not one of these is **skipped and says so**, rather than being guessed either way. |

**Changes and approvals.**

| Setting | Key | Default | Range | Description |
|---------|-----|---------|-------|-------------|
| Snapshot Before Change | `ai.snapshot_before_mutating` | `1` (on) | 0 – 1 | Take a Proxmox snapshot before the assistant runs a command that changes a host. Hosts with no VM mapping are unaffected. While on, a snapshot that **fails blocks the command**. |
| Snapshot Retention | `ai.snapshot_retention_days` | `7` | 0 – 365 days | How long to keep those snapshots (0 = forever). They are deliberately *not* deleted when a session succeeds — the point of them is that you can undo the change after reading what it did, and this is how long "afterwards" lasts. |
| Approval Expiry | `ai.approval_expiry_hours` | `24` | 1 – 720 hours | How long an approval request waits before lapsing. The session is then told the change was not approved and finishes with a report, rather than sitting parked forever. |

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
