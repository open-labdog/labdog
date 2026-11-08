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

ID counter as of last housekeeping pass: `BUG-133`, `SEC-38`,
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

### Correctness — Low

- [ ] **BUG-133** `frontend/components/sync-tray.tsx:132-141` — the
      activity card keeps a finished run's hosts `running`

      Symptom: after `k8s-upgrade` run 396 finished (run and all five host
      runs `succeeded` at 2026-11-08 20:27:26 UTC), the activity card's
      header said "1 finished" and its bar showed 5/5 hosts done, but every
      host row under it still said `running`. The run page showed them all
      `succeeded`.

      Root cause: `RunHosts` polls `/api/actions/runs/{id}` for the host
      rows every 4 s only while `runActive(run)`, and `run` comes from the
      separate list query that drives the header. When the list sees the
      run finish, `refetchInterval` drops to `false` with no final fetch,
      so the rows keep whatever the last poll saw. A group-dispatch run
      always hits this: its host runs finish together, within a few
      hundredths of a second of the run itself, so the last poll before
      the switch sees them all running. Per-host runs usually finish
      hosts earlier, which hides it.

      Fix direction: refetch the run once when `active` turns false (or
      invalidate the `["action-run", id, "tray"]` query when the list
      reports the run finished).

      Severity Low: display only; the run page and the database are right.

## Open — 2026-11-08 load test (PR #270)

Filed 2026-11-08 from the plan step 2 load test (300 fake Debian hosts,
image `openlabdog/labdog:dev-5cb85d7`, results in
`plans/scaling/results/02-baseline.md`). Each was reproduced in every
scenario it could appear in.

### Correctness — High

- [ ] **BUG-128** `backend/app/user_mgmt/generator.py:84,96` — the image
      ships without the `ansible.posix` collection, so every sync that
      includes linux users fails

      Symptom: any host sync whose plan includes the `users` module fails
      at playbook parse with `couldn't resolve module/action
      'ansible.posix.authorized_key'`. The generator emits that task for
      every present user, with or without keys, so no users sync can
      succeed. Seen on 1.1.0 (`dev-5cb85d7`); a 1.0.0 image has the same
      gap.

      Root cause: `backend/pyproject.toml` depends on `ansible-core`,
      which carries only `ansible.builtin`; nothing installs
      `ansible.posix` (no `requirements.yml`, no `ansible-galaxy` step in
      the Dockerfile). Fix: install the collection in the image, or use
      a builtin equivalent.

      Severity High: user management cannot be applied at all. Masked by
      BUG-129, so the UI shows the hosts as in sync.

- [ ] **BUG-129** `backend/app/ansible_runtime/outcomes.py:79`,
      `backend/app/tasks/host_sync_orchestrator.py:540-542` — a playbook
      that fails to parse is reported as a successful sync

      Symptom: with BUG-128's parse error, ansible-runner exits non-zero
      before any task runs and emits no host events. Every module in the
      plan is classed `no_tasks`, which collapses to `in_sync`; the job
      ends `success` and the host is marked in sync although nothing was
      applied.

      Root cause: `no_tasks` ("zero events carry this module's tag")
      cannot tell "desired state already matched" from "the play never
      ran". The runner's return code and status are not consulted before
      the outcomes are trusted. Fix: treat a failed runner status with
      zero task events as `error` for every module in the plan.

      Severity High: any playbook-level failure (missing collection, bad
      template, syntax error) is silent and reported as success.

### Correctness — Medium

- [ ] **BUG-130** `backend/app/cron/collector.py:58` — cron drift is
      always `out_of_sync` when the SSH user isn't root

      Symptom: 300 of 300 cron drift checks were `out_of_sync` in every
      scenario, on hosts the preceding sync had converged. On a fake
      host, `crontab -l -u root` as root lists the `#Ansible:` entries;
      as the `labdog` SSH user it fails with "must be privileged to use
      -u"; with `sudo -n` it works.

      Root cause: the collector runs `crontab -l -u {user}` without sudo
      and with `check=False`, and reads the non-zero exit as an empty
      crontab, so every managed entry looks missing. Fix: run it through
      `sudo -n` as the firewall collector does with `sudo`, and treat a non-zero exit
      other than "no crontab for user" as a collection error, not empty.

      Severity Medium: cron drift is useless for non-root SSH users, and
      the constant drift can trigger needless syncs and alerts.

- [ ] **BUG-131** `backend/app/tasks/sync_sweeper.py:67`,
      `backend/app/tasks/host_sync_orchestrator.py:362` — a sync job whose
      task dies before it claims the host stays `pending` forever

      Symptom: in the first raised-concurrency run, Postgres connection
      errors killed tasks before they claimed their host. 82 of 300
      initial sync jobs then sat `pending` for 2.5 h, with an empty queue
      and idle workers, until the run was stopped. The UI showed them
      waiting, and a later sync for those hosts could not start because
      of `uq_sync_job_active`.

      Root cause: `sync_sweeper` only recovers rows in `running`. The
      next pending job for a host is dispatched only when an in-flight
      job on that host finishes (`_dispatch_next_pending_for_host`), so
      a pending row whose message was lost, with nothing running on its
      host, is never picked up again. Fix: have the sweeper re-dispatch
      (or fail) `pending` rows older than a threshold when no job is
      running on the host.

      Severity Medium: needs a task failure to trigger, but then blocks
      the host's syncs until someone clears the row by hand.

### Documentation — Low

- [ ] **BUG-132** `docs/` (deployment) — the docs don't say how many
      Postgres connections a given worker concurrency needs

      Symptom: at 16 worker / 12 orchestrator slots on stock Postgres
      (`max_connections` 100), LabDog logged 499
      `TooManyConnectionsError` within 4 minutes (96 in
      `run_host_sync`, the rest in beat tasks: sweepers, notifications,
      alert handling), which also triggered BUG-131. Measured at 16/12:
      ~90–130 connections idle, 183 at peak.

      Fix: document the connections needed per slot (roughly 6) and that
      raising concurrency needs a higher `max_connections` or a pooler.

      Severity Low: a sizing gap, but the failure it causes is severe.
