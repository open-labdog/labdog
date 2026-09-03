# TODO

Open tasks and forward-looking design notes for LabDog.

## Convention: open-only

**Only open items belong in this file.** When a task is completed:

1. Land the fix and write a descriptive commit message — that commit
   message is the canonical record (what changed, why, how).
2. Delete the entry from this file in the same commit (or a follow-up
   `docs(todo): Tick off ...` commit). Do **not** mark items `[x]`
   and leave them here.

To retrace a completed task, search the commit log:

```
git log --grep "labdog-playbooks"
git log -- frontend/app/\(dashboard\)/groups/page.tsx
```

---

## Pre-release checklist

### Polish

- [ ] **Audit GitHub Actions pins for Node 24 readiness (low priority).**
      GitHub is deprecating the Node 20 runtime on Actions runners; the
      runner default has already moved to Node 24 (surfaced as a warning
      during the v0.6.1 release run, e.g. under `actions/deploy-pages`).
      Nothing fails on Node 24 today, so this is not urgent — but before
      Node 20 support is fully removed, sweep `.github/workflows/*.yml`
      for any action pinned to a version whose runtime is Node 20 and
      bump to a Node 24-compatible release, so no workflow starts failing
      when the old runtime is dropped.

---

## Content-Security-Policy — finish the header

**Context:** the SPA placeholder XSS fix (see
`git log --grep "SPA dynamic-route rewrite"`) deliberately left the CSP
alone so the security fix stayed independently reviewable and free of
conflicts with the quick-wins branch touching the same middleware. The
allow-list in `_resolve_dynamic_route` is the control; the CSP is the
defence-in-depth that was *not* tightened, and it is weak enough to be
worth doing on its own.

`SecurityHeadersMiddleware` in `backend/app/main.py` currently sends:

    default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'

- [ ] **Add the directives `default-src` does not cover.** `object-src
      'none'`, `base-uri 'self'`, `form-action 'self'` and
      `frame-ancestors 'none'` all fall back to nothing rather than to
      `default-src`. `base-uri` matters most: an injected `<base>` tag
      retargets every relative script URL on the page.

- [ ] **Drop `x-xss-protection`.** Deprecated, ignored by current
      browsers, and actively harmful on some old ones.

- [ ] **Remove `script-src 'unsafe-inline'`.** This is the hard one and
      the reason the whole item is deferred rather than done. Next's
      static export inlines the RSC flight data as `<script>` blocks, so
      a nonce has to be injected per response — which means the backend
      rewriting every served HTML document, on a path that is already
      doing one rewrite and is where the XSS lived. Hashes are the other
      option and are stable per build, but they have to be recomputed at
      build time and shipped with the export. Neither is a one-liner;
      pick one deliberately rather than reaching for whichever is nearer.

---

## k8s-upgrade — broaden OS support

**Context:** The bundled `k8s-upgrade` action is currently apt-only;
the role refuses to run on `ansible_os_family != "Debian"` with a
clear error. RHEL / Rocky / Alma-family hosts are the obvious next
target — `dnf` plus `dnf versionlock` instead of `apt` + `apt-mark
hold`, otherwise the kubeadm flow is identical.

**Sketch:**

- Split `tasks/upgrade-control-plane.yml`,
  `tasks/upgrade-worker.yml`, and `tasks/upgrade-packages.yml` into
  per-distro subtasks (`-debian.yml` / `-redhat.yml`) with
  `ansible.builtin.import_tasks` selected on `ansible_os_family`.
- Drop the `Refuse non-Debian-family hosts` task in
  `tasks/main.yml`.
- Verify the kubeadm + kubelet + kubectl repo at `pkgs.k8s.io`
  serves the requested `target_version` for the host's OS family
  in `tasks/preflight.yml`.
- Smoke-test on at least one Rocky 9 + Debian 12 mixed cluster
  before declaring done.

---

## Grafana metrics — follow-ups

**Context:** 0.4.0 shipped instant CPU/memory/disk on the host page,
querying the **default** Grafana instance by the `labdog_host_id` label
that the alloy-install action stamps. A few deliberate deferrals:

