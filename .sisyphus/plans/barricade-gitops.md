# Barricade GitOps Extension Plan

## TL;DR

> **Quick Summary**: Extend Barricade with a GitOps mode where firewall rules are stored as YAML/JSON in a Git repository. Barricade watches the repo (via webhook or polling), renders diffs, and syncs to nodes — making Git the single source of truth instead of (or alongside) the database. Supports both "push" (Barricade pushes to Git after UI edits) and "pull" (Git is authoritative, Barricade reconciles).

---

## Motivation

The current Barricade model is **database-as-truth**: rules live in PostgreSQL, synced to hosts via Ansible. GitOps adds:

- **Auditability**: every rule change is a Git commit with author, message, diff
- **Rollback**: `git revert` undoes any change
- **PR-based review**: firewall changes go through code review before applying
- **Declarative state**: the repo always reflects desired state
- **CI/CD integration**: lint rules, test playbooks, auto-apply on merge
- **Offline editing**: engineers can edit rules in their editor, push, Barricade reconciles

---

## Architecture Decision: Two Modes

### Mode A — Git as Mirror (Simpler)
DB is still truth. After every rule change in UI, Barricade commits the rendered state to Git. Git is a read-only audit trail + rollback mechanism.

### Mode B — Git as Source of Truth (Full GitOps)
Git is truth. Barricade watches the repo, imports changes, syncs to hosts. UI becomes a read-only viewer + manual trigger. Rule edits happen via PR.

### Recommendation: **Mode A first, Mode B as opt-in per group**
Start with Mode A (low risk, immediate value). Add Mode B as a per-host-group setting: `gitops_mode: mirror | authoritative`.

---

## Work Objectives

Build GitOps integration for Barricade that:
1. Exports current rule state to a Git repo after every change (Mode A)
2. Optionally watches a Git repo and imports changes (Mode B)
3. Provides a diff view between Git state and DB state
4. Supports webhook-triggered sync (GitHub/GitLab/Gitea)
5. Generates human-readable YAML rule files per group

---

## TODOs

### Wave 1 — Git Repository Management

- [ ] G1. **Git Repository Model + Config**

  **What to do**:
  - Add `GitRepository` model to DB:
    ```
    id, name, url (SSH or HTTPS), branch (default: main),
    ssh_key_id (FK to SSHKey — for repo auth),
    mode (enum: mirror | authoritative),
    path_prefix (e.g. "firewall/rules/"),
    webhook_secret (nullable, for validating GitHub/GitLab webhooks),
    last_sync_at, last_commit_sha, created_at, updated_at
    ```
  - Add `git_repository_id` (nullable FK) to `HostGroup` model
  - Alembic migration
  - CRUD API: `GET/POST/PUT/DELETE /api/git-repos`
  - Config: `GIT_AUTHOR_NAME`, `GIT_AUTHOR_EMAIL` env vars (for commits)

  **Acceptance Criteria**:
  - [ ] GitRepository model created and migrated
  - [ ] CRUD API works
  - [ ] HostGroup can be linked to a GitRepository

- [ ] G2. **Git Operations Service**

  **What to do**:
  - Create `app/gitops/git_service.py`:
    - `clone_or_pull(repo: GitRepository) -> Path` — clone to `/tmp/barricade-git-{repo.id}/`, pull if exists
    - `commit_and_push(repo_path: Path, files: dict[str, str], message: str, author: str)` — stage, commit, push
    - `get_file_at_commit(repo_path: Path, path: str, sha: str) -> str` — for rollback
    - `list_commits(repo_path: Path, path: str, limit: int) -> list[CommitInfo]`
    - Use `gitpython` library (add to pyproject.toml)
    - SSH key for repo auth: write to `/dev/shm/` same pattern as Ansible
    - Handle auth for both SSH URLs (`git@github.com:...`) and HTTPS with token

  **Acceptance Criteria**:
  - [ ] Can clone a real Git repo via SSH
  - [ ] Can commit and push changes
  - [ ] SSH key cleaned from `/dev/shm/` after use

- [ ] G3. **Rule Serialization Format (YAML)**

  **What to do**:
  - Create `app/gitops/serializer.py`:
    - `rules_to_yaml(group: HostGroup, rules: list[FirewallRule]) -> str` — human-readable YAML:
      ```yaml
      # Barricade firewall rules — group: web-servers (priority: 100)
      # Last updated: 2026-03-15T20:00:00Z by admin@barricade.io
      # DO NOT EDIT: managed by Barricade. Changes via PR will be imported.
      
      group: web-servers
      priority: 100
      rules:
        - action: allow
          protocol: tcp
          direction: input
          port: 443
          source: 0.0.0.0/0
          comment: "HTTPS from anywhere"
        - action: allow
          protocol: tcp
          direction: input
          port: 22
          source: 10.0.0.0/8
          comment: "SSH from internal"
          system: true  # auto-injected, do not remove
      ```
    - `yaml_to_rules(yaml_str: str) -> list[FirewallRuleSpec]` — parse back
    - Validate parsed rules using existing `app/rules/validation.py`
    - File path convention: `{path_prefix}{group_name}.yaml`

  **Acceptance Criteria**:
  - [ ] YAML roundtrip: serialize → parse → same rules
  - [ ] Invalid YAML raises clear error
  - [ ] System rules preserved in roundtrip

