# Bug Registry

Open bugs in LabDog. New entries are added as bugs are surfaced.

## Convention: open-only

**Only open bugs belong in this file.** When a bug is fixed:

1. Land the fix and write a descriptive commit message that references
   the bug ID (e.g. `fix(sync): BUG-37 — dispatch Celery tasks after
   commit`). That commit message is the canonical record (symptom,
   root cause, fix).
2. Delete the entry from this file in the same commit. Do **not** mark
   bugs `[x]` and leave them here — fixed entries belong in git history,
   not in the registry.

To retrace a historical bug ID referenced elsewhere
(`BUG-NN`, `SEC-NN`, `TYPE-NN`, `DEAD-NN`), search the commit log:

```
git log --grep BUG-37
git log -- backend/app/api/sync.py
```

## How to file an entry

Format each entry as:

    - [ ] **BUG-NN** `path/to/file.ext:LINE` — one-line summary

      Symptom, root cause, severity tier (Critical / High / Medium /
      Low). If reproduced from a specific scenario, note it. Group
      related bugs under the same severity heading.

ID counter as of last housekeeping pass: `BUG-104`, `SEC-35`,
`TYPE-03`, `DEAD-01`. Pick the next number in the relevant series
when filing a new entry.

---

## Open

### Security findings — High

Filed 2026-05-21 from a `security-auditor` whitebox source-level
review (see the `code-audit` branch history for the baseline). Each entry was
spot-checked against current HEAD before filing.

_No bugs are currently open._

---

## Open — 2026-09 whitebox audit

Filed 2026-09-03 from a full whitebox review (security, correctness,
performance) of the backend, frontend, packaging and CI. Every entry was
verified against source at `c784afb` before filing; the ones already
fixed are absent rather than ticked, per the open-only convention.

Findings from the same pass that have already landed are absent rather
than listed: the reflected XSS in the SPA dynamic-route rewrite, the AI
command-classifier bypasses, action-parameter template injection, pack
manifest path containment, and the quick-win batch (unauthenticated
resolver reads, registration privilege flags, `retention_days=0` wiping
the audit log, the scheduler tick poisoning itself, two temp-dir leaks,
the sync-tray request loop, the CSRF-less password change). Search the
log: `git log --grep "SEC"`.

**Note on the privilege model.** LabDog is deliberately flat — every
authenticated user has the same permissions and `is_superuser` gates only
user administration. That is a confirmed design decision, not a finding.
Its consequence shapes the severities below: the AI classifier, the
action-parameter validator and pack content policy are the *only* controls
protecting host root from an ordinary account. All three are now in
place: the classifier hardening, the extra-vars validator, and the pack
content policy.

### Security — Medium

### Security — Low

- [ ] **SEC-35** Grafana, Loki, Mimir and AI-provider base URLs are
      scheme-checked but may point at loopback, RFC1918 or 169.254.169.254
      (`app/grafana/schemas.py:16-25`, `app/ai/schemas.py:25-33`). Close to
      blind — responses must parse as the expected shape and httpx does not
      follow redirects by default — and a documented homelab decision. Git
      repo URLs *do* block these (`app/schemas/git_repos.py:18-25`). Filed
      so it is not re-discovered as new, not because it needs changing.

### Correctness — High

_No bugs are currently open._

### Correctness — Medium

_No bugs are currently open._

---

## Open — 2026-09-27 production investigation

Filed 2026-09-27 from the lin-manager instance (`openlabdog/labdog:test`,
image built from `8516b144`). Confirmed against the running container,
not just read from source.

### Correctness — High

_No bugs are currently open._

### Correctness — Low

_No bugs are currently open._

---

## Open — 2026-09-28 lin-manager upgrade

Filed 2026-09-28 after upgrading lin-manager (container recreated
15:34 UTC, migration 0040 applied). Confirmed against the running
container, its database and its logs.

### Correctness — High

