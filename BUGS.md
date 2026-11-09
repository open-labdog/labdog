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

ID counter as of last housekeeping pass: `BUG-166`, `SEC-51`,
`TYPE-03`, `DEAD-01`. Pick the next number in the relevant series
when filing a new entry. (`BUG-127` to `BUG-133` are taken on the
PR #270 branch, not yet on `dev`.)

---

## Open — 2026-11-09 performance sweep

Filed 2026-11-09 by the Phase 3 performance sweep, against `6a93751`
(the PR #276 head). It looked for work that grows faster than it needs
to, runs more often than it needs to, or holds a scarce resource (a
database connection, an SSH handshake, a worker slot) longer than it
needs to. BUGS.md on every open branch was read first, so nothing here
repeats an entry there.

*Measured* and *Reproduced* entries were run in a container: Postgres
18, Redis 7 and the backend's own venv (ansible-core 2.21.1), with
`sshd` in a second container reached over loopback. The kernel there
has no `netem`, so no measurement includes network latency; on a real
network the per-round-trip findings cost more, not less. The rest were
traced by reading. Each entry carries a fix direction for a fix thread.
None is fixed here.

Checked and not filed:

- The serial drift sweeps and the queue that actions share with syncs
  were measured by the PR #270 load test (package sweep 397 s at 300
  hosts; 0 of 7 par-10 action runs finished in an hour). Its plan
  (`plans/scaling/03-split-workers.md`, sections 3.3 to 3.5) covers the
  drift queue and fan-out, action fairness, `worker_prefetch_multiplier`
  and Postgres connection headroom, and BUG-132 on that branch covers
  connection sizing. BUG-155 below is only the batch barrier, which that
  plan does not touch.
- Playbook composition (`app/ansible_runtime/composer.py`): 23 ms for a
  host with 20 packages and 5 users, 144 ms for 200 and 50. Under 1 %
  of a sync.
- `GET /api/actions/runs` without an index that leads with
  `created_at`: Postgres 18 skip-scans `ix_action_runs_status_created`,
  and the Overview's 7-day query takes 1.6 ms over 65 000 runs.
- The API rate limit and frontend polling: slowapi keys the default
  limit on the URL path as well as the client IP, so the per-host
  preview and output fan-outs below and the shell's 30 s pollers each
  get their own bucket and cannot reach 100/minute.

### Performance — High

- [ ] **BUG-152** `backend/app/api/actions.py:838` — every open live
      output stream holds a database connection until the run ends

      Symptom: with 15 streams open (the API pool is `pool_size` 5 +
      `max_overflow` 10), every other API request waits 30 s and fails
      with `QueuePool limit of size 5 overflow 10 reached, connection
      timed out, timeout 30.00`. *Reproduced*: 15 clients on
      `GET /api/actions/runs/{id}/stream` for a running run, then
      `GET /api/hosts` timed out. The run detail page opens one stream
      per open tab (`components/action-run-detail.tsx:125`), and the
      assistant opens one per session (`stream_session`,
      `app/api/ai.py:1117`), which has the same shape.

      Root cause: both endpoints take `db` from `Depends(get_db)`, read
      one row, and return a `StreamingResponse` that relays Redis
      pub/sub. The session's transaction began with that read, and a
      yield dependency is closed only after the response has finished
      streaming, so the pooled connection stays checked out for the
      whole run. A run whose terminal status is published between the
      status read and the `subscribe` never sends a terminal event, so
      that stream (and its connection) lasts until the page closes.

      Fix direction: read the row, then `await db.close()` before
      returning the `StreamingResponse` (or read it in a short
      `AsyncSessionLocal()` block and drop the dependency). Subscribe
      before reading the status, so a run that ends in between still
      closes the stream. A test can hold `pool_size + max_overflow`
      streams open and assert that another request still succeeds.

      Severity High: a few operators watching runs make the whole UI
      and API fail for everyone, and the failure looks like a database
      outage.

- [ ] **BUG-153** `frontend/app/(dashboard)/plans/_hooks/use-plan-computation.ts:36`
      — computing a plan sends one preview per host at once, and each
      holds a database connection across seven SSH collections

      Symptom: on the Plans page, a plan over many hosts takes all 15
      API pool connections for as long as the previews run, so the rest
      of the UI and API queue behind it. A preview of a host that does
      not answer holds its connection for up to 80 s (eight SSH connects
      at the default 10 s `ssh.connect_timeout`, see BUG-159), so 15
      powered-off or firewalled hosts in scope are enough for every other
      request to wait out the 30 s `pool_timeout` and fail, along with
      the previews still queued. The documented deployment puts Caddy in
      front (`docs/production-deploy.md`), which speaks HTTP/2, so the
      browser's six-connections-per-host cap does not hold the burst
      back.

      Root cause: `compute` runs `Promise.all` over every host in scope
      with no limit. Each `POST /api/sync/hosts/{id}/preview`
      (`app/api/sync.py:175`) reads the host through `get_db` and then
      runs `plan_host_modules` (`app/sync/plan.py:362`), which collects
      the seven modules one after another, each over its own SSH
      connection (BUG-159), while the request's session keeps its
      connection. `plan_group` (`app/api/sync.py:114`) has the same
      hold and also walks the group's hosts one by one inside one
      request.

      Fix direction: release the session before the SSH work (load the
      host, key and desired state, close the session, then collect), or
      collect first and open a session only for the diff. Cap the
      client's concurrency (a small worker pool of 4 to 6 in `compute`)
      so a large scope streams in rather than arriving at once.
      Collecting the modules concurrently within one host, over one
      connection (BUG-159), shortens each preview as well.

      Severity High: the page exists to preview a fleet, and a lab fleet
      with some hosts switched off takes the API down while it runs.
      Traced by reading; the pool exhaustion is the one BUG-152
      reproduces.

- [ ] **BUG-154** `backend/app/ansible_runtime/runner.py:144` — Ansible
      runs without pipelining, so every task pays several extra SSH
      round trips

      Symptom: a 20-task play against one host took ~8.6 s, and ~7.0 s
      with `ANSIBLE_PIPELINING=True`: about 80 ms less per task, 19 %
      of the run, with no network latency at all. *Measured*. A sync of
      the PR #270 load-test host is 19 tasks plus fact gathering (58
      with 20 packages and 5 users, 10 more with nftables), and that
      load test found the LabDog box at ~86 % CPU on ansible-runner
      work, so the per-task cost is what bounds sync throughput.

      Root cause: `merged_env` sets only `ANSIBLE_ROLES_PATH` and
      `ANSIBLE_NOCOLOR`, and nothing else in the tree enables
      pipelining. Without it, each task makes a remote temp directory,
      copies the module over SFTP, marks it executable, runs it and
      removes the directory.

      Fix direction: add `"ANSIBLE_PIPELINING": "True"` to `merged_env`
      (callers' `envvars` can still override it). Pipelining with
      `become` needs sudo without `requiretty`, which is the default on
      current Debian, Ubuntu and RHEL-family releases; note it in the
      host requirements docs.

      Severity High: it is a one-line change on the hottest path in the
      product (every sync, action and built-in), and it scales with the
      task count and with the network round-trip time.

- [ ] **BUG-155** `backend/app/tasks/action_orchestrator.py:837` — an
      action run waits for its slowest host before it starts the next
      batch, and each new batch joins the back of the queue

      Symptom: a run over 30 hosts at `parallelism` 10, where one host
      in each batch takes 10 minutes and the rest take one, takes about
      30 minutes instead of about 13, with 9 of its 10 slots idle most
      of the time. On a busy worker each batch is sent only after the
      previous one finished, so it queues behind everything enqueued
      since, three times over. That compounds the starvation the PR
      #270 load test measured (0 of 7 par-10 runs finished in an hour).

      Root cause: `_drive` sends a batch, then `_wait` polls until no
      host in it is `queued` or `running`, then moves to the next batch
      from `_batches`. `parallelism` is used as a batch size, not as a
      limit on hosts in flight.

      Fix direction: a sliding window. On each check-in (`_check_in`
      already counts the hosts in flight), send
      `parallelism - in_flight` more hosts from the remaining list
      until it is empty, then wait for the last ones. `_resume_batches`
      becomes "in flight plus the rest". Keep the stop and cancel
      checks per check-in as now. Queue priority for action host runs
      stays with the PR #270 plan (section 3.4).

      Severity High: it slows every action run with more than one batch
      on an idle system, and multiplies queue wait on a busy one. Traced
      by reading.

### Performance — Medium

- [ ] **BUG-156** `backend/app/db.py:37` — `task_session()` builds and
      disposes a whole engine per call, so a task opens a new Postgres
      connection for every block

      Symptom: one `task_session()` block running `SELECT 1` takes
      28.13 ms median (p95 41.59 ms); a session from an existing engine
      takes 0.63 ms (p95 1.03 ms). *Measured*. A sync opens five or six
      (`app/tasks/host_sync_orchestrator.py:668`, `:713`, `:791`,
      `:819`, `:872`), a per-host action run around a dozen
      (`app/tasks/action_host.py`), the action orchestrator one every 5
      s for the life of a run (`_check_in`), and `release_host_queue`
      (`app/tasks/host_lock.py:1102`) one per member host. Each one is
      a new Postgres backend: a fork plus authentication on the
      database side.

      Root cause: the docstring's reason holds (an engine made before
      Celery forks must not be used after), and each Celery task runs
      its own `asyncio.run`, so a process-wide async pool would hand out
      connections bound to a closed loop. Making one engine per block
      solves both, at the price above.

      Fix direction: one engine per event loop instead of per block.
      Open it at the top of each task's `asyncio.run` (a
      `task_engine()` context the task wrappers enter), keep it in a
      `ContextVar`, and have `task_session()` take sessions from it,
      falling back to today's behaviour when none is set. The sync then
      makes one connection instead of six, and the orchestrator one per
      run instead of one per 5 s.

      Severity Medium: a few hundred milliseconds per sync and constant
      connection churn on the database, not an outage. Related: BUG-132
      on the PR #270 branch documents about six connections per worker
      slot, a number this fix should bring down.

