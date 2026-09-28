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

## Action runs — rerun, and schedule from a run

**Context:** An `ActionRun` already stores everything needed to start it
again: `action_key`, the target (`host_id` / `group_id`), `parameters`,
`parallelism`, and the `snapshot_enabled` / `verify_enabled` /
`auto_rollback` flags. A `ScheduledAction` holds nearly the same fields
plus `schedule_cron`. Today, repeating a run or making it recurring means
filling in the action dialog again by hand.

- **Rerun a failed run.** A "Rerun" button on a failed, timed-out or
  cancelled run (run view and the Runs page), backed by something like
  `POST /api/actions/runs/{id}/rerun`, which creates a new run from the
  stored fields and dispatches it the way `POST /api/actions/runs` does.
  Open questions:
  - Whole target, or only the hosts whose `ActionHostRun` failed? For a
    group run the latter is usually what is wanted.
  - The action may have changed since: a new `action_version`, a renamed
    or removed parameter, a key that is now contested. Re-validate the
    parameters against the current manifest, and refuse or fall back to
    a prefilled dialog rather than rerunning blind.
  - Link the new run to the one it retries (e.g. `rerun_of_id`), so the
    history and the audit log read as a retry.
- **Save a run as a schedule.** A "Schedule…" action on any run that opens
  the schedule dialog (`components/scheduled-actions/schedule-action-dialog.tsx`)
  prefilled with the run's action, target, parameters and safety flags;
  the user adds the cron expression and enables it.
  `POST /api/scheduled-actions` already takes these fields, so this is
  mostly frontend. Fields that do not map one to one: `parallelism` vs
  `batch_size`, and a preview/report-only parameter on the run, which
  should not silently become a recurring preview.

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

- [ ] **Show plan quota in the usage panel.** The stop-reason half of
  this is done: a refused run now names the window and its reset time,
  and `allowed_warning` raises a banner mid-run. Both are live-only.
  `RateLimitInfo.utilization` is never stored, so the panel still shows
  these providers a money figure that is an estimate of money nobody
  spends.

  **Decided 2026-09-11: persist the last reading per provider.** Add
  `ai_providers.rate_limit_state` (JSONB, keyed by `rate_limit_type`,
  each entry holding `status`, `utilization`, `resets_at`, `seen_at`,
  `source`). Two writers already receive the event: the runner's
  `RateLimitEvent` branch (every status, `allowed` included — a 20%
  reading is as much information as an 85% one) and the provider Test
  probe, which gives an on-demand refresh without spending a session.
  Reassign the dict rather than mutate it, or SQLAlchemy never flushes
  it. `GET /api/ai/usage` gains a `quotas` list for `claude_agent`
  providers; the panel renders a bar per window with `seen_at` and
  `source` as a first-class label, because the figure is stale by
  construction and a timestamped number is information while the same
  number without one is a guess. Rejected: hiding the money meter and
  saying nothing (leaves the signal unused), and closing as done (the
  panel keeps lying to subscription users). Move the window-label map
  out of `runner.py` into a shared module when doing this.

---

## Alert remediation — let an alert investigation fix what it finds

**Goal:** full auto. An alert arrives, the assistant investigates, and it
fixes the cause without anyone watching. The lesser levels ship as well,
as steps on the way and for operators who want a person in the loop.

**Context:** alert investigations are read-only, and that is written into
the code, not a setting. `_decide_and_run` in `app/tasks/ai_alerts.py`
(the automatic path) and `investigate_alert` in `app/api/ai.py` (the
"investigate" button) both create the session with
`autonomy_level="read_only"` and build the read-only system prompt. A
follow-up message re-runs the session at its existing level. Under
read-only, `is_allowed` in `app/ai/safety.py` refuses every mutating
command and does not offer an approval. So today a fix means reading the
report and starting a new chat session by hand.

