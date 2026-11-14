# unbork execution plan

Status: **Planning done (phases 1 to 7). Ready for `/run-stage 0`.**
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
| `roadmap.yaml` | Source of truth: every item's stage, dependencies, class and status |
| `stages/stage-<N>.md` | One stage: items in order, entry and exit criteria |
| `items/<ID>-<slug>.md` | One spec per item |
| `reviews/stage-<N>.md` | Written by `/review-stage` at the end of each stage |

The skills are `.claude/skills/run-stage/` and `.claude/skills/review-stage/`.

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

107 items: 62 Community (`C-`), 14 shared foundations (`S-`) and 31
Enterprise (`E-`, specified in the Enterprise repository). By source: 77
from the two roadmap boards, 14 accepted proposals, 11 asked for by Dennis
during planning, and 5 created by a logged decision. `roadmap.yaml` is the
source of truth; the tables below are a rendering of it.

Board items already done and left out: local accounts with fixed roles,
the action-run queue and `worker-actions` role, the plugin contract, the
landing page, and (on the other board) single sign-on with OIDC and LDAP
and scoped access control. The board's "rename" item was marked done but
only the visible names had changed; decision D-02 reopened the rest as
C-048 to C-051.

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
- The VM operations service (C-028, working name kennel) was split into
  C-028, C-054, C-055, C-057, C-058 and C-059 after decisions D-22 and
  D-23, with C-045 (PBS restore points) by D-19.

Added after the checkpoint: C-044 and C-045 (Dennis), C-046 (D-05),
S-013 (a decision about hardening results), S-014 (D-04), C-064 (D-17),
and the accepted proposals C-047 (PR-01), C-048 to C-051 (PR-02), C-052
(PR-03), C-060 (PR-04), C-051 also covers PR-05, C-061 (PR-06), C-062 and
C-063 (PR-07).

### Scoring

Value, Strategic, Unblocks and Risk reduction are each 1 to 5; effort is
S=1, M=2, L=3; score = sum / effort. Tiers follow the score except where
a rule overrides it: dependencies first, security and data integrity
first, shared foundations before what builds on them. Class A is fully
autonomous, B is autonomous with a human review before merge, H needs a
decision from Dennis first.

Community and shared items by class: 23 A, 50 B, 3 H. All 107: 26 A,
73 B, 8 H.

### Ranked: Community and shared