- [ ] **BUG-157** `backend/app/packages/generator.py:88` — the packages
      play runs one package-manager call per package and gathers every
      fact

      Symptom: 20 packages are 20 `ansible.builtin.package` tasks, each
      a separate module run (about 80 ms of transport each, BUG-154)
      that starts the package manager and reads its cache again. The
      play also sets `gather_facts: True` (`:181`), which took 1.43 s
      against 0.60 s for a play without it; the `min` subset took 1.27
      s. *Measured* (facts). The facts are read only by the repository,
      hold and unhold tasks' `ansible_facts['os_family']` conditions.

      Fix direction: group the packages by target state (absent,
      latest, present, present at a version) and emit one task per
      group with `name:` as a list, so apt and dnf resolve them in one
      transaction. One bad name then fails the whole group, so keep the
      per-package task as a fallback in a `rescue`, or pre-check names.
      Take the OS family from the host row (`Host.os_family`,
      `app/models/host.py:81`) when it is known and drop fact gathering;
      otherwise `gather_subset: ["!all", "!min", "distribution"]`.

      Severity Medium: package changes are the most common sync, and
      the cost grows with each package an operator adds.

- [ ] **BUG-158** `backend/app/packages/collector.py:43` — drift checks
      and previews run one remote command per package, service, user
      and group

      Symptom: collecting 20 packages took 241 ms with one
      `dpkg -l | grep | head` pipeline each, and 10 ms with one
      `dpkg-query` for all of them, over the same connection.
      *Measured*. Each command is a round trip, so on a real network
      the gap grows by the round-trip time per item. The services
      collector runs `systemctl is-active` and more per service
      (`app/services/collector.py:46`), and the users and groups
      collectors run several commands per entry
      (`app/user_mgmt/collector.py:30`, `:133`). These run on every
      drift sweep and every preview.

      Fix direction: one command per module. `dpkg-query -W
      -f='${Package}\t${Version}\t${db:Status-Abbrev}\n' p1 p2 …` (or
      `rpm -q --qf … p1 p2 …`), with missing names read from stderr;
      `systemctl show --property=Id,ActiveState,UnitFileState svc1 svc2
      …`; `getent passwd u1 u2 …` and `getent group g1 g2 …`, then one
      `cat` of the `authorized_keys` files. Keep `shlex.quote` on every
      name: the group lookup (`app/user_mgmt/collector.py:135`) lacks it
      today, and only the schema's name check keeps that safe.

      Severity Medium: it is the largest part of each drift check's
      remote time, and drift checks run for every host and module all
      day.