Everything below the autonomy level already exists: the approval flow
(park and resume without holding a worker or host lock), full auto,
snapshot-before-mutating, the command classifier with its deny list, the
per-session caps and the budgets.

- **Phase 1: an autonomy setting for alert investigations.** Add
  `ai.alert_autonomy_level` (`read_only` | `approval` | `full_auto`),
  defaulting to `read_only` so upgrading changes nothing. Use it in both
  places that create alert sessions, with the matching
  `build_system_prompt(level)`. Say on the Settings page what each level
  means for an alert that nobody triggered. Record the level on the
  `AlertEvent` (or read it from the session) so the `/alerts` page shows
  whether an investigation could act. `approval` is the safe first step:
  the investigation runs, then parks each change as an approval request.
  Depends on **Email notifications** (below). An alert session has no
  operator watching the chat, so a parked request nobody hears about
  just expires after `ai.approval_expiry_hours`. The operator has to be
  told both that the alert fired and that a change is waiting.

- **Phase 2: full auto, scoped.** Allowing `full_auto` in the setting is
  the easy part. What has to exist before it is offered:
  - **Scope per alert, not globally.** An allowlist of `alertname`s (or
    a label matcher) that may remediate at full auto. Everything else
    falls back to the global level. Consider a severity floor as well as
    the existing `ai.auto_investigate_min_severity`.
  - **The webhook becomes a root trigger.** `POST /api/webhooks/grafana-alerts`
    is protected by one shared token (`settings.alerts.webhook_token`).
    With full auto, anyone holding that token can make LabDog change a
    host. Require the token to be set before full auto can be enabled;
    consider an HMAC over the body and a source-IP allowlist.
  - **Prompt injection through alert text.** Labels and annotations are
    rendered into the mission (`app/ai/alert_mission.py`). They are
    sanitised and fenced, but whoever writes alert rules or annotations,
    or controls a label value such as a job or instance name, is now
    writing instructions for an agent with root. The system prompt for
    alert sessions should say the alert text is data, not instructions.
    The classifier's deny list stays the last line.
  - **Restrict the target.** Only the host the alert names
    (`target_host_ids=[event.host_id]`), never fleet-wide. Refuse full
    auto when the alert maps to no host.
  - **Loop and flap guards.** A cooldown per (host, alertname) after a
    remediation, and a cap on remediations per host per day. Otherwise a
    flapping alert, or a fix that does not hold, turns into repeated
    changes. Record every one on the alert.
  - **Tighter caps for unattended runs.** Separate, lower
    `max_commands`, token and wall-clock limits for alert remediation
    than for chat.
  - **Snapshot as a precondition.** `snapshot_if_mutating` only covers
    Proxmox-backed hosts. Decide whether full auto is refused, or
    downgraded to approval, on a host that cannot be snapshotted.
  - **Coordinate with LabDog's own changes.** Alert sessions take no host
    lock, so a remediation can overlap a sync or an action run on the
    same host. Take the lock, or refuse while one is running.

- **Phase 3: close the loop.** Did the fix work?
  - Link the resolution to the remediation: when the same alert (by
    fingerprint) resolves after a remediation session, record that on
    the `AlertEvent`. When it keeps firing past a window, mark the
    remediation as not effective and stop retrying.
  - Tell someone what was done: an email (see **Email notifications**)
    with the commands that ran, the snapshot name and the outcome.
    Unattended changes must not only live in the audit log.
  - Surface remediation outcomes on `/alerts` and the overview's Pending
    lane: which alerts were fixed automatically, which were escalated,
    which failed.

- **Later: remediate through action packs.** `propose_action` (above)
  lets the assistant run a named, vetted action pack instead of shell
  commands, with snapshot, verify and rollback built in. It is the better
  base for full auto, since permission can be granted per pack and per
  alert. Phases 1-3 don't need to wait for it, but when it lands, full
  auto remediation should prefer it, and an allowlist of packs per alert
  should become the way to scope what an alert may do.

---

