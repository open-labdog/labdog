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
- [Cluster-mode actions](#cluster-mode-actions)
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

### Cluster-mode actions

Most actions fan out per-host: LabDog dispatches one Celery task per
target host with a single-host inventory and a parallelism setting
controls how many run concurrently. A few actions need the whole
group's hosts visible to a single ansible run — for example,
`k8s-upgrade` drains, upgrades, and re-admits each node in turn while
the rest of the cluster keeps serving traffic. Such actions declare
`execution_mode: cluster` in their manifest.

**What's different in the UI:**

- The action card only appears on group views (cluster-mode actions
  set `supports_host: false`).
- Every group member must carry a **cluster role** — `control_plane`
  or `worker`. Set them on the group's Members tab via the per-row
  role picker. The Run dialog refuses to submit until every member
  is assigned and at least one `control_plane` exists.
- The parallelism picker is hidden — cluster runs are always one
  ansible-runner invocation against a multi-host inventory; the
  playbook decides ordering with Ansible's `serial:` keyword.
- The run-detail page hides the per-host status grid (there's exactly
  one `ActionHostRun` anchored to the first control-plane host as the
  driver). Watch the streamed ansible stdout for per-node progress.

The bundled `k8s-upgrade` is currently **apt-only** — Debian and
Ubuntu nodes. RHEL / Rocky / Alma support is on the roadmap.

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
declare the same key, the pack at the top of the list on
**Action Packs** wins (see
[below](#pack-precedence-and-resolving-conflicts)); the shadowed pack's
copy is still contributed to the registry history for debugging.

### Bundled pack

LabDog's container image includes a "bundled" pack with three baseline
actions:

| Key | What it does |
|---|---|
| `linux-upgrade` | Upgrades all system packages; reboots if `/var/run/reboot-required`. |
| `linux-os-upgrade` | Major-release upgrade (e.g. Debian 12 → 13, Ubuntu 22.04 → 24.04). |
| `k8s-upgrade` | Drains, upgrades, and re-admits each node in a Kubernetes cluster. |

The bundled pack is immutable — you can't edit or delete it from the UI.
It exists as a safety net so LabDog keeps working even if all other
packs are unreachable. You override its actions by adding a pack —
any pack listed on **Action Packs** sits above bundled by default.

### Adding a pack

Git repository configuration (URL, branch, credentials) is **not**
duplicated on the pack. Configure the repo once under
[Git Repos](gitops-ui.md), then point one or more packs at it. The
pack only carries its own metadata — name, source type, a subpath,
position, enabled flag.

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
exists anywhere but the repo root has an `actions/*.manifest.yml`
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

A new pack lands at the top of the list (highest position). Drag rows
to reorder afterwards — see
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

Packs layer additively. The **Action Packs** page shows a single
ordered list — top of the list wins on action-key collisions. The
bundled pack sits implicitly below every listed pack and never
appears as a row. On keys that only appear in one pack, nothing
conflicts — both contribute freely and the registry is the union.

Drag rows to reorder. Top-first ordering matches the firewall-rules
page convention: the row at the top has the highest priority.

**Common pattern:**

```
my-local-pack   ← a playbook I'm still iterating on (top)
  ↓ overrides
my-team-pack    ← redeclares linux-upgrade (drains K8s first),
                  adds: deploy-frontend, rotate-secrets
  ↓ overrides
labdog-default  ← adds: reboot-host, rotate-certs
  ↓ overrides
bundled         ← linux-upgrade, linux-os-upgrade, k8s-upgrade
                  (implicit; not shown on the page)
```

#### Operator-pinned winners

Drag-to-reorder is the default mechanism, but every contested key
can also be pinned individually. The **conflict banner** at the top
of **Action Packs** lists keys contributed by more than one pack —
click it to open the resolution modal. Each row offers a radio per
candidate pack (including bundled when it contributes). The pin
sticks across reorders and survives sync until you reset it.

#### Freeze-on-fresh-conflict

LabDog never silently flips a winner. When a sync introduces a new
manifest that turns a previously-uncontested key into a contested
one, LabDog **freezes** the winner to whichever pack was previously
serving that key — even if position-based default would now favour
the newcomer. The conflict banner highlights frozen rows with a
"frozen" badge so you can confirm the choice (or pick a different
pack). Once you set or clear a resolution explicitly, the freeze
clears.

### Provenance: which pack won?

Every action card shows a small badge with the pack that supplied it:

| Badge | Meaning |
|---|---|
| Grey: `from <pack>` | The action is uncontested — only one pack declares this key. |
| Amber: `from <pack> (overrides N)` | The action collided; this pack's version won. Hover the badge for the list of shadowed packs. |

The API exposes this at `GET /api/actions/` as `pack_name` and
`overridden_from` fields on each action. Logs also record the override
chain when the registry loads:

```
INFO app.actions.packs action 'linux-upgrade' from pack 'labdog-default' overrides pack 'bundled'
INFO app.actions.packs action 'linux-upgrade' from pack 'my-team-pack' overrides pack 'labdog-default'
```

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
├── pack.yml              (optional; metadata only, not load-critical)
├── actions/
│   ├── <key>.yml         (the Ansible playbook)
│   └── <key>.manifest.yml  (the LabDog action definition)
└── roles/                (optional)
    └── <role-name>/      (standard Ansible role layout)
```

LabDog discovers actions by globbing `actions/*.manifest.yml`. Each
manifest names a `playbook` file relative to the manifest's directory.
A playbook without a matching manifest is silently ignored.

### Manifest schema

```yaml
key: my-action               # stable identifier; collisions resolve by pack position (or operator pin)
name: My Action              # shown on the action card
description: >-              # one-paragraph description; shown under name
  Does a thing to the host.
icon: Zap                    # lucide-react icon name (Zap, Layers, Network, etc.)
playbook: my-action.yml      # filename relative to this manifest
version: "1.0"               # bump on breaking parameter changes
estimated_duration: "30 sec" # human-readable; shown on the card
destructive: false           # see "Destructive actions" below
supports_group: true         # can target a group of hosts?
supports_host: true          # can target a single host?
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
# actions/deploy-app.manifest.yml
key: deploy-app
name: Deploy app
destructive: true
playbook: deploy-app.yml
verify_playbook: deploy-app-verify.yml  # runs after deploy-app.yml
verify_timeout_seconds: 120             # budget; default 300
# ...rest of the manifest
```

The verify playbook runs with the **same inventory, same extra_vars,
same pack roles** as the main playbook — no special plumbing needed.
Typical patterns:

```yaml
# actions/deploy-app-verify.yml
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
`../verify/post-upgrade.yml`) and pass overrides through manifest
parameters. The bundled `linux-upgrade` action in that repo uses
`verify/post-upgrade.yml` and is the reference implementation.

---

## Troubleshooting

**"Sync failed" on a git pack.** The row is saved with a failure reason
— click **Edit** to see the error in the modal. Common causes: wrong
ref, expired PAT on the linked Git repository, missing ssh_key_id on
the repo. The credentials live on the linked repo — fix them under
**Git Repos**, then hit **Sync** on the pack.

**"No actions appear after adding a pack."** Check: (1) the pack's
`actions/` directory exists and contains `*.manifest.yml` sidecars, (2)
each manifest's `playbook:` field names a file that actually exists,
(3) the pack is `Enabled` in the list view. If a single manifest fails
to parse the rest still load — check the API server logs for
`pack 'X': failed to load manifest …`.

**"The wrong version of an action ran."** The amber badge tells you
which pack won. If that's not what you want, either raise the winning
pack's role (Override → Default is a demotion, confusingly — Override
beats Default on collision) or disable the winning pack.

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
