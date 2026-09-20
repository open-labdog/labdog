# Operations

The Operations zone holds the things that *happen* to the fleet: plans,
drift findings, actions, runs and the audit trail. The rule the zone is
built on: an operation you start is contextual (every **Plan sync** button
in the app lands here), and an operation that happened is an object with a
URL.

## Plans

**Path:** `/plans`, `/plans?scope=group:<id>&modules=firewall,services`,
`/plans?hosts=1,2,3`

Plan → apply as a screen rather than a dialog. A plan is a scope (a group,
or a hand-picked set of hosts) and a set of modules.

1. **Define** — pick a group or tick hosts, tick the modules (all seven
   sync modules by default; CA certificates are applied by their own action
   and are not part of a plan). **Compute plan** runs the dry run.
2. **Review** — every host in scope gets a row: `+n −n ~n` change counts,
   *no change*, *skipped* (no SSH key — skipped, not failed) or *preview
   failed*. Expand a row for the per-module diff. Each changing host has a
   checkbox; the blast radius, the acknowledgements and the run itself are
   derived from that **selection**, so a partial run is never described with
   the whole plan's numbers. Pre-flight checks on the right: SSH lockout
   prevention, reachability, firewall backend detection, and whether
   rollback is possible. Apply is armed only after ticking every
   acknowledgement and typing the number of hosts that will change.
3. **Apply** — one coalesced playbook per selected host, queued through the
   per-host serialisation so a busy host waits its turn. The screen polls the
   jobs and shows each host's outcome; the global sync tray follows along.
4. **Result** — applied / failed / excluded / unchanged / skipped, a card
   per failed host with the error and a jump to the host or the assistant,
   and **Plan the remaining** when hosts were excluded.

The URL carries the definition, so a second pair of eyes can open exactly
the same plan and see the same diff before anyone clicks Apply. **Re-plan**
recomputes; **Discard** starts over.

## Drift

**Path:** `/drift`

What the fleet actually looks like versus what it was told to look like, as
a findings list. A finding is one (host, module) whose last collection
disagreed with desired state; a failed collection is a high-severity
finding of its own. Group by host or by module, filter by severity.

- **Remediate** writes a plan for exactly that host and module (or every
  finding in the panel) — nothing here applies anything directly.
- **Re-check** queues a state collection for the host(s); **Re-check fleet**
  does it for everyone.
- **Inspect** opens the host's Config tab on that module to compare desired
  and effective state.

When drift checking is off on every host the page says so instead of
showing an empty list as good news. See [drift detection](drift-detection.md)
for what a check is and the two `drift_check_enabled` flags.

## Actions

**Path:** `/actions` (`?tab=library|packs|schedules`)

- **Library** — every registered action (pack-supplied; built-ins are not
  listed), its pack, what it does, whether it is destructive, contested
  (*unresolved* — pick a winning pack under Packs first) or re-syncs
  modules afterwards, and how often it has run. **run…** asks for a host or
  group target and then opens the run dialog; **schedule…** opens the
  schedule wizard preselected on the action.
- **Packs** — the pack sources and per-key resolution, unchanged from the
  former `/action-packs` page, which redirects here. See
  [actions.md](actions.md).
- **Schedules** — cron-driven runs, unchanged from the former `/schedules`
  page, which redirects here. See [scheduled-actions.md](scheduled-actions.md).

## Runs

**Path:** `/runs`

One stream, newest first: sync jobs (applies), action runs, scheduled runs
and state collections, with kind and status filters. A run with a page of
its own (action runs, including scheduled ones) opens it; a sync job opens
its host. The last 100 of each source.

## Audit

**Path:** `/audit`

The append-only record of every change with before/after state — unchanged.
See [admin.md](admin.md#audit-log).
