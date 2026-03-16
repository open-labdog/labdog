# Barricade GitOps — Git-as-Truth Firewall Management

## TL;DR

> **Quick Summary**: Add GitOps Mode B to Barricade — Git becomes the single source of truth for firewall rules. Engineers push YAML files to a Git repo, Barricade receives webhooks (GitHub/GitLab/Gitea), imports rules to the DB, and auto-syncs to all hosts via Ansible. The UI rule editor is disabled for GitOps-managed groups. Includes a standalone `barricade-lint` pip package for CI/CD validation and fixes the core plan's `fetch_current_state` stub.
>
> **Deliverables**:
> - Real `fetch_current_state` implementation for nftables/firewalld/ufw (fixes core T18/T19 stub)
> - Shared FirewallRule↔RuleSpec conversion helper (DRYs up 4x duplication)
> - GitRepository model + HostGroup gitops fields + Alembic migration
> - Git operations service (clone/pull with SSH + HTTPS token auth)
> - Multi-module YAML serializer/deserializer (extensible format, firewall module only)
> - Webhook endpoints for GitHub, GitLab, and Gitea with HMAC/token verification
> - Import/reconcile engine (YAML → validate → diff → update DB)
> - Auto-sync pipeline (webhook → import → audit log → sync to all hosts)
> - GitRepository CRUD API + GitOps enable/disable per group
> - Rule mutation lockdown for GitOps-enabled groups (API 403 + UI read-only indicators)
> - `barricade-lint` standalone pip package with CLI for CI/CD pipelines
> - pytest suite for all GitOps features
> - End-to-end integration test for full GitOps pipeline
>
> **Estimated Effort**: L (Large)
> **Parallel Execution**: YES — 5 waves + final
> **Critical Path**: T1 → T8 → T11 → T12 → T13 → F1-F4

---

## Context

### Original Request
Extend Barricade with GitOps mode where Git is the authoritative source of truth for firewall rules. Changes are made via Git pushes (YAML files), Barricade auto-imports and syncs to hosts.

### Interview Summary
**Key Discussions**:
- **Mode**: Mode B only — Git is truth, Barricade only reads from Git, never writes
- **UI behavior**: Mutually exclusive per group. GitOps groups have read-only UI (rule editor disabled)
- **Auto-sync**: Full pipeline — Git push → webhook → import to DB → auto-sync to all hosts. No human gate
- **YAML format**: Multi-module from the start. Top-level module keys (`firewall`, `services`, etc.). Only `firewall` processed now. Unknown modules ignored. Syntax close to Ansible
- **Initial state**: Manual YAML creation by user. No export from DB to Git
- **Git providers**: GitHub, GitLab, Gitea — all three webhook formats
- **Git auth**: SSH keys + HTTPS tokens (PATs), both encrypted with AES-256-GCM
- **Branch**: Single configurable branch per repo (default: main)
- **File mapping**: Explicit `gitops_file_path` on HostGroup model
- **Error handling**: Invalid YAML → reject, set `gitops_status` to `error` on group
- **Diff logging**: Compute and log diff before every auto-sync (audit trail)
- **Polling**: No polling fallback — webhook only for v1
- **Linter**: Standalone pip package (`barricade-lint`) for CI/CD
- **Core fix**: T18/T19 `fetch_current_state` stub → real implementation (prerequisite)
- **Testing**: Tests after implementation (pytest)

**Research Findings**:
- Webhook signature formats differ: GitHub (HMAC-SHA256 in `X-Hub-Signature-256`), GitLab (plain token in `X-Gitlab-Token`), Gitea (HMAC-SHA256 in `X-Gitea-Signature`)
- GitLab caps webhook commit list at 20 — must not rely on `commits[].modified` for changed file detection
- Existing codebase has FirewallRule→FirewallRuleSpec conversion duplicated in 4 files
- Existing `fetch_current_state_stub()` returns `[]` — all diffs and drift are currently fake

### Metis Review
**Identified Gaps** (all addressed):
- **File-to-group mapping**: Resolved → explicit `gitops_file_path` on HostGroup
- **Concurrent webhook processing**: → per-group advisory lock during import
- **HTTPS token leakage**: → temp-dir-per-import pattern, credentials never persisted
- **Auto-sync bricking hosts**: → validation before import + SSH lockout rule always auto-injected
- **GitLab commit cap**: → read all group-mapped files at commit SHA, not relying on changed files list
- **gitpython file locking**: → temp-dir-per-import (clone fresh each time)
- **Webhook replay**: → idempotent (check `last_commit_sha` before processing)
- **YAML file deleted from repo**: → set `gitops_status` to `error`, don't remove rules
- **Branch deletion webhook**: → ignore silently, return 200
- **Force push**: → treat as normal push, process `after` SHA
- **Empty rules list**: → import as empty, warn that only system SSH rule remains

---

## Work Objectives

### Core Objective
Add GitOps Mode B to Barricade so that Git repositories serve as the authoritative source of truth for firewall rules, with automatic import and sync triggered by webhooks from GitHub, GitLab, or Gitea.

### Concrete Deliverables
- `backend/app/gitops/` — GitOps service module (git operations, YAML serializer, importer, pipeline)
- `backend/app/api/webhooks.py` — Webhook endpoints for 3 providers
- `backend/app/api/git_repos.py` — GitRepository CRUD API
- `backend/app/models/git_repository.py` — GitRepository model
- `backend/app/sync/parsers/` — Real firewall state parsers (nftables, firewalld, ufw)
- `barricade-lint/` — Standalone pip package at project root
- Alembic migration for GitOps-related schema changes
- pytest test suite for all GitOps features

### Definition of Done
- [ ] YAML file pushed to Git → webhook received → rules imported to DB → hosts synced — fully automated
- [ ] Invalid YAML → import rejected → group shows `error` status → hosts unchanged
- [ ] GitOps-enabled group → rule CRUD endpoints return 403 → UI shows read-only
- [ ] `pip install barricade-lint && barricade-lint rules.yaml` exits 0 on valid, 1 on invalid
- [ ] Real `fetch_current_state` works for nftables/firewalld/ufw (diffs are meaningful)
- [ ] All pytest tests pass
- [ ] All three webhook providers work (GitHub, GitLab, Gitea)

### Must Have
- GitRepository model with SSH key + HTTPS token auth (both encrypted)
- Webhook receivers for GitHub, GitLab, Gitea with signature/token verification
- Multi-module YAML format (only `firewall` module parsed, unknown modules ignored)
- YAML validation: syntax, CIDR, ports, protocols, ICMP+port conflict
- Import engine: YAML → validate → diff against DB → update DB rules → audit log
- Auto-sync: import triggers sync for all hosts in group automatically
- Diff computed and logged to audit trail before every sync
- Per-group advisory lock preventing concurrent imports
- Rule mutation lockdown: 403 on POST/PUT/DELETE to rules for GitOps groups
- UI indicators: "Managed by GitOps" badge, read-only state, gitops_status display
- Idempotent webhook processing (skip if same commit SHA)
- SSH lockout rule always auto-injected regardless of YAML content
- barricade-lint CLI: validates YAML, reports errors with line numbers, exits non-zero on failure
- Real `fetch_current_state` for nftables, firewalld, ufw (replaces stub)

### Must NOT Have (Guardrails)
- ❌ Mode A (Git as mirror) — Barricade never writes to Git
- ❌ Git rollback via Barricade UI — users do `git revert` in their Git hosting
- ❌ Git history viewer in Barricade — use Git hosting's commit history
- ❌ Git conflict resolution UI — Git always wins, no conflicts in Mode B
- ❌ YAML export / DB-to-Git migration tool — manual YAML creation
- ❌ Auto-creation of HostGroups from YAML — groups must exist in DB first
- ❌ Polling fallback — webhook only for v1
- ❌ Processing of non-firewall modules (services, users, packages)
- ❌ Webhook management UI — user configures webhooks in Git hosting
- ❌ Token/key rotation automation
- ❌ Abstract factory / strategy pattern for webhook providers — simple if/elif dispatch
- ❌ barricade-lint depending on barricade backend code — fully standalone
- ❌ `ufw reset` — same as core plan, file write + reload only
- ❌ Persistent repo clones with credentials in `.git/config` — temp dir per import

---

## Verification Strategy

> **ZERO HUMAN INTERVENTION** — ALL verification is agent-executed. No exceptions.

### Test Decision
- **Infrastructure exists**: YES (pytest + pytest-asyncio already configured from core plan)
- **Automated tests**: YES (tests after implementation)
- **Framework**: pytest + pytest-asyncio + httpx (backend), same as core plan
- **DB for tests**: PostgreSQL via testcontainers (same as core)

### QA Policy
Every task MUST include agent-executed QA scenarios.
Evidence saved to `.sisyphus/evidence/task-{N}-{scenario-slug}.{ext}`.

- **API endpoints**: Use Bash (curl) — send requests, assert status + response fields
- **Backend logic**: Use Bash (pytest) — run test suites, assert pass counts
- **YAML serialization**: Use Bash — roundtrip tests, validation tests
- **Webhook handling**: Use Bash (curl) — simulate webhook payloads with signatures
- **barricade-lint CLI**: Use Bash — run against valid/invalid YAML files
- **Frontend UI changes**: Use Playwright — verify read-only state, badges

---

## Execution Strategy

### Parallel Execution Waves