- [ ] **BUG-96** `backend/app/actions/registry.py:163-191`,
      `backend/app/main.py:482-509`,
      `backend/app/tasks/__init__.py:126-158` — after a restart the API
      can be left serving the bundled actions only, because its registry
      reload races the Celery worker's and loses

      Symptom: after the upgrade, Actions › Registry lists only the six
      bundled keys, all won by `bundled`. The two actions from
      `labdog-private` (`docker-compose-update`, `nextcloud-vm-update`)
      are gone from the UI, and so is every key `labdog-playbooks` should
      win. Both packs show `enabled` and `synced`. The Celery worker's
      registry is correct: it logged `loaded 13 action(s) from 3
      pack(s)`, and `action_registry_snapshot` holds all eight keys with
      the private pack's two. Only the API process is wrong, so anything
      that goes through the API, including starting a run by hand,
      cannot see the private actions.

      Root cause: two processes rebuild the registry at startup at the
      same moment. The API's lifespan runs `_sync_packs_then_reload`, and
      the `work` worker's `worker_ready` hook runs the same sync and
      reload. Both end in `_persist_merge_outcome_async`, which does
      `DELETE FROM action_registry_snapshot` and then inserts every key.
      Under READ COMMITTED the second transaction's DELETE cannot see the
      rows the first one just committed, so its INSERT collides: the API
      logged `UniqueViolationError ... pk_action_registry_snapshot, Key
      (action_key)=(alloy-install)` and "action-pack startup sync
      failed; on-disk packs only". `reload_registry_async` calls
      `_install(result)` only after the persist succeeds, so the
      correctly merged registry is thrown away and the API keeps what it
      loaded before the sync. Nothing reloads it again until someone
      syncs a pack or changes a resolution in the UI.

      Why it showed on this upgrade: lin-manager has no volume on
      `/var/lib/labdog/packs` (only `claude-cli` is mounted), although
      `docs/production-deploy.md` lists `labdog_packs` as needed for git
      packs. Recreating the container emptied the checkouts, so the API's
      pre-sync load found only the bundled pack. With the volume, the
      same failed reload would have left the API on the previous
      checkout and hidden the bug. Whether a restart hits it at all
      depends on timing.

      Related: the API and the `work` worker also both run
      `sync_enabled_packs` into the same checkout directories at boot.
      Here the API cloned `packs/1` at 15:34:34 and the worker pulled it
      at 15:34:40, so they did not overlap this time. The orchestrator
      already skips the sync for this reason (docstring on
      `_sync_packs_on_worker_start`); the API and `work` worker do not.

      Fix direction:
      - Install the merged registry in memory before persisting, or
        whether or not persisting succeeds. The snapshot is bookkeeping;
        losing that write must not cost the process its registry.
      - Make the snapshot write safe under concurrency: take a
        transaction-level advisory lock around the persist (and
        preferably the whole sync-and-reload), or replace DELETE and
        INSERT with an upsert plus deleting keys no longer present.
      - Only one process should sync the git checkouts at boot. Let the
        `work` worker own it (or take the same advisory lock), and have
        the API reload from disk once the sync is done, or retry its
        reload on failure.
      - Tests: two concurrent `reload_registry_async` calls both finish
        with the full registry installed and a consistent snapshot; a
        failing persist still installs the merged registry.

      Workaround: press **sync** on any pack in Actions › Pack sources.
      That reloads the API's registry and brings the actions back until
      the next restart. Adding the `labdog_packs` volume on lin-manager
      makes a recurrence much less visible, but does not fix the race.

      Severity High: an upgrade or restart can silently remove every
      git-pack action from the UI and API with both packs reporting
      healthy, and it stays that way until someone happens to sync a
      pack.

### Usability — Low