---

### Wave 2 — Mode A: Mirror (Push to Git)

- [ ] G4. **Post-Change Git Commit Hook**

  **What to do**:
  - Create `app/gitops/mirror.py`:
    - `mirror_group_to_git(group_id: int, actor_email: str)` — Celery task (default queue):
      1. Fetch group + rules from DB
      2. Serialize to YAML
      3. Clone/pull repo
      4. Write file to `{path_prefix}{group_name}.yaml`
      5. Commit: `"chore(firewall): update {group_name} rules [via Barricade by {actor}]"`
      6. Push
      7. Update `GitRepository.last_commit_sha`
  - Hook into existing rule CRUD endpoints: after successful create/update/delete, if group has a linked GitRepository in mirror mode, dispatch `mirror_group_to_git`
  - Also hook into sync completion: commit the "applied" state with message `"feat(firewall): apply {group_name} to {N} hosts"`

  **Acceptance Criteria**:
  - [ ] Rule change → Git commit appears in repo within 30s
  - [ ] Commit message includes actor email
  - [ ] Commit includes only the changed group's file
  - [ ] Push failure does NOT fail the rule change (async, best-effort)

- [ ] G5. **Git History API + Rollback**

  **What to do**:
  - Add to `app/api/git_repos.py`:
    - `GET /api/groups/{id}/git-history` — list commits for this group's file (last 50)
    - `GET /api/groups/{id}/git-history/{sha}` — get rules at a specific commit
    - `POST /api/groups/{id}/git-rollback` — body: `{"sha": "abc123", "message": "revert: undo port 3306 change"}`:
      1. Fetch rules from that commit
      2. Parse YAML → FirewallRuleSpec list
      3. Replace current group rules in DB
      4. Commit the revert to Git
      5. Return new rule list
  - Frontend: add "History" tab to group detail page showing commit list with rollback button

  **Acceptance Criteria**:
  - [ ] History endpoint returns commits with sha, message, author, timestamp
  - [ ] Rollback replaces DB rules with historical state
  - [ ] Rollback creates a new Git commit (not a force-push)

---

### Wave 3 — Mode B: Authoritative (Pull from Git)

- [ ] G6. **Webhook Receiver**

  **What to do**:
  - Create `app/api/webhooks.py`:
    - `POST /webhooks/github` — GitHub push webhook
    - `POST /webhooks/gitlab` — GitLab push webhook
    - `POST /webhooks/gitea` — Gitea push webhook
    - Validate HMAC signature using `webhook_secret` from GitRepository
    - Parse push event: extract changed files, branch
    - If branch matches repo's configured branch AND changed files match `{path_prefix}*.yaml`:
      - Dispatch `import_from_git` Celery task for each changed group file
    - Return 200 immediately (async processing)
  - Also support polling: `app/tasks/gitops.py` — Celery beat task every 5 minutes, `git pull`, check for new commits

  **Acceptance Criteria**:
  - [ ] GitHub webhook with valid signature triggers import
  - [ ] Invalid signature returns 401
  - [ ] Only files matching path_prefix trigger import
  - [ ] Polling fallback works when webhook not configured

- [ ] G7. **Import from Git (Reconcile)**

  **What to do**:
  - Create `app/gitops/importer.py`:
    - `import_group_from_git(repo_id: int, file_path: str, commit_sha: str)` — Celery task:
      1. Fetch file content at commit_sha
      2. Parse YAML → list[FirewallRuleSpec]
      3. Validate all rules
      4. Compute diff vs current DB rules
      5. If has_changes:
         - Replace DB rules for this group (delete all non-system, insert new)
         - Create AuditLog entry: `action="git.import"`, `before_state=old_rules`, `after_state=new_rules`, `entity_type="group"`
         - If group has `auto_sync=True`: trigger sync to all hosts in group
      6. Update `GitRepository.last_sync_at`, `last_commit_sha`
    - Add `auto_sync` boolean to `HostGroup` (default False) — if True, auto-sync after git import
    - Add `gitops_lock` to prevent concurrent imports for same group

  **Acceptance Criteria**:
  - [ ] Git push → webhook → rules updated in DB within 60s
  - [ ] Audit log entry created for git import
  - [ ] Invalid YAML in Git file → error logged, DB unchanged
  - [ ] System rules (is_system=True) never overwritten by git import
  - [ ] auto_sync=True triggers Ansible sync after import