```
Wave 1 (Prerequisites — fix stubs + foundation):
├── Task 1: Fix T18/T19: Implement real fetch_current_state [deep]
├── Task 2: Extract shared FirewallRule↔RuleSpec converter [quick]
└── Task 3: GitRepository model + HostGroup gitops fields + migration [unspecified-high]

Wave 2 (Core Services — 4 parallel after Wave 1):
├── Task 4: Git operations service (clone/pull, SSH+HTTPS) [unspecified-high]
├── Task 5: Multi-module YAML serializer/deserializer [deep]
├── Task 6: GitRepository CRUD API + GitOps enable/disable [unspecified-high]
└── Task 7: Webhook endpoints for GitHub/GitLab/Gitea [unspecified-high]

Wave 3 (Import + Tooling — 3 parallel):
├── Task 8: Import/reconcile engine [deep]
├── Task 9: Rule mutation lockdown + GitOps UI indicators [unspecified-high]
└── Task 10: barricade-lint standalone pip package [unspecified-high]

Wave 4 (Integration — auto-sync pipeline):
└── Task 11: Auto-sync pipeline wiring [deep]

Wave 5 (Tests — 2 sequential):
├── Task 12: GitOps pytest suite [unspecified-high]
└── Task 13: End-to-end integration test [deep]

Wave FINAL (Independent review — 4 parallel):
├── Task F1: Plan compliance audit [oracle]
├── Task F2: Code quality review [unspecified-high]
├── Task F3: Real manual QA [unspecified-high]
└── Task F4: Scope fidelity check [deep]

Critical Path: T1 → T8 → T11 → T12 → T13 → F1-F4
Parallel Speedup: ~55% faster than sequential
Max Concurrent: 4 (Wave 2)
```

### Dependency Matrix

| Task | Depends On | Blocks | Wave |
|------|-----------|--------|------|
| T1 | — | T11 | 1 |
| T2 | — | T5 | 1 |
| T3 | — | T4, T5, T6, T7 | 1 |
| T4 | T3 | T8 | 2 |
| T5 | T2 | T8, T10 | 2 |
| T6 | T3 | T9 | 2 |
| T7 | T3 | T11 | 2 |
| T8 | T4, T5 | T11 | 3 |
| T9 | T6 | T12 | 3 |
| T10 | T5 | T12 | 3 |
| T11 | T1, T7, T8 | T12 | 4 |
| T12 | T9, T10, T11 | T13 | 5 |
| T13 | T12 | F1-F4 | 5 |

### Agent Dispatch Summary

| Wave | Tasks | Categories |
|------|-------|------------|
| 1 | 3 | T1→`deep`, T2→`quick`, T3→`unspecified-high` |
| 2 | 4 | T4→`unspecified-high`, T5→`deep`, T6→`unspecified-high`, T7→`unspecified-high` |
| 3 | 3 | T8→`deep`, T9→`unspecified-high`, T10→`unspecified-high` |
| 4 | 1 | T11→`deep` |
| 5 | 2 | T12→`unspecified-high`, T13→`deep` |
| FINAL | 4 | F1→`oracle`, F2→`unspecified-high`, F3→`unspecified-high`, F4→`deep` |

---

## TODOs

- [x] 1. Fix T18/T19: Implement Real `fetch_current_state` for nftables/firewalld/ufw

  **What to do**:
  - Replace `fetch_current_state_stub()` in `app/sync/diff.py` with real implementations
  - Create `app/sync/parsers/` directory with per-backend parsers:
    - `app/sync/parsers/nftables.py`:
      - Run `nft -j list ruleset` via ansible-runner ad-hoc command on host
      - Parse JSON output → extract rules from `inet filter` table, `input` and `output` chains
      - Map nftables fields to `FirewallRuleSpec`: match expressions → source/dest CIDR, dport → port_start/port_end, accept/drop/reject → action
      - Handle `ct state established,related accept` (skip, this is infrastructure) and `iif lo accept` (skip)
      - Handle `comment` metadata if present
    - `app/sync/parsers/firewalld.py`:
      - Run `firewall-cmd --list-all --zone=public` via ansible-runner ad-hoc
      - Parse output: ports, rich rules, services
      - Map rich rules to `FirewallRuleSpec`: `rule family="ipv4" source address="X" port port="Y" protocol="Z" accept` → allow tcp/Y from X
      - Map simple port entries (e.g., `80/tcp`) to rules
    - `app/sync/parsers/ufw.py`:
      - Slurp `/etc/ufw/user.rules` and `/etc/ufw/user6.rules` via ansible-runner ad-hoc
      - Parse iptables-format rules → extract `-A ufw-user-input -p tcp --dport 80 -j ACCEPT` patterns
      - Map to `FirewallRuleSpec`
  - Update `fetch_current_state` function to dispatch to correct parser based on `host.firewall_backend`
  - Update all 4 call sites that reference `fetch_current_state_stub` to use the new function:
    - `app/api/sync.py` (plan_host, plan_group)
    - `app/api/drift.py`
    - `app/drift/detector.py`
  - Use `lsp_find_references` on `fetch_current_state_stub` to verify all call sites updated

  **Must NOT do**:
  - Do NOT parse iptables format for nftables — use `nft -j` JSON output
  - Do NOT use `ufw status` (human-readable, unreliable) — parse files directly
  - Do NOT cache parsed state — always fetch fresh

  **Recommended Agent Profile**:
  - **Category**: `deep`
    - Reason: Parsing 3 different firewall output formats with edge cases. Security-critical — incorrect parsing could cause sync to brick hosts.
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 1 (with Tasks 2, 3)
  - **Blocks**: Task 11
  - **Blocked By**: None

  **References**:

  **Pattern References**:
  - `backend/app/sync/diff.py:52-62` — Current stub implementation to replace. Shows the `FirewallRuleSpec` return type expected.
  - `backend/app/sync/diff.py:65-105` — `compute_diff()` function that consumes the output. Understand the diff contract.
  - `backend/app/rules/renderers/nftables.py` — Shows nftables syntax that the parser must reverse-engineer. The renderer produces what the parser must read.
  - `backend/app/rules/renderers/firewalld.py` — Shows firewalld task dicts with rich_rule syntax. Parser must parse the reverse direction.
  - `backend/app/rules/renderers/ufw.py` — Shows the `/etc/ufw/user.rules` format that the parser must read.

  **API/Type References**:
  - `backend/app/rules/model.py:FirewallRuleSpec` — The dataclass returned by the parser. All fields documented here.
  - `backend/app/models/host.py:Host.firewall_backend` — Enum field (`nftables`/`firewalld`/`ufw`/`unknown`) used for dispatch.

  **External References**:
  - nftables JSON: `nft -j list ruleset` output format — https://wiki.nftables.org/wiki-nftables/index.php/Scripting
  - firewalld rich rules: https://firewalld.org/documentation/man-pages/firewalld.richlanguage.html

  **Acceptance Criteria**:
  - [ ] `fetch_current_state(host)` returns a `list[FirewallRuleSpec]` for each backend
  - [ ] nftables parser extracts rules from `nft -j list ruleset` JSON output
  - [ ] firewalld parser extracts rules from `firewall-cmd --list-all` output
  - [ ] ufw parser extracts rules from `/etc/ufw/user.rules` content
  - [ ] `grep -r "fetch_current_state_stub" backend/` returns zero matches
  - [ ] `compute_diff()` produces meaningful diffs (not always "add everything")
  - [ ] Connection failure sets host status to `error` (not crash)

  **QA Scenarios**:
  ```
  Scenario: nftables parser extracts rules from JSON
    Tool: Bash
    Preconditions: Parser module importable
    Steps:
      1. Run: cd backend && python -c "
         from app.sync.parsers.nftables import parse_nftables_json
         import json
         # Real nft -j list ruleset output (simplified)
         nft_json = {
           'nftables': [
             {'table': {'family': 'inet', 'name': 'filter'}},
             {'chain': {'family': 'inet', 'table': 'filter', 'name': 'input', 'type': 'filter', 'hook': 'input', 'prio': 0, 'policy': 'drop'}},
             {'rule': {'family': 'inet', 'table': 'filter', 'chain': 'input', 'expr': [
               {'match': {'op': '==', 'left': {'payload': {'protocol': 'tcp', 'field': 'dport'}}, 'right': 80}},
               {'accept': None}
             ]}}
           ]
         }
         rules = parse_nftables_json(json.dumps(nft_json))
         assert len(rules) >= 1, f'Expected at least 1 rule, got {len(rules)}'
         r = rules[0]
         assert r.protocol == 'tcp', f'Expected tcp, got {r.protocol}'
         assert r.port_start == 80, f'Expected port 80, got {r.port_start}'
         assert r.action == 'allow', f'Expected allow, got {r.action}'
         print('PASS: nftables parser works')
         "
      2. Assert output contains "PASS"
    Expected Result: nftables JSON parsed into FirewallRuleSpec
    Evidence: .sisyphus/evidence/task-1-nftables-parser.txt

  Scenario: Stub fully removed from codebase
    Tool: Bash
    Steps:
      1. Run: grep -r "fetch_current_state_stub" backend/
      2. Assert: exit code 1 (no matches)
    Expected Result: No references to stub remain
    Evidence: .sisyphus/evidence/task-1-stub-removed.txt
  ```

  **Commit**: YES
  - Message: `fix(sync): implement real fetch_current_state for nftables/firewalld/ufw`
  - Files: `backend/app/sync/diff.py`, `backend/app/sync/parsers/`
  - Pre-commit: `cd backend && python -c "from app.sync.parsers import nftables, firewalld, ufw"`