- **Per-host metrics backend routing.** Today every host is queried
  against the single default Grafana instance. Add a nullable
  `host.metrics_instance_id` FK, set post-run when alloy-install runs
  against a host with a chosen instance, and query that instead of the
  default — so different hosts can report to different backends. (Needs
  a post-run linking hook analogous to `post_run_register`.)
- **Loki log surfacing** on the host page (the integration already
  stores the Loki push URL; querying/displaying logs is unbuilt).
- **More metrics / tuning:** network throughput, per-mount disk, and
  operator-configurable thresholds + refresh interval.

---

## Drift check — make enabling it discoverable

**Context:** `Host.drift_check_enabled` defaults to `False`, and
`check_all_drift` only walks hosts where it is `True`. So on a fresh
install the periodic sweep runs every 30 minutes and does nothing,
indefinitely, with no indication anywhere that drift checking is off.
Found on a real deployment: 17 hosts, all with drift checking disabled,
where the operator reasonably assumed it was running.

The cost is not just the missing checks — it silently empties every
downstream surface. `drift_samples` stays empty, so the dashboard's
drift-trend chart shows its "collecting history" state forever, and the
exporter emits no `labdog_drift_*` families at all (they are absent
rather than zero, because `module` is a free-text column and cannot be
zero-filled). All three look like bugs and none of them are.

- [ ] **Surface the fleet-wide state.** Nothing tells you "0 of 17 hosts
      have drift checking enabled". The Fleet Overview already has
      `Never Checked` as a passive count — make it, or a sibling tile,
      say *why* and link to the fix. The data is already there
      (`labdog_hosts_drift_check_enabled` / `hosts_never_drift_checked`
      exist precisely because this was invisible).

- [ ] **Explain the two flags.** `Host.drift_check_enabled` and
      `HostModuleStatus.drift_check_enabled` are independent, set from
      three unrelated places — the bulk toggle on the Hosts list, the
      Enabled/Disabled button on Host → Overview, and a per-module
      "Enable Drift Check" action on each module tab (backed by three
      different route prefixes: `/api/drift`, `/api/hosts-mgmt`,
      `/api/cron`). Nothing states how host-level and module-level
      interact, or which one a given control writes.

- [ ] **Make the empty states diagnostic rather than passive.** The
      drift-trend chart should distinguish "no checks are configured"
      from "checks are running, no drift found yet" — currently both
      render the same "collecting history" message. Same for the
      per-module drift panels.

- [ ] **Decide the default.** Whether new hosts should opt in
      automatically is a genuine product call, not an oversight:
      flipping it to `True` means LabDog starts SSHing to every newly
      added host on a timer without being asked. If it stays `False`,
      the onboarding flow should prompt for it explicitly rather than
      leaving it to be discovered.

---

## Metrics export — follow-ups

**Context:** the opt-in Prometheus `/metrics` endpoint shipped (see
[docs/metrics-export.md](docs/metrics-export.md)). These were
deliberately scoped out of that PR.

- [ ] **Redis broker queue depth.** Export `LLEN default` /
      `LLEN long_running` plus a `labdog_broker_reachable` gauge. Needs
      a short (~200ms) `redis.asyncio` timeout and a defined value to
      emit on timeout — it puts a second failure domain into an
      unauthenticated request path, which is why it wasn't bundled in.
      Celery *worker* introspection stays out of scope entirely
      (`inspect().active()` is a multi-second broadcast RPC); point
      operators at `celery-exporter` instead.

