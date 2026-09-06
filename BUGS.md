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

ID counter as of last housekeeping pass: `BUG-81`, `SEC-35`,
`TYPE-03`, `DEAD-01`. Pick the next number in the relevant series
when filing a new entry.

---

## Open

### Security findings — High

Filed 2026-05-21 from a `security-auditor` whitebox source-level
review (see the `code-audit` branch history for the baseline). Each entry was
spot-checked against current HEAD before filing.

_No bugs are currently open._

### AI command classifier — Low

- [ ] **BUG-60** `backend/app/ai/safety.py:452` — `2>&1` is classified
      `mutating`, because the segment splitter breaks on the `&`.

      **Symptom.** Any command ending in the most common redirection
      idiom — `systemctl status sshd 2>&1`, `ls -l 2>&1` — is refused in
      a read-only AI session and raises an approval request in an
      approval session, despite writing nothing.

      **Root cause.** `_SEGMENT_SPLIT` splits on `[;|&\n]`, so
      `ls -l 2>&1` becomes the two segments `ls -l 2>` and `1`. The
      second has head `1`, which is not on `READ_ONLY_HEADS`, so
      default-deny classifies the pipeline as `mutating`. The redirect
      rule itself is innocent here and is asserted not to fire on an fd
      dup (`tests/ai/test_safety.py::TestRedirection`).

      **Severity: Low.** Fails safe — over-classification costs an
      approval prompt, never an unintended write. Filed because the
      idiom is common enough that the prompts read as noise, and noisy
      prompts are how an operator learns to approve without reading.

      **Fix direction.** Teach `_segments` to recognise fd-dup
      redirections before splitting, e.g. mask `\d*>&\d+` out of the
      line, split, then restore. Deliberately not done alongside the
      2026-09 classifier hardening: changing how a command is cut into
      segments is the single edit most able to reopen the bypasses that
      work closed, and it wants its own diff and its own review rather
      than riding along in a security fix.

      Predates the hardening — verified against `dev` at `c784afb`, not
      introduced by it.

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

### Correctness — High

_No bugs are currently open._

### Correctness — Medium

- [ ] **BUG-81** `backend/app/tasks/builtin_dispatchers.py:_sync_async` —
      `_builtin.sync` reports `succeeded` when the underlying sync was
      deferred, not run.

      **Symptom.** A scheduled `_builtin.sync` whose host is genuinely
      busy finishes as `succeeded` with the sync still queued. The
      `SyncJob` is re-dispatched later by the host queue, so the work
      does happen — but the action-run history says it happened at a time
      it did not, and an operator reading the run list has no way to tell
      a real sync from a deferred one.

      **Root cause.** The `status == "deferred"` branch falls through with
      `succeeded` unchanged, on the reasoning that a defer "is not a
      failure either". True, but neither is it success.

      **Severity: Low.** Only misreports; the sync is not lost. Split out
      of the BUG-78/79/80 fix, which was about the path not working at
      all — this is about what it says when it does.

      **Fix direction.** `ActionHostRun` has no "deferred" terminal
      status, so this needs either a new one or the run staying `pending`
      with `pending_reason` set and the parent left un-finalised until the
      queued sync completes. The second is more honest and more work.

- [ ] **BUG-77** `backend/app/models/action_run.py:ActionHostRun.host_id` —
      deleting a host still destroys its per-host action output.

      **Symptom.** After BUG-65, `DELETE /api/hosts/{id}` succeeds and the
      parent `ActionRun` survives with `target_label` intact — but every
      `ActionHostRun` for that host is gone, and with it
      `ActionHostRun.output`, which is where the actual transcript lives.
      The surviving run says *what* was targeted and *that* it succeeded
      or failed; it no longer says what happened.

      **Root cause.** `ActionHostRun.host_id` is `ON DELETE CASCADE`, a
      separate decision from the parent-level FKs BUG-65 addressed.

      **Severity: Medium.** The same argument BUG-65 made against
      cascading the parent applies here with more force, since this is the
      table that holds the evidence. Not folded into the BUG-65 fix
      because making `host_id` nullable ripples into `uq_action_host_run`,
      `check_host_busy`, the whole `host_lock` claim protocol and every
      consumer that assumes the column is set — a materially larger and
      riskier change than the one that stopped the delete from failing.

      **Fix direction.** Mirror BUG-65: `host_id` nullable + `SET NULL`,
      an `ActionHostRun.hostname` snapshot written at dispatch, and the
      unique constraint re-expressed so a nulled row cannot collide.
      Audit every `host_id`-not-null assumption in `tasks/host_lock.py`
      first.

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

      **Re-rated 2026-09-05 after BUG-66.** The second half is now much
      reduced: every command carries a deadline, so a hung host costs the
      sweep one `ssh.command_timeout` rather than stopping it dead. The
      first half is untouched and is the reason this stays open — the
      sweep takes no host lock at all, so it still reads a half-applied
      ruleset mid-sync and writes `out_of_sync` over the status the sync
      is maintaining. Seven modules run this same unlocked loop
      (`drift`, `cron_drift`, `hosts_drift`, `package_drift`,
      `resolver_drift`, `service_drift`, `user_drift`), each with its own
      inline copy of the collect-and-diff body, which is what makes the
      fix a real refactor rather than a patch.

      Note that `_builtin.drift_check` — the locked path the fix should
      route through — only began working with BUG-78/79/80, so this was
      not implementable as written before that landed.

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