- [x] 2. Extract Shared FirewallRule↔RuleSpec Converter

  **What to do**:
  - Create `app/rules/converter.py` with:
    - `firewall_rule_to_spec(rule: FirewallRule) -> FirewallRuleSpec` — converts SQLAlchemy model to dataclass
    - `spec_to_firewall_rule(spec: FirewallRuleSpec, group_id: int) -> FirewallRule` — converts dataclass to model for DB insertion
    - `firewall_rules_to_specs(rules: list[FirewallRule]) -> list[FirewallRuleSpec]` — batch version
  - Use `ast_grep_search` to find all 4 places where this conversion is duplicated:
    - `app/api/rules.py` (around line 180-194)
    - `app/api/sync.py` (around line 72-86)
    - `app/tasks/sync.py` (around line 82-100)
    - `app/api/drift.py`
  - Replace all 4 inline conversions with calls to the shared converter
  - This DRY-up prevents adding a 5th copy in the YAML importer

  **Must NOT do**:
  - Do NOT change the conversion logic — only extract and deduplicate
  - Do NOT change the FirewallRuleSpec or FirewallRule models

  **Recommended Agent Profile**:
  - **Category**: `quick`
    - Reason: Mechanical refactoring — extract duplicated code, replace call sites
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 1 (with Tasks 1, 3)
  - **Blocks**: Task 5
  - **Blocked By**: None

  **References**:

  **Pattern References**:
  - `backend/app/api/rules.py:180-194` — First instance of inline conversion. Use as source for the extracted function.
  - `backend/app/api/sync.py:72-86` — Second instance. Compare to verify same logic.
  - `backend/app/tasks/sync.py:82-100` — Third instance. May have slight variations.
  - `backend/app/api/drift.py` — Fourth instance.

  **API/Type References**:
  - `backend/app/rules/model.py:FirewallRuleSpec` — Target dataclass
  - `backend/app/models/firewall_rule.py:FirewallRule` — Source SQLAlchemy model

  **Acceptance Criteria**:
  - [ ] `app/rules/converter.py` exists with `firewall_rule_to_spec` and `spec_to_firewall_rule` functions
  - [ ] All 4 original inline conversions replaced with calls to converter
  - [ ] `grep -rn "FirewallRuleSpec(" backend/app/api/ backend/app/tasks/` shows only imports (no inline construction)
  - [ ] All existing tests still pass: `cd backend && pytest -v`

  **QA Scenarios**:
  ```
  Scenario: Converter roundtrip preserves all fields
    Tool: Bash
    Preconditions: Converter module importable
    Steps:
      1. Run: cd backend && python -c "
         from app.rules.converter import firewall_rule_to_spec, spec_to_firewall_rule
         from app.rules.model import FirewallRuleSpec
         spec = FirewallRuleSpec(
           action='allow', protocol='tcp', direction='input',
           source_cidr='10.0.0.0/8', destination_cidr=None,
           port_start=443, port_end=None, comment='HTTPS', is_system=False
         )
         rule = spec_to_firewall_rule(spec, group_id=1)
         roundtrip = firewall_rule_to_spec(rule)
         assert roundtrip.action == spec.action
         assert roundtrip.port_start == spec.port_start
         assert roundtrip.source_cidr == spec.source_cidr
         print('PASS: roundtrip preserves fields')
         "
      2. Assert output contains "PASS"
    Expected Result: Conversion is lossless
    Evidence: .sisyphus/evidence/task-2-converter-roundtrip.txt

  Scenario: Existing tests still pass after refactor
    Tool: Bash
    Steps:
      1. Run: cd backend && pytest -v --tb=short
      2. Assert: exit code 0, 0 failures
    Expected Result: Refactoring is behavior-preserving
    Evidence: .sisyphus/evidence/task-2-existing-tests.txt
  ```

  **Commit**: YES
  - Message: `refactor(rules): extract shared FirewallRule↔RuleSpec converter`
  - Files: `backend/app/rules/converter.py`, `backend/app/api/rules.py`, `backend/app/api/sync.py`, `backend/app/tasks/sync.py`, `backend/app/api/drift.py`
  - Pre-commit: `cd backend && pytest -v --tb=short`

- [x] 3. GitRepository Model + HostGroup GitOps Fields + Alembic Migration

  **What to do**:
  - Create `app/models/git_repository.py`:
    ```python
    class GitRepository(Base):
        id: int (PK)
        name: str (unique)
        url: str  # SSH (git@...) or HTTPS (https://...)
        branch: str = "main"
        auth_type: enum (ssh_key | https_token)
        ssh_key_id: int (FK to SSHKey, nullable)
        encrypted_https_token: bytes (nullable)  # AES-256-GCM encrypted, same as SSH keys
        webhook_secret: str (nullable)  # For validating incoming webhooks
        last_commit_sha: str (nullable)  # Last processed commit
        last_sync_at: datetime (nullable)
        created_at: datetime
        updated_at: datetime
    ```
  - Add fields to `app/models/host_group.py` (HostGroup):
    ```python
    git_repository_id: int (FK to GitRepository, nullable)
    gitops_enabled: bool = False
    gitops_file_path: str (nullable)  # e.g. "groups/web-servers.yaml"
    gitops_status: enum (disconnected | synced | error | importing) = disconnected
    gitops_error_message: str (nullable)
    gitops_last_import_at: datetime (nullable)
    ```
  - Create `GitOpsStatus` enum: `disconnected`, `synced`, `error`, `importing`
  - Create `GitAuthType` enum: `ssh_key`, `https_token`
  - Generate Alembic migration `0002_gitops_schema.py`:
    - Add `git_repositories` table
    - Add new columns to `host_groups` table
    - Add FK constraint from `host_groups.git_repository_id` to `git_repositories.id`
  - Follow existing model patterns: `Base` class from `app/models/base.py`, `DateTime(timezone=True)`, `mapped_column`, `lambda: datetime.now(timezone.utc)` defaults

  **Must NOT do**:
  - Do NOT add `git_repository_id` to individual hosts — GitOps is per group
  - Do NOT store HTTPS tokens in plaintext — use same AES-256-GCM encryption as SSH keys
  - Do NOT add auto-creation logic for groups from YAML

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
    - Reason: DB model design with enums, FKs, encrypted fields, migration
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 1 (with Tasks 1, 2)
  - **Blocks**: Tasks 4, 5, 6, 7
  - **Blocked By**: None

  **References**:

  **Pattern References**:
  - `backend/app/models/base.py` — Base class and column conventions to follow exactly
  - `backend/app/models/ssh_key.py` — Pattern for encrypted credential storage (`encrypted_private_key: bytes`)
  - `backend/app/models/host.py` — Pattern for enum fields (`firewall_backend`), FK relationships
  - `backend/app/models/host_group.py` — The model being extended. Read current fields to understand schema.
  - `backend/alembic/versions/0001_initial_schema.py` — Migration pattern (naming conventions, enum creation)

  **API/Type References**:
  - `backend/app/crypto/encryption.py` — `encrypt_ssh_key`/`decrypt_ssh_key` functions to reuse for HTTPS token encryption

  **Acceptance Criteria**:
  - [ ] `alembic upgrade head` runs without errors (applies 0002 migration)
  - [ ] `alembic downgrade -1` rolls back cleanly
  - [ ] `from app.models.git_repository import GitRepository` imports successfully
  - [ ] HostGroup now has `gitops_enabled`, `gitops_file_path`, `gitops_status` fields
  - [ ] GitRepository model has `encrypted_https_token` field (bytes, nullable)
  - [ ] `GitOpsStatus` and `GitAuthType` enums defined correctly

  **QA Scenarios**:
  ```
  Scenario: Migration applies and model works
    Tool: Bash
    Preconditions: PostgreSQL running, DATABASE_URL set
    Steps:
      1. Run: cd backend && alembic upgrade head
      2. Assert: exit code 0
      3. Run: python -c "
         import asyncio
         from app.models.git_repository import GitRepository, GitAuthType
         from app.models.host_group import HostGroup, GitOpsStatus
         from app.db import async_session
         async def test():
           async with async_session() as session:
             repo = GitRepository(
               name='test-repo', url='git@github.com:user/rules.git',
               branch='main', auth_type=GitAuthType.ssh_key
             )
             session.add(repo)
             await session.commit()
             await session.refresh(repo)
             print(f'Created repo id={repo.id}')
             await session.delete(repo)
             await session.commit()
             print('Cleanup done')
         asyncio.run(test())
         "
      4. Assert: output contains "Created repo id=" and "Cleanup done"
    Expected Result: Model CRUD works via async session
    Evidence: .sisyphus/evidence/task-3-model-crud.txt

  Scenario: Migration rolls back cleanly
    Tool: Bash
    Steps:
      1. Run: cd backend && alembic downgrade -1
      2. Assert: exit code 0
      3. Run: alembic upgrade head
      4. Assert: exit code 0
    Expected Result: Migration is reversible
    Evidence: .sisyphus/evidence/task-3-rollback.txt
  ```

  **Commit**: YES
  - Message: `feat(gitops): add GitRepository model + HostGroup gitops fields`
  - Files: `backend/app/models/git_repository.py`, `backend/app/models/host_group.py`, `backend/alembic/versions/0002_gitops_schema.py`
  - Pre-commit: `cd backend && alembic upgrade head`