- [ ] **`drift_samples` retention + rollup.** The table has no
      retention job (unlike `audit_log` / `ssh_session_transcripts`) and
      grows unbounded. The catch: naively deleting rows makes
      `labdog_drift_checks_total` and `labdog_drift_changes_total`
      *decrease*, which Prometheus reads as a counter reset — `rate()`
      copes, `increase()` across the deletion silently under-reports.
      Recommended shape: a `drift_sample_rollup(module_type, status,
      checks, add_count, remove_count, policy_change_count)` table
      incremented **in the same transaction as the delete**, with the
      exporter's aggregates summing live rows + rollup. `app/metrics/
      aggregates.py` is written so this is a one-line `UNION ALL`
      change. Model the job on `app/tasks/audit_retention.py`.

- [ ] **Index `sync_jobs.created_at`.** There is no index on it
      (`0001_initial_schema.py` only has `(host_id, module_type,
      status)` plus a partial unique). This is **not** an exporter
      problem — the exporter's counters are all-time and use no time
      predicate — but `GET /api/dashboard/sync-success-rate` does
      `WHERE created_at >= :since` and full-scans today. Needs
      `CREATE INDEX CONCURRENTLY` in its own migration with Alembic's
      `autocommit_block()`.

- [ ] **Unify `HostModuleStatus.sync_status` vocabulary.** Three
      modules (`package_drift`, `cron_drift`, `user_drift`) write the
      legacy value `"drifted"` where the rest write `"out_of_sync"`;
      `refresh_host_sync_status` already treats them as equivalent, and
      each drift task deliberately normalises before recording a metric
      sample. Consolidating needs a data migration and touches
      `api/user_sync.py`, `api/cron_sync.py`, `api/package_sync.py`,
      the three drift tasks, `api/host_state.py`, and the frontend
      status badges.

- [ ] **Rename `docs/ui/metrics.md` → `docs/ui/host-metrics.md`.** The
      name collides conceptually with the new outbound
      `docs/metrics-export.md`; both now carry disambiguation banners,
      but distinct filenames would be clearer. Docusaurus is configured
      with `onBrokenLinks: 'throw'`, so CI will catch any missed
      reference.

- [ ] **True OpenMetrics 1.0 output.** The endpoint currently serves
      Prometheus text exposition `0.0.4` unconditionally (universally
      parsed; OpenMetrics 1.0's `# EOF` terminator and counter-naming
      differences are a common footgun). Add 1.0 as an additive
      `Accept`-negotiated branch if something in the stack requires it.

---

## AI integration — remaining phases

**Context:** Phase 1 shipped the AI subsystem: three provider backends
behind one streaming interface (OpenAI-compatible / Anthropic Messages /
Claude CLI), a default-deny command classifier, the read-only tool set
(hosts, facts, SSH, Mimir), the agent loop with iteration/command/token/
wall-clock caps, cost accounting with enforced daily and monthly budgets,
the `/assistant` and `/ai-providers` pages, and `ai.*` settings that all
default closed. See `git log --grep "feat(ai)"`.

Phase 2 shipped scheduling (both built-in actions, Loki LogQL and Mimir
range querying, per-session tool allowlists, per-tool cost recording) and
phase 3 shipped approvals: the `AIApprovalRequest` table, park-and-resume
across both runners, snapshot-before-mutating, the expiry reaper, and the
approval UI. See `git log --grep "approval"`.

Phase 5 shipped the AI verify step: `app/ai/verdict.py` (PASS / FAIL /
INCONCLUSIVE with a per-manifest fail-closed policy), `app/ai/evidence.py`
(the evidence pack — readings that are a value or an explicit absence,
with provenance), `app/ai/verify.py` (a toolless `AISession(mode="verify")`
with its own system prompt), and `ai_verify_prompt` /
`ai_verify_fail_closed` on `ActionManifest`, threaded to the two call
sites that used to pass `None`. See `git log --grep "verify"`.

Phase 4 shipped alert intake: the `AlertEvent` table with
(fingerprint, starts_at) dedup, a Grafana contact-point webhook, an
Alertmanager poller, the auto-investigation policy with its outcome
recorded per alert, and the `/alerts` page. See `git log --grep "alert"`.

Every planned phase has now shipped. One item remains, carried over from
phase 3:

- **Remediation through the action system (`propose_action`).** Approvals
  shipped, so the model can now change a host — but only by running a
  shell command. The `allowed_action_keys` column is still unused. A
  `propose_action` tool would let it ask for a named, vetted, idempotent
  actionpack instead, which already carries the snapshot/verify/rollback
  envelope. Two things have to be designed before it is written, and
  neither is obvious from the API it would copy
  (`POST /api/actions/runs`):
  - **It must not wait inside a session that owns a host lock.** A
    session driven by `_builtin.ai_task` holds that host's advisory lock.
    An action run it dispatches for the same host would defer as
    `pending` waiting for the lock the caller is holding, and a tool that
    waits for the result would hang until the task's own time limit.
    Refusing when `ToolContext.action_run_id` is set is the obvious
    guard, but that rules the tool out of exactly the scheduled runs it
    is most useful in — so the real answer is probably handing the lock
    over rather than refusing.
  - **Waiting at all is a problem for cancellation.** `AgentSDKRunner`
    holds `_db_lock` for the whole of `_execute_tool`, so a tool that
    polls for minutes blocks the driver's cancel and cap checks for that
    long. Either the poll needs its own session, or the tool dispatches
    and a separate read-only "what happened to run N" tool reports back.
  - Skip the AI's own snapshot for this tool — the action envelope
    already takes one, and both firing would leave two snapshots per
    change.

  Permission should be granted per actionpack rather than per shell
  command: packs are already named, vetted and idempotent, where a
  command-pattern allowlist would re-create the classifier's problem in a
  weaker form. Two read-only tools are missing alongside it: action
  history (what LabDog recently did to a host, which is exactly the
  context a post-upgrade check wants) and Proxmox status/backup checks.

**Known gaps in what shipped:** the DB-backed tests under `tests/ai/` need
testcontainers, so on a machine without Docker they are verified by review
and by CI rather than executed locally.

Subscription-billed sessions that can *use tools* are no longer a gap.
The `claude_agent` backend drives Claude Code through Anthropic's Claude
Agent SDK, which supplies the bidirectional stream-json transport this
file used to list as work to do. What was verified against the real
binary — that built-in tools are exposed unless `tools=[]` is passed,
that a populated `allowed_tools` shadows the permission callback, and
that `ClaudeAgentOptions.env` overlays rather than replaces the
environment — is recorded in the commit messages and in the module
docstrings under `backend/app/ai/agent_sdk/`; the branch-scoped plan file
it originally lived in was deleted before the PR, as `plans/` always is.

The terms question this list used to carry is answered. `claude
setup-token` is documented for "CI pipelines, scripts, or other
environments where interactive browser login isn't available", the token
"authenticates with your Claude subscription and requires a Pro, Max,
Team, or Enterprise plan", and plan limits are shared across Claude and
Claude Code rather than metered separately. The constraint worth knowing
is in the consumer terms rather than the docs: subscription OAuth is for
ordinary use of Anthropic's own applications, and routing requests
through a plan's credentials *on behalf of other people* is not
permitted. Own instance, own token, own hosts is inside that; running
LabDog for someone else on your plan is not, and that is now said in the
provider form and in `docs/ui/assistant.md`.

Follow-ups it leaves open:

- [ ] **Stream partial text.** The runner emits one SSE `text` event per
  completed assistant message. `include_partial_messages` would give
  token-by-token streaming, which is what the chat page wants.
- [ ] **Persist resume state across restarts.** `resume` relies on the
  CLI's own session files under `CLAUDE_CONFIG_DIR`, so parking a session
  across a container restart needs that path on a volume.
  `ClaudeAgentOptions.session_store` accepts a custom store, so
  Postgres-backed sessions are possible if the volume proves fragile.
- [ ] **Surface rate-limit state.** `RateLimitInfo` carries utilisation
  and reset time. On a subscription the money budget is meaningless but
  quota is not, so that is what the usage panel should show for these
  providers.
