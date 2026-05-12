# Reusable verify playbooks

Templates pack authors can reference (or copy) to define post-run
success for destructive actions. All three are plain Ansible playbooks
— a manifest points at one via `verify_playbook:` and LabDog runs it
after the main action, using the ansible-runner exit status as the
pass/fail signal.

The templates here encode the same kind of checks the built-in Python
verify does (SSH reachable, load sane, disk has headroom, services
running, packages installed) but as Ansible you can read, copy, and
extend. When a manifest doesn't declare `verify_playbook`, LabDog
falls back to the built-in Python check — the templates here are the
recommended opt-in replacement.

| Template | What it checks | Use when |
|---|---|---|
| [`post-upgrade.yml`](./post-upgrade.yml) | SSH round-trip, 1-min load below threshold, / below fill threshold, no failed systemd units, optional services and packages lists | Any destructive action that upgrades or mutates host state. This is what `linux-upgrade` uses. |
| [`host-reachable.yml`](./host-reachable.yml) | SSH round-trip, uptime within a configurable window | Actions that reboot — confirm the box actually rebooted and came back, not "came up, crashed, came up again." |
| [`services-active.yml`](./services-active.yml) | Each systemd unit in `expected_services` is `active` | Actions that deploy or reload specific services; want a focused "this service is running" gate. |

## Referencing a template from your pack

```yaml
# your-pack/actions/my-action/manifest.yml
key: my-action
playbook: playbook.yml
destructive: true
verify_playbook: ../../verify/post-upgrade.yml    # path relative to manifest
verify_timeout_seconds: 180
```

The relative path is resolved at load time. If the file can't be
found, the whole manifest is rejected with a clear error — typos fail
loudly, not at 3 AM when a run would have fired.

## Passing configuration

Each template has `vars:` defaults at the top of the playbook. Override
them via manifest parameters. For example, to make `post-upgrade.yml`
assert a specific service is active:

```yaml
# actions/upgrade-web/manifest.yml
key: upgrade-web
playbook: playbook.yml
destructive: true
verify_playbook: ../../verify/post-upgrade.yml
parameters:
  - key: expected_services
    label: Services that must stay active
    type: string    # UI takes a comma-separated string; see note below
    default: "nginx,postgresql"
```

Then inside `playbook.yml` (or a small adapter playbook) convert
the CSV parameter to a list Ansible can iterate. The cleanest way is
to use a list-typed parameter — at the moment LabDog's UI supports
`string | int | bool | choice`, so list inputs need CSV parsing. If
you need a rich list input, copy the template into your pack and
hardcode the list in the playbook `vars:` block.

## Composing on top of a template

`verify_playbook` only points at one file, but you can combine plays
with `import_playbook` at the top level:

```yaml
# your-pack/actions/my-action/verify.yml
- import_playbook: ../../verify/post-upgrade.yml    # ../../  = pack root
- name: Extra app-specific checks
  hosts: all
  become: false
  gather_facts: false
  tasks:
    - ansible.builtin.uri:
        url: http://127.0.0.1:8080/healthz
        status_code: 200
```

The imported playbook runs first, then your extra plays. Any failure
in either fails the verify.

## Writing your own

The templates are a starting point. The contract for any verify file:

- `hosts: all` (LabDog generates a single-host inventory).
- Any failed task → verify fails → snapshot rollback fires.
- Use `ansible.builtin.assert` or `failed_when:` to encode assertions
  explicitly rather than relying on module-level errors; the messages
  you write show up in the run log, which makes failures debuggable.
- Action parameters arrive as top-level vars — reference them as
  `{{ param_name }}`.
- No filesystem state persists between the main playbook and verify
  (each runs in its own ansible-runner working directory). Persist on
  the host if you need to hand state across.

See the main LabDog docs at `docs/ui/actions.md` for the full verify
contract, and `docs/examples/action-packs/with-verify/` for a
worked deploy-and-verify example.
