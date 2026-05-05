# labdog-playbooks

Default action pack for [LabDog](https://github.com/open-labdog/labdog).

LabDog admins add this repo as an action pack through the UI
(**Integrations → Action Packs → Add Pack**) with role `Default`.
Playbooks here override the bundled pack inside the LabDog image and
are themselves overridable by additional user packs.

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
supports_group: false              # can target a group of hosts?
supports_host: true                # can target a single host?
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
  that repo with role = Default.
- **Local filesystem flow** — add a pack via the UI with
  `source_type = local` and point `repo_url` at the absolute path of
  your working copy. No clone happens; LabDog reads manifests in
  place. Fastest iteration loop.

After either, hit **Sync** (UI) or `POST /api/action-packs/{id}/sync`
and the action will appear. `GET /api/actions/` lists every resolved
action along with its winning pack name and override history.

## Playbook conventions

- `hosts: all` — LabDog generates a single-host inventory per run; the
  host is the only member.
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