- [ ] **Bound what an alert storm can spend.** Auto-investigation is gated
  per alert — severity, dedup, budget — but nothing bounds sessions per
  unit of time. A flapping rule produces a new `(fingerprint, starts_at)`
  on every firing, so each one is a fresh row and a fresh session, and the
  dedup that stops repeat notifications does not stop repeat firings. The
  money budgets are the backstop, except on a subscription-billed provider
  (`claude_agent`) where cost is 0 and every USD limit is therefore inert,
  leaving only the per-session token cap — which is per session, not per
  day. Deferred deliberately on 2026-08-23: the first answer is to design
  the alert rules so they do not flap and route only what is worth
  spending on. A per-hour session cap, or a cooldown keyed on alertname,
  is the backstop if that proves insufficient.

- [ ] **Persist a verify session's evidence pack.** The rendered pack is
  in the session's first user turn, which is enough to read back but not
  to query — "which verifications ran with an unavailable disk reading"
  needs the `EvidenceItem` list stored structurally. Worth doing when
  there is a second evidence producer, not before.

---

## Dependency & supply-chain follow-ups (2026-07 code audit)

**Context:** The 2026-07 code audit's security, correctness, and cleanup
findings were fixed on the `code-audit` branch (see its `git log` — each
commit is the canonical record). The vulnerable dependency floors were
raised (`cryptography>=49`, `gitpython>=3.1.49`, `asyncssh>=2.23.1`,
`starlette>=1.0.1`, `python-multipart>=0.0.30`) and `backend/uv.lock`
added. These are the deferred hardening/maintenance tasks that remain.

- [ ] **Migrate ESLint 9 → 10 (frontend).** ESLint v9 reaches EOL ~2026-08-06.
      Flat config is already in place (`eslint.config.mjs`), so this is just the
      version bump — but it is **currently blocked upstream**: bumping `eslint`
      to 10 crashes lint with `context.getFilename is not a function`, because
      `eslint-config-next` (even the latest 16.2.10) bundles
      `eslint-plugin-react@7.37.5`, which still calls the API ESLint 10 removed.
      Re-attempt once `eslint-plugin-react` ships an ESLint-10-compatible
      release and `eslint-config-next` picks it up (then just bump both).

- [ ] **`lucide-react` 0.577 → 1.x.** Breaking (brand icons removed) — plan
      separately; the safe react-query / tailwindcss / zod / react-hook-form
      minor bumps have already landed.


---

## Supply chain, packaging and CI — 2026-09 audit

**Context:** from the same whitebox pass that produced the SEC-/BUG-
entries in [`BUGS.md`](BUGS.md). These are hardening and maintenance
tasks rather than defects, so they live here. Ordered roughly by value.

- [ ] **Build release artefacts from the lockfile.**
      `packaging/Makefile:104` runs `pip install ../backend/`, which
      re-resolves the `>=` floors in `pyproject.toml` at build time. The
      Docker image and CI both use `uv export --frozen`, and CI even runs
      `uv lock --check` — the release path ignores all of it. So the
      `.deb` a user installs contains a *different* dependency set than
      the one CI tested and Trivy scanned, the same VERSION built twice on
      different days differs, and anything compromised upstream between CI
      and the release job lands in a signed-looking artefact. Mirror the
      Docker stage and add `--require-hashes`.

- [ ] **Stop PR builds overwriting the floating `:test` Docker tag.**
      `.github/workflows/ci.yml:361-407` pushes every PR to both
      `:test-<sha>` and the mutable `:test`. BUG-55 records a production
      instance running `openlabdog/labdog:test`, so an in-review branch is
      one `docker compose pull` from a live fleet-management box. The
      immutable tag next to it is what Trivy actually scans, so the
      floating one buys nothing. **Repoint that instance before removing
      the tag.** Also gate the job on the PR coming from this repo: on a
      fork `DOCKER_HUB_PAT` is empty, the login fails, and the whole job
      plus the trivy scan that `needs:` it goes red for a contributor who
      cannot fix it.

- [ ] **Verify build-time tooling downloads.** `ci.yml:539-542,618-621`
      pipes an nfpm tarball straight into `sudo tar` on the runner that
      then builds and signs the release; `:727-729` does the same for
      gitleaks — the tool whose job is catching secrets. Versions are
      pinned but nothing is checksummed. Both projects publish
      `checksums.txt`; download, verify, then extract.