- [ ] **BUG-97** `frontend/components/shell/zones.ts:160-175` — the
      Overview pane keeps "Summary" highlighted whichever view is open

      Symptom: in the Overview pane, clicking Pending, Fleet state,
      Activity or Upcoming changes the page, but the highlight stays on
      Summary. Reproduced against `next dev`: at
      `/overview/?view=state` the only link with `aria-current="page"`
      is Summary.

      Root cause: `next.config.ts` sets `trailingSlash: true`, so the
      pathname is `/overview/`, while the pane items' hrefs are
      `/overview` and `/overview?view=...`. In `itemIsActive`,
      `pathname !== itemPath` is always true. That branch treats the page
      as one beneath the item, which only matches items without a query:
      Summary is always active and the query items never are. Items
      without a query elsewhere (`/hosts`, `/runs`, ...) still work by
      the same accident, since `/hosts/` starts with `/hosts/`. Overview
      is the only zone whose items differ by query, so it is the only one
      visibly broken.

      Fix direction: normalise a trailing slash off `pathname` (and
      `itemPath`) before comparing in `itemIsActive`. Add a unit test for
      `itemIsActive` with a trailing-slash pathname, or extend an e2e
      spec to assert `aria-current` after clicking an Overview item.

      Severity Low: navigation works; only the highlight is wrong.

- [ ] **BUG-98** `frontend/app/(dashboard)/overview/client-page.tsx:119`,
      `frontend/components/scheduled-actions/scheduled-actions-list.tsx:84`,
      `frontend/components/scheduled-actions/schedule-action-dialog.tsx:121`
      — a schedule of a non-destructive action shows "snap" although no
      snapshot is ever taken

      Symptom: Overview › Scheduled shows a green "snap" tag on "Update
      Docker Compose images" (`docker-compose-update`, group `docker`).
      That action's manifest says `destructive: false`, with the comment
      "No Proxmox snapshot: the playbook waits for health itself and
      rolls back at the image level". No run of it has snapshotted:
      every `action_host_runs.snapshot_name` for runs 166-213 is empty.
      The tag reads as a promise of a rollback point that does not exist,
      and the panel's footer adds "Snapshot-backed ones are reversible."

      Root cause: the schedule dialog's initial state sets
      `snapshotEnabled: true` (also `verifyEnabled` and `autoRollback`),
      and shows the toggles only when `action.destructive`. For a
      non-destructive action the operator never sees them, and the
      defaults are saved anyway: schedule 2 on lin-manager has all three
      true, as do its runs. The runner is right to ignore them
      (`action_host.py:594` only snapshots when
      `spec.destructive and spec.snapshot_enabled`), but both the
      Overview row and the schedules list render the tag from
      `snapshot_enabled` alone.

      Fix direction: render "snap" only when the action is destructive
      *and* `snapshot_enabled`. The schedule response needs the action's
      `destructive` flag next to `action_name`
      (`app/api/scheduled_actions.py:160`). Also stop storing true for
      non-destructive actions: send `false` from the dialog, or have the
      API normalise the three flags when the action is not destructive.
      Optionally a migration clearing them on existing schedules of
      non-destructive actions. The run-action dialog probably defaults the
      same way (runs 166-213 all have `snapshot_enabled = t`); check it
      and the run view for the same tag.

      Severity Low: nothing runs differently, but the UI claims a
      rollback point for an unattended action that has none.

---

## Open — 2026-10-01 scheduled runs on lin-manager

Filed 2026-10-01 from lin-manager's action runs 235-241 (schedules 1
`linux-upgrade`, group "Default allow", 17 hosts, batch size 1; and 2
`docker-compose-update`, group "docker", 6 hosts, all also in "Default
allow"). Confirmed against the database, the container log and the
host's apt history.

### Correctness — High

_No bugs are currently open._

### Correctness — Medium

_No bugs are currently open._

---

## Open — 2026-10-01 found while fixing BUG-101

Filed 2026-10-01. BUG-102 and BUG-103 were found reading the host queue
for BUG-101 and reproduced in tests against Postgres; neither has shown
up on lin-manager yet, where no host-targeted run and no built-in group
run has deferred. BUG-104 is from lin-manager's runs 237 and 240.