- [ ] **BUG-159** `backend/app/ssh_utils.py:178` — each module collector
      opens its own SSH connection, so one preview of a host makes eight
      handshakes

      Symptom: an asyncssh connect and authenticate took 89.7 ms on
      loopback; a command on an open connection took 3.3 ms.
      *Measured*. `plan_host_modules` (`app/sync/plan.py:362`) calls the
      collectors one after another, and each enters `ssh_connect_host`,
      which also writes a temporary `known_hosts` file per connect: one
      per module, two for users and groups (`app/sync/plan.py:258`,
      `:259`). Collect-state for packages connects twice as well
      (`app/api/host_state.py:731`, `:732`). For a host that does not
      answer, every one of them waits out the connect timeout in turn.

      Fix direction: open one connection per host for a preview or a
      collect, and pass it to the collectors (each takes an optional
      `conn` and opens its own only when given none, so the drift
      sweeps keep working unchanged). With one connection the modules
      can also be collected with `asyncio.gather`, as asyncssh
      multiplexes channels.

      Severity Medium: about 0.7 s of handshakes per host preview before
      any latency, and 80 s for an unreachable host instead of 10, which
      BUG-153 then multiplies by the fleet.

- [ ] **BUG-160** `backend/app/metrics/aggregates.py:393` — each metrics
      scrape scans `drift_samples` three times and `sync_jobs` twice

      Symptom: with 3.9 million drift samples (300 hosts, 6 modules, 48
      checks a day, 45 days) and 400 000 sync jobs, one scrape took
      1639 ms: drift counts 352 ms, drift change sums 226 ms
      (`:426`), drift duration histogram 423 ms (`:656`), sync duration
      histogram 261 ms (`:627`), sync job counts 50 ms (`:370`).
      *Measured*. The time grows with retention (90 days by default,
      twice the data measured). The collector's 15 s cache serves only
      repeat scrapes inside that window, so at a 15 s scrape interval it
      recomputes on nearly every scrape.

      Root cause: each aggregate is its own query over the whole live
      table: three over `drift_samples` with different `GROUP BY`s and
      two over `sync_jobs`.

      Fix direction: one pass per table. For drift, a single
      `GROUP BY module_type, status` that returns the count, the three
      sums and each bucket as `count(*) FILTER (WHERE duration_ms <=
      b)`, from which the three families are built; the same for sync
      jobs. Further: since samples are append-only and retention
      already folds old rows into `drift_sample_rollup`, keep running
      totals up to an id watermark and add only newer rows on each
      scrape.

      Severity Medium: metrics are off by default, but where they are on
      this is a steady load on the database that grows every day.