## Email notifications

**Goal:** LabDog can send email, starting with the alert flow. With
alert investigations at `approval`, the user has to hear that an alert
fired and that a change is waiting for their decision; nothing reaches
them today unless they have the UI open. This is the first slice of the
"Notification system" idea in ROADMAP.md.

**Context:** LabDog sends no mail at all today: there is no SMTP config,
no mail library, and `UserManager.on_after_forgot_password`
(`app/auth/users.py`) only logs. Every user already has an email address
(fastapi-users). There is no setting for LabDog's public URL, which a
link in an email needs.

- **Transport.** SMTP config: host, port, TLS mode (none, STARTTLS,
  implicit TLS), username, password and From address. Store the password
  encrypted like other secrets (AES key), never returned by the API.
  Editable in Settings › Integrations, with a "send test email" button
  that reports the SMTP server's actual error. Add `server.public_url`
  (or a setting) for building links, and refuse to send a link-bearing
  email without it rather than guessing from a request's `Host`.
- **Delivery.** Send from a Celery task, never inline, so a slow or down
  mail server cannot hold up an alert, a webhook response or an approval.
  Retry with backoff, and record every attempt (event, recipient, status,
  error) so "why didn't I get an email?" can be answered from the UI.
  Coalesce bursts: a storm of alerts should become one email per
  interval, not one per alert.
- **Who gets what.** Opt-in per user, per event type, on the account
  page. The privilege model is flat, so any user may subscribe to any
  event. Perhaps also a shared address (a team list) in settings.
- **Events, in order:**
  1. Alert fired: alertname, severity, host, and a link to the alert and
     its investigation.
  2. Approval requested: the command or change, the host, the
     investigation's reasoning, the expiry time, and a link to the
     approval. Approving happens in the UI, not by an unauthenticated
     link in the email: an email link that runs a root command would
     turn the inbox into a credential.
  3. Approval about to expire, and approval expired.
  4. Remediation done or failed (alert remediation phase 3).
  5. Later: sync failures, drift, action-run failures, certificate
     expiry (the rest of the ROADMAP idea), and password reset.
- **Content.** Run every value from the alert payload or command output
  through the same redaction the transcript uses (`app/ai/redaction.py`).
  An email is stored in places LabDog doesn't control. Plain-text body
  first; HTML optional.
- **Leave room for other channels.** Put the event and recipient model
  behind a small channel interface, so a webhook, ntfy, Slack or Matrix
  channel can be added without touching the events. Only email ships in
  the first version.

---

## Dependency & supply-chain follow-ups (2026-07 code audit)

**Context:** The 2026-07 code audit's security, correctness, and cleanup
findings were fixed on the `code-audit` branch (see its `git log` — each
commit is the canonical record). The vulnerable dependency floors were
raised (`cryptography>=49`, `gitpython>=3.1.49`, `asyncssh>=2.23.1`,
`starlette>=1.0.1`, `python-multipart>=0.0.30`) and `backend/uv.lock`
added. These are the deferred hardening/maintenance tasks that remain.

- [ ] **React Compiler readiness (frontend).** `eslint-plugin-react-hooks`
      7.1 promoted `set-state-in-effect` and `purity` to errors; the ESLint 10
      move (2026-09-18) demoted both to warnings rather than restructure
      components in a toolchain PR. Four sites are flagged: debounced
      validation in `cron-input.tsx`, prop→state sync in
      `table-filter-cell.tsx`, seeding state from query data in
      `action-run-dialog.tsx`, and `Date.now()` in a render-time helper in
      `groups/[id]/client-page.tsx`. None is a bug. Fix them the React way
      (derive during render, key-based reset, `useSyncExternalStore` or a
      ticking hook for relative time) and restore the two rules to `error`
      in `eslint.config.mjs`. The nine `incompatible-library` warnings are
      react-hook-form's `watch()` and stay until that library is replaced or
      the rule learns it.

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
