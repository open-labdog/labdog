# Actions & Action Packs

**Paths:** Actions tab on [host](hosts.md) and [group](groups.md) detail
pages; management UI at `/action-packs` (sidebar → Integrations → Action
Packs).

Actions are **ad-hoc Ansible playbook runs** LabDog can trigger against a
host or a group. They're distinct from [Update Workflows](workflows.md)
(which are scheduled + opinionated about Linux/K8s upgrades) — actions
are the generic "run this playbook on that host with these parameters,
and optionally wrap it in a Proxmox snapshot so I can roll back if it
goes wrong" primitive.

The catalog of actions comes from **packs** — pluggable collections of
playbooks. LabDog ships with a bundled pack baked into the image; admins
add their own packs (from a git repo or a local directory) via the UI to
extend or override it.

- [Running actions](#running-actions)
- [Action packs](#action-packs)
  - [Bundled pack](#bundled-pack)
  - [Adding a pack](#adding-a-pack)
  - [Roles: default vs override vs local](#roles-default-vs-override-vs-local)
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

**Who can run actions:** any logged-in user. **Who can configure packs:**
superusers only.

---

## Action packs

A pack is a collection of playbooks + their LabDog manifests. Each
action key in the registry comes from exactly one pack. When two packs
declare the same key, the pack with the higher role wins (see
[below](#roles-default-vs-override-vs-local)); the shadowed pack's copy
is still contributed to the registry history for debugging.

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
packs are unreachable. You override its actions by adding a higher-role
pack that redeclares the same keys.

### Adding a pack

**Action Packs** (Integrations → Action Packs) → **Add Pack**. Required
fields:

| Field | What it is |
|---|---|
| Name | Admin-chosen label; must be unique. `bundled` is reserved. |
| Source | `Git remote` or `Local directory`. |
| Repo URL / Filesystem path | Where the pack lives. For git, any URL `git clone` understands (SSH or HTTPS). For local, an absolute path on the LabDog host. |
| Ref | Branch or tag for git sources. Ignored for local. |
| Role | `Default` (canonical baseline) or `Override` (layered customisations). See [below](#roles-default-vs-override-vs-local). Ignored for local sources — they always sit at the top. |
| Enabled | Uncheck to keep the pack configured but out of the registry. |
| Authentication | For git: `None` (public repo), `SSH deploy key`, or `HTTPS token (PAT)`. |

**For SSH auth:** paste the deploy key and the remote's `known_hosts`
entries. LabDog does **not** fall back to trust-on-first-use — you must
supply the fingerprints so the connection can verify the server. Get
them from your provider's canonical fingerprints page (e.g. GitHub's
docs) and paste the lines as-is; LabDog normalises and dedupes them.

**For HTTPS PAT:** paste the token. LabDog delivers it via
`http.extraHeader` at invocation time, so it never lands in
`remote.origin.url` or in logs.

Before saving, click **Test** in the modal. For git sources this runs
`git ls-remote` with the supplied credentials — a cheap round-trip that
validates auth + ref existence without cloning anything. For local it
just checks the path exists and has an `actions/` subdirectory.

On save, LabDog clones (or verifies) and folds the pack's actions into
the registry. If the sync fails, the row is still saved in a `failed`
state; fix the config and hit **Sync** to retry.

### Roles: default vs override vs local

Packs layer additively with four tiers:

| Tier | Where it comes from | Typical use |
|---|---|---|
| **Bundled** (tier 0) | Baked into the LabDog image | Fallback; always present |
| **Default** (tier 10) | Git pack with role = Default | Your canonical set (e.g. `labdog-playbooks`) |
| **Override** (tier 100) | Git pack with role = Override | Team / customer-specific customisations |
| **Local** (tier 1000) | Local directory pack | Admin's local experiments |

On an action-key collision, higher-tier wins. On keys that only appear
in one pack, nothing conflicts — both contribute freely and the registry
is the union.

**Common pattern:**

```
bundled         ← linux-upgrade, linux-os-upgrade, k8s-upgrade
  ↓ overridden by
labdog-default  ← adds: reboot-host, rotate-certs
  ↓ overridden by
my-team-pack    ← redeclares linux-upgrade (drains K8s first),
                  adds: deploy-frontend, rotate-secrets
  ↓ overridden by
local-pack      ← a playbook I'm still iterating on
```

Priority is **not** a number you type — admins pick a semantic role, and
LabDog derives the tier. This is by design: numeric priorities drift
and invite clever-but-fragile schemes.

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

- [`minimal-pack/`](../examples/action-packs/minimal-pack/) — smallest
  possible pack, one action with no parameters.
- [`reboot-pack/`](../examples/action-packs/reboot-pack/) — a
  destructive action with a parameter, suitable as a template for
  things that modify host state.
- [`with-role/`](../examples/action-packs/with-role/) — a pack that
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
key: my-action               # stable identifier; collisions resolve by pack role
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
[`backend/app/ansible/actions/`](../../backend/app/ansible/actions/)
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
[`docs/examples/action-packs/with-verify/`](../examples/action-packs/with-verify/).

**Reusable verify templates** ship in the `labdog-playbooks` repo
under [`verify/`](https://gitlab.lan.tyresson.se/dennis/labdog-playbooks/-/tree/main/verify)
— point `verify_playbook:` at one of them (e.g.
`../verify/post-upgrade.yml`) and pass overrides through manifest
parameters. The bundled `linux-upgrade` action in that repo uses
`verify/post-upgrade.yml` and is the reference implementation.

---

## Troubleshooting

**"Sync failed" on a git pack.** The row is saved with a failure reason
— click **Edit** to see the error in the modal. Common causes: wrong
ref, missing `known_hosts` for SSH, expired PAT. Fix the field, click
**Test** to validate, click **Save** (triggers a re-sync).

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
