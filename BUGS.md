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

ID counter as of last housekeeping pass: `BUG-41`, `SEC-02`,
`TYPE-03`, `DEAD-01`. Pick the next number in the relevant series
when filing a new entry.

---

## Open

### Critical

- [ ] **BUG-38** `backend/app/tasks/host_sync_orchestrator.py:511` — `_claim_or_defer` TOCTOU: two workers can both proceed past the gate for the same host

  Two Celery workers dispatched nearly simultaneously for the same host (e.g.,
  two API calls in quick succession, or the dispatch-next-pending helper racing
  with a direct API dispatch) both execute the Phase-0 SELECT in
  `_claim_or_defer` before either has committed the "running" status write that
  happens in Phase 1 (`_prepare_run`). The check is purely read-only with no
  row lock, so both workers see "no other running job" and both set `claimed=True`.
  Both proceed into `_prepare_run`, both flip the SyncJob to "running" and
  commit, and then both invoke `orchestrate_host_sync` concurrently against the
  same host. This violates the single-in-flight-per-host guarantee the queue
  mechanism was designed to enforce, and results in two simultaneous Ansible
  playbook executions against the target — potentially leaving the host in an
  inconsistent state.
  Root cause: the gate is a plain SELECT with no FOR UPDATE or advisory lock,
  so the critical section between the check and the commit of the "running"
  flip is completely unguarded. The docstring acknowledges the check is
  "read-only" but incorrectly describes the race window as bounded by the
  in-flight task's finally block; that bound only applies once a task is
  *already* running, not to two tasks starting simultaneously.
  Severity: Critical.
  Trigger: two workers pick up tasks for the same host_id within the same
  inter-commit window (typically a few milliseconds at default Postgres
  isolation). More likely under load or when the dispatch-next-pending
  helper fires a new task before the just-finished task's "running" flip
  is visible.

### High

- [ ] **BUG-39** `backend/app/tasks/host_sync_orchestrator.py:520` — `_prepare_run` failure after claim leaves SyncJob permanently stuck in "running" and queue blocked

  If `_prepare_run` raises an exception after it has already committed
  `job.status = "running"` to the database — for example, if the DB commit
  at line 360 succeeds but a subsequent query in a retry path fails, or
  if the exception is raised between the commit and the return — the outer
  `try` block in `_async_run` (line 520) has no inner `except` covering Phase 1.
  The `finally` at line 610 still executes `_dispatch_next_pending_for_host`,
  which scans for pending jobs and dispatches any successor; that successor will
  immediately defer because it sees the current job as "running" (it was
  committed as such and `_finalise_run` was never called to flip it to
  "success"/"failed"). The queue is then stuck: the job sits "running" forever
  with no worker owning it, and every future task for this host defers
  indefinitely. This is a distinct failure mode from the known crash-recovery
  hole (worker-killed-mid-task) because it arises from a normal exception in
  Python code with the worker still alive.
  Root cause: `_prepare_run`'s commit is outside the `try/except` that
  synthesises a finalise call on orchestrator failure. There is no compensating
  path that calls `_finalise_run` (or a minimal status-flip to "failed") when
  `_prepare_run` itself raises post-commit.
  Severity: High.
  Trigger: any transient DB error or unexpected exception inside `_prepare_run`
  that occurs after its commit at line 360 — in the current code the commit is
  the last operation so this window is near-zero, but the absence of a safety
  net means any future edit to `_prepare_run` that adds post-commit logic
  creates an immediate stuck-queue failure.

- [ ] **BUG-40** `backend/app/sync/orchestrator.py:247` — `compose_playbook` called with empty fragment list when resolver is the only requested module and no resolver config exists

  When `module_filter=["resolver"]` is passed (e.g., from the per-tab
  `run_resolver_sync` delegator) and `get_effective_resolver` returns `None`
  (no resolver configuration applies to the host), the resolver block at
  orchestrator lines 237-244 skips appending a fragment. `compose_playbook` is
  then called at line 247 with `fragments=[]`. `yaml.dump([], ...)` returns
  `"[]\n"`, which ansible-runner receives as the playbook. Ansible rejects an
  empty play list, causing the runner to fail; this surfaces as an orchestrator
  exception (or a runner error), which the Celery wrapper catches and marks
  every seeded module as "error". The SyncJob is recorded "failed" even though
  the correct semantic is "resolver is unmanaged for this host" — a successful
  no-op.
  Root cause: `orchestrate_host_sync` does not guard against the
  all-fragments-skipped case before calling `compose_playbook`. The resolver
  module is the only one with a conditional "skip if no config" path; all
  other modules always append a fragment. When resolver is the sole requested
  module and skips, `fragments` is empty.
  Severity: High.
  Trigger: `POST /api/sync/hosts/{id}/bulk` with `module_filter=["resolver"]`
  (or the `run_resolver_sync` per-tab task) against a host that has no
  resolver configuration assigned.

### Medium

- [ ] **BUG-41** `backend/app/api/sync.py:395` — bulk endpoint idempotent 200 path returns caller's `module_filter`, not the existing job's

  When a second `POST /api/sync/hosts/{host_id}/bulk` arrives while a bulk
  SyncJob is already pending or running for the host, the endpoint returns HTTP
  200 with the existing job's ID. However, the `module_filter` field in the
  response body is taken from `body.module_filter` (the current request's
  payload) rather than from the existing job. If the two requests carry
  different filters — for example, the first requested `None` (all modules) and
  the second requests `["firewall"]` — the 200 response claims
  `module_filter=["firewall"]` while the actual queued/running job will execute
  all modules. Consumers that use the response to track what the job will do
  (monitoring, audit UIs) will be misled.
  Root cause: line 395 constructs `BulkSyncResponse(module_filter=module_filter)`
  where `module_filter` is the local variable bound to `body.module_filter`
  from the current request, not to any field stored on or derivable from the
  `existing` SyncJob object (which only stores `module_type="bulk"`, not the
  original filter list).
  Severity: Medium. No data is corrupted; the queued job runs correctly. The
  defect is informational: callers relying on the echoed filter for audit or
  retry logic receive stale/incorrect metadata.
  Trigger: two bulk sync requests for the same host_id with different
  `module_filter` values, where the second arrives before the first job
  completes.
