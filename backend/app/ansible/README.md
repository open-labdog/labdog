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
├── pack.yml                 metadata (name, description — not load-critical)
├── actions/
│   ├── <key>.yml            Ansible playbook
│   └── <key>.manifest.yml   LabDog action definition (sidecar)
├── verify/                  reusable verify playbooks (see verify/README.md)
│   ├── post-upgrade.yml
│   ├── host-reachable.yml
│   └── services-active.yml
└── roles/                   Ansible roles referenced by playbooks
    └── <role-name>/
```

LabDog discovers actions by globbing `actions/*.manifest.yml`. A
playbook without a matching manifest is ignored. Every manifest is
validated against a pydantic schema with `extra="forbid"` — typos fail
loudly rather than silently.

## Manifest schema

```yaml
key: linux-upgrade                 # stable identifier
name: Upgrade Linux packages       # shown in the UI
description: …                     # one-paragraph description
icon: ArrowUpFromLine              # lucide-react icon name
playbook: linux-upgrade.yml        # filename relative to this manifest
version: "1.0"                     # bump on breaking parameter changes
estimated_duration: "5–15 min"
destructive: true                  # enables snapshot/verify/rollback when
                                   # the host has a Proxmox VM mapping
supports_group: true               # can target a group of hosts?
supports_host: true                # can target a single host?
execution_mode: per_host           # per_host (default) or cluster.
                                   # cluster = single ansible run against
                                   # the whole group with a multi-host
                                   # inventory; see "Cluster-mode actions"
                                   # below.
verify_playbook: ../verify/post-upgrade.yml   # (optional) pack-supplied
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
admins can point their manifests at (`verify_playbook: ../verify/…`),
or pack authors can `import_playbook:` from their own custom verify
files. They cover the same ground as LabDog's built-in Python verify
(SSH round-trip, load, disk, systemd failed-unit detection, explicit
service and package lists) but as inspectable, copyable Ansible.

See [`verify/README.md`](./verify/README.md) for the full list and
usage patterns.

## Adding a new action

1. Drop `actions/<key>.yml` and `actions/<key>.manifest.yml` into place.
2. If your playbook needs a role, put it under `roles/` — LabDog joins
   every loaded pack's `roles/` dir into `ANSIBLE_ROLES_PATH`, so
   `include_role: name: <role>` just works.
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
  inventory per run. Cluster-mode actions get a multi-host inventory
  shaped under ``all.children.{control_plane,workers}``; see the
  Cluster-mode actions section below.
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

## Cluster-mode actions

Actions whose manifest declares ``execution_mode: cluster`` are
dispatched as a single ``ansible-playbook`` invocation against a
multi-host inventory (rather than the per-host fan-out used for
``per_host`` actions). The bundled
[`actions/k8s-upgrade/`](./actions/k8s-upgrade) is the canonical
example — it drains, ``kubeadm``-upgrades, and re-admits each node
serially via Ansible's own ``serial:`` keyword across the
``control_plane`` and ``workers`` groups.

For a cluster-mode pack:

- Set ``execution_mode: cluster`` plus ``supports_host: false`` and
  ``supports_group: true`` in the manifest.
- Provide a directory at ``actions/<key>/`` instead of a flat
  ``actions/<key>.yml``. Point ``playbook:`` at the entry file
  (e.g. ``<key>/site.yml``).
- The play layer does the orchestration with ``serial:``, ``hosts:``,
  ``delegate_to:``. LabDog hands you a ``control_plane`` and
  ``workers`` group in the inventory; the operator assigns the role
  on each member from the group's Members tab.
- Cluster-wide ``kubectl`` tasks are easy: ``delegate_to:
  "{{ groups['control_plane'] | first }}"`` so the playbook doesn't
  need a kubeconfig on every node.

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