- [x] 4. Git Operations Service (Clone/Pull with SSH + HTTPS Auth)

  **What to do**:
  - Create `app/gitops/git_service.py`:
    - `clone_repo(repo: GitRepository, target_dir: Path) -> git.Repo`:
      - For SSH auth: decrypt SSH key → write to `/dev/shm/barricade-git-{repo.id}.key` → `chmod 600` → set `GIT_SSH_COMMAND` env var → clone → cleanup key in `finally`
      - For HTTPS auth: decrypt token → construct URL `https://oauth2:{token}@host/repo.git` → clone → never persist URL (use temp remote)
      - Clone to `tempfile.mkdtemp(prefix="barricade-git-")` → return `git.Repo` object
    - `pull_repo(repo: GitRepository, repo_dir: Path) -> str`:
      - Pull latest from configured branch
      - Return current HEAD commit SHA
    - `read_file_at_sha(repo_path: Path, file_path: str, sha: str) -> str`:
      - `git show {sha}:{file_path}` → return file contents
      - Raise clear error if file not found at that SHA
    - `get_current_sha(repo_path: Path) -> str`:
      - Return HEAD commit SHA
    - Cleanup helper: `cleanup_repo(repo_dir: Path)` — `shutil.rmtree()`
  - Add `gitpython` to `pyproject.toml` dependencies
  - All credential operations use existing `app/crypto/encryption.py` (decrypt only inside the function, never pass credentials as arguments to external callers)

  **Must NOT do**:
  - Do NOT persist credentials in `.git/config` — use temp dirs that are deleted after use
  - Do NOT keep repo clones between requests — fresh clone/pull each time
  - Do NOT pass decrypted credentials through function arguments to callers

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
    - Reason: Git operations with credential management, security constraints
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 2 (with Tasks 5, 6, 7)
  - **Blocks**: Task 8
  - **Blocked By**: Task 3

  **References**:

  **Pattern References**:
  - `backend/app/tasks/sync.py:23-198` — Celery task with SSH key handling via `/dev/shm/`. Follow this exact pattern for git SSH auth.
  - `backend/app/crypto/encryption.py` — `decrypt_ssh_key()` function for decrypting stored keys/tokens.

  **External References**:
  - gitpython docs: https://gitpython.readthedocs.io/en/stable/
  - GIT_SSH_COMMAND: https://git-scm.com/docs/git#Documentation/git.txt-codeGITSSHCOMMANDcode

  **Acceptance Criteria**:
  - [ ] `clone_repo()` clones a real Git repo via SSH key
  - [ ] `clone_repo()` clones via HTTPS token
  - [ ] SSH key written to `/dev/shm/`, cleaned in `finally`
  - [ ] HTTPS token never persisted in `.git/config` after clone
  - [ ] `read_file_at_sha()` returns file content at specific commit
  - [ ] `read_file_at_sha()` raises error for non-existent file
  - [ ] Temp directory cleaned up by `cleanup_repo()`

  **QA Scenarios**:
  ```
  Scenario: Clone and read file from local bare repo
    Tool: Bash
    Preconditions: Create a local bare git repo with a test file
    Steps:
      1. Run: mkdir -p /tmp/test-bare && cd /tmp/test-bare && git init --bare
      2. Run: cd /tmp && git clone /tmp/test-bare test-clone && cd test-clone
      3. Run: echo "test content" > rules.yaml && git add . && git commit -m "init" && git push
      4. Run: cd backend && python -c "
         from app.gitops.git_service import clone_repo, read_file_at_sha, cleanup_repo, get_current_sha
         from pathlib import Path
         import tempfile
         target = Path(tempfile.mkdtemp())
         # Clone using local path (no auth needed for local)
         repo = clone_repo_local('/tmp/test-bare', target)
         sha = get_current_sha(target)
         content = read_file_at_sha(target, 'rules.yaml', sha)
         assert content.strip() == 'test content', f'Got: {content}'
         cleanup_repo(target)
         assert not target.exists(), 'Temp dir not cleaned'
         print('PASS: clone, read, cleanup works')
         "
      5. Assert output contains "PASS"
    Expected Result: Git operations work with local repo
    Evidence: .sisyphus/evidence/task-4-git-ops.txt

  Scenario: SSH key cleaned after operation
    Tool: Bash
    Steps:
      1. Check: ls /dev/shm/barricade-git-* 2>/dev/null | wc -l
      2. Assert: count is 0 (no leftover keys)
    Expected Result: No credential artifacts left behind
    Evidence: .sisyphus/evidence/task-4-ssh-cleanup.txt
  ```

  **Commit**: YES
  - Message: `feat(gitops): add git clone/pull service with SSH+HTTPS auth`
  - Files: `backend/app/gitops/git_service.py`, `backend/pyproject.toml`

- [x] 5. Multi-Module YAML Serializer/Deserializer

  **What to do**:
  - Create `app/gitops/schema.py` — Pydantic models for YAML validation:
    ```python
    class FirewallRuleYAML(BaseModel):
        action: Literal["allow", "deny", "reject"]
        protocol: Literal["tcp", "udp", "icmp", "any"]
        direction: Literal["input", "output"]
        source: Optional[str] = None      # CIDR (IPv4 or IPv6)
        dest: Optional[str] = None        # CIDR
        port: Optional[Union[int, str]] = None  # int for single, "start-end" string for range
        comment: Optional[str] = None
        # system field is read but IGNORED on import (system rules are auto-injected)

    class FirewallModuleYAML(BaseModel):
        rules: list[FirewallRuleYAML]

    class BarricadeGroupYAML(BaseModel):
        group: str                         # Human-readable, not used for mapping
        priority: Optional[int] = None     # Informational
        firewall: Optional[FirewallModuleYAML] = None
        # Future modules: services, users, packages, etc.
        model_config = ConfigDict(extra="allow")  # Ignore unknown top-level keys
    ```
  - Create `app/gitops/serializer.py`:
    - `parse_yaml(yaml_str: str) -> BarricadeGroupYAML`:
      - Parse YAML with `pyyaml` → validate with Pydantic schema
      - Raise `YAMLParseError` with line numbers on failure
      - Silently ignore unknown top-level keys (future modules)
      - Strip `system: true` rules with logged warning
    - `yaml_rules_to_specs(rules: list[FirewallRuleYAML]) -> list[FirewallRuleSpec]`:
      - Convert YAML format to internal `FirewallRuleSpec` format
      - Handle port field: `port: 443` → `port_start=443, port_end=None`; `port: "443-445"` → `port_start=443, port_end=445`
      - Validate CIDRs using existing `app/rules/validation.py:validate_cidr()`
      - Validate port ranges using existing `app/rules/validation.py`
    - `specs_to_yaml(specs: list[FirewallRuleSpec], group_name: str, priority: int) -> str`:
      - Convert internal specs to YAML string (for testing/debugging, NOT for writing to Git)
      - Include `# Managed by Barricade` header comment
  - Use existing validation from `app/rules/validation.py` — do NOT duplicate validation logic

  **Must NOT do**:
  - Do NOT process non-firewall modules (services, users, etc.) — only parse `firewall` key
  - Do NOT error on unknown top-level keys — ignore silently (extensibility)
  - Do NOT keep `system: true` rules from YAML — strip with warning

  **Recommended Agent Profile**:
  - **Category**: `deep`
    - Reason: YAML schema design, port format conversion, integration with existing validation
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 2 (with Tasks 4, 6, 7)
  - **Blocks**: Tasks 8, 10
  - **Blocked By**: Task 2

  **References**:

  **Pattern References**:
  - `backend/app/rules/model.py:FirewallRuleSpec` — Target dataclass for conversion output
  - `backend/app/rules/validation.py` — `validate_cidr()`, `validate_port_range()` — reuse these, don't duplicate
  - `backend/app/rules/converter.py` (from Task 2) — Use `spec_to_firewall_rule()` for DB insertion after YAML parsing

  **External References**:
  - PyYAML: https://pyyaml.org/wiki/PyYAMLDocumentation
  - Pydantic v2 model_config extra="allow": https://docs.pydantic.dev/latest/concepts/config/

  **Acceptance Criteria**:
  - [ ] Valid YAML with firewall rules parses to `list[FirewallRuleSpec]`
  - [ ] Port `443` (int) → `port_start=443, port_end=None`
  - [ ] Port `"443-445"` (string) → `port_start=443, port_end=445`
  - [ ] Invalid CIDR raises validation error with clear message
  - [ ] Unknown top-level keys (e.g., `services:`) are silently ignored
  - [ ] `system: true` rules stripped with logged warning
  - [ ] YAML roundtrip: specs → yaml → parse → specs matches original
  - [ ] Empty `firewall.rules: []` parses successfully (returns empty list)

  **QA Scenarios**:
  ```
  Scenario: Valid multi-module YAML parses correctly
    Tool: Bash
    Steps:
      1. Run: cd backend && python -c "
         from app.gitops.serializer import parse_yaml, yaml_rules_to_specs
         yaml_str = '''
         group: web-servers
         priority: 100
         firewall:
           rules:
             - action: allow
               protocol: tcp
               direction: input
               port: 443
               source: 10.0.0.0/8
               comment: HTTPS from internal
             - action: deny
               protocol: tcp
               direction: input
               port: \"3306-3310\"
               comment: Block MySQL range
         services:
           - name: nginx
             state: running
         '''
         parsed = parse_yaml(yaml_str)
         assert parsed.group == 'web-servers'
         assert parsed.firewall is not None
         assert len(parsed.firewall.rules) == 2
         specs = yaml_rules_to_specs(parsed.firewall.rules)
         assert specs[0].port_start == 443
         assert specs[0].port_end is None
         assert specs[1].port_start == 3306
         assert specs[1].port_end == 3310
         print(f'PASS: parsed {len(specs)} rules, unknown services key ignored')
         "
      2. Assert output contains "PASS"
    Expected Result: Multi-module YAML parsed, only firewall extracted, services ignored
    Evidence: .sisyphus/evidence/task-5-yaml-parse.txt

  Scenario: Invalid YAML rejected with line info
    Tool: Bash
    Steps:
      1. Run: cd backend && python -c "
         from app.gitops.serializer import parse_yaml
         try:
           parse_yaml('firewall:\\n  rules:\\n    - action: explode\\n      protocol: tcp')
           print('FAIL: should have raised error')
         except Exception as e:
           assert 'action' in str(e).lower() or 'explode' in str(e).lower()
           print(f'PASS: invalid action rejected: {e}')
         "
      2. Assert output contains "PASS"
    Expected Result: Invalid action value causes validation error
    Evidence: .sisyphus/evidence/task-5-yaml-invalid.txt
  ```

  **Commit**: YES
  - Message: `feat(gitops): add multi-module YAML serializer/deserializer`
  - Files: `backend/app/gitops/serializer.py`, `backend/app/gitops/schema.py`

