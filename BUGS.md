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

ID counter as of last housekeeping pass: `BUG-167`, `SEC-51`,
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
