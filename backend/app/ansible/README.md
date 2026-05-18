# labdog-playbooks

Default action pack for [LabDog](https://github.com/open-labdog/labdog).

LabDog admins add this repo as an action pack through the UI
(**Integrations → Action Packs → Add Pack**). Playbooks here override
the bundled pack inside the LabDog image and are themselves overridable
by additional user packs — pack precedence is managed by drag-to-
reorder on the **Action Packs** page (top wins).

For the full user-facing guide — how packs load, how collisions
resolve, how to write your own — see
[`docs/ui/actions.md`](https://github.com/open-labdog/labdog/blob/main/docs/ui/actions.md)
in the main labdog repo. This README covers the pack-author angle only.

## Pack layout

```
.
├── pack.yml                       metadata (name, description — not load-critical)
├── actions/
│   └── <key>/                     one directory per action
│       ├── manifest.yml           LabDog action definition
│       ├── playbook.yml           Ansible playbook
│       └── roles/                 (optional) action-private roles
│           └── <role-name>/       auto-resolved by Ansible
├── verify/                        reusable verify playbooks (see verify/README.md)
│   ├── post-upgrade.yml
│   ├── host-reachable.yml
│   └── services-active.yml
└── roles/                         shared roles, reusable across actions
    └── <role-name>/
```

An **action is a directory**: one folder under `actions/` per action,
containing a `manifest.yml` and a `playbook.yml` at minimum. To override
a single action in a downstream pack, copy that one directory.

LabDog discovers actions by globbing `actions/*/manifest.yml`. Every
manifest is validated against a pydantic schema with `extra="forbid"` —
typos fail loudly rather than silently.

**Roles.** Put a role under `actions/<key>/roles/<role-name>/` when it
is private to one action (Ansible's playbook-adjacent role search picks
it up with zero configuration). Use the top-level `roles/` for roles
that are genuinely shared across actions — LabDog adds it to
`ANSIBLE_ROLES_PATH` for every playbook in the pack.

## Manifest schema

```yaml
key: linux-upgrade                 # stable identifier
name: Upgrade Linux packages       # shown in the UI
description: …                     # one-paragraph description
icon: ArrowUpFromLine              # lucide-react icon name
playbook: playbook.yml             # filename relative to this manifest
version: "1.0"                     # bump on breaking parameter changes
estimated_duration: "5–15 min"
destructive: true                  # enables snapshot/verify/rollback when
                                   # the host has a Proxmox VM mapping
supports_group: true               # can target a group of hosts?
supports_host: true                # can target a single host? Set to
                                   # false to make this a group-only
                                   # action: LabDog will dispatch as a
                                   # single ansible-playbook invocation
                                   # against a flat all-hosts inventory
                                   # (instead of fanning out per-host).
                                   # See "Group-dispatch actions" below.
verify_playbook: ../../verify/post-upgrade.yml   # (optional) pack-supplied
verify_timeout_seconds: 180                   # verify hook; replaces the
                                              # built-in SSH/services/
                                              # packages check. See
                                              # verify/README.md.
parameters:                        # passed as --extra-vars at runtime
  - key: auto_reboot
    label: Reboot if required
    type: bool                     # string | int | bool | choice
    default: true
    required: false
    help_text: Optional tooltip shown below the input.
```

Full manifest field reference and edge cases:
[docs/ui/actions.md#manifest-schema](https://github.com/open-labdog/labdog/blob/main/docs/ui/actions.md#manifest-schema).

## Verify playbooks

The [`verify/`](./verify/) directory holds reusable verify playbooks
admins can point their manifests at (`verify_playbook: ../../verify/…`),
or pack authors can `import_playbook:` from their own custom verify
files. They cover the same ground as LabDog's built-in Python verify
(SSH round-trip, load, disk, systemd failed-unit detection, explicit
service and package lists) but as inspectable, copyable Ansible.

See [`verify/README.md`](./verify/README.md) for the full list and
usage patterns.

## Adding a new action

1. Create `actions/<key>/` with `manifest.yml` and `playbook.yml`.
2. If your playbook needs a role:
   - **Action-private** (used only by this action): put it under
     `actions/<key>/roles/<role-name>/`. Ansible auto-resolves it via
     playbook-adjacent role search — no config needed.
   - **Shared** (used by multiple actions): put it under the top-level
     `roles/` — LabDog joins every loaded pack's `roles/` dir into
     `ANSIBLE_ROLES_PATH`, so `include_role: name: <role>` just works.
3. If the action is destructive and you want a custom success gate,
   add a `verify_playbook:` to the manifest pointing at one of the
   templates in `verify/` or your own.
4. Test against a LabDog dev instance (below).
5. Commit and push. LabDog instances that have this repo configured as
   a pack will pick up the change on next startup, or immediately when
   an admin clicks **Sync** on the pack in the UI.

## Testing against a local LabDog

Either:

- **Git sync flow** — push your branch somewhere LabDog can reach,
  add a GitRepository under **Integrations → Git Repos** with any
  credentials the repo needs, then add an Action Pack pointing at
  that repo. New packs land at the top of the precedence list; drag
  to reorder.
- **Local filesystem flow** — add a pack via the UI with
  `source_type = local` and point `repo_url` at the absolute path of
  your working copy. No clone happens; LabDog reads manifests in
  place. Fastest iteration loop.

After either, hit **Sync** (UI) or `POST /api/action-packs/{id}/sync`
and the action will appear. `GET /api/actions/` lists every resolved
action along with its winning pack name and override history.

## Playbook conventions

- `hosts: all` — for per-host actions LabDog generates a single-host
  inventory per run. Group-dispatch actions (`supports_host: false`
  in the manifest) get a flat multi-host inventory under ``all`` —
  every member host as a peer, no `children` grouping. See the
  Group-dispatch actions section below.
- `become: true` when you need root.
- Parameters arrive as top-level Ansible variables from `--extra-vars`.
- `ansible_check_mode=true` is set on dry runs — respect it if the
  action has a dry-run path.
- Any Ansible-normal failure is how LabDog knows the action failed.
  No custom exit-code protocol — `failed_when` and
  `ansible.builtin.assert` are your friends.

## Precedence recap

Packs layer in a single linear ordering on the **Action Packs** page.
The pack at the top of the list wins on action-key collisions; bundled
sits implicitly at the bottom (no DB row).

```
my-local-pack         (top of list — highest position)
    └── overrides →
my-team-overrides
    └── overrides →
labdog-playbooks
    └── overrides →
bundled (image-baked, implicit position 0)
```

Same action key → highest-positioned pack wins, shadowed packs still
tracked for provenance (shown as amber badge in the UI). Different
keys → both coexist in the registry. Operators reorder packs by
drag-and-drop; per-key pins are also available from the conflict
banner at the top of the page.

## Group-dispatch actions

Actions whose manifest declares ``supports_host: false`` are
dispatched as a single ``ansible-playbook`` invocation against a
flat multi-host inventory (rather than the per-host fan-out used for
``supports_host: true`` actions). The bundled
[`actions/k8s-upgrade/`](./actions/k8s-upgrade) is the canonical
example — it drains, ``kubeadm``-upgrades, and re-admits each node
serially via Ansible's own ``serial:`` keyword.

For a group-dispatch pack:

- Set ``supports_host: false`` plus ``supports_group: true`` in the
  manifest. LabDog will refuse host-target submissions and dispatch
  group-target submissions in a single invocation.
- LabDog hands you a flat ``all`` group containing every member host.
  The pack does its own topology discovery (e.g. ``add_host`` based
  on a stat probe) and uses Ansible primitives (``serial:``,
  ``delegate_to:``, ``run_once:``) for cluster-wide coordination.
- For ``kubectl``-style tasks that should only run from one node:
  combine ``add_host`` (build a dynamic role group in a setup play)
  with ``delegate_to: "{{ groups['<your-group>'] | first }}"``.
- **Destructive group-dispatched actions get the same per-host
  snapshot/verify/rollback envelope as per-host actions.** LabDog
  snapshots every member with a VM mapping pre-action, runs verify
  (your ``verify_playbook`` if declared, else the built-in
  SSH/services/packages check) per-host post-action, and rolls back
  any host whose action or verify failed (per-host policy — successes
  keep their state). Make the playbook idempotent so the operator
  can re-run it against the same group after fixing a partial
  failure (e.g. probe the node's current state and short-circuit if
  it's already where the playbook would leave it).

The k8s-upgrade role under
[`actions/k8s-upgrade/roles/kubernetes-upgrade/`](./actions/k8s-upgrade/roles/kubernetes-upgrade/)
is a good template — it's apt-only today; RHEL support is on the
LabDog roadmap.

## Examples

Starter packs demonstrating different patterns live in the main
labdog repo at
[`docs/examples/action-packs/`](https://github.com/open-labdog/labdog/tree/main/docs/examples/action-packs):

- `minimal-pack/` — smallest possible pack, one action.
- `reboot-pack/` — destructive action with a parameter.
- `with-role/` — pack that ships its own Ansible role.
- `with-verify/` — pack-supplied verify playbook with healthz/version
  probes.

## License

Licensed under the GNU Affero General Public License v3.0 or later
(**AGPL-3.0-or-later**). See [LICENSE](LICENSE) for the full text.

Copyright © 2026 Dennis Tyresson and contributors.
