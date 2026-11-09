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

ID counter as of last housekeeping pass: `BUG-176`, `SEC-51`,
`TYPE-03`, `DEAD-01`. Pick the next number in the relevant series
when filing a new entry. (`BUG-127` to `BUG-133` are taken on the
PR #270 branch, not yet on `dev`. `BUG-168` to `BUG-171` are
taken on the PR #270 and #279 branches.)

---

## Open — 2026-11-09 live test of PR #278 on lin-manager

Filed 2026-11-09 from a live test of the PR #278 image (`test-6f71547`,
the merge of `dbacc56` into `dev`, which carries #274 to #278) on
lin-manager against the real fleet. Provenance: BUG-172 was rerun
from a one-off `dev-9e0f8b0` container against the same database and
host, with the same result. BUG-174, BUG-175 and BUG-176 were checked
by reading `dev`, which has the same code. BUG-173 comes with the
BUG-128 fix in #275, which added these tasks. BUG-172 was out of sight
before, because a users sync failed at parse time until BUG-128 was
fixed.

### Medium

- [ ] **BUG-172** `backend/app/user_mgmt/collector.py:104` — users
      with keys stay out of sync after a successful sync when LabDog
      connects as a non-root user

      Symptom: on tester (LabDog's SSH user `keymaster`, passwordless
      sudo), a host rule for user `labdogtest` with one authorized key
      synced with status `success`, the key file was on the host and an
      SSH login with the key worked. The next plan, the drift check and
      the Plans page all still said `update user labdogtest`, and the
      drift check wrote `out_of_sync`. *Reproduced* live; the collector
      returned `authorized_keys: []` on both this branch and `dev`.

      Root cause: `_read_user_files` runs `cat <home>/.ssh/authorized_keys`
      and `cat /etc/sudoers.d/<user>` as the connecting user. The key file
      is `0600` and owned by the managed user, and sudoers drop-ins are
      `0440 root`, so both reads fail silently (`2>/dev/null`) unless
      LabDog logs in as root. The diff then sees no keys and no sudo
      rule. Any managed user with keys or a sudo rule drifts forever on
      such a host, and full syncs rewrite the file each time.

      Fix direction: read these files through `sudo -n` when the SSH
      user is not root, as BUG-130 did for `crontab -l`, and report a
      failed read as an error instead of an empty section.

### Low

- [ ] **BUG-173** `backend/app/user_mgmt/generator.py:117` — the
      authorized-keys tasks leave `~/.ssh` and `authorized_keys` in group
      `root`

      Symptom: after the users sync on tester, `/home/labdogtest/.ssh`
      was `drwx------ labdogtest root` and `authorized_keys` was
      `-rw------- labdogtest root`. *Reproduced* live. sshd accepts it
      (the login worked), but `ansible.posix.authorized_key`, which this
      replaced in BUG-128, used the user's primary group, as `ssh-keygen`
      and `ssh-copy-id` do.

      Root cause: the `file` and `copy` tasks set `owner` but no `group`,
      and the play runs with `become`, so new paths get root's group.

      Fix direction: set `group` to the user's primary gid from the same
      `getent passwd` facts (`getent_passwd[user][2]`).

- [ ] **BUG-174** `backend/app/cron/validators.py:37` — a cron schedule
      with trailing whitespace is accepted and then drifts forever

      Symptom: `POST /api/hosts/21/cron-jobs` with
      `"schedule": "* * * * *\n"` returned 201 and stored the newline. The
      sync wrote `* * * * * true` to the crontab and reported the module
      `in_sync`, but every later plan showed `update x (root)`.
      *Reproduced* live.

      Root cause: `validate_cron_expression` checks `expr.split()`, which
      ignores surrounding and repeated whitespace, but the schema keeps
      the raw string. The collector reads the normalised line back from
      the host, so the stored and the actual schedule never compare equal.

      Fix direction: have the schema validators return
      `" ".join(fields)` (create and update), or reject anything that is
      not five fields separated by single spaces.

- [ ] **BUG-175** `frontend/app/(dashboard)/plans/model.ts:53` — the Plans
      page shows a module that could not be previewed as "no change" and
      passes the reachability check

      Symptom: a plan for group `all` listed headscale as `no change ·
      0 CHANGES`, and the pre-flight check said `Reachability PASS — all
      20 hosts answered the dry run`. Only after expanding the row did the
      firewall module show `SSH to headscale (194.164.199.139) failed:
      Permission denied for user root`. *Reproduced* in a browser
      (headless Chromium against lin-manager). `dev` has the same
      `errored` filter in `client-page.tsx`.

      Root cause: `errored` counts a host only when the whole preview
      request fails (`p.error`). A per-module `error` inside a 200
      response (`ModuleDiff.error`) is not counted, so the row summary
      and the pre-flight check treat the host as previewed.

      Fix direction: treat a host with any module `error` as errored for
      the row badge and the Reachability check, and show the module error
      in the collapsed row.

- [ ] **BUG-176** `backend/app/ansible_runtime/runner.py:164` — every
      line of every playbook is logged by the Celery worker at WARNING

      Symptom: during one 5-host action run and two syncs, the container
      log got about 90 lines like `WARNING/ForkPoolWorker-1] ok:
      [alloy-relay]`, one per line of Ansible output, with the task
      banners in between. *Reproduced* live.

      Root cause: ansible-runner echoes the playbook's stdout unless
      `quiet=True`, and Celery redirects a task's stdout to its logger at
      WARNING. The output is already captured through `event_handler` and
      stored with the run, so the echo adds nothing.

      Fix direction: pass `quiet=True` to `init_runner`, so a real
      warning from LabDog stands out again.

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
