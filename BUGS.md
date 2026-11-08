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

ID counter as of last housekeeping pass: `BUG-142`, `SEC-51`,
`TYPE-03`, `DEAD-01`. Pick the next number in the relevant series
when filing a new entry. (`BUG-127` to `BUG-133` are taken on the
PR #270 branch, not yet on `dev`.)

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

### Security — Medium

- [ ] **SEC-43** `backend/app/gitops/schema.py:37,54,64,74,84` — GitOps
      YAML for firewall rules, services, packages, package repositories
      and hosts entries skips the validation the API applies

      Symptom: `HostsEntryYAML`, `ServiceYAML`, `PackageYAML`,
      `PackageRepositoryYAML` and `FirewallRuleYAML` declare plain `str`
      fields, and their importers write them without round-tripping
      through the matching `*Create` schema (the users, resolver and
      scheduled-action importers do round-trip). So a GitOps file can
      carry a hostname or alias with spaces or line breaks into
      `/etc/hosts`, a `service_name` containing `/` that places the unit
      outside `/etc/systemd/system` (the generator builds
      `/etc/systemd/system/{name}.service`), and package names, repo URLs
      and firewall CIDRs the API would refuse.

      Fix direction: in each importer, validate every entry through the
      API's `*Create` model (as `users.py:198,229` does) and report a
      failure as an import error, so there is one rule per field. Add an
      importer test per module with a value the API rejects.

      Severity Medium: reachable only by a committer to a GitOps repo, but
      it bypasses SEC-24 and the service-name rules entirely.

- [ ] **SEC-44** `backend/app/api/ssh_terminal.py:38-45,164-176` — an open
      terminal survives logout, password change and deactivation

      Symptom: the WebSocket authenticates once at the handshake. Nothing
      re-checks the user afterwards, so a root shell opened before the
      account was deactivated, its password changed, or its sessions
      revoked (SEC-30) stays open. The idle timer is reset by every
      keystroke, so there is no upper bound either.

      Fix direction: in `idle_checker`, also reload the user every 60 s
      and close with 4401 when it is inactive or its `token_version` no
      longer matches the one in the handshake token; add an absolute
      session lifetime setting (e.g. `ssh.max_session_seconds`). Test with
      a deactivation during an open session.

      Severity Medium: revocation does not reach the most powerful
      session type.

- [ ] **SEC-45** `backend/app/ai/safety.py:430`,
      `backend/app/ai/tools/ssh.py:191` — the AI command classifier models
      bash, but commands run in the target user's login shell

      Symptom: `conn.run(command)` hands the string to the remote login
      shell. When that shell is zsh or fish, some syntax that bash treats
      as a literal argument is command execution, and the classifier
      reports those commands as `read_only`, which runs without approval.
      *Reproduced* at the classifier.

      Fix direction: stop letting the login shell parse operator or model
      text. Run `conn.run("/bin/bash -s", input=command)` (or `sh -s`) so
      the wrapper contains no model input and bash, the shell the
      classifier models, does the parsing. Refuse the tool on hosts
      without `/bin/bash`. Test that a zsh-only construct is not run.

      Severity Medium: needs a non-bash login shell on the target, but
      then an injected prompt gets unapproved writes.

- [ ] **SEC-46** `backend/app/ansible_runtime/runner.py:144-156` — Ansible
      runs inherit LabDog's whole environment, secrets included

      Symptom: ansible-runner starts from `os.environ.copy()`, and
      `merged_env` adds to it without removing anything, so every
      ansible-playbook process sees `LABDOG_SECURITY__SECRET_KEY`,
      `LABDOG_SECURITY__ENCRYPTION_KEY` and the database URL. Any
      controller-side evaluation (SEC-39, SEC-40) reads them directly.

      Fix direction: build the child environment from an allowlist
      (`PATH`, `HOME`, `LANG`, `ANSIBLE_*`, the proxy variables) instead of
      copying, or at least drop every `LABDOG_` variable, and pass it via
      ansible-runner's `envvars` with the inherited environment cleared.
      Test that no `LABDOG_SECURITY__` key reaches the runner.

      Severity Medium: hardening; it turns any controller-side bug into
      a full key compromise.

- [ ] **SEC-47** `backend/app/ai/command_policy.yaml:234,984` — read-only
      AI tools can both read secrets and send data out

      Symptom: `cat` of root-only files such as `/etc/shadow` and private
      keys classifies `read_only`, and so do `curl` GETs and DNS lookups
      to any name. A model steered by prompt injection (log lines, file
      contents it was asked to read) can read a secret in one call and
      carry it out in the URL or query name of the next, with no approval
      at any step.

      Fix direction: classify reads of credential paths (`/etc/shadow`,
      `/etc/gshadow`, `/etc/ssh/*_key`, `~/.ssh/`, `*.key`, `*.pem` outside
      `/etc/ssl/certs`) as needing approval, and make `curl`/`wget` and
      lookups of names outside the host's own search domains need
      approval too, or limit `curl` to loopback. Note the trade-off in the
      policy header.

      Severity Medium: design hardening; the denylist already blocks the
      obvious redirect forms.

### Security — Low

- [ ] **SEC-48** `backend/app/api/webhooks.py:140,180`,
      `backend/app/tasks/gitops.py:54,86` — a webhook deploys whatever
      commit its payload names

      Symptom: the importer reads files at the payload's `after` /
      `checkout_sha` and only skips a SHA equal to the last one. A
      replayed genuine GitHub delivery (the signature covers the body, and
      there is no delivery-id or age check), or any request carrying the
      static GitLab token, rolls the groups back to an older commit on the
      branch, such as one before a firewall rule was tightened.

      Fix direction: treat the webhook as a trigger only: fetch the branch
      and import its tip, ignoring the payload SHA, or require the SHA to
      equal the fetched tip. Record `X-GitHub-Delivery` ids to refuse
      replays.

      Severity Low: needs the GitLab token or a captured delivery.

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

### Correctness — Medium

- [ ] **BUG-135** `backend/app/tasks/gitops.py:201-248`
      (`_trigger_group_sync`) — a GitOps push applies only the firewall,
      and skips busy hosts for good

      Symptom: the staged `SyncJob` has no `module_type` (server default
      `firewall`) and no `module_filter`, and it is dispatched to
      `run_sync_playbook`, which syncs the firewall only. A push that
      changes users, packages, services, cron, hosts or resolver is
      imported and reported as synced but not applied. A host with any
      pending or running job is skipped and never queued, so it misses
      the change entirely. When a host has two active jobs (different
      modules), `scalar_one_or_none()` raises; the group is marked in
      error but the rows already flushed for its other hosts are
      committed as `pending` without a dispatch. Same family as BUG-120.

      Fix direction: create `SyncJob(module_type="bulk",
      module_filter=<modules the import changed>)` (`ImportResult.modules`
      has `changed` per module) and dispatch `run_host_sync` with that
      filter, as `POST /api/sync/hosts/{id}/bulk` does; for a busy host
      insert the row as pending and let the host queue run it; replace
      `scalar_one_or_none()` with an `exists()` query.

      Severity Medium: GitOps silently does less than it reports.

- [ ] **BUG-136** `backend/app/tasks/scan_run.py:127-128`,
      `backend/app/tasks/scan_schedule.py:28-39` — a long scan is
      re-dispatched every minute while it runs

      Symptom: the run sets `last_run_at` and `last_run_status="running"`
      and only flushes, so other sessions keep seeing the old values until
      the scan commits at the end. The scheduler, ticking every 60 s,
      finds the config still due and sends another `scans.run_config`
      each tick. Each one blocks on `pg_advisory_xact_lock`, holding a
      worker slot and a database connection, and runs a full scan as
      soon as the previous one finishes. The UI never shows "running".

      Fix direction: commit the "running" mark in its own short
      transaction before the scan, and take the lock with
      `pg_try_advisory_lock` (session-level), returning "skipped" when it
      is held; re-check `_is_due` after acquiring it.

      Severity Medium: wasted workers and connections, and repeated scans
      of the network, for every scan longer than a minute.

### Correctness — Low

- [ ] **BUG-137** `backend/app/api/packages.py:238` — an uninstall's
      package sync is dispatched before its row is committed

      Symptom (by reading): the uninstall branch of the delete endpoint
      calls `run_package_sync.delay` inside the loop, after `flush` and
      before the `commit` at line 241 — the BUG-37 pattern, as in BUG-121.
      A worker that starts first finds no row, claims the idle host and
      fails in `_seed` with `LookupError`; the row is committed as
      `pending` and waits for the next operation on that host.

      Fix direction: collect `(job_id, host_id)` and dispatch after
      `db.commit()`, as `_trigger_group_sync` does.

      Severity Low: an uninstall can wait instead of starting.

- [ ] **BUG-138** `backend/app/api/ai.py:1055-1110`,
      `backend/app/tasks/ai_task.py:240-285` — two concurrent decisions on
      one approval can run the approved command twice

      Symptom (by reading): `decide_approval` checks `status == "pending"`
      and then writes, with no row lock, so two simultaneous requests both
      pass and both dispatch `resume_session`. `_resume_session_async`
      checks `session.status == "waiting_approval"` but does not change it
      before `execute_approved`, so both tasks run the command. The
      docstring promises exactly once.

      Fix direction: in the API, `UPDATE … SET status=… WHERE id=:id AND
      status='pending' RETURNING` and 409 when no row comes back; in the
      task, claim the session with a conditional update to `running`
      before executing.

      Severity Low: needs a race, but the command it repeats is a write.

- [ ] **BUG-139** `backend/app/api/actions.py:236-252` — the duplicate-run
      guard only catches requests that arrive at the same instant

      Symptom (by reading): the `pg_try_advisory_xact_lock` is released
      at the request's commit, so a second request a moment later starts
      a second run of the same action on the same target while the first
      is queued or running. The key `host_id or group_id` also makes host
      5 and group 5 share a lock.

      Fix direction: check for an existing queued or running `ActionRun`
      for the action and target under the lock in every case, not only
      when the lock is busy, and put the target kind in the key
      (`actions.<key>.host.<id>` / `.group.<id>`).

      Severity Low: two runs of one action on one target, serialised by
      the host queue but not refused.

- [ ] **BUG-140** `backend/app/api/admin_users.py:115-123`,
      `backend/app/main.py:703-706` — the last-superuser guard can be
      sidestepped

      Symptom (by reading): `update_user` refuses demoting the last
      superuser but not deactivating them (`is_active: false`), and it
      counts inactive superusers as "others". The fastapi-users router
      mounted at `/api/users` also offers `PATCH` and `DELETE
      /api/users/{id}` to superusers, with neither the last-superuser nor
      the "cannot delete yourself" check.

      Fix direction: count only active superusers, apply the guard to
      `is_active=False` as well, and mount the users router without the
      `{id}` routes (or override them) so `admin_users` is the only path.

      Severity Low: a superuser can lock everyone out of user
      administration, recoverable from the database.

- [ ] **BUG-141** `backend/app/api/webhooks.py:56-67`
      (`_find_repo_by_url`) — two repositories with the same URL break
      every webhook for it

      Symptom (by reading): two `GitRepository` rows for one URL (two
      branches of one repo is a normal setup) make `scalar_one_or_none()`
      raise `MultipleResultsFound`, so every delivery answers 500.

      Fix direction: select all rows for the URL and keep those whose
      `branch` matches the push's ref, verifying the signature per row.

      Severity Low: webhooks stop for that repository; manual sync works.

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
