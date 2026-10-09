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

## k8s-upgrade — broaden OS support

**Context:** The `k8s-upgrade` action in `labdog-playbooks` is currently apt-only;
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

## Protected hosts — a per-host "never write config here" flag

**Context:** Some hosts are worth inventorying and watching but must
never receive LabDog's desired state: a Proxmox node, a NAS, an
appliance, a database box someone else owns. Today the only protection
is keeping the host out of every group, and that fails silently the day
someone adds it to one. A hosts-file sync to a Proxmox node, for
example, purges the `/etc/hosts` entries PVE's cluster name resolution
depends on.

**Sketch:** a `Host.protected` boolean (name open — "protected" rather
than "unmanaged", because reading still works and some actions still
run):

- **Config syncs are refused**, whatever triggers them. Nearly every
  path converges on `run_host_sync`
  (`app/tasks/host_sync_orchestrator.py`) — per-tab and bulk endpoints,
  group fan-out, `_builtin.sync`, GitOps, `post_run_sync` — so one
  refusal there covers them. The CA-cert path does not:
  `app/ca_certs/actions.py` dispatches `run_ca_cert_action` directly,
  including auto-enqueue on group membership, and needs its own check.
  `post_run_register` should skip protected hosts too, since it writes
  desired state that a later unprotect would push.
- **Group membership is allowed but inert.** Group fan-out skips
  protected members and says so in the run/sync result, rather than
  counting them as failures.
- **Actions run only when the manifest opts in**, e.g.
  `allow_on_protected: true`. Without that, both dispatch paths
  (`action_host`, `action_group`) refuse the host at creation time with
  a clear 409, and the target picker greys it out. A blanket ban would
  also block legitimate work (an OS upgrade, a backup restore test
  driven over SSH), so the opt-in lives on the action, not the host.
- **The AI SSH tool** (`app/ai/tools/ssh.py`) may read but not run
  mutating commands on a protected host, whatever the session's
  approval mode.
- **Reading is unchanged:** state collection, drift reports (shown as
  "would differ", never as something to fix), metrics.
- **UI:** a badge on the host list and header, a toggle on the host's
  settings, and an audit row when it changes. Under the flat privilege
  model anyone can flip it — like the pack `trusted` flag, its value is
  making a write deliberate, not preventing one.

Open: whether the overview's drift and pending-change counts should
leave protected hosts out or show them with the reason.

This is a safety net for hosts that *are* managed hosts. It is not how
LabDog should drive hypervisors — see "Hypervisor context" in
[ROADMAP.md](ROADMAP.md), which avoids making the hypervisor a managed
host at all.

---

## Terminal and log windows — follow-ups

Shipped in 1.0.0: resize, maximize, minimize and text size on both
windows, and a coloured, searchable run log with wrap and a jump to the
first failure (`components/ld/window.tsx`, `lib/ansible-log.ts`). Left:

- **Collapse a task's lines under its header** in the run log. The parser
  already marks every `TASK` line; a fold needs a way to keep the copied
  text whole.
- **Show `PLAY RECAP` as a per-host table.** The recap rows are coloured
  in place now; a table would read better for a big group run, but has to
  sit outside the `<pre>` so a copy of the log stays the text Ansible
  printed.

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

## AI command policy — rules operators can edit

**Context:** SEC-38 moved what the assistant may run without approval
out of Python into `backend/app/ai/command_policy.yaml` (the read-only
forms of each command, option tables, argument gates and wrappers),
loaded and judged by `app/ai/policy.py`. The file ships in the image,
so a read the fleet needs (`kubeadm version`, a vendor CLI) still takes
a release. Decided 2026-10-02: any signed-in user may edit the rules,
consistent with the flat privilege model, with the guardrails below
keeping a rule to reads. Remaining PRs, in order:

- **Database overlay and API.** The shipped file stays the default, and
  the database holds only what operators change: added rules, and
  shipped rules switched off. Upgrades never overwrite an edit. Build
  each session's policy from the file plus the overlay through the same
  loader (`CommandPolicy.from_data`), so an operator's rule is checked
  exactly like a shipped one.