- [x] 6. GitRepository CRUD API + GitOps Enable/Disable

  **What to do**:
  - Create `app/api/git_repos.py` with REST endpoints:
    - `GET /api/git-repos` — list all Git repositories (superuser only)
    - `POST /api/git-repos` — create repository. Fields: name, url, branch, auth_type, ssh_key_id (if SSH), https_token (if HTTPS), webhook_secret
    - `GET /api/git-repos/{id}` — get repo detail (NEVER return decrypted token)
    - `PUT /api/git-repos/{id}` — update repo
    - `DELETE /api/git-repos/{id}` — delete repo (reject if groups still linked)
    - `POST /api/git-repos/{id}/test-connection` — clone repo to temp dir, verify access, return success/failure
  - Create `app/api/groups.py` additions (modify existing file):
    - `POST /api/groups/{id}/gitops/enable` — body: `{"git_repository_id": int, "file_path": "groups/web-servers.yaml"}`. Sets `gitops_enabled=True`, `gitops_file_path`, `git_repository_id`, `gitops_status="disconnected"`
    - `POST /api/groups/{id}/gitops/disable` — sets `gitops_enabled=False`, clears gitops fields. **Does NOT remove rules from DB** — they stay as-is
    - `GET /api/groups/{id}/gitops/status` — returns gitops config + status + error message
  - Create `app/schemas/git_repos.py` — Pydantic schemas for request/response
  - HTTPS token encrypted before storage using `encrypt_ssh_key()` from `app/crypto/encryption.py`
  - HTTPS token NEVER returned in API responses (same pattern as SSH private keys)

  **Must NOT do**:
  - Do NOT return decrypted HTTPS tokens in any API response
  - Do NOT allow deletion of repos that have groups linked to them
  - Do NOT allow enabling GitOps on a group that already has GitOps enabled

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
    - Reason: CRUD with encryption, test-connection logic, gitops enable/disable flow
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 2 (with Tasks 4, 5, 7)
  - **Blocks**: Task 9
  - **Blocked By**: Task 3

  **References**:

  **Pattern References**:
  - `backend/app/api/groups.py` — Existing group endpoints to extend with gitops routes
  - `backend/app/api/ssh_keys.py` — Pattern for encrypted credential CRUD (upload, never expose private data)
  - `backend/app/schemas/hosts.py` — Pydantic schema patterns used in the project

  **Acceptance Criteria**:
  - [ ] Full CRUD for GitRepository works
  - [ ] HTTPS token never in API responses
  - [ ] Test-connection endpoint clones repo and reports success/failure
  - [ ] Enable GitOps on group sets all gitops fields
  - [ ] Disable GitOps clears gitops fields but keeps rules
  - [ ] Delete repo with linked groups returns 400

  **QA Scenarios**:
  ```
  Scenario: GitOps enable/disable lifecycle
    Tool: Bash (curl)
    Preconditions: Superuser logged in, group exists
    Steps:
      1. POST /api/git-repos {"name":"rules","url":"git@github.com:user/rules.git","branch":"main","auth_type":"ssh_key","ssh_key_id":1} → Assert 201
      2. POST /api/groups/{id}/gitops/enable {"git_repository_id":1,"file_path":"groups/web-servers.yaml"} → Assert 200
      3. GET /api/groups/{id}/gitops/status → Assert gitops_enabled=true, status="disconnected"
      4. POST /api/groups/{id}/gitops/disable → Assert 200
      5. GET /api/groups/{id}/gitops/status → Assert gitops_enabled=false
    Expected Result: GitOps can be enabled and disabled per group
    Evidence: .sisyphus/evidence/task-6-gitops-lifecycle.txt

  Scenario: HTTPS token never exposed
    Tool: Bash (curl)
    Steps:
      1. POST /api/git-repos {"name":"test","url":"https://github.com/user/rules.git","auth_type":"https_token","https_token":"ghp_secret123"} → Assert 201
      2. GET /api/git-repos/{id} → Assert response does NOT contain "ghp_secret123" or "https_token"
    Expected Result: Token encrypted and never returned
    Evidence: .sisyphus/evidence/task-6-token-hidden.txt
  ```

  **Commit**: YES
  - Message: `feat(api): add GitRepository CRUD + GitOps enable/disable`
  - Files: `backend/app/api/git_repos.py`, `backend/app/schemas/git_repos.py`, `backend/app/api/groups.py` (additions)

- [x] 7. Webhook Endpoints for GitHub/GitLab/Gitea

  **What to do**:
  - Create `app/api/webhooks.py` with endpoints:
    - `POST /webhooks/github` — GitHub push webhook
    - `POST /webhooks/gitlab` — GitLab push webhook
    - `POST /webhooks/gitea` — Gitea push webhook
  - Signature/token verification per provider:
    - **GitHub**: Read raw body bytes. Compute `HMAC-SHA256(webhook_secret, body)`. Compare hex digest against `X-Hub-Signature-256` header (strip `sha256=` prefix). Use `hmac.compare_digest()` (timing-safe).
    - **GitLab**: Compare `X-Gitlab-Token` header value against `webhook_secret` using `hmac.compare_digest()`.
    - **Gitea**: Read raw body bytes. Compute `HMAC-SHA256(webhook_secret, body)`. Compare hex digest against `X-Gitea-Signature` header (no prefix). Use `hmac.compare_digest()`.
  - Parse push event payload:
    - Extract `after` commit SHA (the commit to process). For GitLab use `checkout_sha`.
    - Extract `ref` to verify it matches the configured branch (`refs/heads/{branch}`)
    - Handle `deleted: true` / `after: "0000..."` (branch deletion) → return 200 immediately, no processing
  - On valid webhook:
    1. Look up GitRepository by matching URL from payload against stored repos
    2. Find all HostGroups linked to this repo with `gitops_enabled=True`
    3. Dispatch `process_gitops_webhook` Celery task with `repo_id` and `commit_sha`
    4. Return 200 immediately (async processing)
  - On invalid signature: return 401
  - On unrecognized repo URL: return 200 (silent ignore — not an error)
  - Register webhooks router in `app/main.py` (NOT under `/api` prefix — webhooks are at `/webhooks/`)

  **Must NOT do**:
  - Do NOT use `==` for signature comparison — must use `hmac.compare_digest()`
  - Do NOT block on processing — return 200 immediately, process async
  - Do NOT rely on `commits[].modified` for changed files — process all group files at the SHA
  - Do NOT create abstract base classes for webhook providers — simple if/elif or separate functions

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
    - Reason: Three webhook formats with signature verification, security-critical
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 2 (with Tasks 4, 5, 6)
  - **Blocks**: Task 11
  - **Blocked By**: Task 3

  **References**:

  **Pattern References**:
  - `backend/app/main.py` — Where to register the webhooks router (follow existing `app.include_router()` pattern)

  **External References**:
  - GitHub webhook signature: https://docs.github.com/en/webhooks/using-webhooks/validating-webhook-deliveries
  - GitLab webhook token: https://docs.gitlab.com/ee/user/project/integrations/webhooks.html#validate-payloads-by-using-a-secret-token
  - Gitea webhook signature: https://docs.gitea.com/usage/webhooks

  **Acceptance Criteria**:
  - [ ] GitHub webhook with valid HMAC signature returns 200 and dispatches task
  - [ ] GitHub webhook with invalid signature returns 401
  - [ ] GitLab webhook with valid token returns 200
  - [ ] Gitea webhook with valid HMAC returns 200
  - [ ] Branch deletion event returns 200 with no processing
  - [ ] Non-matching branch (e.g., push to `dev` when configured for `main`) returns 200 with no processing
  - [ ] All signature verification uses `hmac.compare_digest()`

  **QA Scenarios**:
  ```
  Scenario: GitHub webhook with valid signature
    Tool: Bash (curl)
    Preconditions: GitRepository exists with webhook_secret="test-secret-123"
    Steps:
      1. Compute HMAC: echo -n '{"ref":"refs/heads/main","after":"abc123","repository":{"clone_url":"https://github.com/user/rules.git"}}' | openssl dgst -sha256 -hmac "test-secret-123" | awk '{print $2}'
      2. Run: curl -s -w "%{http_code}" -X POST http://localhost:8000/webhooks/github \
           -H "Content-Type: application/json" \
           -H "X-Hub-Signature-256: sha256=<computed>" \
           -H "X-GitHub-Event: push" \
           -d '{"ref":"refs/heads/main","after":"abc123","repository":{"clone_url":"https://github.com/user/rules.git"}}'
      3. Assert: HTTP 200
    Expected Result: Valid webhook accepted
    Evidence: .sisyphus/evidence/task-7-github-webhook.txt

  Scenario: Invalid signature rejected
    Tool: Bash (curl)
    Steps:
      1. Run: curl -s -w "%{http_code}" -X POST http://localhost:8000/webhooks/github \
           -H "X-Hub-Signature-256: sha256=invalidsignature" \
           -H "X-GitHub-Event: push" \
           -d '{"ref":"refs/heads/main","after":"abc123"}'
      2. Assert: HTTP 401
    Expected Result: Invalid signature returns 401
    Evidence: .sisyphus/evidence/task-7-invalid-sig.txt
  ```

  **Commit**: YES
  - Message: `feat(api): add webhook endpoints for GitHub/GitLab/Gitea`
  - Files: `backend/app/api/webhooks.py`, `backend/app/main.py` (router registration)

