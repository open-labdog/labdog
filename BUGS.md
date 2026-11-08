# Bug Registry

Open bugs in LabDog. New entries are added as bugs are surfaced.

## Convention: open-only

**Only open bugs belong in this file.** When a bug is fixed:

1. Land the fix and write a descriptive commit message that references
   the bug ID (e.g. `fix(sync): BUG-37 — dispatch Celery tasks after
   commit`). That commit message is the canonical record (symptom,
   root cause, fix).
2. Delete the entry from this file in the same commit, and its heading
   and intro when it was the last entry under them. Do **not** mark
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

ID counter as of last housekeeping pass: `BUG-127`, `SEC-38`,
`TYPE-03`, `DEAD-01`. Pick the next number in the relevant series
when filing a new entry.

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

### Security — Low

- [ ] **SEC-35** Grafana, Loki, Mimir and AI-provider base URLs are
      scheme-checked but may point at loopback, RFC1918 or 169.254.169.254
      (`app/grafana/schemas.py:16-25`, `app/ai/schemas.py:25-33`). Close to
      blind — responses must parse as the expected shape and httpx does not
      follow redirects by default — and a documented homelab decision. Git
      repo URLs *do* block these (`app/schemas/git_repos.py:18-25`). Filed
      so it is not re-discovered as new, not because it needs changing.

---

## Open — 2026-10-29 valheim

Filed 2026-10-29 while tracing why a hosts sync rewrote `/etc/hosts` on
the valheim game-server VM (BUG-115 to BUG-117 and BUG-119, since fixed),
against lin-manager's `sync_jobs` and audit rows. BUG-120 and BUG-121
turned up while fixing those, by reading the code.

### Correctness — Medium

- [ ] **BUG-118** `backend/app/hosts_mgmt/merge.py:95`,
      `backend/app/hosts_mgmt/diff.py:34-35` — hosts entries that share an
      address collapse into one line, and the lower-priority names are
      dropped without a word

      Symptom: lin-manager's `add-hosts` group maps both
      `loki.lan.tyresson.se` (priority 100) and `mimir.lan.tyresson.se`
      (priority 101) to 10.10.200.3. nextcloud's effective view and its
      file preview carry only the mimir line, and nothing in the UI says
      why. Its `/etc/hosts` still has both: the file was last written
      2026-08-21, five minutes after LabDog's sync, apparently by hand. The
      next hosts sync there drops loki.

      Root cause: the merge is keyed on the resolved address (`first_wins`
      with the IP as key), so per address only the highest-priority entry
      survives and its names replace the others'. An `/etc/hosts` line may
      carry any number of names, and two lines for one address are valid
      too. The diff and the collector use the same key, so a host file with
      two lines for one address is also read as one.

      Workaround: put every name for an address in one entry, as hostname
      plus aliases.

      Severity Medium: a configured name silently does not resolve.

- [ ] **BUG-120** `backend/app/tasks/builtin_dispatchers.py:565-569` — a
      `_builtin.sync` row records neither its module type nor its modules

      Symptom (by reading): `_sync_async` creates its `SyncJob` with no
      `module_type`, so the column takes its server default `firewall`, and
      no `module_filter`. The modules are passed to the inner run only. If
      the host is busy the job defers, and the queue later re-dispatches it
      from the row: `module_filter_for` reads `firewall`, so a scheduled
      sync of every module applies the firewall alone and reports success.
      The same default makes the insert collide with an operator's pending
      firewall sync on `uq_sync_job_active`, and the run fails with a
      duplicate-key error.

      Severity Medium: a scheduled sync silently does less than it says.

### Correctness — Low

- [ ] **BUG-121** `backend/app/ca_certs/actions.py:72-78` — a CA
      certificate deploy is dispatched before its row is committed

      Symptom (by reading): `enqueue_ca_cert_action_for_host` flushes the
      `SyncJob` and calls `run_ca_cert_action.delay` at once, leaving the
      commit to its five callers — the BUG-37 pattern. A worker that picks
      the task up first cannot see the row. It used to raise and leave the
      row `pending` for good; since the deploy takes its turn in the host
      queue (BUG-119) it returns "deferred", and the row waits for the next
      operation on the host to release it.

      Severity Low: a deploy that should start at once can wait; it is no
      longer lost.

## Open — 2026-11-08 k8s-upgrade

Filed 2026-11-08 when the first `k8s-upgrade` run on lin-manager (action
run 395, LabDog 1.1.0, pack action 1.3) never left `queued`. Confirmed
against the container log and the 1.1.0 image.

### Correctness — High

- [ ] **BUG-127** `backend/app/tasks/__init__.py:221-260`,
      `backend/app/tasks/action_orchestrator.py:53` — group-dispatch
      actions never run: the worker does not register
      `run_action_group`

      Symptom: run 395 (`k8s-upgrade` on group K8s-cluster, a dry run)
      stayed `queued`. The orchestrator logged `action_run 395 →
      group-dispatch (action=k8s-upgrade supports_host=False)`, then the
      worker logged `Received unregistered task of type
      'app.tasks.action_group.run_action_group'` with a `KeyError` and
      dropped the message. Nothing ran on any host and no snapshot was
      taken. The run carries no `started_at` and no children, so the
      action sweeper fails it an hour later as an orphan
      (`QUEUED_ORPHAN_THRESHOLD_SECONDS`). The UI shows `queued` until
      then, and nothing says why.

      Root cause: the orchestrator dispatches the group task by name
      (`send_task(_DEFAULT_GROUP_TASK, queue=CHILD_QUEUE)`), but
      `app.tasks.action_group` is missing from `celery_app.conf.include`,
      and nothing else a worker imports pulls it in. Every pack action
      with `supports_host: false` is affected; `_builtin.ai_task_group`
      is not, because its task lives in `app.tasks.ai_task`.
      `tests/test_task_routing.py` cannot catch it: it checks the routes
      of tasks loaded from `conf.include`, so a module missing from that
      list has no tasks to check.

      Fix direction: add `app.tasks.action_group` to `conf.include` (and a
      `task_routes` entry for `long_running`, to match `action_host`).
      Add a test that every task name passed to `send_task`, and every
      value in the orchestrator's dispatch tables, is registered once
      `import_default_modules()` has run.

      Severity High: every group-dispatch action is unusable, and the
      failure is silent.