- [ ] **Pin GitHub Actions to commit SHAs.** Every `uses:` in
      `ci.yml` and `bump-playbooks-ref.yml` names a mutable tag
      (`actions/checkout@v4`, `docker/login-action@v3`,
      `softprops/action-gh-release@v2`, …). A compromised upstream action
      retroactively poisons every run, including `release-artifacts`,
      which holds `contents: write`, and the Docker login, which holds the
      registry PAT. Pin to 40-char SHAs with a trailing version comment
      and add `.github/dependabot.yml` for `github-actions` so they keep
      moving. Distinct from the Node 24 item above: that is about runtime
      versions, this is about ref immutability.

- [ ] **Attest the release.** `SHA256SUMS` is generated and uploaded by
      the same job that uploads the artefacts, so it proves nothing
      against anyone who can modify the release. `actions/attest-build-
      provenance` is a three-line addition needing `id-token: write` +
      `attestations: write` on that job only, and gives verifiable
      provenance without key management.

- [ ] **Bump the build toolchain off Node 20.** `Dockerfile:6`,
      `frontend/Dockerfile:4,14` and seven `ci.yml` sites pin Node 20,
      which is out of Maintenance LTS. `website/package.json` already
      declares `>=20.0` and Next 16 supports 22/24, so nothing blocks it.

- [ ] **Pin base images by digest.** `Dockerfile:6,16,58` use
      `node:20-alpine` and `python:3.12-slim` by tag. `python:3.12-slim`
      is rebuilt continuously, so two builds of one commit differ — which
      is also why `Dockerfile:70-77` needs its `BUILD_DATE` cache-buster.
      Pinning by digest makes that hack unnecessary.

- [ ] **Verify the bundled pack really is the pinned SHA.**
      `scripts/fetch-bundled-pack.sh:80-96` is careful — flag-injection
      and `ext::` are both handled, and the "fallback" is an explicit
      failure, not a silent switch to `main`. The remaining gap: it tries
      `--branch "$REF"` first, so if upstream is compromised and someone
      creates a branch or tag *named* like the pinned SHA, that ref's tree
      is fetched and nothing notices. Three lines after the clone:
      `git rev-parse HEAD` must equal `$REF` when `$REF` is 40 hex chars.
      Also replace the PID-based temp path with `mktemp -d`, and guard
      `rm -rf "$DEST"` against a `$DEST` of `/`.

- [ ] **Finish the systemd hardening.** `packaging/systemd/labdog.service`
      is already better than most — `NoNewPrivileges`, `ProtectSystem=strict`,
      `PrivateTmp`, a dedicated user, scoped `ReadWritePaths`. Missing and
      zero-risk for this workload: `CapabilityBoundingSet=` (empty),
      `RestrictNamespaces`, `RestrictRealtime`,
      `RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6`,
      `SystemCallFilter=@system-service`, `SystemCallArchitectures=native`,
      `ProtectProc=invisible`, `PrivateDevices`. Note `ReadWritePaths=/dev/shm`
      grants the *host's* shared `/dev/shm` — `PrivateTmp` covers only
      `/tmp` and `/var/tmp` — and `PrivateDevices=true` is what the
      "per-run SSH key on tmpfs" comment actually wants. Add a
      `systemd-analyze security` assertion to `packaging-smoke`.

- [ ] **Gate `release-artifacts` on more than the smoke test.** It
      `needs: [packaging-smoke]` only, and every lint/test/scan job
      excludes push-to-`main`. Branch protection makes that safe for the
      normal merge path but `workflow_dispatch` on `main` bypasses the
      reasoning entirely.

- [ ] **Don't ship `frontend/Dockerfile` as a footgun.** It runs as root,
      installs `serve` unpinned, and emits none of the security headers
      the FastAPI middleware provides. Fine for the standalone testing it
      documents — but add a non-root user and a pinned version, or a
      banner saying it is not for deployment.

