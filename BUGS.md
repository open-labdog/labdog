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

ID counter as of last housekeeping pass: `BUG-44`, `SEC-06`,
`TYPE-03`, `DEAD-01`. Pick the next number in the relevant series
when filing a new entry.

---

## Open

### High

- [ ] **BUG-44** `backend/app/sync/orchestrator.py:78-99` — orchestrator outcome aggregation always returns `"no_tasks"` because `event_data.task_tags` isn't populated by ansible-runner

  Verified empirically: a bulk sync against a real host runs the unified
  playbook to completion (Celery worker log shows real `runner_on_ok` /
  `runner_on_changed` events with `ok=13 changed=6 failed=0`), but the
  orchestrator's `module_outcomes` returns `"no_tasks"` for every module
  in the audit log. The Celery wrapper translates `"no_tasks"` →
  `"in_sync"` when writing `HostModuleStatus`, so the operator-visible
  per-module status ends up correct *only because the playbook didn't
  fail*. If any task fails, its `runner_on_failed` event is also tagless
  per the same bug — `aggregate_module_outcomes` ignores tagless events,
  no module gets marked `error`, and the SyncJob is reported as `success`
  with all modules `in_sync` despite real failure.
  Root cause: `_runner_events_to_task_events` reads
  `event_data.task_tags` (line 93 of `orchestrator.py`). ansible-runner's
  event payloads don't include `task_tags` for `runner_on_*` events when
  the playbook isn't invoked with explicit `--tags` filtering — the field
  is present at play-level events (`playbook_on_task_start` carries tag
  metadata under different keys) but not on the per-task result events
  the aggregator consumes. The unit tests in `test_outcomes.py` build
  synthetic events with a top-level `tags` key (the contract the
  aggregator expects), so they don't catch the projection mismatch.
  Severity: High. Functionally masks real Ansible failures — a single
  failed task in the unified playbook can produce a SyncJob marked
  `success` with all modules `in_sync` and no audit-visible error.
  Trigger: any real bulk sync (verified with `POST /api/sync/hosts/2/bulk`
  against tester3 — outcomes audit log shows
  `{"firewall": "no_tasks", "services": "no_tasks", ...}` despite the
  playbook running tasks).
  Fix sketch: instead of relying on `event_data.task_tags`, parse the
  `play_pattern` / `task_path` / `task` fields available on
  `runner_on_*` events and either (a) match each task's path against
  the play's name to attribute it to a module, or (b) walk
  `playbook_on_task_start` events first to build a task→play→module
  map, then look each result event up by `task_uuid`. Approach (b) is
  more robust to play-name renames.

### Medium

- [ ] **BUG-43** `frontend/app/(dashboard)/groups/[id]/rules/` — system firewall rules render with enabled Edit/Delete buttons

  The Rules page renders Edit and Delete buttons on every row, including
  rows where `is_system = TRUE` (auto-injected rules like the SSH
  lockout-prevention rule). Clicking Edit or Delete on a system row
  presumably hits a server-side guard and returns an error — but the
  operator gets to that point thinking the action is allowed, which is
  a confusing UX and a violation of the documented contract that system
  rules are read-only.
  Root cause: the SortableRow / row-action component does not check
  `rule.is_system` when deciding whether to enable Edit/Delete. The E2E
  test at `e2e/rules.spec.ts:100` asserts the disabled-on-system
  behaviour and fails (received `enabled`); the test reflects the design
  intent.
  Severity: Medium. No data corruption (server-side guard catches the
  attempt), but UX violates the documented invariant.
  Trigger: log in, navigate to any group with at least one host (so the
  SSH lockout rule appears) → `/groups/{id}/rules` → observe Edit/Delete
  buttons enabled on the system row.

### Low

- [ ] **BUG-42** `frontend/e2e/dashboard.spec.ts:21,83` — stale test references "Check All" button label after rename to "Collect State"

  The dashboard's primary action button was renamed from "Check All" to
  "Collect State" in commit `6d62d31`, but the two corresponding
  Playwright tests (`Check All button is visible` and
  `Check All button triggers drift check`) still look up the button by
  its old `name: "Check All"` accessible label. Both fail with
  `element(s) not found`.
  Root cause: test staleness — the tests were not updated when the UI
  string changed. The button still works correctly in the app.
  Severity: Low (test-only; no production impact).
  Trigger: `npx playwright test dashboard.spec.ts -g "Check All"`.