| ID | Item | Source | Tier | Score | V/S/U/R | Effort | Class | Stage | Depends on | Why this rank |
|---|---|---|---|---|---|---|---|---|---|---|
| [C-002](items/C-002-release-dev-to-main-as-unbork-2.md) | Release dev to main as unbork 2.0.0 | roadmap | P0 | 17.0 | 4/5/5/3 | S | B | 3 | C-001, S-007, S-008, S-009, S-014, C-051 | 632 unreleased commits is the largest single risk on the board; version decided in D-03. Dennis merges the PR. |
| [C-008](items/C-008-audit-rows-for-action-runs-who-started.md) | Audit rows for action runs: who started what | roadmap | P0 | 15.0 | 4/3/4/4 | S | A | 1 | none | The audit log is the base of three Enterprise items and has a hole exactly where the product does its riskiest work. |
| [S-011](items/S-011-contributor-licence-agreement-and-a-cla-check.md) | Contributor licence agreement and a CLA check on pull requests | roadmap | P0 | 14.0 | 1/5/3/5 | S | B | 0 | none | D-01 fixed the form: Apache-style ICLA and CCLA, CLA Assistant Lite. Dennis creates the private signatures repository and marks the check required. |
| [S-001](items/S-001-extension-hook-subscribe-to-audit-entries.md) | Extension hook: subscribe to audit entries | roadmap | P0 | 13.0 | 2/4/4/3 | S | B | 1 | C-008 | One 37-line module is the single choke point for audit writes; a hook there unblocks every consumer of the audit stream. |
| [C-022](items/C-022-harden-the-alert-webhook.md) | Harden the alert webhook | roadmap | P0 | 12.0 | 3/3/1/5 | S | A | 1 | none | Security work on an unauthenticated-by-nature endpoint goes first by rule. |
| [C-047](items/C-047-audit-rows-for-firewall-rule-changes-bug.md) | Audit rows for firewall rule changes (BUG-147) | proposed | P0 | 12.0 | 3/3/2/4 | S | A | 1 | none | Accepted proposal PR-01: the same hole as C-008, in the module where the record matters most. |
| [S-007](items/S-007-say-in-the-readme-which-parts-are.md) | Say in the README which parts are AGPL and which are commercial | roadmap | P0 | 11.0 | 2/4/2/3 | S | B | 3 | none | Open point of the repository-split item, and it should be true on the day of the first release. |
| [S-008](items/S-008-rename-the-claude-code-provider-labels.md) | Rename the Claude Code provider labels | roadmap | P0 | 11.0 | 2/4/1/4 | S | A | 2 | none | Two labels; cheap to fix before the first release under the new name. |
| [C-003](items/C-003-bring-the-docs-site-back.md) | Bring the docs site back | roadmap | P0 | 10.0 | 3/4/2/1 | S | A | 3 | C-002 | README links to a site that does not exist; cheap once the release is out. PR #295 already covers a manual deploy from dev. |
| [S-014](items/S-014-remove-the-subscription-token-field-from-ai.md) | Remove the subscription token field from AI providers | decision | P0 | 10.0 | 1/4/1/4 | S | B | 2 | none | D-04: Community keeps only the operator's own Claude Code sign-in. |
| [C-001](items/C-001-verify-and-document-the-upgrade-from-labdog.md) | Verify and document the upgrade from LabDog 1.1.0 to unbork | roadmap | P0 | 9.5 | 4/5/5/5 | M | B | 3 | C-048, C-049, C-050, C-052 | Nothing can be released until an existing install is known to survive it; the rename (D-02) makes the upgrade the riskiest step. |
| [C-004](items/C-004-rollback-policy-stateful-flag-and-rollback-mode.md) | Rollback policy: stateful flag and rollback_mode on hosts | roadmap | P0 | 9.0 | 5/4/4/5 | M | B | 4 | none | Automatic rollback of a database host silently discards writes; data integrity comes first, and three later items build on the flag. |
| [C-048](items/C-048-rename-configuration-and-environment-with-the-old.md) | Rename: configuration and environment, with the old names as fallbacks | proposed | P0 | 7.0 | 3/4/4/3 | M | B | 2 | none | D-02 and accepted proposal PR-02: the first release is the cheapest moment to rename. |
| [C-049](items/C-049-rename-paths-packages-and-service-names-migrated.md) | Rename: paths, packages and service names, migrated on upgrade | proposed | P0 | 7.0 | 3/4/3/4 | M | B | 2 | C-048 | D-02: data and config paths and unit names move, and an upgrade must carry an install across. |
| [C-051](items/C-051-rename-readme-docs-site-claude-md-and.md) | Rename: README, docs site, CLAUDE.md and changelog wording | proposed | P0 | 7.0 | 2/3/1/1 | S | A | 2 | C-048 | Accepted proposals PR-02 and PR-05: what a new user and a new session read first. |
| [S-002](items/S-002-extension-hook-gate-a-run-before-it.md) | Extension hook: gate a run before it starts, and hear when it ends | roadmap | P0 | 7.0 | 2/4/5/3 | M | B | 1 | none | Several planned extensions need to refuse or defer a sync, run, schedule or AI fix; one seam is safer than four. |
| [C-052](items/C-052-test-the-upgrade-from-1-1-0.md) | Test the upgrade from 1.1.0 in packaging-smoke | proposed | P0 | 6.5 | 2/3/3/5 | M | A | 3 | C-049 | Accepted proposal PR-03: turns C-001 from a one-off manual check into a gate. |
| [S-009](items/S-009-stop-bundling-the-claude-code-binary-in.md) | Stop bundling the Claude Code binary in the Docker image | roadmap | P0 | 6.5 | 2/4/2/5 | M | B | 2 | none | D-04: installed on first use instead of shipped in the image. |
| [C-064](items/C-064-make-planning-a-named-exception-to-the.md) | Make planning/ a named exception to the plans/ rule | decision | P0 | 6.0 | 1/1/3/1 | S | A | 0 | none | D-17: without it the release checklist and the image build treat the plan as a mistake. |
| [C-050](items/C-050-rename-on-host-markers-alert-labels-metrics.md) | Rename: on-host markers, alert labels, metrics and pack variables | proposed | P0 | 4.3 | 3/3/2/5 | L | B | 2 | C-048 | D-02: host checks must recognise both names and the first sync removes the old chains and files; a mistake here touches every managed host, so it is tested on hosts named test first. |
| [C-012](items/C-012-notify-on-action-run-failure.md) | Notify on action-run failure | roadmap | P1 | 15.0 | 5/3/3/4 | S | A | 4 | none | A scheduled patch run that fails silently defeats the product; the board names it a prerequisite for unattended work. |
| [C-023](items/C-023-password-reset-by-email.md) | Password reset by email | roadmap | P1 | 11.0 | 4/3/1/3 | S | B | 4 | none | Table stakes for local accounts now that roles shipped; account-recovery flow, so reviewed. |
| [S-006](items/S-006-frontend-extension-seam-navigation-entries-pages-host.md) | Frontend extension seam: navigation entries, pages, host and group panels | roadmap | P1 | 11.0 | 2/4/4/1 | S | B | 5 | none | Extensions can add a Settings tab and nothing else; additive type change. |
| [C-006](items/C-006-log-what-a-rollback-discarded.md) | Log what a rollback discarded | roadmap | P1 | 10.0 | 3/2/1/4 | S | A | 4 | C-004 | Small, and turns an invisible data loss into an auditable one. |
| [C-062](items/C-062-measure-backend-coverage-in-ci.md) | Measure backend coverage in CI | proposed | P1 | 8.0 | 1/1/3/3 | S | A | 0 | none | Accepted proposal PR-07: gives every stage review a number to compare. |
| [S-012](items/S-012-copyright-holder-line-in-license.md) | Copyright holder line in LICENSE | roadmap | P1 | 8.0 | 1/3/1/3 | S | A | 0 | none | D-07: Dennis personally. One line. |
| [C-005](items/C-005-check-that-a-snapshot-is-possible-before.md) | Check that a snapshot is possible before a destructive run | roadmap | P1 | 7.0 | 4/3/2/5 | M | B | 4 | C-004, S-003 | Refusing before the run is safer than discovering mid-run that no rollback point exists. |
| [C-016](items/C-016-protected-hosts-a-per-host-never-write.md) | Protected hosts: a per-host never-write-config flag | roadmap | P1 | 7.0 | 4/3/2/5 | M | B | 4 | none | One group mistake away from breaking a Proxmox node; a guard the fleet model lacks today. |
| [C-024](items/C-024-ci-stop-every-pr-moving-the-floating.md) | CI: stop every PR moving the floating :test image tag | roadmap | P1 | 7.0 | 2/1/2/2 | S | A | 0 | none | Stage branches will open many PRs; a moving tag makes test deploys unreliable. |
| [C-014](items/C-014-quiesce-hooks-in-both-action-executors-rollback.md) | Quiesce hooks in both action executors (rollback phase 2) | roadmap | P1 | 6.5 | 4/3/2/4 | M | B | 6 | C-004 | Lets a stateful host keep automatic rollback; changes the pack manifest, a public contract. |
| [C-021](items/C-021-alert-remediation-takes-the-host-lock-instead.md) | Alert remediation takes the host lock instead of refusing | roadmap | P1 | 6.5 | 3/3/2/5 | M | B | 6 | none | Closes a real race between an AI command and a sync on the same host. |
| [C-044](items/C-044-keep-the-vm-s-memory-in-the.md) | Keep the VM's memory in the rollback snapshot | user | P1 | 6.5 | 5/3/1/4 | M | B | 4 | S-003 | Asked for by Dennis; D-19 confirmed a backup never holds RAM, so this is the only route to memory restore. |
| [S-003](items/S-003-snapshot-provider-interface-with-proxmox-as-the.md) | Snapshot provider interface, with Proxmox as the first provider | roadmap | P1 | 6.5 | 2/4/4/3 | M | B | 4 | none | Snapshot calls are spread over three executors today; C-005 and every other snapshot source want one interface. Behaviour must not change. |
| [S-004](items/S-004-secret-provider-seam-for-stored-credentials.md) | Secret provider seam for stored credentials | roadmap | P1 | 6.5 | 2/4/3/4 | M | B | 7 | none | Lets a credential be resolved from somewhere other than the database without touching 52 call sites twice. |
| [C-007](items/C-007-rollback-safety-in-the-ui-and-docs.md) | Rollback safety in the UI and docs; record BUG-177 to BUG-179 | roadmap | P1 | 5.5 | 4/3/1/3 | M | B | 4 | C-004, C-005, C-006 | The flag is useless if operators cannot see or set it; the three bugs from closed PR #282 never reached BUGS.md. |
| [C-013](items/C-013-notify-on-sync-failure-drift-and-certificate.md) | Notify on sync failure, drift and certificate expiry | roadmap | P1 | 5.5 | 4/3/1/3 | M | A | 6 | C-012 | Same pattern as C-012, three more events. |
| [C-017](items/C-017-rerun-only-the-failed-hosts-of-a.md) | Rerun only the failed hosts of a run, linked to the original | roadmap | P1 | 5.5 | 4/3/2/2 | M | B | 6 | none | The everyday follow-up to a partly failed patch run; adds a column and server-side re-validation. |
| [C-061](items/C-061-a-unit-test-harness-for-the-frontend.md) | A unit-test harness for the frontend | proposed | P1 | 5.0 | 1/2/4/3 | M | A | 0 | none | Accepted proposal PR-06: stage reviews cannot reproduce a frontend bug with a failing test without one. |
| [S-005](items/S-005-alert-source-seam-with-grafana-as-the.md) | Alert source seam, with Grafana as the first source | roadmap | P1 | 5.0 | 2/3/3/2 | M | B | 7 | C-022 | Intake is written for one sender; a normaliser per source keeps dedupe and severity policy in one place. |
| [S-010](items/S-010-third-party-notices-and-a-source-offer.md) | Third-party notices and a source offer in the Docker image | roadmap | P1 | 5.0 | 1/4/1/4 | M | B | 2 | none | Licence hygiene for a public image; best done with the first release. |
| [C-026](items/C-026-make-the-ai-assistant-removable-from-a.md) | Make the AI assistant removable from a build or install | roadmap | P1 | 4.7 | 3/4/3/4 | L | B | 2 | S-009 | Promised for Community, and the clean way to deliver S-009. |
| [C-060](items/C-060-bind-stored-secrets-to-their-row-sec.md) | Bind stored secrets to their row (SEC-50) | proposed | P1 | 4.5 | 2/2/1/4 | M | B | 7 | S-004 | Accepted proposal PR-04: same call sites as S-004, so one pass. |
| [C-063](items/C-063-record-a-load-test-baseline.md) | Record a load-test baseline | proposed | P1 | 4.5 | 1/2/3/3 | M | A | 0 | none | Accepted proposal PR-07: the only performance measure the project has. |
| [S-013](items/S-013-store-each-action-run-s-structured-per.md) | Store each action run's structured per-host results | decision | P1 | 4.5 | 2/3/3/1 | M | B | 6 | S-002 | D-10: generic and useful on its own; anything that reports on run results needs it. |
| [C-010](items/C-010-ai-verify-narrow-the-evidence-window-to.md) | AI verify: narrow the evidence window to before the action | roadmap | P2 | 9.0 | 3/2/1/3 | S | A | 8 | none | Removes a known false-positive source for little effort. |
| [C-020](items/C-020-command-policy-count-diagnostics-as-reads.md) | Command policy: count diagnostics as reads | roadmap | P2 | 8.0 | 3/2/1/2 | S | A | 8 | none | Small rule-table change with existing tests to extend. |
| [C-037](items/C-037-alert-remediation-visibility-on-the-overview-lift.md) | Alert remediation visibility on the Overview; lift the 24-hour lockout | roadmap | P2 | 8.0 | 3/2/1/2 | S | A | 8 | none | Board P3, but small and it finishes the alert-fix story. |
| [C-018](items/C-018-save-an-action-run-as-a-schedule.md) | Save an action run as a schedule | roadmap | P2 | 7.0 | 3/2/1/1 | S | A | 6 | none | Convenience on top of existing schedule API. |
| [C-030](items/C-030-rest-api-tokens.md) | REST API tokens | roadmap | P2 | 6.5 | 4/4/3/2 | M | B | 7 | none | Board P3, raised to P2: every integration and the CLI need non-cookie auth. |
| [C-058](items/C-058-kennel-per-vm-readiness-api.md) | kennel: per-VM readiness API | user | P2 | 6.0 | 3/3/3/3 | M | B | 11 | C-028 | D-22 scope: one call that says whether a VM can be snapshotted, restored or leased. |
| [C-055](items/C-055-kennel-automatic-backup-restore-tests.md) | kennel: automatic backup restore tests | user | P2 | 5.0 | 5/4/1/5 | L | B | 11 | C-054, C-057 | Dennis's first use case: a backup that has never been restored is not a backup. |
| [C-028](items/C-028-kennel-repository-service-skeleton-and-proxmox-pool.md) | kennel: repository, service skeleton and Proxmox pool token | roadmap | P2 | 4.7 | 3/4/5/2 | L | H | 11 | S-003 | D-22: Dennis decided to build it. Class H because the repository, its name on GitHub and the Proxmox tokens are his to create. |
| [C-029](items/C-029-run-workers-on-more-than-one-box.md) | Run workers on more than one box | roadmap | P2 | 4.7 | 3/4/4/3 | L | B | 10 | none | Board P3, raised to P2 because the HA item depends on it. |
| [C-054](items/C-054-kennel-leases-and-the-sweeper.md) | kennel: leases and the sweeper | user | P2 | 4.7 | 3/4/4/3 | L | B | 11 | C-028 | D-22 scope: every VM kennel creates has an owner and an expiry, and nothing is left behind. |
| [C-057](items/C-057-kennel-isolated-sandbox-network-with-a-jump.md) | kennel: isolated sandbox network with a jump host | user | P2 | 4.3 | 3/3/3/4 | L | H | 11 | C-028 | D-23: supported from the start. Class H because it needs a VLAN or SDN zone on Dennis's Proxmox. |
| [C-009](items/C-009-propose-action-let-the-assistant-request-a.md) | propose_action: let the assistant request a vetted action | roadmap | P2 | 4.0 | 3/4/2/3 | L | B | 8 | none | Design settled in D-13: the tool hands off and never waits. |
| [C-015](items/C-015-quiesce-playbooks-for-gitlab-upgrade-and-nextcloud.md) | Quiesce playbooks for gitlab-upgrade and nextcloud-vm-update | roadmap | P2 | 4.0 | 3/1/1/3 | M | B | 6 | C-014 | Proves the hooks on real stateful apps; lives in the private pack repository. |
| [C-045](items/C-045-kennel-pbs-restore-points-restored-beside-the.md) | kennel: PBS restore points, restored beside the original | user | P2 | 4.0 | 4/3/1/4 | L | B | 11 | C-028, C-054 | Asked for by Dennis; D-19 and D-23 put backup restore in kennel, beside the original first. |
| [C-046](items/C-046-zabbix-alert-intake.md) | Zabbix alert intake | decision | P2 | 4.0 | 3/3/1/1 | M | B | 7 | S-005 | D-05: intake is Community, an adapter on the S-005 seam. |
| [C-019](items/C-019-command-policy-rules-that-operators-can-edit.md) | Command policy rules that operators can edit | roadmap | P2 | 3.7 | 3/3/2/3 | L | B | 8 | none | D-14: a new ai.policy.manage permission, admins only. |
| [C-059](items/C-059-unbork-talks-to-kennel-snapshot-provider-and.md) | unbork talks to kennel: snapshot provider and surge nodes for upgrades | user | P2 | 3.7 | 4/4/1/2 | L | B | 11 | S-003, C-054, C-058 | Dennis's second use case: surge nodes during Kubernetes upgrades. |
| [C-011](items/C-011-ai-verify-second-evidence-producer-with-stored.md) | AI verify: second evidence producer with stored evidence packs | roadmap | P2 | 3.5 | 2/2/1/2 | M | B | 8 | C-010 | Only pays off once a second producer exists; schema change, so reviewed. |
| [C-025](items/C-025-ci-ubuntu-26-04-runners-and-the.md) | CI: Ubuntu 26.04 runners and the React Compiler lint rules | roadmap | P2 | 3.5 | 2/1/2/2 | M | A | 0 | none | Toolchain drift is cheapest to fix before a run of feature stages. |
| [C-031](items/C-031-command-line-tool-for-the-api.md) | Command-line tool for the API | roadmap | P3 | 4.0 | 3/3/1/1 | M | B | parked | C-030 | Useful after tokens exist; board P3. |
| [C-032](items/C-032-host-tagging-and-filtering.md) | Host tagging and filtering | roadmap | P3 | 4.0 | 3/2/2/1 | M | B | parked | none | Board P3 idea. |
| [C-036](items/C-036-enable-or-disable-modules-per-group.md) | Enable or disable modules per group | roadmap | P3 | 4.0 | 3/2/1/2 | M | B | parked | none | Board P3 idea; overlaps protected hosts. |
| [C-039](items/C-039-notification-channels-webhook-ntfy-slack-matrix-email.md) | Notification channels: webhook, ntfy, Slack, Matrix; email follow-ups | roadmap | P3 | 4.0 | 3/3/1/1 | M | B | parked | C-013 | Board P3; email covers the need for now. |
| [C-041](items/C-041-k8s-upgrade-on-rhel-family-hosts.md) | k8s-upgrade on RHEL-family hosts | roadmap | P3 | 4.0 | 3/3/1/1 | M | B | parked | none | Needs RHEL-family test hosts. |
| [C-027](items/C-027-application-rollback-and-cluster-rejoin-starting-with.md) | Application rollback and cluster rejoin, starting with etcd | roadmap | P3 | 3.3 | 3/2/1/4 | L | B | parked | C-014 | Needs a real cluster to test against; board P3. |
| [C-033](items/C-033-export-gitops-yaml-standalone-playbooks-settings-backup.md) | Export: GitOps YAML, standalone playbooks, settings backup | roadmap | P3 | 3.3 | 3/3/1/3 | L | B | parked | none | Three exports in one item; split when promoted. |
| [C-038](items/C-038-assistant-usage-plan-quota-and-session-limits.md) | Assistant usage: plan quota and session limits | roadmap | P3 | 3.0 | 2/2/1/1 | M | B | parked | none | Board P3. |
| [C-035](items/C-035-visualise-rule-calculation-show-losing-candidates.md) | Visualise rule calculation: show losing candidates | roadmap | P3 | 2.5 | 2/1/1/1 | M | A | parked | none | Board P3 idea. |
| [C-042](items/C-042-run-log-fold-task-lines-play-recap.md) | Run log: fold task lines, PLAY RECAP as a table | roadmap | P3 | 2.5 | 2/1/1/1 | M | A | parked | none | Polish. |
| [C-040](items/C-040-grafana-and-alloy-follow-ups.md) | Grafana and Alloy follow-ups | roadmap | P3 | 2.3 | 2/2/1/2 | L | B | parked | none | Six small follow-ups; split when promoted. |
| [C-034](items/C-034-apt-yum-repository-hosting.md) | APT/YUM repository hosting | roadmap | P3 | 2.0 | 2/2/1/1 | L | H | parked | none | Arguably outside the scope CONTRIBUTING.md sets; needs a product decision. |
| [C-043](items/C-043-readability-refactors-left-from-the-phase-2.md) | Readability refactors left from the Phase 2 pass | roadmap | P3 | 2.0 | 1/1/2/2 | L | B | parked | none | No frontend unit tests exist, so each refactor needs its own behaviour harness first (see proposal PR-06). |

