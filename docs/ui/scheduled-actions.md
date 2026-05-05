# Scheduled Actions

**Path:** `/schedules` (sidebar) and `/hosts/{id}/?tab=schedules` /
`/groups/{id}/?tab=schedules` (per-target tabs).

Scheduled Actions are cron-driven runs of any registered
[Action](actions.md) — pack-supplied (e.g. `linux-upgrade`,
`linux-os-upgrade`, `k8s-upgrade`) or built-in (`_builtin.sync`,
`_builtin.drift_check`, `_builtin.collect_state`). The scheduler ticks
every 60 s, walks the `scheduled_actions` table, and dispatches due
rows through the same execution path as the ad-hoc Run button. There's
no separate "scheduled-only" or "ad-hoc-only" action type.

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

## List page

**Path:** `/schedules`

Filter strip:

- **Category** — Built-in / Pack action / All.
- **Target** — Host / Group / Fleet / All.
- **Enabled-only** toggle.
- **Search** — free text over action name + target.

Each row shows the action (with a `built-in` badge or pack name), the
target (linked to `/hosts/{id}` or `/groups/{id}`; "All hosts" for
fleet), the cron expression with a human-readable preview, the last
run's status badge + relative timestamp, and three icon chips for
snapshot / verify / auto-rollback (only when the action is
destructive). The kebab menu offers Edit, Run now, View runs, Delete.

Click a row to open the run-history drawer — the latest 20
`action_runs` for that schedule, each linking to the run-detail page.

---

## Creating a schedule

Three entry points share the same dialog (`<ScheduleActionDialog>`):

1. **`/schedules` → "+ New"** — full picker walk: pick action, pick
   target, fill parameters, set cron, review, submit.
2. **Action card → "Schedule…"** (on `/hosts/{id}?tab=actions` or
   `/groups/{id}?tab=actions`) — preselects the action_key and the
   target. Operator only fills parameters + cron.
3. **Host / group detail → "Schedule action"** (on the **Schedules**
   tab) — preselects the target. Operator picks action + parameters
   + cron.

The dialog is a four-step wizard:

| Step | What |
|------|------|
| **Action & target** | Action picker (grouped: Built-in / Pack-supplied), target radio (Host / Group / Fleet — Fleet greyed when the action doesn't `supports_fleet`), and the host or group selector. |
| **Parameters** | Form generated from the action's manifest. String / int / bool / choice — same shape as the ad-hoc Run dialog. |
| **Schedule** | 5-field cron input. Live `cronToHuman` preview, server-validated next-3-fire-times, plus quick-pick chips ("Hourly", "Nightly 03:00 UTC", etc.). |
| **Review** | Read-only summary. **Destructive options block** is shown only when `action.destructive=true`: snapshot, verify, auto_rollback toggles + batch_size for non-host targets. |

Submit creates the row via `POST /api/scheduled-actions`. On success
the dialog closes, the list (and any per-target Schedules tab)
refreshes, and the row appears immediately.

`action_key` and `target_*` are **immutable on edit** — re-creating is
the right path for "I want a different action against this target."
The backend rejects edits that change them with `422`.

---

## Run history

Every scheduled run creates an `action_run` row with
`scheduled_action_id` pointing at the schedule. The same table holds
ad-hoc runs (where `scheduled_action_id` is NULL), so:

- The `/schedules` row drawer shows runs for that schedule.
- The host detail's Actions tab shows runs for that host (both ad-hoc
  and scheduled).
- The group detail's Actions tab shows runs for that group.

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
- Per-host work is serialised via the option-c PostgreSQL advisory
  lock (`pg_try_advisory_lock(hashtext('host_sync.{host_id}'))`) for
  `_builtin.sync`. The two read-only built-ins
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