- [ ] **BUG-161** `backend/app/api/actions.py:549` — the run detail
      endpoint loads every host's full output, which it then drops, and
      it is polled every few seconds

      Symptom: `GET /api/actions/runs/{id}` selects whole
      `ActionHostRun` rows, including `output` (up to 1 MiB per host,
      `MAX_OUTPUT_BYTES` in `app/tasks/action_host.py:50`), but
      `ActionHostRunOut` has no output field. The run detail page polls
      it every 3 s while a run is open
      (`components/action-run-detail.tsx:78`), and the sync tray every
      4 s per active run (`components/sync-tray.tsx:139`). For a 100-host
      run near its end, that is up to 100 MiB read from TOAST and
      decompressed per poll. Once the run ends, the page fetches every
      host's output at once (`:99`) and joins them into one string held
      in React state, though only one host is read at a time.

      Fix direction: `.options(defer(ActionHostRun.output))` in
      `get_run` (or `deferred=True` on the column, with `undefer` where
      output is read: the AI run tools, subject investigation, alert
      handling and the per-host finalisers). In the page, fetch a
      host's output when it is opened, and fetch the combined view on
      demand.

      Severity Medium: the waste scales with the run's size and lasts
      as long as anyone watches it. Traced by reading.

### Performance — Low

- [ ] **BUG-162** `backend/app/api/sync.py:243` — listing sync jobs
      loads every module's collected and desired state for three
      columns, and its `limit` has no ceiling

      Symptom: `_modules_by_host` selects whole `HostModuleStatus` rows,
      JSON state included, to build `ModuleSubStatus` (module type, sync
      status, error message). `list_jobs` (`:688`) calls it for every
      host in the page, and the Overview polls
      `/api/sync/jobs?limit=200`, or `500` for the 7-day view, every 30
      s (`frontend/lib/activity.ts:147`). `limit` is a bare `int`, so
      `?limit=1000000` loads the whole table, unlike `list_runs`, which
      caps it at 500.

      Fix direction: select only the three columns in
      `_modules_by_host`, and declare `limit: int = Query(20, ge=1,
      le=500)`.

