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

Four phases remain, each independently useful:

- **Scheduled AI tasks.** Register `_builtin.ai_task` in
  `app/actions/builtins.py` and a per-host wrapper in
  `app/tasks/ai_task.py`, routed via `PER_HOST_TASK_FOR_BUILTIN` in
  `app/tasks/action_orchestrator.py`. That inherits `ScheduledAction`
  cron dispatch, the run history, per-host advisory locking, and the
  action-run SSE stream without new infrastructure — a nightly health
  check becomes a scheduled action like any other. Needs the remaining
  read-only tools too: Mimir `query_range`, Loki LogQL (the client has
  neither today), action history, and Proxmox status/backup checks.
- **Approvals and write autonomy.** The `approval` autonomy level is
  accepted and currently behaves as read-only. Making it real needs an
  `AIApprovalRequest` table and a resumable loop: on hitting a gate,
  persist the cursor to `AISession.resume_state`, park the session, and
  **return from the Celery task** rather than blocking a worker on human
  think-time; `POST /api/ai/approvals/{id}` then re-dispatches a
  `resume_session` task. The parked session must also release its host
  advisory lock, or one pending approval wedges that host's queue.
  Mutating commands should take a Proxmox snapshot first, reusing
  `app/workflows/steps/snapshot.py`.
- **Grafana alert intake.** An `AlertEvent` table plus two producers: a
  `POST /api/webhooks/grafana-alerts` receiver following the HMAC-verify
  → `send_task` → return-immediately shape of the existing GitOps
  webhooks, and a RedBeat poller against the default Mimir instance's
  Alertmanager API as a fallback for when Grafana cannot reach LabDog.
  Both dedupe on the alert fingerprint. Eligible alerts spawn a
  read-only investigation session under a configurable severity policy.
- **AI verify step.** `app/workflows/steps/ai_verify.py` still shells out
  to `claude -p` and is effectively dead — its callers
  (`action_host.py`, `action_group.py`) pass `verification_prompt=None`.
  Rewrite it onto the provider abstraction and add `ai_verify_prompt` /
  `ai_verify_fail_closed` to `ActionManifest`, threading the prompt
  through to those call sites. Default fail-open; let a manifest opt into
  fail-closed for critical upgrades.

**Known gaps in what shipped:** the `approval` level is accepted but not
yet enforced as a distinct behaviour (it refuses like read-only); the
Claude CLI backend is single-shot only, so it cannot drive a tool-using
investigation; and the DB-backed tests under `tests/ai/` need
testcontainers, so they were verified by review and by the 123
non-DB tests rather than executed locally.

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