---

## Frontend polish — 2026-09 audit

Small correctness and cost items; none are defects worth a BUGS entry.

- [ ] **Run the Playwright suite in CI.** 18 spec files under
      `frontend/e2e/` cover auth, hosts, groups, rules, sync, the
      terminal, the command palette, mobile and toasts — and nothing runs
      them, so nobody notices when they rot. The CSRF-less password change
      fixed in the quick-wins batch is exactly the class they would catch.
      Bring up postgres+redis+backend, run them non-blocking for one
      cycle, then make it a required check. If some cannot be kept green,
      delete those and be honest about the coverage rather than leaving
      them to decay.

- [ ] **Broaden `stripAnsi`.** `components/action-run-detail.tsx:23`
      strips SGR sequences only, so cursor movement, OSC title/hyperlink
      sequences and bare `\r` survive into the `<pre>`. Not an XSS risk
      (React escapes), but the rendered log is wrong and copying it into a
      terminal re-injects the control codes.

- [ ] **Only set `Content-Type` when there is a body.** `lib/api.ts:47`
      sets it on every request including GETs. When `NEXT_PUBLIC_API_URL`
      points cross-origin — the documented dev setup — that makes every
      read a non-simple CORS request and doubles the request count with
      preflights.

- [ ] **Stable row keys in the filterable tables.**
      `hosts/[id]/client-page.tsx` uses `getRowKey={(_, i) => i}` in five
      places. Harmless in append-only lists, wrong in a table that can be
      filtered and reordered, where index keys reuse DOM state across
      different rows.

- [ ] **Stop re-dispatching the facts refresh.**
      `components/actions-tab.tsx:55-64` POSTs `/facts/refresh` whenever
      `os_facts_collected_at` is older than seven days. Collection is
      async, so the timestamp does not change synchronously and the effect
      re-fires on every mount of the tab until the Celery job lands — one
      extra SSH round trip per navigation. Track dispatch in a ref keyed
      by host id.

- [ ] **Clear the query cache on logout.** `app/providers.tsx:44-56` does
      `setUser(null)` then a full-page `window.location.href`, which
      destroys the cache — so this is safe today by accident. If that ever
      becomes a client-side `router.push`, cached host facts, SSH
      transcripts and AI session content survive into the next user's
      session on a shared browser. `queryClient.clear()` in the `finally`
      costs nothing and removes the dependency on the redirect style.

- [ ] **Align `eslint-config-next` with `next`.** Pinned to `16.1.6`
      while `next` is `^16.2.11`.

---

## Refactors the audit surfaced — deliberately deferred

- [ ] **Unify `_run_action_host_async` and `_run_action_group_async`.**
      980 and ~530 lines, both carrying `# noqa: C901, PLR0912, PLR0915`,
      and both the same lifecycle written twice: claim → snapshot → run →
      verify → rollback → cleanup → finalise → dispatch-next. That
      duplication is *why* the `/dev/shm` guard and the verify-tempdir
      leak each had to be fixed in two places, and why BUG-62's claim-window
      race exists in both. Genuinely divergent in the middle, though — flat
      `all` inventory, event routing by inventory hostname, different
      snapshot fan-out — so this is a real refactor, not a merge. Do it
      *after* BUG-62's fixes land, so their tests exist as a safety net.

- [ ] **Extract the merge scaffold.** The six non-firewall `merge.py`
      modules repeat the same twenty lines: membership query ordered by
      priority, a per-group `SELECT` in a loop, first-wins dict, host
      overwrite, sort. Extracting it would fix BUG-69's ordering and
      BUG-57's dead per-entry `priority` in one place instead of six, and
      remove the per-group N+1 that `rules/desired_state.py` already shows
      how to avoid. The blocker is that the modules disagree on first-wins
      versus last-wins semantics — reconciling that *is* BUG-57, so it is a
      product decision before it is a refactor. The seven inline
      `.value if hasattr(x, "value") else str(x)` sites should move to the
      existing `app.enum_utils.enum_str` regardless.
