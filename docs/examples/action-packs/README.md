# Action Pack Examples

Three working examples of the pack format, each focused on a different
concept. Drop any of these into a git repo or local directory, add it
via **Action Packs** in the LabDog UI, and the actions show up on host
and group detail pages.

For the mechanics — what a pack is, how it loads, how overrides resolve
— see [the Actions user guide](../../ui/actions.md). For the end-to-end
reference of all manifest fields, see
[the `labdog-playbooks` README](https://github.com/open-labdog/labdog-playbooks).

| Example | What it demonstrates |
|---|---|
| [`minimal-pack/`](https://github.com/open-labdog/labdog/tree/main/docs/examples/action-packs/minimal-pack) | The smallest possible pack. One action, no parameters, no roles, no destructive wrapping. Start here. |
| [`reboot-pack/`](https://github.com/open-labdog/labdog/tree/main/docs/examples/action-packs/reboot-pack) | A realistic destructive action with a parameter. Shows how `destructive: true` activates the Proxmox snapshot → verify → rollback pipeline automatically. |
| [`with-role/`](https://github.com/open-labdog/labdog/tree/main/docs/examples/action-packs/with-role) | A pack that ships its own Ansible role and reuses it from a playbook. Shows how `ANSIBLE_ROLES_PATH` resolution works across bundled + pack roles. |
| [`with-verify/`](https://github.com/open-labdog/labdog/tree/main/docs/examples/action-packs/with-verify) | A destructive action with a pack-supplied verify playbook that decides pass/fail after the main playbook runs. Replaces LabDog's built-in SSH/services/packages check with the pack's own definition of success. |

## Quick start

```bash
# Clone or copy one of these into a place on your LabDog host
cp -r docs/examples/action-packs/minimal-pack /srv/labdog-packs/my-first-pack

# Or make it a git repo
cd /srv/labdog-packs/my-first-pack
git init -b main
git add -A
git commit -m "initial pack"
```

Then in the LabDog UI:

1. Go to **Action Packs** (sidebar → Integrations → Action Packs).
2. Click **Add Pack**.
3. Either point at a git URL, or pick **Local directory** and paste the
   filesystem path.
4. Leave Role as **Override** (you're not replacing a default pack).
5. Save. The action will appear on any host's detail page under the
   **Actions** tab.

## Pack layout recap

```
<pack>/
├── pack.yml              (optional — pack metadata)
├── actions/
│   ├── <key>.yml         (the Ansible playbook)
│   └── <key>.manifest.yml  (the LabDog action definition)
└── roles/                (optional)
    └── <role-name>/      (standard Ansible role layout)
```

Every manifest is a sidecar next to its playbook, named
`<same-stem>.manifest.yml`. LabDog discovers actions by globbing
`actions/*.manifest.yml` — a playbook without a manifest is ignored.