- **A "Command policy" page** showing the effective rules, and who
  changed each one and when.
- **"Allow this" on a refused call.** It pre-fills a rule from the
  refused command for the operator to narrow and confirm, so the list
  grows where the friction is. Claude could suggest the narrowest
  read-only form. A rule says a command only reads; it is not a
  standing approval for changes, so the one-shot rule in `approvals.py`
  still holds.

Guardrails, whichever PR they land in:

- **Precedence.** The denylist outranks the shipped gates and option
  tables, which outrank operator rules, which outrank default-deny. An
  operator rule can only turn "unrecognised" into "read-only"; it cannot
  make `systemctl restart` or `kubectl exec` read-only.
- **Blocked commands.** The loader already refuses read-only rules for
  shells, interpreters and exec wrappers (`NEVER_READ_ONLY` in
  `policy.py`). Operator rules go through the same refusals.
- **Audit and consistency.** Every change is audit-logged. A session
  reads the policy once when it starts or resumes, so nothing can go
  stale the way BUG-105's action registry did.
- **Later:** scoping a rule to a host group (`kubeadm` only on k8s
  hosts), and policy as code in the GitOps `_global.yaml`, with a
  `source` column so GitOps manages only its own rules.

---

## Alert remediation — let an alert investigation fix what it finds

**Shipped:** phases 1 and 2. `ai.alert_autonomy_level` (`read_only` |
`approval`) sets what every alert's session may change, and
`ai.alert_full_auto_alertnames` names the alerts that may change the host
unattended, behind the safeguards in `app/ai/alert_autonomy.py`: firing,
mapped to a host, a 32-character webhook token, a snapshot precondition,
one remediation per host at a time, a per-(host, alertname) cooldown, a
per-host daily cap, lower command and wall-clock caps, an alert section
in the system prompt, and a refusal to change a host while a sync or
action run is working on it. See `git log --grep "alert remediation"`.

Left open:

- **Take the host lock instead of refusing.** The busy guard checks
  `check_host_busy` before each mutating command and refuses while a
  sync or action run holds the host, but a sync can still be claimed
  while the AI's command runs, because AI sessions are not participants
  in the per-host queue. Closing that means a claim for the session (or
  for each command) and a dispatch-next when it ends — the same
  machinery `app/tasks/host_lock.py` gives syncs and runs (a rollback
  already takes part, see `prepare_rollback` in `app/ai/remediation.py`).

- **Harden the webhook as a root trigger.** Full auto now needs a token
  of at least 32 characters. Still worth having: a source-IP allowlist,
  and an HMAC over the body where the sender's Grafana version can sign
  webhook requests.

- **A severity floor for full auto** was considered and not added: the
  alertname list is already explicit. Revisit if operators want a
  named alert to act only at `critical`.

