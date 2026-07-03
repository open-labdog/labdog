# Groups

![Groups list](screenshots/groups.png)

## Groups List

**Path:** `/groups`

Groups are the core organisational unit in LabDog. Each group holds desired-state configuration for one or more modules. Hosts belong to groups, and when a host belongs to multiple groups, configurations are merged by priority (higher number wins).

### Columns

| Column | Description |
|--------|-------------|
| Name | Group name |
| Priority | Higher number = higher precedence in merges |
| Hosts | Number of hosts assigned to this group |
| Modules | Icon row showing which modules have config in this group |
| GitOps | Whether this group is controlled by a Git repository |

### Categories

Groups are displayed in collapsible category sections. The category is set when creating or editing a group. Uncategorised groups appear under **Other**.

### Creating a Group

Click **New Group**. Fields:

| Field | Notes |
|-------|-------|
| Name | Unique identifier for this group |
| Priority | `1`–`9999`. Higher wins in merges. |
| Category | Optional label for visual grouping |
| Description | Free-text note |

---

## Group Detail

![Group detail](screenshots/group-detail.png)

**Path:** `/groups/{id}`

Shows the group's metadata, sync status summary, GitOps status, and tabs for every configuration module.

### Sync Status Card

| Metric | Description |
|--------|-------------|
| Hosts | Total hosts in this group |
| In Sync | Hosts where all modules match desired state |
| Out of Sync | Hosts where at least one module has drifted |
| Error | Hosts where the last check failed |
| Unknown | Hosts never checked |

