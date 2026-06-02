# Actions & Action Packs

**Paths:** Actions tab on [host](hosts.md) and [group](groups.md) detail
pages; management UI at `/action-packs` (sidebar → Integrations → Action
Packs).

Actions are **Ansible playbook runs** LabDog can trigger against a host,
a group, or the entire fleet. Run them ad-hoc from the Actions tab, or
schedule them via cron on the [Schedules page](#scheduled-actions).
The same execution path serves both — there's no "ad-hoc only" or
"schedule only" action.

The catalog comes from two sources:

- **Built-in pseudo-actions** (`_builtin.*`) wrap operations LabDog
  already performs internally: `_builtin.sync` (coalesced per-host
  module sync), `_builtin.drift_check` (compare desired vs current
  state), and `_builtin.collect_state` (refresh cached host facts).
  They have no Ansible playbook on disk — they're pure code dispatch
  paths. The leading underscore is reserved; pack-supplied actions
  cannot register a key starting with `_`.
- **Pack-supplied actions** come from **packs** — pluggable collections
  of playbooks. LabDog ships with a bundled pack baked into the image;
  admins add their own packs (from a git repo or a local directory) to
  extend or override it.

- [Running actions](#running-actions)
- [Scheduled actions](#scheduled-actions)
- [Group-dispatch actions](#group-dispatch-actions)
- [Action packs](#action-packs)
  - [Bundled pack](#bundled-pack)
  - [Adding a pack](#adding-a-pack)
  - [Pack precedence and resolving conflicts](#pack-precedence-and-resolving-conflicts)
  - [Provenance: which pack won?](#provenance-which-pack-won)
- [Writing your own playbook (BYO)](#writing-your-own-playbook-byo)
  - [Pack layout](#pack-layout)
  - [Manifest schema](#manifest-schema)
  - [Playbook conventions](#playbook-conventions)
  - [Destructive actions and snapshot safety](#destructive-actions-and-snapshot-safety)
- [Troubleshooting](#troubleshooting)

---

## Running actions

Open a host or a group, click the **Actions** tab. Each card is one
action:

```
┌──────────────────────────────────────────────────────────┐
│  ⚡  Reboot host                [from my-pack]            │
│      Reboots the target host and waits for SSH.          │
│      ~1–3 min                                            │
│                                                          │
│                              ▶ Run                       │
└──────────────────────────────────────────────────────────┘
```

Click **Run**, fill in parameters (if any), click **Run** in the dialog.
Live stdout streams into the run-detail page; when it finishes you see a
green/red status chip and the full captured output. Runs are stored in
the DB — history is visible in the same tab.

**Who can run actions:** any logged-in user. **Who can configure packs
or schedules:** superusers only.

### Group-dispatch actions

Most actions fan out per-host: LabDog dispatches one Celery task per
target host with a single-host inventory, and a parallelism setting
controls how many run concurrently. A few actions need the whole
group's hosts visible to a single ansible run — for example,
`k8s-upgrade` drains, upgrades, and re-admits each node in turn while
the rest of the cluster keeps serving traffic. Such actions declare
`supports_host: false` in their manifest.

**What's different in the UI:**

- The action card only appears on group views (the action's manifest
  sets `supports_host: false`, so a host target makes no sense).
- The Run dialog has no special pre-flight — there are no per-member
  roles to assign in LabDog. Just pick the group and run.
- The parallelism picker is hidden — group-dispatched runs are always
  one ansible-runner invocation against a flat multi-host inventory;
  the pack's playbook decides ordering with Ansible's `serial:`,
  `add_host`, `delegate_to`, and `run_once` primitives.
- The run-detail page shows one `ActionHostRun` per member, all
  driven by the same ansible-runner invocation. Per-host events get
  routed back to the matching row by inventory hostname.
- Destructive group-dispatched runs get the same per-host
  snapshot/verify/rollback envelope as host-targeted runs: LabDog
  snapshots every member with a Proxmox VM mapping before the
  playbook, runs verify on each host after, and rolls back any host
  whose action or verify failed (auto_rollback toggle). Per-host
  rollback policy — succeeded hosts keep their state, only the
  failed ones revert. Operator inspects the partial outcome, fixes
  the underlying issue, re-runs the action; pack idempotency lets
  the re-run skip already-succeeded hosts (k8s-upgrade detects
  already-on-target nodes via a `kubelet --version` probe).

The bundled `k8s-upgrade` discovers control-plane vs worker by
probing each node for `/etc/kubernetes/manifests/kube-apiserver.yaml`
in a setup play, then `serial: 1` upgrades control-plane nodes
followed by workers. The action is currently **apt-only** — Debian
and Ubuntu nodes. RHEL / Rocky / Alma support is on the roadmap.

---

## Scheduled actions

`/schedules` (sidebar → **Schedules**) lists every cron-driven
action across the fleet. Each row pairs an action_key with a target
(host / group / fleet) and a 5-field cron expression. The unified
scheduler ticks every 60 seconds, walks the table, and dispatches any
row that's due into the same execution path as the ad-hoc Run button.

**Three places to create a schedule:**

- **+ New** on the Schedules page — pick action + target
  through the wizard.
- **Schedule…** button on an action card — preselects the action_key.
- **Schedule action** on a host / group detail page — preselects the
  target.

**Targets:**

- **Host** — runs against one host. Available for any action with
  `supports_host: true` in its manifest.
- **Group** — runs against every member of a host group. Requires
  `supports_group: true`.
- **Fleet** — runs against every host in the inventory. Reserved for
  actions that explicitly opt in via `supports_fleet: true` — meaning
  `_builtin.drift_check` and `_builtin.collect_state` today.
  Pack-supplied actions default to `false`; flip the manifest field
  if your action genuinely makes sense fleet-wide.

**Run history** is the unified `action_runs` table: scheduled runs
appear in the same drawer as ad-hoc runs, with a `scheduled_action_id`
column tying them back to the row that fired them. Deleting a schedule
sets that FK to NULL — history is preserved.

**GitOps:** a group YAML file can declare `scheduled_actions:` as a
list, one entry per action_key. The importer applies leave-alone-on-
absence semantics: omitting the section preserves DB rows; an empty
list deletes them all for that group.

```yaml
scheduled_actions:
  - action_key: linux-upgrade
    enabled: true
    schedule_cron: "0 3 * * 0"
    parameters: {}
    snapshot_enabled: true
    auto_rollback: true
```

---

## Action packs

A pack is a collection of playbooks + their LabDog manifests. Each
action key in the registry comes from exactly one pack. When two packs
declare the same key, the operator pins which pack wins via a per-key
*resolution*; until pinned, the key is **unresolved** and the action
is unrunnable (see
[below](#pack-precedence-and-resolving-conflicts)). There is no
global pack ordering.

### Bundled pack

LabDog's container image includes a "bundled" pack with three baseline
actions:

| Key | What it does |
|---|---|
| `linux-upgrade` | Upgrades all system packages; reboots if `/var/run/reboot-required`. |
| `linux-os-upgrade` | Major-release upgrade (e.g. Debian 12 → 13, Ubuntu 22.04 → 24.04). |
| `k8s-upgrade` | Drains, upgrades, and re-admits each node in a Kubernetes cluster. |

The bundled pack is built **at container build time** by cloning
[`open-labdog/labdog-playbooks`](https://github.com/open-labdog/labdog-playbooks)
at the SHA pinned in the labdog repo's `LABDOG_PLAYBOOKS_REF` file.
The bundled pack content shipped with a particular labdog release
therefore corresponds exactly to a labdog-playbooks commit — bumping
labdog typically bumps the bundled pack as well. The bundled pack is
immutable — you can't edit or delete it from the UI. It exists as a
safety net so LabDog keeps working even if all other packs are
unreachable. It appears as a read-only row in the **Pack Sources**
table on `/action-packs` (no Sync / Edit / Delete buttons — just a
"built-in" badge) so its always-present-candidate status is
discoverable.

To override a bundled action, add a pack that declares the same key
and pin the per-key resolution to your pack on `/action-packs`.

### Adding a pack

Git repository configuration (URL, branch, credentials) is **not**
duplicated on the pack. Configure the repo once under
[Git Repos](gitops-ui.md), then point one or more packs at it. The
pack only carries its own metadata — name, source type, a subpath,
enabled flag.

**Recommended path — the repo onboarding wizard.** When the repo
contains action packs (and optionally GitOps group YAML), use
**Integrations → Git Repos → Add Repository**. The three-step wizard
clones the repo, walks the tree, and presents every detected pack
and gitops file as a checkbox row. For action keys the new pack(s)
contribute that already have an owner, the wizard shows a per-key
**winner radio**: pick the pack from this activation, or keep the
existing winner. The wizard refuses to activate until every contested
key has a decision. GitOps files auto-bind to the `HostGroup` whose
name matches the file's top-level `group:` value. The same review
panel is reachable later from a repo's detail page via **Re-scan**,
so newly-pushed packs can be picked up without re-onboarding.

The wizard treats any directory containing a `pack.yml` as a pack
root and walks the whole repo to find them — `packs/<name>/`,
`actions/<name>/`, the repo root itself, all work. If no `pack.yml`
exists anywhere but the repo root has an `actions/*/manifest.yml`
tree, the repo is treated as a single root-level pack (matches the
[`labdog-playbooks`](https://github.com/open-labdog/labdog-playbooks)
convention).

**Per-pack alternative.** When you want to add a single pack, or the
repo's layout doesn't match the wizard's conventions, use **Action
Packs** (Integrations → Action Packs) → **Add Pack**. Fields:

| Field | What it is |
|---|---|
| Name | Admin-chosen label; must be unique. `bundled` is reserved. |
| Source | `Git repository` or `Local directory`. |
| Git repository | Dropdown of configured `GitRepository` rows. Only shown for source = Git. If empty, add one under [Git Repos](gitops-ui.md) first. |
| Path inside the repo | Subpath where the pack lives (e.g. `packs/labdog-default`). Leave empty when the pack is at the repo root. Only shown for source = Git. |
| Filesystem path | Absolute path on the LabDog host. Only shown for source = Local. LabDog reads the directory in place — nothing is cloned. |
| Enabled | Uncheck to keep the pack configured but out of the registry. |

A new pack joins the **Pack Sources** table — packs are unordered.
Any action key the pack contributes that doesn't collide with an
existing pack wins automatically. Keys that *do* collide become
*unresolved* (you must pin a winner). See
[below](#pack-precedence-and-resolving-conflicts).

On save, LabDog resolves the linked repository's URL, branch, and
credentials; clones into `<packs_root>/<pack_id>/`; and folds the
pack's actions into the registry. If the sync fails, the row is still
saved with `last_sync_status=failed`; fix the config and hit **Sync**
to retry.

**Credentials** live on the `GitRepository` row. The Git Repos page is
where you configure SSH keys (`ssh_key_id` referencing an SSH Key row)
or HTTPS tokens. Rotating a credential there propagates to every pack
using that repo at the next sync — no per-pack duplication.

**SSH host-key verification** uses TOFU (trust-on-first-use) to stay
consistent with the rest of LabDog's git integration. Packs don't
require pasted `known_hosts`.

### Pack precedence and resolving conflicts

Packs are **unordered**. Each action key has at most one source
pack — the **winner** that the registry serves. There are three
cases:

| Case | What happens |
|---|---|
| **Uncontested** — one pack declares the key | That pack wins automatically. Status `OK`. No picker. |
| **Contested + pinned** — multiple packs declare the key, operator has chosen a winner | The pinned pack wins. Status `Pinned` (or `Frozen` for auto-pins LabDog wrote on a fresh sync conflict, awaiting your confirmation). |
| **Contested + unresolved** — multiple packs declare the key, no pin yet | Action is *unrunnable*: `POST /api/actions/runs` returns 409, the Run button is disabled on host/group action cards, and the row is amber-tinted in the registry table with a "Pick winner" prompt. |

The **Action Packs** page is two surfaces stacked vertically:

1. **Action Registry** (primary surface). One row per action key:
   action key, winner, status. Contested rows expand on click into
   an inline radio group with every candidate pack — pick one and
   it auto-saves via `POST /api/action-resolutions/{key}`.
   Uncontested rows are plain text — there's no picker because the
   key has only one contributor; if and when another pack appears
   later, freeze-on-fresh-conflict kicks in and you pin then.
2. **Pack Sources** (management-only). Add, sync, edit, delete
   packs. Each row also has a **Make winner for all keys** button
   that bulk-pins every key the pack contributes via `POST
   /api/action-packs/{id}/claim-all-keys` — a confirmation dialog
   shows the diff (how many keys are already pinned here, how many
   would be moved from other packs) before commit. The bundled pack
   is a read-only row here so its presence is discoverable.

#### Bulk-pin: "Make winner for all keys"

When you add a new pack that should own every key it contributes,
clicking **Make winner for all keys** in the Pack Sources row is
the one-click flow. The endpoint writes one `action_resolution` row
per key the pack contributes (creating new rows where the key was
unresolved, overwriting rows that pointed at other packs, leaving
rows that already pointed here untouched). The confirmation dialog
shows the per-category counts before commit and the toast surfaces
the final `{created, updated, skipped}` numbers.

#### Freeze-on-fresh-conflict

LabDog never silently flips a winner. When a sync introduces a new
manifest that turns a previously-uncontested key into a contested
one, the rebuild **freezes** the winner to whichever pack was
previously serving that key by writing an `action_resolution` row
pinning it. The row's `decided_by_user_id` is `NULL`, which the UI
surfaces as a **Frozen** status — you can confirm by re-pinning the
same pack (which sets `decided_by_user_id` to you and clears the
Frozen badge) or switch to a different candidate. Without the
freeze, an upstream sync could turn a working action into an
unresolved one — frozen behaviour preserves status quo until you
look.

#### Pack disappears

If the pack a resolution points at is deleted, disabled, or
removed by a sync, the resolution row CASCADEs away (or is swept
by the registry rebuild's stale-resolution check). The key reverts
to either uncontested (one remaining contributor wins
automatically) or unresolved (multiple remaining contributors,
needs a new pick). The action becomes unrunnable in the unresolved
case until you re-pin.

### Provenance: which pack won?

Every action card shows a small badge with the pack that supplied
it, plus an extra "Unresolved" badge when the key has multiple
contributors and no winner pinned:

| Badge | Meaning |
|---|---|
| Grey: `from <pack>` | The action is uncontested — only one pack declares this key. |
| Amber: `from <pack> (overrides N)` | The action is contested and pinned; this pack's version won. Hover for the list of other contributors. |
| Amber: `Unresolved` (on host/group action cards) | Multiple packs declare this key and no winner is pinned. Run is disabled. Click through to `/action-packs` to pick. |

The API exposes this at `GET /api/actions/` as `pack_name`,
`winning_pack_id` (the `ActionPack.id` of the winner, `null` for
unresolved keys and bundled-pack actions), `unresolved` (boolean),
and `overridden_from` (every other contributor's name).

---

## Writing your own playbook (BYO)

The three example packs in
[`docs/examples/action-packs/`](../examples/action-packs/) cover the
bread and butter:

- [`minimal-pack/`](https://github.com/open-labdog/labdog/tree/main/docs/examples/action-packs/minimal-pack) — smallest
  possible pack, one action with no parameters.
- [`reboot-pack/`](https://github.com/open-labdog/labdog/tree/main/docs/examples/action-packs/reboot-pack) — a
  destructive action with a parameter, suitable as a template for
  things that modify host state.
- [`with-role/`](https://github.com/open-labdog/labdog/tree/main/docs/examples/action-packs/with-role) — a pack that
  ships its own Ansible role and reuses it from a playbook.

### Pack layout

```
<pack>/
├── pack.yml                  (optional; metadata only, not load-critical)
├── actions/
│   └── <key>/                (one directory per action)
│       ├── manifest.yml      (the LabDog action definition)
│       ├── playbook.yml      (the Ansible playbook)
│       └── roles/            (optional; action-private roles)
│           └── <role-name>/  (auto-resolved by Ansible)
└── roles/                    (optional; pack-shared roles)
    └── <role-name>/          (added to ANSIBLE_ROLES_PATH)
```

LabDog discovers actions by globbing `actions/*/manifest.yml`. Each
manifest names a `playbook` file relative to its own directory
(conventionally `playbook.yml`). An action is a directory: copy
`actions/<key>/` into another pack to override that action wholesale.

**Action-private vs shared roles.** Put a role under
`actions/<key>/roles/<role-name>/` when it is private to one action —
Ansible's playbook-adjacent role search picks it up automatically with
zero config. Use the top-level `<pack>/roles/` only for roles genuinely
reused across multiple actions.

### Manifest schema

```yaml
key: my-action               # stable identifier; collisions require an operator-pinned winner per key
name: My Action              # shown on the action card
description: >-              # one-paragraph description; shown under name
  Does a thing to the host.
icon: Zap                    # lucide-react icon name (Zap, Layers, Network, etc.)
playbook: playbook.yml       # filename relative to this manifest
version: "1.0"               # bump on breaking parameter changes
estimated_duration: "30 sec" # human-readable; shown on the card
destructive: false           # see "Destructive actions" below
supports_group: true         # can target a group of hosts?
supports_host: true          # can target a single host?
supports_fleet: false        # can target every host in the inventory?
                             # Conservative default; flip to true only for
                             # truly fleet-wide work (drift checks, etc.).
playbook_timeout_seconds: 1800  # optional; floor for the main playbook's
                             # wall-clock budget. Effective timeout is
                             # max(this, the global ansible.playbook_timeout
                             # setting). Omit to use the global setting alone.
                             # Set it for long actions (e.g. package upgrades)
                             # the short global default can't accommodate.
parameters:                  # passed as --extra-vars at run time
  - key: my_param
    label: My parameter
    type: string             # string | int | bool | choice
    default: "hello"
    required: false
    help_text: Optional tooltip shown below the input.
  - key: severity
    label: Severity
    type: choice
    choices: ["low", "medium", "high"]
    default: low
```

Unknown fields are rejected (the manifest is validated with pydantic
`extra="forbid"`), so typos fail loudly instead of silently doing
nothing. The bundled pack's manifests in
[`backend/app/ansible/actions/`](https://github.com/open-labdog/labdog/tree/main/backend/app/ansible/actions)
are working examples of every field.

### Playbook conventions

- **`hosts: all`.** LabDog generates a single-host inventory per run;
  anything more specific won't match.
- **`become: true`** when you need root. Most actions do.
- **Parameters arrive as top-level Ansible variables** via
  `--extra-vars`. A manifest param with `key: wait_seconds` becomes the
  fact `wait_seconds` in your playbook.
- **`ansible_check_mode=true`** is set on dry runs — respect it if the
  action has a dry-run path.
- **Ansible-normal exit semantics.** A failing task fails the play
  fails the runner fails the action. No custom protocol. If you need
  an explicit assertion:

  ```yaml
  - name: Assert the service is healthy
    ansible.builtin.uri:
      url: http://localhost:8080/health
      return_content: true
    register: health
    failed_when: health.status != 200 or 'ok' not in health.content
  ```

- **Roles in your pack's `roles/`** are automatically on
  `ANSIBLE_ROLES_PATH` alongside the bundled roles. `include_role: name:
  my-role` just works.

### Destructive actions and snapshot safety

Set `destructive: true` on the manifest when the action mutates host
state in a way that's hard to undo (reboots, package upgrades, config
rewrites). For destructive actions, **if the host has a Proxmox VM
mapping**, LabDog automatically:

1. Takes a Proxmox snapshot named `labdog-<run_id>-<timestamp>`.
2. Runs the playbook.
3. Runs a post-run SSH + services + packages health check.
4. On success, deletes the snapshot.
5. On failure, rolls the VM back to the snapshot, starts it, waits for
   SSH, and marks the host as `out_of_sync`.

Destructive actions on hosts **without** a VM mapping still execute —
you'll see a `[snapshot] skipped — host has no Proxmox VM mapping (no
rollback available on failure)` line in the run log, and the playbook
runs without the safety net. This is a deliberate choice: the action
system works on bare metal too.

Non-destructive actions never trigger snapshot wrapping regardless of
VM mapping.

### Custom verification (pack-supplied)

Packs can declare their own definition of success. When a manifest sets
`verify_playbook`, LabDog runs that playbook after the main one and its
`ansible-runner` exit status becomes the verification result — a
failed task fails the verify, which fails the action, which triggers
rollback.

```yaml
# actions/deploy-app/manifest.yml
key: deploy-app
name: Deploy app
destructive: true
playbook: playbook.yml
verify_playbook: verify.yml             # sibling of playbook.yml in the same action dir
verify_timeout_seconds: 120             # budget; default 300
# ...rest of the manifest
```

The verify playbook runs with the **same inventory, same extra_vars,
same pack roles** as the main playbook — no special plumbing needed.
Typical patterns:

```yaml
# actions/deploy-app/verify.yml
- name: Verify the deploy worked
  hosts: all
  gather_facts: false
  tasks:
    - name: Service is up
      ansible.builtin.systemd_service: { name: "{{ service_name }}" }
      register: svc
      failed_when: svc.status.ActiveState != "active"

    - name: App reports healthy
      ansible.builtin.uri:
        url: "http://127.0.0.1:{{ port }}/healthz"
        status_code: 200
```

**When the verify hook fires:** same gate as the built-in check —
destructive action + host with a Proxmox VM mapping + the main playbook
succeeded. Non-destructive actions or hosts without a VM mapping skip
verification entirely (there's no snapshot to protect, so the safety
net doesn't apply). If you need verification on a non-destructive
action, either mark it destructive or do the checks as tasks inside
the main playbook.

**Default behaviour when no verify_playbook is set:** LabDog runs the
built-in SSH + services + packages check (healthy if every
currently-desired service is running and every desired package is
installed). The full working example is
[`docs/examples/action-packs/with-verify/`](https://github.com/open-labdog/labdog/tree/main/docs/examples/action-packs/with-verify).

**Reusable verify templates** ship in the `labdog-playbooks` repo
under [`verify/`](https://github.com/open-labdog/labdog-playbooks/tree/main/verify)
— point `verify_playbook:` at one of them (e.g.
`../../verify/post-upgrade.yml` — `../../` reaches the pack root from
inside the action directory) and pass overrides through manifest
parameters. The bundled `linux-upgrade` action in that repo uses
`verify/post-upgrade.yml` and is the reference implementation.

### Post-run reconciliation: sync and register

An action's manifest can opt in to two post-success hooks that bridge
the action world and labdog's desired-state modules. Both are
declarative, both fire only on successful non-dry-run completion,
both fan out per-host for group-dispatched actions, and both log
without affecting the action's terminal status if they fail.

#### `post_run_sync` — re-enforce existing desired state

```yaml
post_run_sync:
  - firewall
  - services
```

After success, labdog dispatches a normal per-host sync for each
named module against the same target host. The sync routes through
the standard `host_sync_orchestrator` pipeline: per-host advisory
lock, coalesced playbook, audit row, the works.

Semantics are **push, not collect**. Use this when the action
needs labdog's existing config to be (re-)enforced after the
action runs — e.g. a kernel-upgrade action that wants the firewall
ruleset reapplied because the upgrade swapped backends, or a cert-
rotation action that wants services reloaded.

**Don't use `post_run_sync` for "install this new thing" actions.**
Those modules manage labdog's *declared* desired state. If the
action installs a service labdog doesn't declare (e.g. a fresh
`alloy.service` install), `post_run_sync: [services]` won't add
alloy to labdog's desired set; the existing cleanup pass would
also skip it (no labdog marker). For install actions, use
`post_run_register` instead.

#### `post_run_register` — register installed resources

```yaml
post_run_register:
  packages:
    - package_name: alloy
      state: present
  services:
    - service_name: alloy.service
      state: running
      enabled: true
```

After success, labdog inserts each declared resource as a **host-
scope override row** (`host_id=<target>`, `group_id=NULL`) so the
resource becomes labdog-managed from that point on. After the
inserts, a `post_run_sync` is automatically dispatched for the
affected modules so the Host detail tabs reflect the new state
immediately (no waiting for the next drift check).

Top-level keys are canonical module names: `packages`, `resolver`,
`services`, `hosts-file`, `cron`, `linux-users`, `firewall`. Each
value is a list of dicts validated against the module's REST API
Create schema — same fields, same defaults, same validators
operators get from the UI / API. `host_id` and `group_id` are
implicit and must not be declared.

**Conflict semantic: skip silently on uniqueness collision.** If
the operator has already declared the resource for that host
(e.g. `alloy.service` with `state: stopped`, deliberately), the
manifest declaration is ignored and labdog logs `post_run_register:
skipping services[0] on host=42 -- already declared`. Operator
intent wins.

**When to use `post_run_register`:** action installs something
labdog should manage going forward. Examples: an `alloy-install`
action registers `alloy.service` + the `alloy` package; an
`add-monitoring-user` action registers the new user under
`linux-users`. After the action runs, drift checks and syncs treat
the new rows like any other operator-declared row.

#### Purge-mode modules: a warning

Four module categories overwrite the host with labdog's complete
desired state on every sync (i.e. unknown entries get removed):

- **firewall** — rebuilds the nftables ruleset from scratch
- **hosts-file** — overwrites `/etc/hosts` with desired entries
- **resolver** — overwrites resolver config (resolv.conf / NM /
  systemd-resolved) with desired settings
- **SSH keys** (within `linux-users`) — exclusive mode; deletes all
  keys not in labdog's desired set

**Do not use actions to mutate these without also declaring them in
labdog's desired state — either via `post_run_register:` or via the
UI/API first.** Otherwise the next sync removes whatever the action
installed.

The other module behaviours are tolerant: `packages`, `cron`,
`linux-users` (non-SSH), and `services` (non-labdog-marked units)
leave unknown entries alone. Install-this-new-thing actions
targeting only those modules are safe even without
`post_run_register` — though without it, the resources won't
appear in the corresponding UI tabs until the next drift check
collects state.

---

## Troubleshooting

**"Sync failed" on a git pack.** The row is saved with a failure reason
— click **Edit** to see the error in the modal. Common causes: wrong
ref, expired PAT on the linked Git repository, missing ssh_key_id on
the repo. The credentials live on the linked repo — fix them under
**Git Repos**, then hit **Sync** on the pack.

**"No actions appear after adding a pack."** Check: (1) the pack's
`actions/` directory exists and contains `<key>/manifest.yml` files, (2)
each manifest's `playbook:` field names a file that actually exists,
(3) the pack is `Enabled` in the list view. If a single manifest fails
to parse the rest still load — check the API server logs for
`pack 'X': failed to load manifest …`.

**"The wrong version of an action ran."** The amber badge tells you
which pack won. If that's not what you want, pin the pack you want as
the winner for that key on the **Action Packs** page — expand the
contested row in the Active Action Catalog and pick it, or use the
pack's **Make winner for all keys** button in Pack Sources to claim
every key it contributes. Disabling the unwanted pack works too if
you don't need it at all. (There is no pack ordering — precedence is
per-key pinning, not a global priority.)

**"The action doesn't show up on a host but shows on groups."** Check
the manifest's `supports_host` / `supports_group` flags.

**"Snapshot rollback didn't happen for a failed destructive action."**
Check that the host has a VM mapping under the Proxmox integration
(host detail → Proxmox panel). Without a mapping, destructive actions
run without snapshot protection.

**Where the checkouts live.** LabDog clones git packs into
`<settings.ansible.packs_root_dir>/<pack_id>` — default
`/var/lib/labdog/packs/<pack_id>`. If you're running in Docker and
don't want cold-starts to re-clone everything, mount a persistent
volume at that path.
