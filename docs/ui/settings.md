# Settings

**Path:** `/settings` — the gear at the foot of the rail

Settings has five sections, each a tab with its own URL
(`/settings?section=ai` …):

| Section | What is in it |
|---------|---------------|
| **Integrations** | A registry of what LabDog is connected to — Proxmox, Grafana / Mimir / Loki, Git remotes, AI providers, the Prometheus export and the inbound webhooks — each with its state and a link to configure it. |
| **AI** | The AI providers, then every `ai.*` setting. See [AI](#ai). |
| **Access** | [SSH keys](admin.md#ssh-keys), [Users](admin.md#users) (superusers only) and your own account — change password, sign out. |
| **Fleet defaults** | How LabDog talks to hosts: drift checks, SSH, Ansible, actions, workflows and discovery. |
| **System** | Logging and retention, the [Prometheus export](../metrics-export.md) and [About](#about). |

The settings in the sections below are stored in the database and take
effect immediately — no config file edit or restart.

> **Note:** Infrastructure settings (database URL, TLS, secrets, rate
> limits, discovery's largest scan range, terminal session caps) are set in
> `labdog.toml` or environment variables. Only the settings below are
> managed here.

Each row shows a one-line description, the setting's key, its range and its
default, and the control to change it — a toggle, a select or a field, with
**Save** (or Enter). Where a setting has caveats worth knowing before you
change it — a condition it only applies under, or a consequence that is not
obvious — a **why** disclosure under the description opens them. The
Description column below carries the same detail in full, so nothing is only
available behind the disclosure.

The AI settings are a long list, so they sit in a disclosure that opens
while AI is on; `ai.enabled` is pinned above it, so the master switch is
always visible.

---

## Fleet defaults

### Drift Detection

| Setting | Key | Default | Range | Description |
|---------|-----|---------|-------|-------------|
| Check Interval | `drift.check_interval_minutes` | `30` | 1 – 1440 min | How often the scheduled drift check runs across all hosts that have drift detection enabled. Lower values catch drift faster but increase SSH and CPU load. |

### SSH

| Setting | Key | Default | Range | Description |
|---------|-----|---------|-------|-------------|
| Connect Timeout | `ssh.connect_timeout` | `10` | 1 – 120 sec | How long to wait when opening an SSH connection to a host (used for sync, drift checks, and state collection). Increase if managed hosts are on high-latency links. |
| Command Timeout | `ssh.command_timeout` | `60` | 5 – 900 sec | The longest a single remote command may take on an already-open session — state collectors and reachability probes, not Ansible playbooks, which have their own timeout. A host that accepts the connection but then hangs on one command fails after this long instead of blocking every host behind it. |
| Idle Timeout | `ssh.idle_timeout_seconds` | `1800` | 60 – 86400 sec | How long a web terminal session can be idle before it is automatically disconnected. 1800 = 30 minutes. |

### Ansible

| Setting | Key | Default | Range | Description |
|---------|-----|---------|-------|-------------|
| Playbook Timeout | `ansible.playbook_timeout` | `300` | 30 – 3600 sec | Maximum time allowed for a single Ansible playbook run. If a sync exceeds this, the run is killed and marked as failed. Increase for large host fleets or slow package installs. |

### Actions

| Setting | Key | Default | Range | Description |
|---------|-----|---------|-------|-------------|
| Preflight reachability check | `actions.preflight_enabled` | `1` (on) | `0` / `1` | When enabled, every per-host action run first performs a bounded SSH liveness probe. A genuinely unreachable host fails in ~25 s with a clear `host unreachable (preflight)` error instead of tying up a worker for the full playbook timeout. Set to `0` to disable if the probe is too aggressive for flaky hosts. |

### Scheduling

| Setting | Key | Default | Range | Description |
|---------|-----|---------|-------|-------------|
| Timezone | `scheduling.timezone` | `UTC` | IANA name | The timezone [scheduled actions](scheduled-actions.md#timezone) and cron-scheduled discovery scans read their cron expressions in, e.g. `Europe/Stockholm`. Picked from a searchable list of the names the server knows: type any part of one, such as `stockholm` or `new york`. Changing it keeps each schedule's clock time and moves it to the new zone — `0 3 * * *` then runs at 03:00 there — so check existing schedules afterwards. Cron jobs LabDog manages on hosts run on each host's own clock and are not affected. |

### Workflows

| Setting | Key | Default | Range | Description |
|---------|-----|---------|-------|-------------|
| Snapshot Max Age | `workflow.snapshot_max_age_hours` | `24` | 1 – 168 hours | Proxmox VM snapshots taken before a destructive scheduled action are automatically cleaned up after this many hours when the action completes successfully. |

### Discovery

| Setting | Key | Default | Range | Description |
|---------|-----|---------|-------|-------------|
| Scan Timeout | `discovery.scan_timeout` | `1.0` | 0.1 – 30.0 sec | Per-host TCP connect timeout during a network scan. Lower values speed up scans but miss hosts on slow links. |
| Max Concurrent | `discovery.max_concurrent` | `100` | 1 – 1000 | Maximum simultaneous TCP probes during a network scan. Reduce if your network drops packets under high connection rates. |

---

## System

### Logging and retention

| Setting | Key | Default | Range | Description |
|---------|-----|---------|-------|-------------|
| Log Level | `logging.level` | `info` | `debug` `info` `warning` `error` `critical` | Application log verbosity. Use `debug` to trace API calls and task execution. Use `warning` or higher in production to reduce noise. |
| Audit Retention | `logging.audit_retention_days` | `90` | 0 – 3650 days | How many days to keep audit log entries and terminal transcripts. Entries older than this are purged daily. `0` keeps them forever. |
| Run Retention | `logging.run_retention_days` | `90` | 0 – 3650 days | How many days to keep finished action runs, their transcripts, sync jobs, and the record of sent and failed [notifications](notifications.md). `0` keeps them forever. |
| Drift Retention | `logging.drift_retention_days` | `90` | 0 – 3650 days | How many days to keep individual drift-check samples. Older samples are folded into running totals before they are deleted, so the exported drift counters never go backwards. `0` keeps them forever. |

The System section also holds the [Prometheus export](../metrics-export.md)
card — status, scrape URL and a config snippet — and [About](#about).

---

## AI

The AI section starts with the configured
[AI providers](assistant.md#ai-providers) — **Manage providers…** opens their
page — followed by these settings.

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
| Max Iterations | `ai.max_iterations` | `15` | 1 – 100 | Model turns in one session. A full-auto alert session gets at least `ai.alert_max_commands` plus five. |
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
| Auto Investigate | `ai.auto_investigate_enabled` | `0` (off) | 0 – 1 | Start an investigation when an eligible alert arrives. What it may change is set under *Alert remediation* below. |
| Minimum Severity | `ai.auto_investigate_min_severity` | `critical` | info / warning / critical | Lowest severity that triggers one. An alert whose severity is missing or not one of these is **skipped and says so**, rather than being guessed either way. |
| Investigation Prompt | `ai.alert_mission_template` | built-in wording | up to 8000 characters | The prompt an alert investigation starts from. See below. |

**Alert remediation.** What an alert's investigation may change. Full auto
is reached only by naming an alert, never instance-wide — see
[Alerts](alerts.md#what-the-session-can-do) for why, and for the
safeguards a named alert must pass.

| Setting | Key | Default | Range | Description |
|---------|-----|---------|-------|-------------|
| Alert Autonomy | `ai.alert_autonomy_level` | `read_only` | read_only / approval | What every alert's session may change. At `approval` each change waits in the approvals queue, and a request nobody sees expires after `ai.approval_expiry_hours` — subscribe to [email](notifications.md) for it. |
| Full-Auto Alerts | `ai.alert_full_auto_alertnames` | empty | up to 4000 characters | Alerts, one exact name per line, whose session may change the host without asking. No wildcards. |
| Full Auto Needs Snapshot | `ai.alert_full_auto_requires_snapshot` | `1` (on) | 0 – 1 | Allow full auto only on hosts LabDog can snapshot first. Off allows it on bare metal and unmapped hosts, whose changes then have no rollback point. |
| Remediation Cooldown | `ai.alert_remediation_cooldown_minutes` | `60` | 0 – 10080 min | After a full-auto session for an alert changes a host, how long before that alert may change it again (0 = no cooldown). |
| Remediation Daily Cap | `ai.alert_remediation_daily_cap` | `3` | 1 – 100 | Most full-auto sessions that may change one host in 24 hours, across all alerts. |
| Alert Max Commands | `ai.alert_max_commands` | `15` | 1 – 200 | Shell commands in a full-auto alert session. The lower of this and `ai.max_commands` applies. Too few, and a fix whose first attempts fail runs out partway and leaves the host half changed. The session gets this many model turns plus five, even above `ai.max_iterations`. |
| Alert Wall Clock | `ai.alert_wall_clock_seconds` | `900` | 30 – 21600 s | Run time for a full-auto alert session. The lower of this and `ai.wall_clock_seconds` applies. Each change waits for its snapshot first, so a fix of several changes needs more time than an investigation. |
| Fix Check Delay | `ai.alert_remediation_check_minutes` | `10` | 2 – 120 min | How long after a full-auto session ends before LabDog [checks whether its fix worked](alerts.md#checking-the-fix-and-rolling-back). Long enough for the alert to resolve. |
| Automatic Rollback | `ai.alert_auto_rollback` | `1` (on) | 0 – 1 | Restore the snapshot from before the first change when the check finds the fix made the host worse. A fix that only failed is never rolled back automatically. |

**Changes and approvals.**

| Setting | Key | Default | Range | Description |
|---------|-----|---------|-------|-------------|
| Snapshot Before Change | `ai.snapshot_before_mutating` | `1` (on) | 0 – 1 | Take a Proxmox snapshot before the assistant runs a command that changes a host. Hosts with no VM mapping are unaffected. While on, a snapshot that **fails blocks the command**. |
| Snapshot Retention | `ai.snapshot_retention_days` | `7` | 0 – 365 days | How long to keep those snapshots (0 = forever). They are deliberately *not* deleted when a session succeeds — the point of them is that you can undo the change after reading what it did, and this is how long "afterwards" lasts. |
| Approval Expiry | `ai.approval_expiry_hours` | `24` | 1 – 720 hours | How long an approval request waits before lapsing. The session is then told the change was not approved and finishes with a report, rather than sitting parked forever. |

### The investigation prompt

`ai.alert_mission_template` is the text an alert investigation begins with,
and it is editable because the built-in wording has to work for an alert
LabDog has never seen. It asks a deliberately generic question — is this
real, and what is causing it. You know things it cannot: which alerts on
your estate are chronically noisy, that an exporter lies during backups,
that an answer should always name the service the host runs.

It renders as a text area rather than a one-line field, with a **Reset to
default** button that restores the shipped wording — worth knowing before
you edit it, because the default is a paragraph nobody retypes from memory.

These placeholders are filled in from the alert:

| Placeholder | Expands to |
|---|---|
| `{alertname}` | The alert's name |
| `{severity}` | The severity label, or `(not labelled)` |
| `{status}` | `firing` or `resolved` |
| `{starts_at}` | When the alert started, in UTC (2026-08-23 19:00:00 UTC) |
| `{labels}` | Every label, one per line as `- key: value` |
| `{annotations}` | Every annotation, one per line as `- key: value` |

Anything else in braces is refused when you save, naming both what it did
not recognise and what is available — a template that named a placeholder
that does not exist would otherwise fail hours later, inside a background
task, leaving an alert uninvestigated with nothing on screen to say why.
Write `{{` and `}}` for a literal brace. A prompt with no placeholders at
all is allowed: standing instructions with no alert detail are a real
choice.

You do not have to keep any particular placeholder. Dropping `{labels}`
genuinely does deprive the model of that context — that is your call to
make, not something the field stops you doing.

---

## Resetting a Setting

Every row shows the setting's default beside its current value, so you can
see at a glance what has been changed from the shipped configuration.

Numeric and choice settings are reverted by typing or selecting the
default shown on the row. Multiline settings — currently only the
[investigation prompt](#the-investigation-prompt) — have an explicit
**Reset to default** button, because their default is a paragraph nobody
retypes from memory.

---

## Proxmox Settings

**Path:** `/hypervisors` (Settings › Integrations › Proxmox;
`/settings/proxmox` redirects here)

The connections to one or more Proxmox VE nodes. See
[Proxmox integration](../README.md) for setup details.

Each node row shows its name, API URL, token ID, whether TLS is
**verified**, and whether a CA certificate is configured (with the start of
its fingerprint), with **test**, **edit** and **delete**. **Add node…**
asks for a name, the API URL, the API token ID and secret, and the TLS
settings below.

Two actions sit in the head:

- **Discover VM mappings** scans every configured node and links each
  LabDog host to its backing VM or container in one pass. A single host's
  mapping can also be discovered from
  [its page](hosts.md#proxmox-vm-mapping).
- **Clean up orphaned snapshots** deletes LabDog's snapshots that are no
  longer needed: those tied to a finished action run, and those older than
  `workflow.snapshot_max_age_hours` whose run LabDog does not know.

### TLS verification

Each node has two TLS settings that together decide how LabDog validates
the node's HTTPS certificate when it calls the Proxmox API:

- **verify the node's TLS certificate** — when unticked, LabDog performs
  **no** certificate validation at all. This is the last-resort escape hatch
  for a node with a certificate LabDog can't otherwise trust; prefer a CA
  certificate (below) instead.
- **ca certificate** — paste a PEM-encoded certificate to verify the node
  against it, instead of the operating system's trust store. Use this for
  nodes with a **private-CA** or **self-signed** certificate so verification
  stays on. The field is shown only while verification is ticked.

| Verify | CA certificate | Result |
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
  use **clear it** in the node's edit dialog.

---

## About

**Path:** Settings › System (`/settings?section=system`; `/settings/about`
redirects here). **About LabDog** in the account menu opens it too.

Build metadata for the running LabDog instance, read from
`GET /api/version` (a public endpoint — no authentication required):

| Field | Source |
|---|---|
| version | `VERSION` file at the repo root, baked into `backend/pyproject.toml` and `frontend/package.json` by the release pipeline. Read at runtime via `importlib.metadata.version("labdog-backend")`. |
| commit | The git commit the image / package was built from. Set via the `GIT_SHA` Docker build arg or the `bake-build-info` Makefile target (writes `app/_build_info.py`). Falls back to the `LABDOG_COMMIT_SHA` env var. Empty on a dev install with neither source populated. |
| built | ISO 8601 timestamp of the build. Set via `BUILD_DATE` build arg / Makefile target / env var. Empty when not provided. |
| license | `AGPL-3.0-or-later` (constant). |
| source | Upstream project URL (constant). |

Below them is a **support line** with a copy button — version, short SHA
and build date in one string, for pasting into bug reports so maintainers
know exactly what build you're on.

For operators automating health checks, hit `/api/version` directly
rather than scraping this page — it returns the same data as JSON.