- [x] 8. Import/Reconcile Engine

  **What to do**:
  - Create `app/gitops/importer.py`:
    - `import_group_from_yaml(group_id: int, yaml_content: str, commit_sha: str, db: AsyncSession) -> ImportResult`:
      ```python
      class ImportResult:
          success: bool
          rules_added: int
          rules_removed: int
          rules_unchanged: int
          diff: Optional[RulesetDiff]
          error_message: Optional[str]
      ```
      1. Parse YAML using `app/gitops/serializer.py:parse_yaml()`
      2. Validate all rules using existing `app/rules/validation.py`
      3. Convert YAML rules to `FirewallRuleSpec` list using `yaml_rules_to_specs()`
      4. Fetch current DB rules for this group using `app/rules/converter.py:firewall_rules_to_specs()`
      5. Compute diff using `app/sync/diff.py:compute_diff()`
      6. If `diff.has_changes`:
         - Delete all non-system rules for this group (`is_system=False`)
         - Insert new rules from YAML using `app/rules/converter.py:spec_to_firewall_rule()`
         - Create AuditLog entry: `action="gitops.import"`, `before_state=old_rules_json`, `after_state=new_rules_json`, include `commit_sha` in metadata
      7. Update HostGroup: `gitops_status="synced"`, `gitops_last_import_at=now()`, clear `gitops_error_message`
      8. Return `ImportResult`
    - On validation failure:
      - Update HostGroup: `gitops_status="error"`, `gitops_error_message="<descriptive message>"`
      - Return `ImportResult(success=False, error_message=...)`
      - Do NOT modify rules in DB
  - Use per-group advisory lock (`pg_advisory_xact_lock(group_id + offset)`) to prevent concurrent imports
  - System rules (`is_system=True`) in YAML are silently stripped — system rules are always auto-injected by `merge_group_rules()`

  **Must NOT do**:
  - Do NOT modify rules on validation failure — DB stays unchanged
  - Do NOT process `system: true` rules from YAML — strip them
  - Do NOT write to Git — read-only operations only
  - Do NOT modify rules for groups that have `gitops_enabled=False`

  **Recommended Agent Profile**:
  - **Category**: `deep`
    - Reason: Core reconciliation logic — YAML parsing, diff computation, DB update, audit logging, error handling
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 3 (with Tasks 9, 10)
  - **Blocks**: Task 11
  - **Blocked By**: Tasks 4, 5

  **References**:

  **Pattern References**:
  - `backend/app/sync/diff.py:compute_diff()` — Reuse for computing rule diff
  - `backend/app/audit/logger.py:log_action()` — Reuse for audit trail
  - `backend/app/rules/converter.py` (from Task 2) — Reuse for ORM↔spec conversion
  - `backend/app/gitops/serializer.py` (from Task 5) — Reuse for YAML parsing
  - `backend/app/api/sync.py:187-242` — Pattern for per-host advisory locking (adapt for per-group)

  **Acceptance Criteria**:
  - [ ] Valid YAML → rules imported to DB → ImportResult shows counts
  - [ ] Invalid YAML → rules unchanged → ImportResult shows error
  - [ ] Diff correctly computed between old and new rules
  - [ ] AuditLog entry created with `action="gitops.import"` and commit_sha
  - [ ] System rules in YAML stripped (not imported)
  - [ ] Concurrent import for same group blocked (advisory lock)
  - [ ] Group gitops_status updated correctly (synced/error)

  **QA Scenarios**:
  ```
  Scenario: Import replaces DB rules with YAML rules
    Tool: Bash
    Preconditions: Group exists with 2 existing rules
    Steps:
      1. Call import_group_from_yaml with YAML containing 3 new rules
      2. Assert: ImportResult.success is True
      3. Assert: ImportResult.rules_added == 3
      4. Assert: ImportResult.rules_removed == 2
      5. Query DB: group now has exactly 3 rules (plus system rules)
      6. Query AuditLog: entry exists with action="gitops.import"
    Expected Result: Old rules replaced with YAML rules
    Evidence: .sisyphus/evidence/task-8-import-success.txt

  Scenario: Invalid YAML leaves DB unchanged
    Tool: Bash
    Steps:
      1. Call import_group_from_yaml with YAML containing invalid CIDR "999.999.999.999/99"
      2. Assert: ImportResult.success is False
      3. Assert: ImportResult.error_message contains "CIDR" or "invalid"
      4. Query DB: group rules unchanged from before import
      5. Query HostGroup: gitops_status == "error"
    Expected Result: Validation failure prevents DB modification
    Evidence: .sisyphus/evidence/task-8-import-invalid.txt
  ```

  **Commit**: YES
  - Message: `feat(gitops): add import/reconcile engine`
  - Files: `backend/app/gitops/importer.py`

- [x] 9. Rule Mutation Lockdown + GitOps UI Indicators

  **What to do**:
  - **Backend** — Modify `app/api/rules.py`:
    - Add a dependency check at the top of POST/PUT/DELETE rule endpoints:
      ```python
      async def check_gitops_lock(group_id: int, db: AsyncSession):
          group = await db.get(HostGroup, group_id)
          if group and group.gitops_enabled:
              raise HTTPException(
                  status_code=403,
                  detail="This group is managed by GitOps. Rule changes must be made via Git."
              )
      ```
    - Apply to: `POST /api/groups/{id}/rules`, `PUT /api/groups/{id}/rules/{id}`, `DELETE /api/groups/{id}/rules/{id}`, `PUT /api/groups/{id}/rules/reorder`
    - Read endpoints (`GET`) remain accessible — UI can display rules, just can't modify
  - **Frontend** — Modify group detail and rules pages:
    - Add `gitops_enabled` to group API response (already in model from Task 3)
    - On group detail page: show "Managed by GitOps" badge when `gitops_enabled=True`
    - On rules page: hide "Add Rule" button, hide edit/delete buttons per row, hide reorder drag handles
    - Show info banner: "Rules are managed via Git repository: {repo_name} ({file_path}). Changes must be pushed to Git."
    - Show `gitops_status` badge: `synced` (green), `error` (red), `importing` (blue), `disconnected` (gray)
    - Show `gitops_error_message` when status is `error`
    - Show `gitops_last_import_at` timestamp
  - Add `gitops_enabled`, `gitops_status`, `gitops_error_message`, `gitops_last_import_at` to GroupResponse schema

  **Must NOT do**:
  - Do NOT block GET endpoints — read access stays open
  - Do NOT build a full GitOps management UI (repo CRUD, webhook setup) — just status indicators
  - Do NOT add a "Push to Git" button — Barricade never writes to Git

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
    - Reason: Backend API guard + frontend UI changes across multiple pages
  - **Skills**: [`frontend-ui-ux`]
    - `frontend-ui-ux`: Clean badge/indicator design for GitOps status

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 3 (with Tasks 8, 10)
  - **Blocks**: Task 12
  - **Blocked By**: Task 6

  **References**:

  **Pattern References**:
  - `backend/app/api/rules.py` — Existing rule CRUD endpoints to modify with gitops check
  - `backend/app/auth/rbac.py` — Pattern for FastAPI `Depends()` guards
  - `frontend/app/(dashboard)/groups/[id]/rules/page.tsx` — Rules page to modify for read-only state
  - `frontend/app/(dashboard)/groups/[id]/page.tsx` — Group detail page to add badges

  **Acceptance Criteria**:
  - [ ] POST/PUT/DELETE on rules for GitOps group returns 403 with clear message
  - [ ] GET on rules for GitOps group returns 200 (read-only access preserved)
  - [ ] UI shows "Managed by GitOps" badge on GitOps groups
  - [ ] Add/edit/delete/reorder buttons hidden on GitOps group rules page
  - [ ] Info banner displayed with repo name and file path
  - [ ] gitops_status badge visible (synced/error/importing/disconnected)
  - [ ] Error message displayed when status is `error`

  **QA Scenarios**:
  ```
  Scenario: Rule mutation blocked on GitOps group
    Tool: Bash (curl)
    Preconditions: Group has gitops_enabled=True
    Steps:
      1. POST /api/groups/{id}/rules {"action":"allow","protocol":"tcp","direction":"input","port_start":8080} → Assert 403
      2. Assert response contains "GitOps" and "Git"
      3. GET /api/groups/{id}/rules → Assert 200 (read still works)
    Expected Result: Write blocked, read allowed
    Evidence: .sisyphus/evidence/task-9-lockdown.txt

  Scenario: UI shows GitOps indicators
    Tool: Playwright
    Preconditions: Group with gitops_enabled=True, logged in
    Steps:
      1. Navigate to /groups/{id}
      2. Assert: page contains text "Managed by GitOps" or similar badge
      3. Navigate to /groups/{id}/rules
      4. Assert: "Add Rule" button is NOT present
      5. Assert: page contains info banner about Git repository
    Expected Result: UI correctly reflects GitOps read-only state
    Evidence: .sisyphus/evidence/task-9-ui-indicators.png
  ```

  **Commit**: YES
  - Message: `feat(gitops): lock rule mutations on GitOps groups + UI indicators`
  - Files: `backend/app/api/rules.py` (modification), `frontend/app/(dashboard)/groups/[id]/page.tsx`, `frontend/app/(dashboard)/groups/[id]/rules/page.tsx`