The **Sync all modules** button in the top-right of the card previews every changed module across all hosts in the group, then applies them on confirmation. See [Syncing changes](#syncing-changes).

### GitOps Card

Shows whether this group is managed by a Git repository. Click **Enable** to link a repository — after enabling, all module configuration for this group becomes read-only in the UI (a banner replaces the Add/Edit/Delete controls). See [GitOps UI](gitops-ui.md).

### Module Tabs

| Tab | Page |
|-----|------|
| Overview | Members table + GitOps status. The Members table is a flat list of hosts in the group; group-dispatched actions (e.g. [`k8s-upgrade`](actions.md#group-dispatch-actions)) target the whole group and the pack's playbook handles any per-member topology discovery itself. |
| Rules | [Firewall Rules](#firewall-rules) |
| Services | [Service Rules](#services) |
| Hosts File | [/etc/hosts entries](#hosts-file) |
| Users | [Linux Users & Groups](#linux-users) |
| Cron Jobs | [Cron Jobs](#cron-jobs) |
| Packages | [Packages](#packages) |
| CA Certs | [CA Certificates](#ca-certificates) |
| DNS Resolver | [DNS Resolver](#dns-resolver) |
| Schedules | [Schedules](scheduled-actions.md) |

Every module tab carries its own **Sync** button; the Sync Status card also has a **Sync all modules** button. Both are preview-first — see [Syncing changes](#syncing-changes).

---

## Firewall Rules

![Firewall rules](screenshots/group-rules.png)

**Path:** `/groups/{id}/rules`

Manages the desired firewall state for all hosts in this group.

### Default Policies

At the top of the page, two dropdowns set the **default input** and **default output** policies:

- `Default (drop)` — block all traffic not explicitly allowed (recommended for input)
- `Default (accept)` — allow all traffic not explicitly denied

### Rule Table

Each rule row shows:

| Column | Description |
|--------|-------------|
| Priority | Rules are applied in priority order (lower number first) |
| Action | `Allow` (green) or `Deny` (red) |
| Protocol | `TCP`, `UDP`, `ICMP`, or `any` |
| Direction | `Input` or `Output` |
| Source | Source IP or CIDR (`any` = unrestricted) |
| Destination | Destination IP or CIDR |
| Port | Port number or range (`22`, `8000-8080`) |
| Comment | Optional note |

The lock icon (🔒) on priority `0` rules marks the auto-generated SSH lockout rule — LabDog always injects this to prevent locking itself out.

Rows can be reordered by dragging the handle on the left.

### Adding a Rule

Click **Add Rule**. All fields except Comment are required. Port is only shown for TCP/UDP.

---

## Services

![Service rules](screenshots/group-services.png)

**Path:** `/groups/{id}/services`

Defines which systemd services should be running (or stopped) and enabled (or disabled) on all hosts in this group.

### Columns

| Column | Description |
|--------|-------------|
| Service Name | The systemd unit name, e.g. `nginx.service` (`.service` suffix optional) |
| State | `running` or `stopped` |
| Enabled | Whether the unit is enabled at boot |
| Priority | Used during multi-group merges |
| Comment | Optional note |

### Unit File Management

Click **Edit** on any service to optionally attach a unit file. LabDog can deploy a full unit file or a drop-in override (stored under `/etc/systemd/system/<name>.d/`).

---

## Packages

![Package rules](screenshots/group-packages.png)

**Path:** `/groups/{id}/packages`

Two tables: **Package Rules** and **Package Repositories**.

### Package Rules

| Column | Description |
|--------|-------------|
| Package Name | e.g. `nginx`, `postgresql-18` |
| Version | `any` (latest), or a pinned version string |
| State | `present`, `absent`, or `latest` |
| Package Manager | `auto` (detect from OS), `apt`, `dnf`, or `yum` |
| Hold | Pin the package at its current version (apt `hold` / dnf `versionlock`) |
| Comment | Optional note |

### Package Repositories

Custom APT or YUM/DNF repositories to add before installing packages. Required when using packages not in the default distribution repos (e.g. PostgreSQL PGDG, Docker CE).

| Column | Description |
|--------|-------------|
| Name | Repository identifier |
| URL | Repository base URL |
| Type | `apt` or `yum` |
| Distribution | APT codename (e.g. `bookworm`) — APT only |
| State | `present` or `absent` |

---

## Hosts File

![Hosts file](screenshots/group-hosts-entries.png)

**Path:** `/groups/{id}/hosts-entries`

Manages `/etc/hosts` entries on all hosts in this group.

### Columns

| Column | Description |
|--------|-------------|
| IP Address | IPv4 or IPv6 address |
| Hostname | Primary hostname for this entry |
| Aliases | Space-separated additional names |
| Comment | Optional note |

LabDog always injects `127.0.0.1 localhost` and the host's own entry — these system entries cannot be removed from the UI.

When a host belongs to multiple groups with conflicting entries for the same hostname, the highest-priority group wins. See the [Precedence guide](../examples/precedence/README.md) for examples.

---

## Cron Jobs

![Cron jobs](screenshots/group-cron-jobs.png)

**Path:** `/groups/{id}/cron-jobs`

Manages scheduled tasks deployed via `ansible.builtin.cron`.

### Columns

| Column | Description |
|--------|-------------|
| Name | Unique identifier for this job (used for idempotent updates) |
| User | The Linux user the cron job runs as |
| Schedule | Five-field cron expression (`minute hour day month weekday`) |
| Command | Shell command to execute |
| State | `present` or `absent` |
| Comment | Optional note |

**Schedule field accepts standard cron syntax:**
- `0 2 * * *` — daily at 02:00
- `*/5 * * * *` — every 5 minutes
- `0 9 * * 1-5` — weekdays at 09:00

---

## Linux Users

![Linux users](screenshots/group-users.png)

**Path:** `/groups/{id}/users`

Two tables: **Linux Users** and **Linux Groups**.

### Linux Users

| Column | Description |
|--------|-------------|
| Username | Linux account name |
| UID | Numeric user ID (`auto` = OS-assigned) |
| Shell | Login shell (e.g. `/bin/bash`, `/usr/sbin/nologin`) |
| State | `present` or `absent` |
| Keys | Count of authorized SSH public keys |
| Sudo | Whether this user has passwordless sudo |
| Priority | Used during multi-group merges |

Click **Edit** to manage SSH authorized keys and supplementary group memberships for a user.

### Linux Groups

System groups (not host groups). Used to create groups that users can be added to as supplementary members.

| Column | Description |
|--------|-------------|
| Group Name | Linux group name |
| GID | Numeric group ID (`auto` = OS-assigned) |
| State | `present` or `absent` |

---

## DNS Resolver

![DNS resolver](screenshots/group-resolver.png)

**Path:** `/groups/{id}/resolver`

Manages DNS resolver configuration. This is a **singleton** per group — there is at most one resolver config per scope (group or host). If no resolver is configured, the host's existing DNS settings are left untouched.

### Backends

| Backend | What it configures |
|---------|-------------------|
| `resolv_conf` | Writes `/etc/resolv.conf` directly |
| `systemd_resolved` | Configures `/etc/systemd/resolved.conf` |
| `network_manager` | Uses `nmcli` to set DNS on the primary connection |

### Fields

| Field | Description |
|-------|-------------|
| Nameservers | One or more DNS server IPs (e.g. `1.1.1.1`, `8.8.8.8`) |
| Search Domains | Domain suffixes appended to short hostnames |
| DNS over TLS | Enable DoT (systemd-resolved only) |
| Options | Advanced resolv.conf options (`ndots`, `timeout`, `rotate`, etc.) |

Click **Configure DNS** to set up the resolver for this group. If a resolver is already configured, the form pre-populates with existing values.

---

## CA Certificates

**Tab:** CA Certs

Deploys trusted certificate authorities into the system trust store of every host in the group, so the hosts trust services signed by an internal/private CA. Rules can also be set per host (host detail → CA Certs) and merge with the group's rules.

### Columns

| Column | Description |
|--------|-------------|
| Name | Operator-chosen label for the certificate (e.g. `Internal Root CA`) |
| Subject | The certificate's subject, parsed from the PEM |
| Expires | The certificate's `notAfter` date, parsed from the PEM |
| Fingerprint (SHA-256) | Hash of the certificate, used as its stable identity |
| State | `present` (install into the trust store) or `absent` (remove it) |
| Actions | Edit (name / state / comment) or delete the rule |

### Adding a Certificate

Click **Add Certificate** and paste a PEM-encoded certificate. The subject, issuer, expiry, and fingerprint are parsed from the PEM automatically. The **PEM content is immutable** — to change the certificate, add a new entry and remove the old one (a different certificate has a different fingerprint, so it is a new identity). Set **State** to `absent` to actively remove a previously deployed certificate from the hosts' trust store rather than just stopping managing it.

The **Recent Deployment Runs** panel below the table shows the per-host outcome of the most recent CA-certificate deployments.

---

## Syncing changes

LabDog applies a group's desired configuration to its hosts over SSH.
There are two entry points, both **preview-first**:

- **Per module** — each module tab (Rules, Services, Hosts File, Users,
  Cron Jobs, Packages, CA Certs, DNS Resolver) has a **Sync _module_**
  button (e.g. "Sync Firewall Rules") that previews and applies just that
  module across every host in the group.
- **All modules at once** — the **Sync all modules** button on the Sync
  Status card previews and applies every changed module across the group
  in a single operation.

### 1. Preview

Both buttons open a **Preview** dialog first. LabDog computes a per-host
diff between the desired state (stored in its database, merged across all
groups the host belongs to) and the current state fetched live over SSH.
Each host card shows:

- Config to **add** (green, `+` prefix)
- Config to **remove** (red, `-` prefix)
- Config **unchanged** (grey, indented)

Hosts already in sync are flagged "no changes". A module whose current
state could not be read is shown as an error and is **never applied
blind** — only cleanly-previewed, changed modules are sent.

### 2. Apply

Confirm the dialog to apply. Each host runs as a background job through
the unified per-host orchestrator (v0.2.0+): one Ansible playbook per
host covering every requested module, so two syncs targeting the same
host queue rather than race over SSH. LabDog generates the playbook from
the previewed diff, runs it via `ansible-runner`, updates the per-host
and per-module sync status, and writes an audit-log entry.

### Progress

Applied syncs are tracked in the **global Sync tray** at the bottom-right
of every page — a live progress bar per operation, a per-host and
per-module drill-down, and a success/failure toast on completion. You can
navigate away while a sync runs; the tray keeps tracking until every job
reaches a terminal state.
