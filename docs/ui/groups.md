# Groups

![Groups list](screenshots/groups.png)

## Groups List

**Path:** `/groups`

Groups are the core organisational unit in LabDog. Each group holds desired-state configuration for one or more modules. Hosts belong to groups, and when a host belongs to multiple groups, configurations are merged by priority (higher number wins).

### Columns

| Column | Description |
|--------|-------------|
| Group | Group name; the row opens the group's page |
| Priority | Higher number = higher precedence in merges — the list is sorted by it, strongest first |
| Category | Free label (`baseline`, `role`, `policy` …); the **category** filter narrows the list |
| Hosts | Number of hosts assigned to this group |
| Drifted | Members currently out of sync |
| Declares | Which modules have config in this group, with item counts — a tag opens that module's editor |
| GitOps | Whether this group is controlled by a Git repository |

Selecting rows enables **Delete selected** and, for a single group, **Plan sync**.

### Creating a Group

Click **New group**. The editor dialog shows the merge ladder — where the new group's priority lands among the existing ones — before you create it. Fields:

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

A group's own page, on the same pattern as [host detail](hosts.md#host-detail): a head with the group's name, category and priority (and a **gitops** tag when it imports from Git), then four tabs. Everything about a group is edited here, inline — there is no edit dialog.

The head offers **Run action…** (pick an action the group supports, preview or run it — see [Actions](actions.md)) and **Plan sync — N hosts**, which opens [Operations › Plans](operations.md#plans) scoped to the group.

### Overview

| Panel | What it does |
|-------|--------------|
| Settings | Name, category, description and priority, saved inline. Moving the priority shows a tie warning and **every winner/loser flip** the move causes — which other group this one starts beating or losing to, on how many shared hosts, for which modules — before you save. Nothing reaches a host until a plan runs. |
| Modules | The modules this group declares, with item counts. A row opens that module in the Config tab. |
| Members | The first hosts in the group and their sync summary (in sync · drifted · failed); **View all members →** opens the Members tab. |
| Danger zone | **Delete group** — two clicks (the second confirms with the number of hosts that drop the group). Hosts keep their other groups; nothing on a host changes until a plan runs. |
| GitOps | **Enable…** links a registered Git repository and file path; the group's desired state is then imported from that file and the module editors become read-only. Status, repository, file and last import are shown; **Disable GitOps** keeps the current items and stops importing. See [GitOps UI](gitops-ui.md). |
| Recent activity | The group's latest action runs; **all activity →** opens the Activity tab. |

### Config

The eight modules on the left — **Firewall**, **Services**, **Hosts file**, **Packages**, **Users & SSH**, **Cron**, **DNS resolver**, **CA certificates** — with item counts, and the selected module's editor on the right. A module is *declared* once the group has an item for it: adding the first item declares it, deleting the last undeclares it. The editors are documented below. The URL carries the selection (`?tab=config&module=firewall`), so a module's editor can be linked to directly; the command palette's *Firewall — group: web* entries land here.

With the Config tab open, **Plan sync** plans just that module.

### Members

The hosts that inherit this group — status, IP, their other groups (strongest first) and how many overrides of their own they carry. **+ add hosts** opens an inline picker (filter, tick a host to add it, or **add N** for everything that matches); **remove** drops a host from the group. Both change desired state only; the host re-merges on its next plan.

### Activity

**Runs** — every action run against the group, newest first; a row opens the run. **Schedules** — the group's [scheduled actions](scheduled-actions.md).

---

## Module editors

Each module's editor is the Config tab of the group's page (`/groups/{id}?tab=config&module=<id>`). The standalone paths below (`/groups/{id}/rules` …) still work as deep links and render the same editor on its own.

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

LabDog applies a group's desired configuration to its hosts over SSH,
always **preview-first**, on the [Plans](operations.md#plans) screen:

- **Plan sync — N hosts** on the group's page opens a plan scoped to the
  group (`/plans?scope=group:<id>`); from the Config tab it plans just the
  selected module (`&modules=firewall`).
- A plan can also be started from a host's page, or from Operations ›
  Plans with any set of hosts and modules.

### 1. Preview

Opening a plan runs a dry run on every host in scope: a per-host diff
between the desired state (stored in LabDog's database, merged across all
groups the host belongs to) and the current state fetched live over SSH.
Each host shows config to **add**, **remove** and leave **unchanged**;
hosts already in sync are flagged as such, hosts that cannot be reached are
skipped, not failed. A module whose current state could not be read is
shown as an error and is **never applied blind**.

### 2. Apply

Untick any host you want to leave behind; the blast radius and the
acknowledgements (hosts changing, an SSH-affecting rule, excluded hosts)
follow the selection, and **Review & apply…** arms only once you have
typed the host count. Each selected host then runs as a background job
through the unified per-host orchestrator: one Ansible playbook per host
covering every requested module, so two syncs targeting the same host
queue rather than race over SSH. LabDog generates the playbook from the
previewed diff, runs it via `ansible-runner`, updates the per-host and
per-module sync status, and writes an audit-log entry.

### Progress

The plan's URL is shareable, so a second pair of eyes can review before
anyone clicks Apply, and the screen follows every job to its per-host
result. Applied syncs are also tracked in the **global Sync tray** at the
bottom-right of every page — a live progress bar per operation, a per-host
and per-module drill-down, and a success/failure toast on completion. You
can navigate away while a sync runs; the tray keeps tracking until every
job reaches a terminal state.