- [x] 10. barricade-lint Standalone Pip Package

  **What to do**:
  - Create `barricade-lint/` directory at project root (NOT inside `backend/`):
    ```
    barricade-lint/
    ├── barricade_lint/
    │   ├── __init__.py
    │   ├── cli.py           # Click CLI entry point
    │   ├── schema.py        # Pydantic YAML schema (standalone copy)
    │   ├── validators.py    # CIDR, port, protocol validation (standalone copy)
    │   └── formatter.py     # Error output formatting with line numbers
    ├── tests/
    │   ├── __init__.py
    │   ├── test_lint.py
    │   └── fixtures/
    │       ├── valid-rules.yaml
    │       ├── invalid-cidr.yaml
    │       ├── invalid-action.yaml
    │       ├── icmp-with-port.yaml
    │       └── dangerous-rules.yaml
    ├── pyproject.toml        # Standalone package with click + pyyaml + pydantic deps
    └── README.md
    ```
  - CLI interface (`click`):
    - `barricade-lint <file> [file...]` — validate one or more YAML files
    - `--strict` flag: treat warnings as errors (e.g., `0.0.0.0/0` all-ports rule)
    - Exit codes: 0 = valid, 1 = errors found, 2 = warnings-as-errors (with `--strict`)
    - Output format: `{file}:{line}: ERROR: {message}` (one per line, grep-friendly)
  - Validations:
    - YAML syntax check (parseable YAML)
    - Required structure: top-level must have `firewall.rules` key
    - Each rule: valid `action` (allow/deny/reject), valid `protocol` (tcp/udp/icmp/any), valid `direction` (input/output)
    - CIDR validation: `source` and `dest` must be valid IPv4 or IPv6 CIDR
    - Port validation: integer 1-65535, or range string "start-end" where end >= start
    - ICMP + port conflict: protocol=icmp must not have port field
    - Warning: source `0.0.0.0/0` with no port restriction (wildcard open rule)
    - Unknown top-level keys: warning (not error) — future modules
  - Use `ruamel.yaml` (instead of `pyyaml`) for line number tracking in error messages
  - Zero dependencies on barricade backend code — own `pyproject.toml`

  **Must NOT do**:
  - Do NOT depend on `barricade-backend`, `sqlalchemy`, `fastapi`, or any Barricade package
  - Do NOT use pyyaml if line numbers are needed — use `ruamel.yaml` for mark info
  - Do NOT make it a CLI-only tool — expose validation as a Python API too (for testing)

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
    - Reason: Standalone package with CLI, validation logic, test fixtures
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 3 (with Tasks 8, 9)
  - **Blocks**: Task 12
  - **Blocked By**: Task 5 (uses same YAML schema design)

  **References**:

  **Pattern References**:
  - `backend/app/gitops/schema.py` (from Task 5) — YAML schema to replicate (standalone copy). Same field names and types.
  - `backend/app/rules/validation.py` — CIDR and port validation to replicate (standalone copy). Same logic.

  **External References**:
  - Click CLI: https://click.palletsprojects.com/
  - ruamel.yaml for line numbers: https://yaml.readthedocs.io/en/latest/

  **Acceptance Criteria**:
  - [ ] `pip install -e barricade-lint/` installs without errors
  - [ ] `barricade-lint tests/fixtures/valid-rules.yaml` exits 0
  - [ ] `barricade-lint tests/fixtures/invalid-cidr.yaml` exits 1 with error message containing file:line
  - [ ] `barricade-lint tests/fixtures/dangerous-rules.yaml` exits 0 (warning only)
  - [ ] `barricade-lint --strict tests/fixtures/dangerous-rules.yaml` exits 2
  - [ ] `barricade-lint tests/fixtures/icmp-with-port.yaml` exits 1 with ICMP error
  - [ ] No imports from `barricade-backend` anywhere in `barricade-lint/`

  **QA Scenarios**:
  ```
  Scenario: Lint valid YAML file
    Tool: Bash
    Steps:
      1. Run: pip install -e barricade-lint/
      2. Run: barricade-lint barricade-lint/tests/fixtures/valid-rules.yaml
      3. Assert: exit code 0
      4. Assert: output is empty or shows "OK" message
    Expected Result: Valid file passes lint
    Evidence: .sisyphus/evidence/task-10-lint-valid.txt

  Scenario: Lint invalid CIDR
    Tool: Bash
    Steps:
      1. Run: barricade-lint barricade-lint/tests/fixtures/invalid-cidr.yaml
      2. Assert: exit code 1
      3. Assert: output contains line number and "CIDR" or "invalid"
    Expected Result: Invalid CIDR caught with line reference
    Evidence: .sisyphus/evidence/task-10-lint-invalid.txt

  Scenario: No backend dependencies
    Tool: Bash
    Steps:
      1. Run: grep -r "from app\." barricade-lint/ || grep -r "import app\." barricade-lint/
      2. Assert: exit code 1 (no matches)
    Expected Result: Linter is fully standalone
    Evidence: .sisyphus/evidence/task-10-lint-standalone.txt
  ```

  **Commit**: YES
  - Message: `feat(lint): add barricade-lint standalone pip package`
  - Files: `barricade-lint/`

- [ ] 11. Auto-Sync Pipeline Wiring (Webhook → Import → Diff → Sync)

  **What to do**:
  - Create `app/tasks/gitops.py` — Celery task on `long_running` queue:
    - `process_gitops_webhook(repo_id: int, commit_sha: str)` — orchestrates the full pipeline:
      1. Fetch GitRepository from DB
      2. **Idempotency check**: if `repo.last_commit_sha == commit_sha`, skip (webhook replay)
      3. Clone/pull repo using `app/gitops/git_service.py:clone_repo()`
      4. Find all HostGroups linked to this repo with `gitops_enabled=True`
      5. For each group:
         a. Read YAML file at `commit_sha` using `read_file_at_sha()`
         b. If file not found: set `gitops_status="error"`, `gitops_error_message="File not found: {path}"`, continue to next group
         c. Call `app/gitops/importer.py:import_group_from_yaml()` — this handles parse, validate, diff, DB update, audit
         d. If import succeeded AND `diff.has_changes`:
            - For each host in this group: trigger existing `run_sync_playbook` Celery task (from core plan T17)
            - This reuses the EXACT same sync pipeline as manual sync (merge rules → render → generate playbook → ansible-runner)
         e. If import failed: group already has `gitops_status="error"` from importer
      6. Update `repo.last_commit_sha = commit_sha`, `repo.last_sync_at = now()`
      7. `finally`: clean up temp repo directory
  - Create `app/gitops/pipeline.py` — helper functions:
    - `trigger_group_sync(group_id: int, triggered_by: str = "gitops")`:
      - Get all hosts in group
      - For each host: get merged rules → render → generate playbook → create SyncJob → dispatch Celery task
      - Reuses `app/api/sync.py` logic (extract into shared function if needed)
      - Uses existing `run_sync_playbook` task — NOT a new sync mechanism
  - Celery retry configuration: 3 retries with exponential backoff (30s, 60s, 120s) for transient git/network failures
  - Per-group advisory lock already handled by importer (Task 8)
  - Log the diff to audit trail (already done by importer)

  **Must NOT do**:
  - Do NOT create a new sync mechanism — reuse existing `run_sync_playbook` Celery task
  - Do NOT skip the diff computation — it's required for audit trail
  - Do NOT process groups where `gitops_enabled=False`
  - Do NOT proceed with sync if import failed
  - Do NOT write to Git — read-only

  **Recommended Agent Profile**:
  - **Category**: `deep`
    - Reason: Critical integration point tying together 5+ subsystems. Error handling for partial failures (some groups succeed, some fail).
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: NO (this is the critical path integration point)
  - **Parallel Group**: Wave 4 (alone)
  - **Blocks**: Task 12
  - **Blocked By**: Tasks 1, 7, 8

  **References**:

  **Pattern References**:
  - `backend/app/tasks/sync.py:23-198` — Existing Celery task pattern with `asyncio.run()` bridge, cleanup in `finally`. Follow exactly.
  - `backend/app/api/sync.py:187-242` — Sync trigger logic (merge → render → playbook → dispatch). Extract into shared function or call directly.
  - `backend/app/gitops/git_service.py` (from Task 4) — `clone_repo()`, `read_file_at_sha()`, `cleanup_repo()`
  - `backend/app/gitops/importer.py` (from Task 8) — `import_group_from_yaml()`

  **Acceptance Criteria**:
  - [ ] Webhook dispatch → Celery task processes → rules imported → hosts synced
  - [ ] Idempotent: same commit SHA processed twice → second time is no-op
  - [ ] Missing YAML file → group status set to `error`, other groups still processed
  - [ ] Import failure → no sync triggered for that group, other groups unaffected
  - [ ] Celery retries on transient failure (3x exponential backoff)
  - [ ] Temp repo directory cleaned up in `finally` block
  - [ ] `repo.last_commit_sha` updated after successful processing
  - [ ] Existing `run_sync_playbook` task reused (not a new sync path)

  **QA Scenarios**:
  ```
  Scenario: Full pipeline webhook → import → sync
    Tool: Bash
    Preconditions: GitRepository configured, group linked with valid YAML in test repo
    Steps:
      1. Trigger process_gitops_webhook task directly (or via webhook endpoint)
      2. Wait for task completion (poll Celery result, max 60s)
      3. Query DB: group rules match YAML content
      4. Query DB: SyncJob records created for each host in group
      5. Query DB: AuditLog entry with action="gitops.import" exists
      6. Query DB: gitops_status == "synced"
      7. Query DB: repo.last_commit_sha matches the processed SHA
    Expected Result: Full pipeline completes
    Evidence: .sisyphus/evidence/task-11-pipeline.txt

  Scenario: Idempotent webhook replay
    Tool: Bash
    Steps:
      1. Process commit SHA "abc123" → succeeds
      2. Process commit SHA "abc123" again
      3. Assert: second processing is skipped (no new rules import, no new SyncJobs)
    Expected Result: Duplicate webhook is no-op
    Evidence: .sisyphus/evidence/task-11-idempotent.txt
  ```

  **Commit**: YES
  - Message: `feat(gitops): wire auto-sync pipeline (webhook→import→sync)`
  - Files: `backend/app/tasks/gitops.py`, `backend/app/gitops/pipeline.py`

- [ ] 12. GitOps Pytest Suite

  **What to do**:
  - Create test files in `backend/tests/`:
    - `tests/test_gitops_serializer.py`:
      - Valid YAML parsing with all field types
      - Port format conversion (int → single, string → range)
      - Invalid YAML rejection (bad action, bad CIDR, ICMP+port)
      - Unknown top-level keys ignored
      - System rules stripped
      - Empty rules list
      - YAML roundtrip
    - `tests/test_gitops_importer.py`:
      - Successful import replaces DB rules
      - Invalid YAML leaves DB unchanged, sets error status
      - System rules not imported from YAML
      - Concurrent import blocked (advisory lock)
      - AuditLog entry created with commit_sha
      - gitops_status updated correctly
    - `tests/test_gitops_webhooks.py`:
      - GitHub HMAC signature validation (valid → 200, invalid → 401)
      - GitLab token validation
      - Gitea HMAC signature validation
      - Branch mismatch → 200, no processing
      - Branch deletion → 200, no processing
      - Use GitHub's known test vector: secret=`"It's a Secret to Everybody"`, payload=`"Hello, World!"` (from GitHub docs)
    - `tests/test_gitops_pipeline.py`:
      - Webhook → import → rules updated in DB
      - Idempotent replay (same SHA → skip)
      - Missing YAML file → error status on group
      - Import failure → no sync triggered
    - `tests/test_gitops_lockdown.py`:
      - POST/PUT/DELETE rules on GitOps group → 403
      - GET rules on GitOps group → 200
      - Enable/disable GitOps on group
    - `tests/test_gitops_converter.py`:
      - Roundtrip converter (if not already covered by Task 2 tests)
  - Use pytest + pytest-asyncio + httpx `AsyncClient` with `ASGITransport` (same pattern as existing tests)
  - Use PostgreSQL via testcontainers (same as core plan)
  - Mock git operations (gitpython) — don't require real Git repo for unit tests
  - Minimum 3 tests per module: happy path + error path + edge case

  **Must NOT do**:
  - Do NOT use SQLite — use PostgreSQL testcontainers
  - Do NOT test against real GitHub/GitLab — mock webhook payloads
  - Do NOT mock the database — use real PostgreSQL with transaction rollback

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
    - Reason: Comprehensive test suite across 6 test files, mocking strategy
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: NO (depends on all previous tasks)
  - **Parallel Group**: Wave 5
  - **Blocks**: Task 13
  - **Blocked By**: Tasks 9, 10, 11

  **References**:

  **Pattern References**:
  - `backend/tests/conftest.py` — Existing test fixtures and configuration
  - `backend/tests/test_crypto.py` — Pattern for unit testing backend logic
  - `backend/tests/test_rules.py` — Pattern for testing rule-related functionality

  **External References**:
  - GitHub HMAC test vector: https://docs.github.com/en/webhooks/using-webhooks/validating-webhook-deliveries

  **Acceptance Criteria**:
  - [ ] `cd backend && pytest tests/test_gitops_*.py -v` passes all tests
  - [ ] Minimum 18 tests across 6 files (3 per module)
  - [ ] Webhook signature tests use known test vectors
  - [ ] No SQLite, no mocked DB
  - [ ] GitOps lockdown tested (403 for writes, 200 for reads)

  **QA Scenarios**:
  ```
  Scenario: GitOps test suite passes
    Tool: Bash
    Steps:
      1. Run: cd backend && pytest tests/test_gitops_*.py -v --tb=short
      2. Assert: exit code 0
      3. Assert: 0 failures
      4. Assert: test count >= 18
    Expected Result: All GitOps tests pass
    Evidence: .sisyphus/evidence/task-12-pytest.txt
  ```

  **Commit**: YES
  - Message: `test(gitops): add pytest suite for GitOps features`
  - Files: `backend/tests/test_gitops_*.py`