### Where each item lands in the code

- **C-001** Verify and document the upgrade from LabDog 1.1.0 to unbork: docs/upgrade.md, packaging/, Dockerfile, backend/app/config.py
- **C-002** Release dev to main as unbork 2.0.0: VERSION, CHANGELOG.md, .github/PULL_REQUEST_TEMPLATE/release.md
- **C-003** Bring the docs site back: website/docusaurus.config.ts (still titled LabDog), .github/workflows/ci.yml pages-build/pages-deploy
- **C-004** Rollback policy: stateful flag and rollback_mode on hosts: backend/app/models/host.py, backend/app/tasks/action_host.py, action_group.py, alembic
- **C-005** Check that a snapshot is possible before a destructive run: backend/app/tasks/action_host.py (_phase_a_snapshot), action_group.py (_snapshot_all), backend/app/proxmox/client.py
- **C-006** Log what a rollback discarded: backend/app/tasks/action_host.py, action_group.py, backend/app/audit/logger.py
- **C-007** Rollback safety in the UI and docs; record BUG-177 to BUG-179: frontend/app/(dashboard)/hosts/[id], docs/ui/actions.md, BUGS.md
- **C-008** Audit rows for action runs: who started what: backend/app/api/actions.py (no log_action call today), backend/app/tasks/action_orchestrator.py
- **C-009** propose_action: let the assistant request a vetted action: backend/app/ai/tools/, backend/app/ai/approvals.py, backend/app/api/actions.py
- **C-010** AI verify: narrow the evidence window to before the action: backend/app/ai/evidence.py, backend/app/ai/verify.py
- **C-011** AI verify: second evidence producer with stored evidence packs: backend/app/ai/evidence.py, backend/app/ai/models.py
- **C-012** Notify on action-run failure: backend/app/notifications/events.py, backend/app/tasks/action_orchestrator.py
- **C-013** Notify on sync failure, drift and certificate expiry: backend/app/notifications/events.py, backend/app/tasks/host_sync_orchestrator.py, drift_sweep.py, backend/app/ca_certs
- **C-014** Quiesce hooks in both action executors (rollback phase 2): backend/app/actions/manifest.py, backend/app/tasks/action_host.py, action_group.py
- **C-015** Quiesce playbooks for gitlab-upgrade and nextcloud-vm-update: therealscienta/unbork_playbooks_private: actions/gitlab-upgrade, actions/nextcloud-vm-update
- **C-016** Protected hosts: a per-host never-write-config flag: backend/app/models/host.py, backend/app/sync/orchestrator.py, backend/app/api/sync.py, groups.py
- **C-017** Rerun only the failed hosts of a run, linked to the original: backend/app/models (ActionRun.rerun_of_id, new), backend/app/api/actions.py, frontend/components/rerun-action-button.tsx
- **C-018** Save an action run as a schedule: frontend/components/rerun-action-button.tsx, backend/app/api/scheduled_actions.py
- **C-019** Command policy rules that operators can edit: backend/app/ai/command_policy.yaml, backend/app/ai/policy.py, new table and settings page
- **C-020** Command policy: count diagnostics as reads: backend/app/ai/command_policy.yaml, backend/app/ai/policy.py
- **C-021** Alert remediation takes the host lock instead of refusing: backend/app/ai/remediation.py, backend/app/tasks/host_lock.py
- **C-022** Harden the alert webhook: backend/app/api/webhooks.py (/grafana-alerts)
- **C-023** Password reset by email: backend/app/auth/users.py (on_after_forgot_password only logs), backend/app/notifications/email.py, urls.py
- **C-024** CI: stop every PR moving the floating :test image tag: .github/workflows/ci.yml (build-test-image)
- **C-025** CI: Ubuntu 26.04 runners and the React Compiler lint rules: .github/workflows/ci.yml, frontend/eslint.config.mjs
- **C-026** Make the AI assistant removable from a build or install: backend/app/ai/, backend/pyproject.toml ([agent] extra), Dockerfile, frontend ai pages
- **C-027** Application rollback and cluster rejoin, starting with etcd: open-unbork/unbork-playbooks k8s-upgrade, backend/app/actions/manifest.py
- **C-028** kennel: repository, service skeleton and Proxmox pool token: new repository (Dennis creates it); design in closed PR #281 and https://claude.ai/artifact/MJo5wKpzFdKspSeMNDrL3S
- **C-029** Run workers on more than one box: backend/app/celery_manager.py, docs/scaling-out.md, dev/docker-compose.split.yml
- **C-030** REST API tokens: backend/app/auth/, backend/app/api/admin_users.py
- **C-031** Command-line tool for the API: labdog-lint/ (existing validator), new package
- **C-032** Host tagging and filtering: backend/app/models/host.py, frontend hosts list
- **C-033** Export: GitOps YAML, standalone playbooks, settings backup: backend/app/gitops/ (serializer exists), backend/app/ansible_runtime/
- **C-034** APT/YUM repository hosting: none found: no code exists for this
- **C-035** Visualise rule calculation: show losing candidates: backend/app/merge_utils.py, frontend plans pages
- **C-036** Enable or disable modules per group: backend/app/models (HostGroup), backend/app/sync/orchestrator.py
- **C-037** Alert remediation visibility on the Overview; lift the 24-hour lockout: backend/app/api/dashboard.py, backend/app/ai/alert_autonomy.py
- **C-038** Assistant usage: plan quota and session limits: backend/app/ai/providers, ai_providers.rate_limit_state (new)
- **C-039** Notification channels: webhook, ntfy, Slack, Matrix; email follow-ups: backend/app/notifications/delivery.py, service.py
- **C-040** Grafana and Alloy follow-ups: backend/app/grafana/, open-unbork/unbork-playbooks alloy-install
- **C-041** k8s-upgrade on RHEL-family hosts: open-unbork/unbork-playbooks actions/k8s-upgrade
- **C-042** Run log: fold task lines, PLAY RECAP as a table: frontend/lib/ansible-log.ts, frontend/components/ld/window.tsx
- **C-043** Readability refactors left from the Phase 2 pass: frontend/app/(dashboard)/hosts/[id]/client-page.tsx and config tabs
- **C-044** Keep the VM's memory in the rollback snapshot: backend/app/proxmox/client.py:229 (create_snapshot sends no vmstate), backend/app/tasks/action_host.py, action_group.py, backend/app/actions/manifest.py
- **C-045** kennel: PBS restore points, restored beside the original: kennel repository
- **C-046** Zabbix alert intake: backend/app/api/webhooks.py, backend/app/ai/alerts.py, docs/ui/alerts.md
- **C-047** Audit rows for firewall rule changes (BUG-147): backend/app/api/rules.py (no log_action), backend/app/api/_scoped_rules.py (RuleAudit)
- **C-048** Rename: configuration and environment, with the old names as fallbacks: backend/app/config.py (LABDOG_ prefix, /etc/labdog), dev/labdog.toml, dev/dev.sh, backend/pyproject.toml (labdog-backend)
- **C-049** Rename: paths, packages and service names, migrated on upgrade: Dockerfile (/var/lib/labdog), packaging/, docs/production-deploy.md
- **C-050** Rename: on-host markers, alert labels, metrics and pack variables: backend/app/rules/renderers (LABDOG-INPUT chains), backend/app/api/host_state.py, backend/app/alerts/labels.py (labdog_* labels), backend/app/metrics, open-unbork/unbork-playbooks
- **C-051** Rename: README, docs site, CLAUDE.md and changelog wording: README.md, website/docusaurus.config.ts, CLAUDE.md, docs/
- **C-052** Test the upgrade from 1.1.0 in packaging-smoke: packaging/tests/run-smoke.sh, .github/workflows/ci.yml (packaging-smoke)
- **C-054** kennel: leases and the sweeper: kennel repository
- **C-055** kennel: automatic backup restore tests: kennel repository
- **C-057** kennel: isolated sandbox network with a jump host: kennel repository; Proxmox SDN or VLAN on the test cluster
- **C-058** kennel: per-VM readiness API: kennel repository
- **C-059** unbork talks to kennel: snapshot provider and surge nodes for upgrades: backend/app/proxmox/, the S-003 provider interface, open-unbork/unbork-playbooks k8s-upgrade
- **C-060** Bind stored secrets to their row (SEC-50): backend/app/crypto/encryption.py:21,35, every encrypt and decrypt call site, a re-encrypting migration
- **C-061** A unit-test harness for the frontend: frontend/package.json (no unit-test script), .github/workflows/ci.yml (frontend-build-check)
- **C-062** Measure backend coverage in CI: .github/workflows/ci.yml (backend-test), backend/pyproject.toml
- **C-063** Record a load-test baseline: dev/loadtest/ (run on local Docker on lin-manager-2), planning/reviews/
- **C-064** Make planning/ a named exception to the plans/ rule: CONTRIBUTING.md, CLAUDE.md, .github/PULL_REQUEST_TEMPLATE/release.md, .dockerignore
- **S-001** Extension hook: subscribe to audit entries: backend/app/audit/logger.py (log_action), backend/app/auth/permissions.py (PLUGIN_API 1.3)
- **S-002** Extension hook: gate a run before it starts, and hear when it ends: backend/app/tasks/host_lock.py, host_sync_orchestrator.py, action_orchestrator.py, scheduled_action_schedule.py, backend/app/ai/gate.py
- **S-003** Snapshot provider interface, with Proxmox as the first provider: backend/app/proxmox/client.py, vm_mapping.py, backend/app/tasks/action_host.py, action_group.py, backend/app/ai/snapshots.py
- **S-004** Secret provider seam for stored credentials: backend/app/crypto/encryption.py, key_management.py (52 files call decrypt)
- **S-005** Alert source seam, with Grafana as the first source: backend/app/api/webhooks.py (/grafana-alerts), backend/app/ai/alerts.py, backend/app/alerts/
- **S-006** Frontend extension seam: navigation entries, pages, host and group panels: frontend/lib/extensions.ts (settings sections and user panels only today)
- **S-007** Say in the README which parts are AGPL and which are commercial: README.md (License section)
- **S-008** Rename the Claude Code provider labels: frontend/app/(dashboard)/ai-providers/client-page.tsx, docs/ui/assistant.md
- **S-009** Stop bundling the Claude Code binary in the Docker image: Dockerfile:85-112, backend/pyproject.toml ([agent] extra), backend/app/ai/agent_sdk/
- **S-010** Third-party notices and a source offer in the Docker image: Dockerfile, packaging/
- **S-011** Contributor licence agreement and a CLA check on pull requests: CONTRIBUTING.md (says no CLA), .github/workflows/
- **S-012** Copyright holder line in LICENSE: LICENSE (no holder), README.md (names Dennis Tyresson and contributors)
- **S-013** Store each action run's structured per-host results: backend/app/models (ActionHostRun), backend/app/tasks/action_host.py, action_group.py, backend/app/ansible_runtime/outcomes.py
- **S-014** Remove the subscription token field from AI providers: frontend/app/(dashboard)/ai-providers/client-page.tsx, backend/app/ai/providers/, docs/ui/assistant.md

