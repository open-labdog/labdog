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