- [ ] **BUG-163** `backend/app/ansible_runtime/generator.py:362` — every
      iptables sync runs two package installs for persistence

      Symptom: the iptables play ends with `ansible.builtin.package` for
      `iptables-persistent` and then `netfilter-persistent`, both with
      `ignore_errors`, on every sync, whether or not they are installed.
      Each is a package-manager call, and on a host that has no such
      packages (any RPM host) both are failures that Ansible prints and
      then ignores.

      Fix direction: check for `netfilter-persistent` once (a
      `command -v` with `changed_when: false`), and install only when it
      is missing on a Debian-family host. The play gathers no facts, so
      take the family from `Host.os_family` as in BUG-157.

- [ ] **BUG-164** `backend/app/user_mgmt/generator.py:107` — the users
      play looks up each user's home directory in a task of its own

      Symptom: every present user with keys gets an
      `ansible.builtin.getent` task keyed on that user, before the
      directory and key tasks: 50 users are 50 extra module runs.

      Fix direction: one `getent` task with `database: passwd` and no
      `key`, after the user tasks and before the per-user key tasks. It
      fills `ansible_facts.getent_passwd` for every account, which the
      home path already reads. A user missing in check mode is then
      absent from the dict rather than `None`, so the `when:` becomes
      `labdog_key_user in ansible_facts.getent_passwd`.

- [ ] **BUG-165** `frontend/app/(dashboard)/hosts/[id]/client-page.tsx:19`
      — every host page downloads the terminal emulator

      Symptom: the host detail page imports `TerminalTab` statically,
      which imports `@xterm/xterm` (`components/ssh-terminal.tsx:4`). In
      the static export that chunk is 337 KB, 84 KB gzipped, loaded by
      the host page and the terminal page whether or not the Terminal
      tab is opened. *Measured* from `npm run build`. Nothing in the
      frontend uses `next/dynamic`.

      Fix direction: load `SshTerminal` with `next/dynamic(() =>
      import("@/components/ssh-terminal"), { ssr: false })` in
      `_tabs/terminal.tsx`.

- [ ] **BUG-166** `frontend/lib/sync-tray.tsx:170` — two job pollers keep
      polling in hidden tabs

      Symptom: the sync tray polls `/api/sync/jobs?ids=…` every 3 s and
      the Plans apply step every 2 s
      (`app/(dashboard)/plans/_hooks/use-apply-jobs.ts:77`) with a bare
      `setInterval`, which keeps running in a background tab. The
      TanStack Query pollers elsewhere stop there, because
      `refetchIntervalInBackground` defaults to false.

      Fix direction: skip the tick while `document.visibilityState` is
      `hidden` and poll once on `visibilitychange`, or move both to
      `useQuery` with `refetchInterval`.

## Open — 2026-11-09 found during the Phase 2 refactor

