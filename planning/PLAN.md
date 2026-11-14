# unbork execution plan

Status: **Phases 1 to 3 done (discover, normalise, prioritise). Waiting at
the checkpoint for Dennis before staging (Phase 4).**
Last updated 2026-11-14.

This directory turns the two roadmap boards into a staged plan that a
later session can run stage by stage. It is long-lived on purpose, unlike
the branch-scoped `plans/` scratchpad in CONTRIBUTING.md (see D-17).

| File | What it holds |
|---|---|
| `PLAN.md` | Codebase baseline, inventory, ranking, stage outline |
| `DECISIONS.md` | Open questions with options and a recommendation, then the decision log |
| `PROPOSALS.md` | Work that is on neither roadmap; never scheduled until Dennis accepts it |
| `PROGRESS.md` | Append-only session log |

Still to come after the checkpoint: `stages/`, `items/`, `roadmap.yaml`,
and the `/run-stage` and `/review-stage` skills.

## Inputs and assumptions

- **Community roadmap:** GitHub project
  [Unbork roadmap](https://github.com/orgs/open-unbork/projects/1), 36
  draft items, read 2026-11-14.
- **Enterprise roadmap:** a private board, 19 draft items, read the same
  day. This repository is public and that board is not, so Enterprise features
  appear here by title only (D-18). Their scores and specs are in
  `planning/` in `open-unbork/unbork-enterprise`.
- **Product:** unbork is a self-hosted patch and update orchestration tool
  for server fleets (Ansible action packs, snapshot and rollback on
  Proxmox). Enterprise adds single sign-on, scoped access control and
  governance for larger organisations.
- **Constraints:** none were stated. Assumed: no deadline, one reviewer
  (Dennis), no area off limits.
- The Inputs block of the brief was left unfilled; the four lines above are
  the planning session's reading of it.

## Codebase baseline

Measured on `dev` at `5d7e443` (632 commits ahead of `main`, which is
still v1.1.0 under the old name LabDog).

### Layout

| Part | Where | Size |
|---|---|---|
| Backend | `backend/app` (FastAPI, SQLAlchemy async, Celery, ansible-runner) | about 80,700 lines of Python, 61 Alembic revisions |
| Frontend | `frontend/` (Next.js 16 static export, TanStack Query, the `components/ld` kit) | about 36,200 lines of TypeScript |
| Docs | `docs/` (Markdown), published by `website/` (Docusaurus) | |
| Packaging | `packaging/` (.deb, .rpm, tarball), `Dockerfile` | |
| Load test | `dev/loadtest/` | |

The main backend modules: `api/` (one router per feature), `sync/` and
`ansible_runtime/` (plan, compose, run), `tasks/` (Celery: the per-host
sync orchestrator, action executors, the host lock, drift sweeps),
`actions/` and `packs/` (action packs), `ai/` (assistant, command policy,
alert remediation, verify), `auth/` (roles, permissions, plugin contract),
`audit/`, `notifications/`, `proxmox/`, `gitops/`, `crypto/`.

### How the editions are kept apart

- Community is this repository, AGPL-3.0-or-later, and ships no Enterprise
  code. No per-file licence headers.
- Enterprise is a separate private package, `unbork-enterprise`, installed
  next to Community. It reaches Community only through the plugin
  contract, versioned by `PLUGIN_API` (now `1.2`) in
  `backend/app/auth/permissions.py`:
  - `backend/app/plugins.py` loads the `unbork.plugins` entry points
    (`register(app)`) and runs `unbork.migrations` entry points with their
    own Alembic version table.
  - Hooks today: the permission resolver and the visible-hosts and
    visible-groups resolvers (`app/auth/permissions.py`), login providers
    and `resolve_external_user()` (`app/auth/external.py`), password
    login paths (`app/auth/login_paths.py`).
  - Frontend seam: `frontend/lib/extensions.ts`, two lists (Settings
    sections, Users-page panels). The Enterprise build copies this
    frontend and replaces that one module.
- Rule for this plan: a Community or shared item never imports, names or
  depends on Enterprise code. When an Enterprise feature needs something
  Community lacks, the plan adds an `S-` item for the hook and bumps
  `PLUGIN_API`'s minor.
- **Not found:** hooks for the audit stream, for gating a run, for
  snapshot providers, for secret storage and for alert sources. Each is an
  `S-` item below.

### Tests, lint and CI

| Check | Command (from the directory shown) | Baseline 2026-11-14 |
|---|---|---|
| Backend tests | `backend/`: `pytest tests/ -q` (Postgres through testcontainers, or `DATABASE_URL=postgresql+asyncpg://...` as in CI) | 4,721 passed, 2 failed, 5 skipped in 8 min 14 s with coverage on (CONTRIBUTING.md quotes about 40 s for an older suite). Both failures are the environment: see notes |
| Backend coverage | `backend/`: `pytest tests/ -q --cov=app --cov-report=term` | 76% of statements (30,478 statements, 7,301 missed) |
| Backend lint | `backend/`: `ruff check .` and `ruff format --check .` | 0 findings; 715 files formatted |
| Backend SAST | `backend/`: `bandit -q -r app -b bandit-baseline.json` | no new findings against the baseline |
| Frontend types | `frontend/`: `npx tsc --noEmit` | clean |
| Frontend lint | `frontend/`: `npm run lint` | 0 errors, 11 warnings |
| Frontend E2E | `frontend/`: `npx playwright test --trace off` (needs the full stack) | not run in this session; 36 spec files |
| Docs build | `website/`: `npm run build` | not run |

Notes:

- The two failures come from the planning container, not the code:
  `tests/ai/test_agent_sdk_env.py::TestStateDir::test_it_does_not_raise_on_a_permission_error`
  (the container runs as root, which ignores directory permissions) and
  `tests/test_cron_walk.py::test_every_offered_name_is_one_the_validator_takes`
  (the system time-zone data has no `US/Eastern`). Neither was checked
  against CI in this session.
- `CLAUDE.md` says "326 tests"; the suite collects 4,728.
- The baseline ran against PostgreSQL 16 and Redis started locally,
  because the planning container has no Docker daemon. CI uses PostgreSQL
  18. Treat a difference between the two as environment, not regression.
- The frontend has no unit tests, only Playwright E2E.
- CI is one workflow, `.github/workflows/ci.yml`: lint, bandit, pytest,
  frontend build, docs build, gitleaks, pip-audit, npm audit, trivy on PRs
  to `dev`; image, packages, tag and docs site on push to `main`.
  Coverage is not measured in CI.
- Release: bump `VERSION` and `CHANGELOG.md` on `dev`, open a
  `dev` to `main` PR with the `release.md` template, merge. See
  CONTRIBUTING.md, "Release process".

### Quality baseline for stage reviews

- **Performance.** There are no benchmarks or performance tests in the
  test suite. There is a load-test harness, `dev/loadtest/` (N fake SSH
  hosts, scenarios S1 to S5, `measure_live.sql`), whose last results are
  in PR #270. It needs Docker, so it was not run here; under the project's
  testing rules it belongs on local Docker on lin-manager-2. Stage 0
  should record one `./run_one.sh S2 300 default` run as the number later
  stages compare against (see proposal PR-07).
  Hot paths to watch: the per-host sync orchestrator
  (`app/tasks/host_sync_orchestrator.py`), the host lock claim
  (`app/tasks/host_lock.py`), drift sweeps (`app/tasks/drift_sweep.py`),
  list routes that filter by visibility (`visible_filter()`), the
  dashboard reads (`app/api/dashboard.py`) and the metrics endpoint.
- **Lint warnings:** backend 0, frontend 11.
- **Coverage:** 76% of backend statements. Frontend: not measurable, no unit tests.

## Inventory

77 items: 43 Community (`C-`), 12 shared foundations (`S-`) and 22
Enterprise (`E-`, listed in the Enterprise repository). Source is
`roadmap` for every item; nothing proposed is in here.

Board items already done and left out: local accounts with fixed roles,
the action-run queue and `worker-actions` role, the rename, the plugin
contract, the landing page, and (on the other board) single sign-on with
OIDC and LDAP and scoped access control.

No board item was dropped. Splits and merges:

- "Basic rollback safety" became C-004 to C-007; "Notification events"
  C-012 and C-013; "Quiesce hooks" C-014 and C-015; "Action runs: rerun
  and save as schedule" C-017 and C-018; "Command policy" C-019 and
  C-020; "Alert remediation hardening" C-021 and C-022; "CI and toolchain
  maintenance" C-024 and C-025; "REST API tokens and a command-line tool"
  C-030 and C-031.
- "Release dev to main" became C-001 (upgrade path) and C-002 (the
  release itself).
- Five Enterprise items extend something Community does, so each was
  split into a Community-side hook (S-001 to S-005) and an Enterprise
  part that plugs into it. S-006 widens the frontend seam for the same
  reason.
- Four items from the other board change this repository and nothing in
  the Enterprise one, so they are shared: S-007 to S-012.

Added on 2026-11-14 from a decision (D-10 in the Enterprise repository),
to be scored in Phase 4: **S-013 Store each action run's structured
per-host results** (Ansible `set_stats`) on `ActionHostRun`
(`backend/app/models`, `backend/app/tasks/action_host.py`,
`action_group.py`) and pass them through the S-002 run-end hook. Shared,
M, class B.

Added on 2026-11-14 by decision D-05, to be scored in Phase 4: **C-046
Zabbix alert intake**, an adapter on the S-005 alert source seam next to
Grafana (`backend/app/api/webhooks.py`). Community, M, class B, stage 5.

Added by Dennis on 2026-11-14 (`source: user`), to be scored in Phase 4:
C-044 (memory in the rollback snapshot, stage 2), C-045 (PBS restore
points through kennel, parked with C-028 by D-19) and one Enterprise item. See
PROPOSALS.md.

### Scoring

Value, Strategic, Unblocks and Risk reduction are each 1 to 5; effort is
S=1, M=2, L=3; score = sum / effort. Tiers follow the score except where
a rule overrides it: dependencies first, security and data integrity
first, shared foundations before what builds on them. Class A is fully
autonomous, B is autonomous with a human review before merge, H needs a
decision from Dennis first.

### Ranked: Community and shared

| ID | Item | Tier | Score | V/S/U/R | Effort | Class | Stage | Depends on | Why this rank |
|---|---|---|---|---|---|---|---|---|---|
| C-002 | Release dev to main as the first unbork release | P0 | 17.0 | 4/5/5/3 | S | B | 1 | C-001, S-007, S-008, S-009 | 632 unreleased commits is the largest single risk on the board; every later stage ships through this path. Dennis merges the PR. |
| C-008 | Audit rows for action runs: who started what | P0 | 15.0 | 4/3/4/4 | S | A | 0 | none | The audit log is the base of three Enterprise items and has a hole exactly where the product does its riskiest work. |
| S-011 | Contributor licence agreement and a CLA check on pull requests | P0 | 14.0 | 1/5/3/5 | S | B | 0 | none | Board P0: one outside PR merged under today's terms cannot be undone. |
| S-001 | Extension hook: subscribe to audit entries | P0 | 13.0 | 2/4/4/3 | S | B | 0 | C-008 | One 37-line module is the single choke point for audit writes; a hook there unblocks every consumer of the audit stream. |
| C-022 | Harden the alert webhook | P0 | 12.0 | 3/3/1/5 | S | A | 0 | none | Security work on an unauthenticated-by-nature endpoint goes first by rule. |
| S-007 | Say in the README which parts are AGPL and which are commercial | P0 | 11.0 | 2/4/2/3 | S | B | 1 | D-06 | Open point of the repository-split item, and it should be true on the day of the first release. |
| S-008 | Rename the Claude Code provider labels | P0 | 11.0 | 2/4/1/4 | S | A | 1 | none | Two labels; cheap to fix before the first release under the new name. |
| C-003 | Bring the docs site back | P0 | 10.0 | 3/4/2/1 | S | A | 1 | C-002 | README links to a site that does not exist; cheap once the release is out. PR #295 already covers a manual deploy from dev. |
| C-001 | Verify and document the upgrade from LabDog 1.1.0 to unbork | P0 | 9.5 | 4/5/5/5 | M | B | 1 | PR-02 | Nothing can be released until an existing install is known to survive it; data loss on upgrade is the worst first impression. |
| C-004 | Rollback policy: stateful flag and rollback_mode on hosts | P0 | 9.0 | 5/4/4/5 | M | B | 2 | none | Automatic rollback of a database host silently discards writes; data integrity comes first, and three later items build on the flag. |
| S-002 | Extension hook: gate a run before it starts, and hear when it ends | P0 | 7.0 | 2/4/5/3 | M | B | 0 | none | Several planned extensions need to refuse or defer a sync, run, schedule or AI fix; one seam is safer than four. |
| S-009 | Stop bundling the Claude Code binary in the Docker image | P0 | 6.5 | 2/4/2/5 | M | B | 1 | none | A 131 MB third-party binary in a public image; install it on demand instead, before the next image is published. |
| C-012 | Notify on action-run failure | P1 | 15.0 | 5/3/3/4 | S | A | 2 | none | A scheduled patch run that fails silently defeats the product; the board names it a prerequisite for unattended work. |
| C-023 | Password reset by email | P1 | 11.0 | 4/3/1/3 | S | B | 2 | none | Table stakes for local accounts now that roles shipped; account-recovery flow, so reviewed. |
| S-006 | Frontend extension seam: navigation entries, pages, host and group panels | P1 | 11.0 | 2/4/4/1 | S | B | 3 | none | Extensions can add a Settings tab and nothing else; additive type change. |
| C-006 | Log what a rollback discarded | P1 | 10.0 | 3/2/1/4 | S | A | 2 | C-004 | Small, and turns an invisible data loss into an auditable one. |
| S-012 | Copyright holder line in LICENSE | P1 | 8.0 | 1/3/1/3 | S | A | 0 | none | One line, but only Dennis can say who the holder is. |
| C-005 | Check that a snapshot is possible before a destructive run | P1 | 7.0 | 4/3/2/5 | M | B | 2 | C-004, S-003 | Refusing before the run is safer than discovering mid-run that no rollback point exists. |
| C-016 | Protected hosts: a per-host never-write-config flag | P1 | 7.0 | 4/3/2/5 | M | B | 2 | none | One group mistake away from breaking a Proxmox node; a guard the fleet model lacks today. |
| C-024 | CI: stop every PR moving the floating :test image tag | P1 | 7.0 | 2/1/2/2 | S | A | 0 | none | Stage branches will open many PRs; a moving tag makes test deploys unreliable. |
| C-014 | Quiesce hooks in both action executors (rollback phase 2) | P1 | 6.5 | 4/3/2/4 | M | B | 4 | C-004 | Lets a stateful host keep automatic rollback; changes the pack manifest, a public contract. |
| C-021 | Alert remediation takes the host lock instead of refusing | P1 | 6.5 | 3/3/2/5 | M | B | 4 | none | Closes a real race between an AI command and a sync on the same host. |
| S-003 | Snapshot provider interface, with Proxmox as the first provider | P1 | 6.5 | 2/4/4/3 | M | B | 2 | none | Snapshot calls are spread over three executors today; C-005 and every other snapshot source want one interface. Behaviour must not change. |
| S-004 | Secret provider seam for stored credentials | P1 | 6.5 | 2/4/3/4 | M | B | 5 | none | Lets a credential be resolved from somewhere other than the database without touching 52 call sites twice. |
| C-007 | Rollback safety in the UI and docs; record BUG-177 to BUG-179 | P1 | 5.5 | 4/3/1/3 | M | B | 2 | C-004, C-005, C-006 | The flag is useless if operators cannot see or set it; the three bugs from closed PR #282 never reached BUGS.md. |
| C-013 | Notify on sync failure, drift and certificate expiry | P1 | 5.5 | 4/3/1/3 | M | A | 4 | C-012 | Same pattern as C-012, three more events. |
| C-017 | Rerun only the failed hosts of a run, linked to the original | P1 | 5.5 | 4/3/2/2 | M | B | 4 | none | The everyday follow-up to a partly failed patch run; adds a column and server-side re-validation. |
| S-005 | Alert source seam, with Grafana as the first source | P1 | 5.0 | 2/3/3/2 | M | B | 5 | C-022 | Intake is written for one sender; a normaliser per source keeps dedupe and severity policy in one place. |
| S-010 | Third-party notices and a source offer in the Docker image | P1 | 5.0 | 1/4/1/4 | M | B | 1 | none | Licence hygiene for a public image; best done with the first release. |
| C-026 | Make the AI assistant removable from a build or install | P1 | 4.7 | 3/4/3/4 | L | B | 1 | S-009 | Promised for Community, and the clean way to deliver S-009. |
| C-010 | AI verify: narrow the evidence window to before the action | P2 | 9.0 | 3/2/1/3 | S | A | 6 | none | Removes a known false-positive source for little effort. |
| C-020 | Command policy: count diagnostics as reads | P2 | 8.0 | 3/2/1/2 | S | A | 6 | none | Small rule-table change with existing tests to extend. |
| C-037 | Alert remediation visibility on the Overview; lift the 24-hour lockout | P2 | 8.0 | 3/2/1/2 | S | A | 6 | none | Board P3, but small and it finishes the alert-fix story. |
| C-018 | Save an action run as a schedule | P2 | 7.0 | 3/2/1/1 | S | A | 4 | none | Convenience on top of existing schedule API. |
| C-030 | REST API tokens | P2 | 6.5 | 4/4/3/2 | M | B | 5 | none | Board P3, raised to P2: every integration and the CLI need non-cookie auth. |
| C-029 | Run workers on more than one box | P2 | 4.7 | 3/4/4/3 | L | B | 8 | none | Board P3, raised to P2 because the HA item depends on it. |
| C-009 | propose_action: let the assistant request a vetted action | P2 | 4.0 | 3/4/2/3 | L | B | 6 | none | Valuable, but the board itself says two design questions must be settled first (waiting inside a locked session, approval shape). |
| C-015 | Quiesce playbooks for gitlab-upgrade and nextcloud-vm-update | P2 | 4.0 | 3/1/1/3 | M | B | 4 | C-014 | Proves the hooks on real stateful apps; lives in the private pack repository. |
| C-019 | Command policy rules that operators can edit | P2 | 3.7 | 3/3/2/3 | L | B | 6 | none | Stops a release being needed for every new read-only command; the 2026-10-02 decision that any user may edit predates roles. |
| C-011 | AI verify: second evidence producer with stored evidence packs | P2 | 3.5 | 2/2/1/2 | M | B | 6 | C-010 | Only pays off once a second producer exists; schema change, so reviewed. |
| C-025 | CI: Ubuntu 26.04 runners and the React Compiler lint rules | P2 | 3.5 | 2/1/2/2 | M | A | 0 | none | Toolchain drift is cheapest to fix before a run of feature stages. |
| C-031 | Command-line tool for the API | P3 | 4.0 | 3/3/1/1 | M | B | park | C-030 | Useful after tokens exist; board P3. |
| C-032 | Host tagging and filtering | P3 | 4.0 | 3/2/2/1 | M | B | park | none | Board P3 idea. |
| C-036 | Enable or disable modules per group | P3 | 4.0 | 3/2/1/2 | M | B | park | none | Board P3 idea; overlaps protected hosts. |
| C-039 | Notification channels: webhook, ntfy, Slack, Matrix; email follow-ups | P3 | 4.0 | 3/3/1/1 | M | B | park | C-013 | Board P3; email covers the need for now. |
| C-041 | k8s-upgrade on RHEL-family hosts | P3 | 4.0 | 3/3/1/1 | M | B | park | none | Needs RHEL-family test hosts. |
| C-027 | Application rollback and cluster rejoin, starting with etcd | P3 | 3.3 | 3/2/1/4 | L | B | park | C-014 | Needs a real cluster to test against; board P3. |
| C-033 | Export: GitOps YAML, standalone playbooks, settings backup | P3 | 3.3 | 3/3/1/3 | L | B | park | none | Three exports in one item; split when promoted. |
| C-028 | Companion VM layer, "kennel" (separate project) | P3 | 3.0 | 3/3/1/2 | L | H | park | none | A new product, not a stage item; needs a go/no-go from Dennis. |
| C-038 | Assistant usage: plan quota and session limits | P3 | 3.0 | 2/2/1/1 | M | B | park | none | Board P3. |
| C-035 | Visualise rule calculation: show losing candidates | P3 | 2.5 | 2/1/1/1 | M | A | park | none | Board P3 idea. |
| C-042 | Run log: fold task lines, PLAY RECAP as a table | P3 | 2.5 | 2/1/1/1 | M | A | park | none | Polish. |
| C-040 | Grafana and Alloy follow-ups | P3 | 2.3 | 2/2/1/2 | L | B | park | none | Six small follow-ups; split when promoted. |
| C-034 | APT/YUM repository hosting | P3 | 2.0 | 2/2/1/1 | L | H | park | none | Arguably outside the scope CONTRIBUTING.md sets; needs a product decision. |
| C-043 | Readability refactors left from the Phase 2 pass | P3 | 2.0 | 1/1/2/2 | L | B | park | none | No frontend unit tests exist, so each refactor needs its own behaviour harness first (see proposal PR-06). |

### Where each item lands in the code

- **C-001** Verify and document the upgrade from LabDog 1.1.0 to unbork: Nothing can be released until an existing install is known to survive it; data loss on upgrade is the worst first impression.
- **C-002** Release dev to main as the first unbork release: 632 unreleased commits is the largest single risk on the board; every later stage ships through this path. Dennis merges the PR.
- **C-003** Bring the docs site back: README links to a site that does not exist; cheap once the release is out. PR #295 already covers a manual deploy from dev.
- **C-004** Rollback policy: stateful flag and rollback_mode on hosts: Automatic rollback of a database host silently discards writes; data integrity comes first, and three later items build on the flag.
- **C-005** Check that a snapshot is possible before a destructive run: Refusing before the run is safer than discovering mid-run that no rollback point exists.
- **C-006** Log what a rollback discarded: Small, and turns an invisible data loss into an auditable one.
- **C-007** Rollback safety in the UI and docs; record BUG-177 to BUG-179: The flag is useless if operators cannot see or set it; the three bugs from closed PR #282 never reached BUGS.md.
- **C-008** Audit rows for action runs: who started what: The audit log is the base of three Enterprise items and has a hole exactly where the product does its riskiest work.
- **C-009** propose_action: let the assistant request a vetted action: Valuable, but the board itself says two design questions must be settled first (waiting inside a locked session, approval shape).
- **C-010** AI verify: narrow the evidence window to before the action: Removes a known false-positive source for little effort.
- **C-011** AI verify: second evidence producer with stored evidence packs: Only pays off once a second producer exists; schema change, so reviewed.
- **C-012** Notify on action-run failure: A scheduled patch run that fails silently defeats the product; the board names it a prerequisite for unattended work.
- **C-013** Notify on sync failure, drift and certificate expiry: Same pattern as C-012, three more events.
- **C-014** Quiesce hooks in both action executors (rollback phase 2): Lets a stateful host keep automatic rollback; changes the pack manifest, a public contract.
- **C-015** Quiesce playbooks for gitlab-upgrade and nextcloud-vm-update: Proves the hooks on real stateful apps; lives in the private pack repository.
- **C-016** Protected hosts: a per-host never-write-config flag: One group mistake away from breaking a Proxmox node; a guard the fleet model lacks today.
- **C-017** Rerun only the failed hosts of a run, linked to the original: The everyday follow-up to a partly failed patch run; adds a column and server-side re-validation.
- **C-018** Save an action run as a schedule: Convenience on top of existing schedule API.
- **C-019** Command policy rules that operators can edit: Stops a release being needed for every new read-only command; the 2026-10-02 decision that any user may edit predates roles.
- **C-020** Command policy: count diagnostics as reads: Small rule-table change with existing tests to extend.
- **C-021** Alert remediation takes the host lock instead of refusing: Closes a real race between an AI command and a sync on the same host.
- **C-022** Harden the alert webhook: Security work on an unauthenticated-by-nature endpoint goes first by rule.
- **C-023** Password reset by email: Table stakes for local accounts now that roles shipped; account-recovery flow, so reviewed.
- **C-024** CI: stop every PR moving the floating :test image tag: Stage branches will open many PRs; a moving tag makes test deploys unreliable.
- **C-025** CI: Ubuntu 26.04 runners and the React Compiler lint rules: Toolchain drift is cheapest to fix before a run of feature stages.
- **C-026** Make the AI assistant removable from a build or install: Promised for Community, and the clean way to deliver S-009.
- **C-027** Application rollback and cluster rejoin, starting with etcd: Needs a real cluster to test against; board P3.
- **C-028** Companion VM layer (separate project): A new product, not a stage item; needs a go/no-go from Dennis.
- **C-029** Run workers on more than one box: Board P3, raised to P2 because the HA item depends on it.
- **C-030** REST API tokens: Board P3, raised to P2: every integration and the CLI need non-cookie auth.
- **C-031** Command-line tool for the API: Useful after tokens exist; board P3.
- **C-032** Host tagging and filtering: Board P3 idea.
- **C-033** Export: GitOps YAML, standalone playbooks, settings backup: Three exports in one item; split when promoted.
- **C-034** APT/YUM repository hosting: Arguably outside the scope CONTRIBUTING.md sets; needs a product decision.
- **C-035** Visualise rule calculation: show losing candidates: Board P3 idea.
- **C-036** Enable or disable modules per group: Board P3 idea; overlaps protected hosts.
- **C-037** Alert remediation visibility on the Overview; lift the 24-hour lockout: Board P3, but small and it finishes the alert-fix story.
- **C-038** Assistant usage: plan quota and session limits: Board P3.
- **C-039** Notification channels: webhook, ntfy, Slack, Matrix; email follow-ups: Board P3; email covers the need for now.
- **C-040** Grafana and Alloy follow-ups: Six small follow-ups; split when promoted.
- **C-041** k8s-upgrade on RHEL-family hosts: Needs RHEL-family test hosts.
- **C-042** Run log: fold task lines, PLAY RECAP as a table: Polish.
- **C-043** Readability refactors left from the Phase 2 pass: No frontend unit tests exist, so each refactor needs its own behaviour harness first (see proposal PR-06).
- **S-001** Extension hook: subscribe to audit entries: One 37-line module is the single choke point for audit writes; a hook there unblocks every consumer of the audit stream.
- **S-002** Extension hook: gate a run before it starts, and hear when it ends: Several planned extensions need to refuse or defer a sync, run, schedule or AI fix; one seam is safer than four.
- **S-003** Snapshot provider interface, with Proxmox as the first provider: Snapshot calls are spread over three executors today; C-005 and every other snapshot source want one interface. Behaviour must not change.
- **S-004** Secret provider seam for stored credentials: Lets a credential be resolved from somewhere other than the database without touching 52 call sites twice.
- **S-005** Alert source seam, with Grafana as the first source: Intake is written for one sender; a normaliser per source keeps dedupe and severity policy in one place.
- **S-006** Frontend extension seam: navigation entries, pages, host and group panels: Extensions can add a Settings tab and nothing else; additive type change.
- **S-007** Say in the README which parts are AGPL and which are commercial: Open point of the repository-split item, and it should be true on the day of the first release.
- **S-008** Rename the Claude Code provider labels: Two labels; cheap to fix before the first release under the new name.
- **S-009** Stop bundling the Claude Code binary in the Docker image: A 131 MB third-party binary in a public image; install it on demand instead, before the next image is published.
- **S-010** Third-party notices and a source offer in the Docker image: Licence hygiene for a public image; best done with the first release.
- **S-011** Contributor licence agreement and a CLA check on pull requests: Board P0: one outside PR merged under today's terms cannot be undone.
- **S-012** Copyright holder line in LICENSE: One line, but only Dennis can say who the holder is.

### Enterprise features

Enterprise items are listed for transparency, with no dates or
commitments (decision D-18). Their scores, specs and decisions, and a few
internal items, are in the Enterprise repository.

| ID | Feature | Edition |
|---|---|---|
| E-002 | Send the audit log to a SIEM (syslog/CEF and HTTPS webhook) | Enterprise |
| E-003 | Maintenance and change-freeze windows: model, API and enforcement | Enterprise |
| E-004 | Maintenance windows: calendar UI on Settings, host and group pages | Enterprise |
| E-005 | SAML sign-in | Enterprise |
| E-006 | Secrets from HashiCorp Vault | Enterprise |
| E-007 | Zabbix write-back | Enterprise |
| E-008 | Change-ticket integration: core and one first system | Enterprise |
| E-009 | Change tickets: the remaining systems | Enterprise |
| E-010 | Snapshot and rollback on VMware | Enterprise |
| E-011 | Bare-metal rollback: detect LVM, ZFS and Btrfs layouts in state collection | Enterprise |
| E-012 | Bare-metal rollback: LVM snapshot provider with driven reboot | Enterprise |
| E-013 | Bare-metal rollback: ZFS provider | Enterprise |
| E-014 | Multi-tenancy | Enterprise |
| E-015 | Compliance evidence export (NIS2, DORA, ISO 27001) | Enterprise |
| E-016 | Hardening baselines | Enterprise |
| E-017 | Offline install for air-gapped networks | Enterprise |
| E-018 | Disaster recovery: verified backup and restore of unbork's own state | Enterprise |
| E-019 | High availability reference deployment | Enterprise |
| E-023 | Sync host groups and servers from LDAP | Enterprise |
| E-024 | Map user permissions from LDAP groups | Enterprise |

### Dependencies

No cycles. The chains that matter:

- C-008 then S-001, then the Enterprise audit consumers.
- S-002, then two Enterprise items.
- C-004 then C-005, C-006, C-007, C-014; S-003 then C-005.
- C-001 then C-002 then C-003; S-007, S-008 and S-009 before C-002.
- S-009 then C-026.
- C-030 then C-031.
- No `C-` or `S-` item depends on an `E-` item.

## Proposed stage outline

Not final: stage files are written in Phase 4, after the checkpoint.
Enterprise items are interleaved; their IDs are shown so the order is
visible.

| Stage | Goal | Items |
|---|---|---|
| 0 | Foundations: audit and run-gate hooks, webhook hardening, CI, contributor terms | C-008, C-022, S-001, S-002, C-024, C-025, S-011, S-012, E-001, E-021, E-022 |
| 1 | The first release as unbork | S-008, S-009, S-014, C-026, S-010, S-007, E-020, C-001, C-002, C-003 |
| 2 | Rollback you can trust | C-004, S-003, C-005, C-006, C-007, C-016, C-012, C-023 |
| 3 | Governance for larger organisations | S-006, E-002, E-003, E-004 |
| 4 | Day-two operations | C-017, C-018, C-013, C-014, C-015, C-021 |
| 5 | Integrations | C-030, S-004, S-005, E-005, E-006, E-007 |
| 6 | A safer assistant | C-010, C-020, C-037, C-011, C-019, C-009 |
| 7 | Rollback beyond Proxmox | E-011, E-012, E-008 |
| 8 | Scale and assurance | C-029, E-018, E-019, E-017, E-016, E-015 |
| parked | P3 | C-027, C-028, C-031 to C-036, C-038 to C-043, E-009, E-010, E-013, E-014 |

Critical path: stage 0 hooks, then the release in stage 1. Everything
after stage 1 ships through the release path that stage 1 proves.

Main risks:

- Stage 1 cannot finish without Dennis: D-02, D-03 and D-04 block it, and
  he merges the release PR.
- Nine of the 22 Enterprise items are class H. Stages 7 and 8 are mostly
  blocked on decisions today.
- S-003 refactors the snapshot path that every destructive action runs
  through, in the same stage that changes rollback behaviour.