- [ ] G8. **Git Conflict Resolution**

  **What to do**:
  - Handle the case where DB was modified while Git was also modified (diverged state):
    - `GET /api/groups/{id}/git-diff` — compare DB rules vs Git HEAD:
      ```json
      {
        "db_ahead": [...],   // rules in DB not in Git
        "git_ahead": [...],  // rules in Git not in DB
        "conflicts": [...],  // same port/protocol but different action
        "in_sync": true/false
      }
      ```
    - `POST /api/groups/{id}/git-resolve` — body: `{"strategy": "db_wins" | "git_wins" | "manual"}`:
      - `db_wins`: push DB state to Git
      - `git_wins`: import Git state to DB
      - `manual`: return conflict list for user to resolve in UI
  - Frontend: conflict resolution UI on group detail page

  **Acceptance Criteria**:
  - [ ] Conflict detection works when both DB and Git have changes
  - [ ] `db_wins` pushes DB state to Git
  - [ ] `git_wins` imports Git state to DB
  - [ ] Manual resolution shows per-rule conflict UI

---

### Wave 4 — Frontend + Polish

- [ ] G9. **GitOps UI**

  **What to do**:
  - Add "GitOps" section to group detail page:
    - Repository link badge (shows connected repo + branch)
    - Mode indicator: "Mirror" or "Authoritative"
    - Last sync time + commit SHA (clickable → opens GitHub/GitLab)
    - "Sync from Git" button (manual pull)
    - "Push to Git" button (manual push)
    - Commit history table (last 20 commits for this group's file)
    - Rollback button per commit row
  - Add "Git Repositories" page: `/git-repos`
    - List repos with status
    - Add/edit/delete repos
    - Test connection button
  - Add GitOps status to dashboard: groups with git repos show git sync status badge

  **Acceptance Criteria**:
  - [ ] Group detail shows git history when repo is linked
  - [ ] Rollback works from UI
  - [ ] Git repo CRUD works

- [ ] G10. **CI/CD Integration Docs + Example**

  **What to do**:
  - Create `docs/gitops/` directory:
    - `README.md` — GitOps setup guide
    - `github-actions-example.yml` — GitHub Actions workflow:
      ```yaml
      # On PR: validate YAML syntax, run barricade-lint
      # On merge to main: webhook triggers Barricade import
      ```
    - `barricade-lint` — CLI tool (Python script) that validates rule YAML files:
      - Checks YAML syntax
      - Validates CIDRs, ports, protocols
      - Warns on dangerous rules (0.0.0.0/0 all ports)
      - Exits non-zero on errors (for CI gate)
    - `.github/workflows/firewall-lint.yml` — example workflow using barricade-lint

  **Acceptance Criteria**:
  - [ ] `barricade-lint rules/web-servers.yaml` exits 0 on valid file
  - [ ] `barricade-lint rules/bad.yaml` exits 1 with clear error
  - [ ] GitHub Actions example is copy-pasteable

---

### Wave FINAL — GitOps Verification

- [ ] GF1. **GitOps Integration Test**
  - Full flow: UI rule change → Git commit → webhook → DB update → Ansible sync
  - Rollback flow: git history → rollback → DB updated → sync triggered
  - Conflict resolution: diverged state → resolve → consistent

---

## Dependency Matrix

| Task | Depends On | Wave |
|------|-----------|------|
| G1 | Barricade T4 (DB models) | 1 |
| G2 | G1 | 1 |
| G3 | G1 | 1 |
| G4 | G2, G3 | 2 |
| G5 | G4 | 2 |
| G6 | G1 | 3 |
| G7 | G3, G6 | 3 |
| G8 | G7 | 3 |
| G9 | G4, G5, G7, G8 | 4 |
| G10 | G3 | 4 |
| GF1 | G9, G10 | FINAL |

---

## Key Design Decisions

### File Format: YAML (not JSON)
YAML is human-readable, diff-friendly, supports comments. Comments carry metadata (last updated, managed-by notice).

### One file per group (not one file per host)
Groups are the unit of management. Hosts inherit rules from groups. This matches the DB model.

### System rules in Git
System rules (SSH lockout) ARE written to Git files (marked `system: true`) but are never overwritten by git import. They're informational in Git.

### No force-push
All Git operations use regular commits. Rollback creates a new commit, not a revert of history. This preserves the audit trail.

### Webhook-first, polling-fallback
Webhooks are instant but require network access. Polling (every 5 min) is the fallback for air-gapped or self-hosted Git.

### gitpython vs subprocess git
Use `gitpython` for Python-native Git operations. Fall back to `subprocess` for complex operations (rebase, cherry-pick) if needed.

---

## New Dependencies

```toml
# pyproject.toml additions
"gitpython>=3.1",
```

---

## Estimated Effort

| Wave | Tasks | Effort |
|------|-------|--------|
| 1 | G1-G3 | M |
| 2 | G4-G5 | M |
| 3 | G6-G8 | L |
| 4 | G9-G10 | M |
| FINAL | GF1 | S |
| **Total** | **10 tasks** | **XL** |

Parallel speedup: ~50% (waves 1 and 4 are fully parallel within wave)