Filed 2026-11-09 by the Phase 2 readability pass (PR #276), which moved
code without changing what it does. These are bugs that pass ran into in
the code it moved, and kept as they are. Each was checked against
`d63308b` (the PR #275 head that PR #276 starts from), so none comes from
the refactor. *Reproduced* entries were run through the API in the test
suite, or in a browser for the frontend one. The rest were traced by
reading. Each carries a fix direction for a fix thread. None is fixed
here.

### Medium

- [ ] **BUG-147** `backend/app/api/rules.py` — firewall rule changes
      leave no audit row

      Symptom: creating, editing, deleting or reordering a firewall rule,
      on a group or a host, writes nothing to the audit log. Every other
      configuration module (packages, repositories, services, cron jobs,
      hosts entries, CA certificates, Linux users and groups) audits its
      create, update and delete through `RuleAudit` in
      `app/api/_scoped_rules.py`. `rules.py` has no `log_action` call at
      all.

      Fix direction: give `rules.py` a `RuleAudit("firewall_rule", ...)`
      and end its endpoints with `commit_created` / `commit_updated` /
      `delete_and_commit`, and write one row for a reorder.

      Severity Medium: every account has the same rights, so the audit
      log is the only record of who opened a port, and firewall rules are
      where that record matters most.

### Low

- [ ] **BUG-143** `backend/app/api/packages.py:368` — renaming a package
      repository to a name the group already has returns 500, not 409

      Symptom: `PUT /api/groups/{id}/package-repos/{repo_id}` with the
      `name` of another repository in the group fails with
      `MissingGreenlet`. *Reproduced.*

      Root cause: `flush_or_409` rolls the session back and then builds
      the detail, and this endpoint's detail reads `repo.name`. The
      rollback expired the row, so reading it starts a lazy load outside
      the async context.

      Fix direction: build the detail from `body.name`. The same
      after-rollback read sits in the package rule and cron job updates,
      but neither can reach it: `PackageRuleUpdate` has no
      `package_name`, and `cron_jobs` has no unique constraint
      (BUG-146).

- [ ] **BUG-144** `backend/app/tasks/drift_sweep.py:305` — a drift check
      that times out reports "Host unreachable: " with nothing after it

      Symptom: `record_unreachable` formats `{error or 'connection timed
      out'}`. An exception object is always truthy, so the fallback never
      shows, and `str(TimeoutError())` is empty. The cron, hosts,
      package, resolver, service and user drift checks all go through it.

      Fix direction: `str(error) or 'connection timed out'`, as
      `app/api/host_state.py:312` and `app/tasks/action_host.py:151`
      already do.

- [ ] **BUG-145** `backend/app/schemas/rules.py:46` — a firewall port sent
      as a JSON string, list or object returns 500, not 422

      Symptom: `port_start` or `port_end` given as `"22"`, `["22"]` or
      `{}` raises `TypeError: '<=' not supported` from `_check_port`.
      Pydantic turns only `ValueError` and `AssertionError` into a
      validation error, so the request fails with a 500. `true` is taken
      as port 1. *Reproduced* on the group rule create.

      Root cause: the check runs in `mode="before"`, on the raw input,
      before pydantic has parsed it as an integer.

      Fix direction: check the range after parsing (`mode="after"`, or
      `Annotated[int, Field(ge=1, le=65535)]`). Decide at the same time
      whether a string `"22"` should be accepted, as lax parsing would,
      and whether to refuse booleans (`StrictInt`).

- [ ] **BUG-146** `backend/app/cron/models.py:28` (and services, Linux
      users, Linux groups) — a duplicate rule is accepted and then never
      applied

      Symptom: two cron jobs with the same name and user in one group,
      two services with the same `service_name`, two Linux users with the
      same `username`, or two Linux groups with the same `groupname` are
      both created with 201, and both are listed. The merge keeps the
      first (`first_wins` on that key), so the host gets one and the
      second is silently dropped. *Reproduced* for all four at group
      scope. The host-scoped tables have no unique constraint either.

      Root cause: these tables have no unique constraint on their merge
      key, unlike `package_rules` and `ca_cert_rules`. The
      `IntegrityError` → 409 handling in `app/api/cron_jobs.py` can
      never run.

      Fix direction: unique constraints on `(group_id, key)` and
      `(host_id, key)`, with a migration that first reports or merges
      existing duplicates. That makes the cron 409 path live. The other
      three need the same `flush_or_409`.

- [ ] **BUG-148** `backend/app/api/rules.py:68` — creating a firewall rule
      for a group that does not exist returns 500, not 404

      Symptom: `POST /api/groups/999999/rules` fails with a foreign-key
      `IntegrityError`. *Reproduced.* `check_gitops_lock` returns quietly
      when the group is missing, and the endpoint never calls
      `require_group`, as the other modules' group creates do. Related:
      the six module group syncs answer a missing group with 400 "No
      hosts in this group" (`app/api/_group_sync.py:34`), because an
      empty member list and a missing group look the same there.

      Fix direction: `require_group` before the insert, and a 404 for a
      missing group in `group_member_ids_or_400`.

- [ ] **BUG-149** `backend/app/ai/loop.py:788` — a session that hits a cap
      partway through a turn ends without its summary

      Symptom: when one tool call in a turn of several uses up a cap
      (commands, tokens, iterations or time), `_run_turn_tools` returns
      at once, and the turn's remaining calls get no result. The wrap-up
      turn in `_summarize_partial` then sends a transcript with those
      unanswered calls. Both wire formats refuse that (the comment on the
      parked branch just above says so), so the wrap-up fails, is logged
      at info, and the session ends "failed" with no summary. A resume
      would replay the same transcript. Traced by reading.

      Fix direction: fill the remaining calls with a "not run, the
      session reached its limit" result before returning, as the parked
      branch does with `SKIPPED_RESULT`.

- [ ] **BUG-150** `frontend/app/(dashboard)/plans/_hooks/use-plan-computation.ts:58`
      — the plan page starts a dry run as soon as a scope is picked

      Symptom: on a fresh `/plans`, picking a group or a host computes
      the plan at once, over every module, before the operator has chosen
      modules or pressed Compute. The effect is meant for plans opened
      from a URL (its comment says so), but it fires the first time the
      scope is defined, however that happened. *Reproduced* in a browser
      against the static export, on the page before and after its split.

      Fix direction: arm the effect only when the URL defined the scope
      on first render.

- [ ] **BUG-151** `backend/app/main.py:687,704` — the middleware comments
      describe the opposite of the order the middleware runs in

      Symptom: the comments say the HTTPS redirect "must be outermost"
      and that CSRF "runs after CORS". Starlette runs the middleware
      added last first. The real order, outermost first, is login rate
      limit, API rate limit, CSRF, CORS, security headers, HTTPS
      redirect. So with `tls.force_https` on, a plain-HTTP request goes
      through both rate limiters and the CSRF check before it is
      redirected, and CSRF runs before CORS.

      Fix direction: decide which order is wanted, then make the code or
      the comments match. Moving the redirect outermost changes
      behaviour, which is why the refactor left both alone.

---

## Open — 2026-11-08 security and bug audit

Filed 2026-11-08 from a whitebox review of the backend at `9e0f8b0`
(`dev`), aimed at injection, authentication, data handling, logic bugs,
edge cases and races. BUGS.md on every open branch was read first, so
nothing here repeats an entry on `dev` or on the PR #270 branch
(`BUG-127` to `BUG-133`). Entries marked *Reproduced* were run against
the real code (ansible-core 2.21.1 from `uv.lock` where Ansible is
involved); the rest were traced by reading. Each carries a fix direction
for the fix thread; none is fixed here.

### Security — Low

- [ ] **SEC-50** `backend/app/crypto/encryption.py:21,35` — stored secrets
      are encrypted without binding them to their row

      Symptom: `context_id` (the AES-GCM associated data) is never passed
      by any caller, so a ciphertext copied from one row to another
      decrypts fine; someone with database write access can swap which SSH
      key a host or repo uses without knowing the master key.

      Fix direction: pass `"<table>:<id>:<column>"` as `context_id` on
      encrypt and decrypt, with a migration that re-encrypts existing rows
      (try with context, fall back to none, rewrite).

      Severity Low: needs database write access.

      Left open by the 2026-11-08 fix pass (PR #275): the binding needs
      the row's id at all 67 encrypt/decrypt call sites, several of which
      pass ciphertext around without its row (`clone_repo`,
      `ssh_connect_host`), plus a re-encrypting migration and a change to
      the key-rotation script. That is a refactor of its own, and someone
      with database write access can already make themselves superuser,
      so it buys little until the database is otherwise trusted less.

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