- [ ] 13. End-to-End GitOps Pipeline Integration Test

  **What to do**:
  - Create `backend/tests/integration/test_gitops_workflow.py`:
    - Full pipeline test using a local bare Git repo (no external services):
      1. Create a local bare Git repo in `/tmp/`
      2. Clone it, add a valid `groups/web-servers.yaml` file, commit, push
      3. Create GitRepository in DB pointing to local bare repo (no auth needed for local)
      4. Create HostGroup "web-servers", enable GitOps with file_path="groups/web-servers.yaml"
      5. Simulate webhook by directly calling `process_gitops_webhook` task
      6. Verify: rules imported to DB match YAML content
      7. Verify: AuditLog entry exists with action="gitops.import"
      8. Verify: gitops_status == "synced"
      9. Verify: SyncJob records created for hosts (if any hosts assigned)
      10. Push invalid YAML update, trigger webhook again
      11. Verify: gitops_status == "error", rules unchanged
      12. Push valid YAML update, trigger webhook again
      13. Verify: rules updated, status back to "synced"
    - Test rule lockdown in same workflow:
      14. Try POST /api/groups/{id}/rules → Assert 403
      15. Disable GitOps
      16. Try POST /api/groups/{id}/rules → Assert 201 (works again)
  - Use local bare Git repo — no external Git hosting needed
  - Use Celery `task_always_eager=True` for synchronous execution in tests

  **Must NOT do**:
  - Do NOT require external Git hosting (GitHub/GitLab) — local bare repo only
  - Do NOT skip any step in the workflow

  **Recommended Agent Profile**:
  - **Category**: `deep`
    - Reason: Complex integration test with git operations, Celery, DB, and API assertions
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: NO
  - **Parallel Group**: Wave 5 (after T12)
  - **Blocks**: F1-F4
  - **Blocked By**: Task 12

  **References**:

  **Pattern References**:
  - `backend/tests/integration/test_full_workflow.py` — Existing integration test pattern (319 lines). Follow same structure.

  **Acceptance Criteria**:
  - [ ] Full GitOps workflow completes without errors
  - [ ] YAML → import → DB rules verified
  - [ ] Invalid YAML → error status → rules preserved
  - [ ] Rule lockdown verified (403 when GitOps on, 201 when off)
  - [ ] All assertions pass in one run

  **QA Scenarios**:
  ```
  Scenario: Full GitOps integration test
    Tool: Bash
    Steps:
      1. Run: cd backend && pytest tests/integration/test_gitops_workflow.py -v
      2. Assert: exit code 0
      3. Assert: all assertions pass
    Expected Result: Complete GitOps pipeline verified end-to-end
    Evidence: .sisyphus/evidence/task-13-integration.txt
  ```

  **Commit**: YES
  - Message: `test(gitops): add end-to-end GitOps pipeline integration test`
  - Files: `backend/tests/integration/test_gitops_workflow.py`

---

## Final Verification Wave (MANDATORY — after ALL implementation tasks)

> 4 review agents run in PARALLEL. ALL must APPROVE. Rejection → fix → re-run.

- [ ] F1. **Plan Compliance Audit** — `oracle`
  Read the plan end-to-end. For each "Must Have": verify implementation exists (read file, curl endpoint, run command). For each "Must NOT Have": search codebase for forbidden patterns — reject with file:line if found. Check evidence files exist in .sisyphus/evidence/. Compare deliverables against plan.
  Output: `Must Have [N/N] | Must NOT Have [N/N] | Tasks [N/N] | VERDICT: APPROVE/REJECT`

- [ ] F2. **Code Quality Review** — `unspecified-high`
  Run linter + `pytest`. Review all changed files for: `as any`/type ignores, empty catches, console.log in prod, commented-out code, unused imports. Check AI slop: excessive comments, over-abstraction, generic names. Verify no secrets in code. Check webhook signature verification uses `hmac.compare_digest()`. Verify HTTPS tokens are encrypted in DB.
  Output: `Build [PASS/FAIL] | Lint [PASS/FAIL] | Tests [N pass/N fail] | Files [N clean/N issues] | VERDICT`

- [ ] F3. **Real Manual QA** — `unspecified-high`
  Start from clean state. Test full GitOps pipeline: create group → enable GitOps → push YAML to test repo → verify webhook received → verify rules imported → verify hosts synced. Test error cases: invalid YAML → error status, GitOps group → rule CRUD returns 403. Run barricade-lint against valid and invalid files. Save to `.sisyphus/evidence/final-qa/`.
  Output: `Scenarios [N/N pass] | Integration [N/N] | Edge Cases [N tested] | VERDICT`

- [ ] F4. **Scope Fidelity Check** — `deep`
  For each task: read "What to do", read actual code. Verify 1:1 — everything in spec was built, nothing beyond spec was built. Check "Must NOT Have" compliance: no Git writing, no Mode A, no auto-group creation, no polling, no non-firewall module processing. Flag unaccounted features.
  Output: `Tasks [N/N compliant] | Forbidden [CLEAN/N issues] | Unaccounted [CLEAN/N files] | VERDICT`

---

## Commit Strategy

| Task | Commit Message | Key Files |
|------|---------------|-----------|
| T1 | `fix(sync): implement real fetch_current_state for nftables/firewalld/ufw` | `app/sync/diff.py`, `app/sync/parsers/` |
| T2 | `refactor(rules): extract shared FirewallRule↔RuleSpec converter` | `app/rules/converter.py`, 4 call sites |
| T3 | `feat(gitops): add GitRepository model + HostGroup gitops fields` | `app/models/git_repository.py`, `alembic/versions/0002_*` |
| T4 | `feat(gitops): add git clone/pull service with SSH+HTTPS auth` | `app/gitops/git_service.py` |
| T5 | `feat(gitops): add multi-module YAML serializer/deserializer` | `app/gitops/serializer.py`, `app/gitops/schema.py` |
| T6 | `feat(api): add GitRepository CRUD + GitOps enable/disable` | `app/api/git_repos.py`, `app/schemas/git_repos.py` |
| T7 | `feat(api): add webhook endpoints for GitHub/GitLab/Gitea` | `app/api/webhooks.py` |
| T8 | `feat(gitops): add import/reconcile engine` | `app/gitops/importer.py` |
| T9 | `feat(gitops): lock rule mutations on GitOps groups + UI indicators` | `app/api/rules.py` (mod), frontend components |
| T10 | `feat(lint): add barricade-lint standalone pip package` | `barricade-lint/` |
| T11 | `feat(gitops): wire auto-sync pipeline (webhook→import→sync)` | `app/gitops/pipeline.py`, `app/tasks/gitops.py` |
| T12 | `test(gitops): add pytest suite for GitOps features` | `backend/tests/test_gitops_*.py` |
| T13 | `test(gitops): add end-to-end GitOps pipeline integration test` | `backend/tests/integration/test_gitops_workflow.py` |

---

## Success Criteria

### Verification Commands
```bash
# GitOps pytest suite
cd backend && pytest tests/test_gitops_*.py -v  # Expected: all pass

# barricade-lint CLI
pip install -e barricade-lint/
barricade-lint tests/fixtures/valid-rules.yaml  # Expected: exit 0
barricade-lint tests/fixtures/invalid-rules.yaml  # Expected: exit 1

# Webhook simulation (GitHub)
curl -X POST http://localhost:8000/webhooks/github \
  -H "X-Hub-Signature-256: sha256=<computed-hmac>" \
  -H "X-GitHub-Event: push" \
  -d '{"ref":"refs/heads/main","after":"abc123",...}'  # Expected: 200

# Rule lockdown
curl -X POST http://localhost:8000/api/groups/{gitops_group_id}/rules \
  -H "Cookie: ..." -d '{"action":"allow",...}'  # Expected: 403

# Full integration
cd backend && pytest tests/integration/test_gitops_workflow.py -v  # Expected: all pass
```

### Final Checklist
- [ ] Full GitOps pipeline works: YAML push → webhook → import → sync
- [ ] All three webhook providers verified (GitHub, GitLab, Gitea)
- [ ] Invalid YAML rejected with error status on group
- [ ] GitOps groups: rule CRUD returns 403, UI shows read-only
- [ ] barricade-lint validates and rejects correctly
- [ ] Real fetch_current_state works for all 3 firewall backends
- [ ] All pytest tests pass
- [ ] No secrets in code, HTTPS tokens encrypted