### Correctness — Medium

- [ ] **BUG-102** `backend/app/tasks/host_lock.py:726-732`,
      `backend/app/tasks/action_orchestrator.py:336-346` — a host-targeted
      action that deferred behind a busy host fails with a duplicate-key
      error when the host frees up, instead of running

      Symptom: run an action on a host while a sync is running on it.
      The per-host task defers, and its row and the run both go
      `pending`. When the sync finishes, `dispatch_next_pending_for_host`
      re-sends `run_action` for the run, the orchestrator inserts the
      host's row a second time, and the insert fails on
      `uq_action_host_run`. The run ends `failed` with the
      IntegrityError text, and its row stays `pending` for good: the
      queue only re-fires rows whose run is still open.

      Root cause: the pick has two candidates for the same work, the run
      and its row, and both carry the run's `created_at`. The run is
      appended to the candidate list first, so it wins the tie. Re-sending
      the run is right for a group dispatch (`supports_host: false`),
      which defers before it has any rows; a host-targeted run defers
      per row and has to be resumed through that row.

      Fix direction: the run branch of the pick only takes pending runs
      with no `ActionHostRun` rows (`_has_no_host_runs()`, as in
      `check_host_busy`), so a host-targeted run comes back through its
      row. When that row claims the host, put a single-host parent that
      is `pending` back to `running` and clear its `pending_reason`, in
      both `action_host._claim_or_defer` and
      `builtin_dispatchers._begin_host_run`.

      Severity Medium: any host action that collides with another
      operation on its host fails instead of waiting its turn.

- [ ] **BUG-103** `backend/app/tasks/host_lock.py:751-753` — a deferred
      member of a group run of a built-in is re-dispatched to the
      pack-playbook runner, which cannot run it

      Symptom: `_builtin.drift_check` (or `_builtin.collect_state`,
      `_builtin.ai_task`) on a group where one member is busy: that
      member's row defers, and when the host frees up the queue sends
      `run_action_host(run, row)`. That is the Ansible runner for pack
      actions; the built-in has no playbook (`playbook_path` is `None`),
      so the member fails. A deferred `_builtin.sync` row is picked up by
      the same branch, although the SyncJob it queued is what is meant to
      close it (BUG-81).

      Root cause: the `action_host_run` branch hardcodes
      `run_action_host`. The orchestrator picks the per-host task from
      `PER_HOST_TASK_FOR_BUILTIN`.

      Fix direction: dispatch through the same lookup the orchestrator
      uses. Leave a `_builtin.sync` row alone when a pending SyncJob
      carries it as `origin_action_host_run_id`, because that job closes
      the row when it runs.

      Severity Medium: a built-in run against a group reports a member as
      failed whenever that member was busy when its turn came.

### Correctness — Low

- [ ] **BUG-104** `backend/app/api/actions.py:392-411` — cancelling a run
      leaves the hosts it had not started `queued` or `pending`, and the
      run without a finish time, unless an orchestrator is still running
      to clean up

      Symptom: as of 2026-10-01, run 240 (cancelled after its
      orchestrator died) shows two hosts `queued`, and run 237 (cancelled
      after its orchestrator had dispatched everything and was only
      waiting on deferred hosts) shows six `pending`. Neither run has a
      `finished_at`. A `pending` run, such as a host-targeted run waiting
      for its host, is not marked cancelled at all, only given the Redis
      token.

      Root cause: the endpoint sets the run `cancelled` (only from
      `queued`/`running`) and sets the cancel token, and leaves the hosts
      to the orchestrator, which cancels the rest at its next check.
      With no orchestrator left, nothing does.

      Fix direction: the endpoint cancels the run's `queued` and
      `pending` rows itself, accepts a `pending` run, and sets
      `finished_at` once no row is `running`.

      Severity Low: nothing runs that should not, but the run page
      reports hosts that will never run as waiting, and the run never
      shows an end.
