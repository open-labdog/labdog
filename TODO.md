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

## Theme migration — documentation and screenshot follow-ups

**Context:** The remaining pages, modals and shared components move onto
the LabDog kit in a stacked series of PRs (`feat/theme-*`). The user guide
and the frontend reference are deliberately *not* updated in those PRs —
CONTRIBUTING asks for docs per PR, but here every screen changes and the
prose would be rewritten six times. Docs and the `docs/ui/screenshots/`
set land together in one follow-up once the last PR is in. Each PR appends
what it made stale; this section is the input to that follow-up and is
deleted when it lands.

### `frontend/FRONTEND.md`

- "The LabDog kit": list the new pieces — `Field`, `Help`, `Banner`,
  `Toolbar`, `BulkBar`, `Facts`, `Stat`, `CodeBlock`, `Copy`, `Confirm`,
  `Steps`, `RunStatus` — and `lib/status.ts` (the status vocabularies
  beside `lib/fleet.ts`); document the modal form idiom (`Modal onSubmit`,
  `Field` + `.inp`, footer caption · Cancel · primary).
- "Page file rules": a screen with enough going on gets private `_tabs/`
  and `_dialogs/` folders beside its `page.tsx` (the host detail split is
  the example — `app/(dashboard)/hosts/[id]/_tabs/config/*.tsx` etc.),
  each file a single tab or dialog, sharing query keys by string rather
  than a prop-drilled hook.
- "Confirmation Dialogs": `Confirm` from `@/components/ld`
  (`components/ui/confirm-dialog.tsx` is gone — every caller now imports
  `Confirm` directly).
- "Loading States": no skeletons — `Table loading`, a pulsing `Dot`, a
  button label swap.
- "Tooltips": `title=` on icon-only affordances, `Help` (a `<details>`)
  for paragraphs, `Field hint=` for one-liners; `InfoPopover` goes.
- The `.btn` comment in `globals.css` no longer says `components/ui/button`
  stays for dialogs; the "Stack" row still lists shadcn. `components/ui/`
  is down to `badge button input label markdown select textarea` —
  `data-table table-filter-cell table dialog tooltip card breadcrumb
  skeleton confirm-dialog` and `hooks/{use-table-state,use-column-resize}`
  are gone, their last callers converted.

### `docs/ui/*.md`

- `settings.md`, `admin.md` — the Settings sub-pages are shell screens now
  (crumbs `settings / access` or `settings / integrations`, `PageHead`,
  kit tables with head filters instead of per-column filter popovers, no
  column resizing); Users, SSH keys, Git repositories, Grafana, Proxmox and
  AI providers forms are modals with lowercase `.tt` labels; Proxmox lives
  at `/hypervisors` with its own head (no "Workflow tab" — that text was
  stale); AI providers gains a delete confirmation and `why` disclosures
  in place of the info popovers; the settings editor's AI category is a
  native disclosure with the pinned `ai.enabled` row above it.
- `gitops-ui.md` — the Git repositories list, repository page and the
  connect wizard (steps as `Steps`, one panel per step, review rows as
  checkbox rows in panels; "Webhook URLs" is a modal with copy buttons).
- `actions.md` — the Packs tab: registry and sources are two panels; a
  contested key expands under the registry table; row actions are the
  lowercase `sync · win all keys · edit · delete`. The host/group Activity
  tab's Actions view is the same two-panel shape (available actions ·
  recent runs) at a smaller size; run detail (`/hosts/{id}/actions/runs/{id}`
  and the generic and group equivalents) is a screen now, not a bare page —
  crumbs `fleet / hosts / <name>` (or `operations / runs` for a fleet run),
  the per-host grid is a `Table`, the log is a `CodeBlock` with pin-to-bottom.
- `scheduled-actions.md` — the wizard is a `Modal` with `Steps`, not a
  plain dialog; the list's row actions are inline `edit · runs · delete`,
  no kebab; "Run history" is a modal with a `Table` of runs and "Run now"
  in the footer, not a slide-in drawer. `/schedules` redirects into the
  Actions page's Schedules tab (already true before this PR; the tab's
  own content is what changed here).
- `operations.md` › Audit — a search box (user, entity, IP address),
  from/to dates and `action`/`entity` chips in the head, all applied to
  the entries loaded so far (load more fetches the next 100); the SSH
  transcript is a modal with a `CodeBlock`.
- `alerts.md` — Alerts is a list screen (a `Seg` firing/all switch, one
  table row per alert) under the Assistant zone, not a stack of cards;
  investigate and view → are the row's actions.
- `hosts.md` › Discovery — the three tabs (pending, schedules, scan now)
  are all `Toolbar` + `Table` now; the scan-schedule list drops its kebab
  menu for inline `edit · run now · pending · delete`; the pending queues
  (fleet-wide and per-scan) select rows with the kit `Table`'s own
  checkbox column, not a bespoke table component.
- `groups.md` — "## Group Detail" › Config: the module list + editor pattern
  is unchanged, but every editor (rules, services, hosts file, packages,
  users, cron, resolver, CA certs) is now `components/config/*-editor.tsx`,
  embedded-only (`groupId` prop, no `embedded` flag, no own head/h1). Drop
  the sentence about dragging rows to reorder firewall rules — the design
  uses ▲/▼ only and that is what shipped; `@dnd-kit/*` is gone. The
  standalone URLs (`/groups/{id}/rules` etc., and `/groups/{id}/actions`)
  now redirect into the Config/Activity tab — mention this once, since
  every module editor's screenshot section referenced its own URL.
- `hosts.md`, `host-metrics.md` — the host page is Overview · Config ·
  Metrics · Terminal · Activity, same five-tab shape as the group page;
  the eight module tabs behind Config use the same effective/override
  table pattern as `groups.md` describes for the group editors, with a
  "comes from" provenance column; their per-column filter popovers are
  gone and nothing replaces them (each list is one host's worth). The
  Services tab's live inventory keeps start/stop/restart, edit (opens the
  override dialog with the on-disk unit) and remove (confirmed). Terminal
  renders in place now — drop the sentence about a bottom drawer. Group
  membership on Overview is the add/remove picker, not a dialog with
  confirmations. New host (`/hosts/new`) and New group (`/groups/new`) are
  shell screens with the design's form recipe; the group multi-select on
  both is an always-visible checkbox box, not a dropdown.

### `docs/ui/screenshots/`

- Every PNG predates the theme; the list grows as each PR rebuilds the
  screens they show. From the module editors:
  `group-rules.png`, `group-services.png`, `group-hosts-entries.png`,
  `group-packages.png`, `group-users.png`, `group-cron-jobs.png`,
  `group-resolver.png` (all currently show the old per-module standalone
  page — retake them as the group page's Config tab instead). From the
  host detail: `hosts.png` (retake with the Config tab open on a module
  that has both group and host rows, and once with Terminal open). From
  operations and discovery: `hosts-discover.png` (the scan-now tab,
  mid-scan and with results), `discovery.png` (the scan-schedules tab).

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