### Enterprise features

Enterprise items are listed for transparency, with no dates or
commitments (decision D-18). Their scores, specs and decisions, and a few
internal items, are in the Enterprise repository.

| ID | Feature | Stage |
|---|---|---|
| E-002 | Send the audit log to a SIEM (syslog/CEF and HTTPS webhook) | 5 |
| E-003 | Maintenance and change-freeze windows: model, API and enforcement | 5 |
| E-004 | Maintenance windows: calendar UI on Settings, host and group pages | 5 |
| E-005 | SAML sign-in | 7 |
| E-006 | Secrets from HashiCorp Vault | 7 |
| E-007 | Zabbix write-back: acknowledge problems with the run result, sync maintenance periods | 7 |
| E-008 | Change tickets through ServiceNow | 9 |
| E-009 | Change tickets: the remaining systems | parked |
| E-010 | Snapshot and rollback on VMware | parked |
| E-011 | Bare-metal rollback: detect LVM, ZFS and Btrfs layouts in state collection | 9 |
| E-012 | Bare-metal rollback: LVM snapshot provider with driven reboot | 9 |
| E-013 | Bare-metal rollback: ZFS provider | parked |
| E-014 | Multi-tenancy: close the soft-tenancy gaps for teams | 9 |
| E-015 | Compliance evidence export (NIS2, DORA, ISO 27001) | parked |
| E-016 | Hardening baselines | 10 |
| E-017 | Offline install for air-gapped networks | 10 |
| E-018 | Disaster recovery: verified backup and restore of unbork's own state | 10 |
| E-019 | High availability reference deployment | 10 |
| E-023 | LDAP sync: read servers and groups from the directory, with a preview | 5 |
| E-024 | Refresh LDAP group memberships between sign-ins and bind them to synced groups | 5 |
| E-025 | Multi-tenancy: provision and upgrade many installs | parked |
| E-030 | LDAP sync: apply on a schedule, flag servers that disappear | 5 |
| E-031 | LDAP sync: settings and review UI | 5 |