- **Diagnostics the command policy cannot prove read-only count as
  changes.** Seen in the live test on 2026-10-04 (sessions 20–22 on
  `tester`): `nginx -t`, `nginx -V`, `last -x` and `sudo journalctl -k`
  were classified as writes, so at full auto each took a Proxmox snapshot
  (three for one real restart), and sessions that only read were counted
  as having changed the host — by the cooldown, the daily cap and the
  check afterwards. Add the common diagnostic forms to
  `command_policy.yaml`, or let operators add them (see
  [AI command policy](#ai-command-policy--rules-operators-can-edit)).

- **Show fix outcomes on the Overview.** `/alerts` tags each checked fix
  as fixed, not effective or made worse, and with any rollback. The
  Overview's Pending lane does not, and a fix that did not work or was
  rolled back is exactly what should be in front of an operator without
  opening the alerts page.

- **Let an operator lift the 24-hour lockout.** A fix judged `made_worse`
  keeps full auto off that host for 24 hours (`WORSE_SUSPENDS_FULL_AUTO`
  in `app/ai/remediation.py`, read by `recently_made_worse`). The window
  is fixed, covers every alert on the host, and nothing in the UI, the
  API or the settings shows or clears it; the only trace is the note on
  the next listed alert that runs read-only. In the 2026-10-04 live test
  the test host stayed locked until the window ran out, and the way past
  it was moving `remediation_checked_at` on the old alert row by hand in
  the database. Make the window a setting (`ai.alert_worse_suspension_hours`,
  0 = no lockout), show "full auto suspended until …" on the host, and add
  a button that lifts it, audit-logged.

- **Later: remediate through action packs.** `propose_action` (above)
  lets the assistant run a named, vetted action pack instead of shell
  commands, with snapshot, verify and rollback built in. It is the better
  base for full auto, since permission can be granted per pack and per
  alert. When it lands, full auto remediation should prefer it, and an
  allowlist of packs per alert should become the way to scope what an
  alert may do.

---

## Email notifications — follow-ups

**Shipped:** SMTP settings with a test button, per-user opt-in
subscriptions, an outbox drained once a minute with per-recipient
coalescing and retries, a delivery log, and five events — alert fired,
approval requested / about to expire / expired, and a full-auto alert
fix. See `git log --grep "email notifications"`.

Left open:

- **More events.** Sync failures, drift, action-run failures, and
  certificate expiry — the rest of the ROADMAP "Notification system"
  idea. Each is an entry in `app/notifications/events.py` and a
  `notify()` call where it happens.
- **Password reset by email.** `UserManager.on_after_forgot_password`
  (`app/auth/users.py`) still only logs the token. The transport exists
  now; the reset link needs `notifications.public_url` like every other.
- **Other channels.** Subscriptions and the outbox are already keyed by
  `channel`, but `notify()` only fans out to email and the drain only
  sends email. A webhook, ntfy, Slack or Matrix channel needs a recipient
  per channel (a URL or topic rather than the user's address) and a
  drain of its own.
- **A shared address.** A team list in settings that receives chosen
  events, for an install where nobody wants them in a personal inbox.
- **Link an alert email to its investigation.** It links to `/alerts`:
  the email is queued when the alert is recorded, before the
  investigation that the policy may start has a session.
- **HTML bodies.** Plain text only for now.

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
      components in a toolchain PR. Two sites are still flagged, both
      `set-state-in-effect`: debounced validation in `cron-input.tsx` and
      seeding state from query data in `action-run-dialog.tsx`. Neither is
      a bug. Fix them the React way (derive during render, key-based reset)
      and restore that rule to `error` in `eslint.config.mjs`. `purity` has
      no hits left (`table-filter-cell.tsx` is gone, and the `groups/[id]`
      page no longer calls `Date.now()` while rendering), so it can go back
      to `error` now. The nine `incompatible-library` warnings are
      react-hook-form's `watch()` and stay until that library is replaced or
      the rule learns it.

---

## Supply chain, packaging and CI — 2026-09 audit

**Context:** from the same whitebox pass that produced the SEC-/BUG-
entries in [`BUGS.md`](BUGS.md). These are hardening and maintenance
tasks rather than defects, so they live here. Ordered roughly by value.

- [ ] **Stop PR builds overwriting the floating `:test` Docker tag.**
      `build-test-image` (`.github/workflows/ci.yml:491-537`) pushes every
      PR, and every push to `dev`, to both `:test-<sha>` and the mutable
      `:test`. BUG-55 records a production instance running
      `openlabdog/labdog:test` (lin-manager still does), so an in-review
      branch is one `docker compose pull` from a live fleet-management box.
      The immutable tag next to it is what Trivy actually scans, so the
      floating one buys nothing. **Repoint that instance before removing
      the tag**: `build-dev-image` already pushes `:dev-latest`, from `dev`
      only. Also gate the job on the PR coming from this repo: on a fork
      `DOCKER_HUB_PAT` is empty, the login fails, and the whole job plus
      the trivy scan that `needs:` it goes red for a contributor who cannot
      fix it.

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
