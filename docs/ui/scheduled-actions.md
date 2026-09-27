# Schedules

**Path:** `/actions?tab=schedules` (Operations → Actions → Schedules;
`/schedules` redirects), and a host's or group's Activity tab ›
**Schedules** for the schedules that target it.

Scheduled Actions are cron-driven runs of any registered
[Action](actions.md) — pack-supplied (e.g. `linux-upgrade`,
`linux-os-upgrade`, `k8s-upgrade`). The scheduler ticks every 60 s,
walks the `scheduled_actions` table, and dispatches due rows through
the same execution path as an ad-hoc run. There's no separate
"scheduled-only" or "ad-hoc-only" action type.

> Built-in pseudo-actions (`_builtin.sync`, `_builtin.drift_check`,
> `_builtin.collect_state`) are **not** surfaced in the new-schedule
> dialog — they have dedicated UI entry points (Plans, the drift
> check buttons, collect). The data model still accepts them, so a
> GitOps YAML can declare a `_builtin.*` schedule and existing rows
> targeting them keep firing.

> **Scheduled Actions vs. [Actions](actions.md):** Actions are the
> primitive — a playbook + manifest (or a built-in pseudo-action) you
> can run ad-hoc. Scheduled Actions are the same primitive plus a
> target, a cron schedule, and (for destructive actions) the
> snapshot / verify / rollback toggles. Same dispatch path, same
> safety net.

---

## Targets

A schedule binds an action to one of three target shapes:

| Kind | Meaning | Available when |
|------|---------|----------------|
| **Host** | Runs against one host. | Action's manifest sets `supports_host: true` (default). |
| **Group** | Runs against every member of a host group. | `supports_group: true` in the manifest (default). |
| **Fleet** | Runs against every host in the inventory. | `supports_fleet: true` — opt-in only. The three built-ins set this for `_builtin.drift_check` and `_builtin.collect_state`; pack-supplied actions default to `false`. |

Fleet runs are **schedule-only** — there's no ad-hoc fleet path
through `POST /api/actions/runs`. The `action_runs` check constraint
enforces this: a row with both `host_id` and `group_id` NULL is only
accepted when `scheduled_action_id` is set.

---

## The list

**Path:** `/actions?tab=schedules`

One row per schedule:

| Column | Description |
|--------|-------------|
| action | The action's name, with a **built-in** tag or its pack |
| target | The host or group (linked to its page), or *All hosts* for the fleet |
| schedule | The cron expression, with a plain-English reading |
| last run | The last run's status and how long ago |
| enabled | Whether it fires |
| options | **snap**, **verify** and **rollback** tags — on or off — for destructive actions |

Each row has **edit · runs · delete**. **runs** opens the schedule's run
history in a dialog — the latest 20 `action_runs` for that schedule, each
linking to its run screen — with **Run now** in the footer (disabled while
a run is already in flight). Delete asks first.

The same list, without the target column, is on each host's and group's
Activity tab › Schedules.

---

## Creating a schedule

Three entry points share the same dialog (`<ScheduleActionDialog>`):

1. **Schedules tab → "+ New"** (or **Create a schedule** when there are
   none) — full picker walk: pick action, pick target, fill parameters,
   set cron, review, submit.
2. **"schedule…"** on an action in the Library, or in a host's or group's
   Activity › Actions — preselects the action_key (and the target, from a
   host or group). Operator only fills parameters + cron.
3. **"Schedule action"** on a host's or group's Activity › Schedules —
   preselects the target. Operator picks action + parameters + cron.

The dialog is a four-step wizard — the steps across the top, **Back** and
**Continue** at the foot:

| Step | What |
|------|------|
| **Action & target** | Action picker (pack-supplied actions only — `_builtin.*` pseudo-actions have their own UI surfaces and are hidden here), target choice (Host / Group / Fleet — Fleet disabled when the action doesn't `supports_fleet`), and the host or group selector. |
| **Parameters** | Form generated from the action's manifest. String / int / bool / choice — same shape as the ad-hoc run dialog. |
| **Schedule** | 5-field cron input. Live `cronToHuman` preview, server-validated next-3-fire-times, plus quick picks (*Every 15 minutes*, *Hourly*, *Nightly (03:00 UTC)*, *Weekdays 03:00 UTC*, *Weekly Sun 03:00 UTC*, *Monthly 1st 03:00 UTC* …). |
| **Review** | Read-only summary. **Destructive options block** is shown only when `action.destructive=true`: snapshot, verify, auto_rollback toggles + batch_size for non-host targets. |

Submit creates the row via `POST /api/scheduled-actions`. On success
the dialog closes, the list (and any per-target Schedules view)
refreshes, and the row appears immediately.

`action_key` and `target_*` are **immutable on edit** — re-creating is
the right path for "I want a different action against this target."
The backend rejects edits that change them with `422`.

---

## Run history

Every scheduled run creates an `action_run` row with
`scheduled_action_id` pointing at the schedule. The same table holds
ad-hoc runs (where `scheduled_action_id` is NULL), so:

- A schedule's **runs** dialog shows runs for that schedule.
- A host's Activity › Actions shows recent runs for that host (both
  ad-hoc and scheduled).
- A group's Activity › Runs shows runs for that group.
- [Operations › Runs](operations.md#runs) shows everything.

Per-host detail (status, output, failures) is the existing
`<ActionRunDetail>` component. Fleet runs use a generic
`/actions/runs/{runId}` route since they don't have a single host or
group context.

Deleting a schedule sets `action_runs.scheduled_action_id` to NULL via
`ON DELETE SET NULL` — run history is preserved.

---

## Concurrency & idempotency

- The cron walk skips a schedule if a non-terminal `ActionRun`
  (`status IN ('queued', 'running')`) already exists for it. No
  double-dispatch when the previous run hasn't finished.
- Per-host work is serialised via a PostgreSQL advisory lock
  (`pg_try_advisory_lock(hashtext('host_sync.{host_id}'))`) for
  `_builtin.sync` — the same per-host orchestrator used by every
  module sync. The two read-only built-ins
  (`_builtin.drift_check`, `_builtin.collect_state`) are idempotent
  and don't need it.
- `last_dispatched_at` is the cron walk's reference, not "wall-clock
  now" — so a missed tick (worker restart, Redis hiccup) doesn't
  fire-twice on the next minute.
- The ad-hoc create-run endpoint takes a per-target advisory transaction
  lock, so two concurrent `POST /run-now` against the same schedule
  collide cleanly with a 409.

---

## GitOps

A group YAML can declare `scheduled_actions:` as a list, one entry
per action_key:

```yaml
scheduled_actions:
  - action_key: linux-upgrade
    enabled: true
    schedule_cron: "0 3 * * 0"
    parameters: {}
    batch_size: 1
    snapshot_enabled: true
    auto_rollback: true
```

Semantics: leave-alone-on-absence — section absent ⇒ DB rows
untouched; section present (even `[]`) ⇒ delete-and-replace among
rows where `target_kind='group' AND target_id=this_group`. An empty
list deletes every schedule for the group.

See [`docs/examples/gitops/modules/scheduled-actions.yaml`](../examples/gitops/modules/scheduled-actions.yaml)
for a fully-commented example.

---

## Permissions

All `/api/scheduled-actions/*` endpoints require **superuser**.
Scheduling work that affects shared infrastructure is privileged.
Ad-hoc runs (`POST /api/actions/runs`) are still open to any
authenticated user.