### Dependencies

No cycles, no item depends on a later stage, and no `C-` or `S-` item
depends on an `E-` item (checked by script when `roadmap.yaml` was
generated). The chains that matter:

- C-008 then S-001, then the Enterprise audit consumers.
- S-002, then maintenance windows and change tickets.
- C-048 then C-049 and C-050; the rename and S-009 before the release.
- C-001 and C-052 then C-002 then C-003.
- C-004 then C-005, C-006, C-007, C-014; S-003 then C-005, C-044 and,
  much later, kennel (C-059) and the Enterprise non-Proxmox providers.
- S-013 then the Enterprise hardening results view.
- C-028 then every other kennel item.

## Stages

Each stage file lists its items in execution order with entry and exit
criteria, and ends with a "Review and hardening" step. Enterprise items
run from the Enterprise repository's own stage file with the same number.

| Stage | Goal | Items, in order |
|---|---|---|
| [0](stages/stage-0.md) | Foundations: safety nets and contributor terms | C-064, S-012, S-011, C-024, C-025, C-062, C-061, C-063, E-001, E-028, E-022, E-021 |
| [1](stages/stage-1.md) | A complete audit trail and the first extension hooks | C-008, C-047, C-022, S-001, S-002 |
| [2](stages/stage-2.md) | The rename, and a clean image | C-048, C-049, C-050, C-051, S-008, S-014, S-009, C-026, S-010 |
| [3](stages/stage-3.md) | Release unbork 2.0.0 | S-007, C-052, C-001, E-020, C-002, C-003, E-026, E-027 |
| [4](stages/stage-4.md) | Rollback you can trust | C-004, S-003, C-005, C-044, C-006, C-007, C-016, C-012, C-023 |
| [5](stages/stage-5.md) | Governance and the directory | S-006, E-002, E-003, E-004, E-023, E-030, E-031, E-024, E-029 |
| [6](stages/stage-6.md) | Day-two operations | C-017, C-018, C-013, S-013, C-014, C-015, C-021 |
| [7](stages/stage-7.md) | Integrations | C-030, S-004, C-060, S-005, C-046, E-005, E-006, E-007 |
| [8](stages/stage-8.md) | A safer assistant | C-010, C-020, C-037, C-011, C-019, C-009 |
| [9](stages/stage-9.md) | Rollback beyond Proxmox, tickets and teams | E-011, E-012, E-008, E-014 |
| [10](stages/stage-10.md) | Scale and assurance | C-029, E-018, E-019, E-017, E-016 |
| [11](stages/stage-11.md) | kennel, a VM operations service | C-028, C-058, C-054, C-057, C-045, C-055, C-059 |
| parked | P3, not scheduled | C-027, C-031, C-032, C-033, C-034, C-035, C-036, C-038, C-039, C-040, C-041, C-042, C-043, E-009, E-010, E-013, E-015, E-025 |

Critical path: stage 1 (audit and the two hooks), stage 2 (rename), stage
3 (release 2.0.0). Everything after stage 3 ships through the release
path that stage 3 proves. Stages 4, 6 and 8 need nothing from the
Enterprise repository and could run in any order after stage 3; the
numbering is the recommended order.

Main risks:

- Stage 2 changes names that live on managed hosts (firewall chains, file
  markers). A mistake there is a gap in firewall coverage. The specs make
  hosts named "test" the first target and keep every old name as a
  fallback.
- Stage 3 cannot finish without Dennis: he merges the release PR.
- S-003 refactors the snapshot path that every destructive action runs
  through, in the same stage that changes rollback behaviour. Its spec
  requires the existing snapshot tests to pass unchanged before anything
  else in stage 4 builds on it.
- Stage 11 (kennel) needs a repository that does not exist yet, and its
  design is the least settled part of the plan.

## How to run it

In a fresh Claude Code session in this repository: `/run-stage 0`. The
skill reads `roadmap.yaml`, works through the stage's items, runs
`/review-stage`, writes `planning/reviews/stage-0.md` and opens a pull
request against `dev`. Enterprise items of the same stage are run the
same way from the Enterprise repository, after the Community half is
merged.
