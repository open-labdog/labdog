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

---

## Content-Security-Policy — finish the header

**Context:** the SPA placeholder XSS fix (see
`git log --grep "SPA dynamic-route rewrite"`) deliberately left the CSP
alone so the security fix stayed independently reviewable and free of
conflicts with the quick-wins branch touching the same middleware. The
allow-list in `_resolve_dynamic_route` is the control; the CSP is the
defence-in-depth that was *not* tightened, and it is weak enough to be
worth doing on its own.

`SecurityHeadersMiddleware` in `backend/app/main.py` now sends:

    default-src 'self'; script-src 'self' 'unsafe-inline';
    style-src 'self' 'unsafe-inline'; object-src 'none'; base-uri 'self';
    form-action 'self'; frame-ancestors 'none'

The four directives that do not fall back to `default-src` are set, and
`x-xss-protection` is gone. `tests/test_security_headers.py` holds the
header set. One item is left, and it is the hard one:

- [ ] **Remove `script-src 'unsafe-inline'`.** Next's
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

- [ ] **Unify `HostModuleStatus.sync_status` vocabulary.** Three
      modules (`package_drift`, `cron_drift`, `user_drift`) write the
      legacy value `"drifted"` where the rest write `"out_of_sync"`;
      `refresh_host_sync_status` already treats them as equivalent, and
      each drift task deliberately normalises before recording a metric
      sample. Consolidating needs a data migration and touches
      `api/user_sync.py`, `api/cron_sync.py`, `api/package_sync.py`,
      the three drift tasks, `api/host_state.py`, and the frontend
      status badges.

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

---

## Frontend polish — 2026-09 audit

Small correctness and cost items; none are defects worth a BUGS entry.

- [ ] **Persisted `firewall_rules.is_system` rows are unreachable.** The
      column exists, `api/rules.py` refuses edit, delete, reorder and
      import on any row that has it set, and the UI disables the row's
      Edit/Delete buttons — but nothing in the codebase ever writes it.
      `RuleCreate` deliberately does not accept it, and the only system
      rules LabDog builds are synthesised at merge time in
      `app/rules/merge.py` and never persisted. So four guards and a UI
      state protect rows that can only exist if someone edits the database
      by hand. The e2e test that covered the disabled buttons was deleted
      for exactly this reason (see the note in `frontend/e2e/rules.spec.ts`).
      Decide whether the column is legacy and should go, or whether
      something is meant to set it.

---

## Refactors the audit surfaced — deliberately deferred

**Not planned — unifying the host and group run lifecycles.** This
section once listed three things as "the same code written twice":
claim-or-defer, load-and-mark-running, and dispatch-next. Only the third
was, and it is now `host_lock.release_host_queue`, shared by all five
call sites. Claim-or-defer is now a function on both paths
(`action_host._claim_or_defer`, `action_group._claim_or_defer_group`)
but they are *not* shared — extracting each was for testability, so the
single-transaction invariant BUG-62 broke can be asserted, and
`tests/test_release_host_queue.py` asserts it for both. The two
remaining pairs share a *protocol* over different cardinality: one host
versus every member, `check_host_busy` with an exclude versus
`check_hosts_busy` without, flipping a child row versus flipping the
parent run and every child. A helper covering both needs three
callbacks, which is the "third thing, harder to read than either
original" this list warns against.
