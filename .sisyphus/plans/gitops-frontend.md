# GitOps Frontend — Management UI for Git Repositories and GitOps Settings

## TL;DR

> **Quick Summary**: Add the missing frontend UI for GitOps management. Create a Git Repositories page (CRUD), add GitOps settings to group detail pages (enable/disable with repo + file path selection), add GitOps status to the groups list, and update the sidebar navigation.
>
> **Deliverables**:
> - Git Repositories management page (`/git-repos`) with create/edit/delete
> - GitOps settings section on group detail page (enable/disable, repo selection, file path, webhook URL display)
> - GitOps status column on groups list page
> - Sidebar navigation updated with "Git Repos" item
> - TypeScript interfaces for GitRepository
>
> **Estimated Effort**: Medium
> **Parallel Execution**: YES — 2 waves
> **Critical Path**: T1 → T2 → T3 → T4

---

## Context

### Current State
The backend has full GitOps support:
- `GET/POST/PUT/DELETE /api/git-repos` — CRUD for git repositories
- `POST /api/groups/{id}/gitops/enable` — link repo + file path to group
- `POST /api/groups/{id}/gitops/disable` — unlink
- `GET /api/groups/{id}/gitops/status` — current status
- `POST /api/git-repos/{id}/test-connection` — verify repo access
- `POST /webhooks/{github|gitlab|gitea}` — webhook receivers

The frontend currently has ONLY:
- A "Managed by GitOps" badge on group detail page
- Read-only state on rules page (disables add/edit/delete buttons)
- An info banner on rules page

What's MISSING:
- No way to create/manage Git repositories
- No way to enable/disable GitOps on a group through the UI
- No GitOps status visible on the groups list
- No sidebar navigation to Git Repositories
- No webhook URL display for setting up in Git hosting

---

## Work Objectives

### Core Objective
Give superusers a complete UI to manage GitOps — create git repos, link them to groups, see sync status, and get webhook URLs for setup.

### Definition of Done
- [ ] User can create a Git repository (name, URL, branch, auth type, credentials, webhook secret)
- [ ] User can see all Git repositories in a list with linked group count
- [ ] User can edit and delete Git repositories
- [ ] User can enable GitOps on a group (select repo, enter file path)
- [ ] User can disable GitOps on a group
- [ ] User can see GitOps sync status (synced/error/importing/disconnected) on group detail
- [ ] Groups list shows which groups are GitOps-managed
- [ ] Sidebar has "Git Repos" navigation item
- [ ] Frontend builds without errors

### Must NOT Have
- No webhook management UI (user configures webhooks in Git hosting, we just show the URL)
- No Git history viewer
- No YAML editor in the UI
- No git operations (clone/push) triggered from the UI

---

## Verification Strategy

> Frontend build + visual verification via Playwright

---

## Execution Strategy

### Parallel Execution Waves

```
Wave 1 (Foundation — 2 parallel):
├── Task 1: TypeScript interfaces + sidebar nav update [quick]
└── Task 2: Git Repositories management page [visual-engineering]

Wave 2 (Integration — 2 parallel):
├── Task 3: GitOps settings on group detail page [visual-engineering]
└── Task 4: GitOps status column on groups list page [quick]

Wave 3 (Verify):
└── Task 5: Build verification + visual check [quick]

Wave FINAL (Review — 2 parallel):
├── F1: Plan compliance audit [unspecified-high]
└── F2: Code quality review [unspecified-high]

Critical Path: T1 → T2 → T3 → T5
Max Concurrent: 2
```

### Dependency Matrix

| Task | Depends On | Blocks | Wave |
|------|-----------|--------|------|
| T1 | — | T2, T3, T4 | 1 |
| T2 | T1 | T5 | 1 |
| T3 | T1 | T5 | 2 |
| T4 | T1 | T5 | 2 |
| T5 | T2, T3, T4 | F1, F2 | 3 |

---

## TODOs

- [ ] 1. TypeScript Interfaces + Sidebar Navigation Update

  **What to do**:
  - Add to `frontend/lib/types.ts`:
    ```typescript
    export interface GitRepository {
      id: number
      name: string
      url: string
      branch: string
      auth_type: "ssh_key" | "https_token"
      ssh_key_id: number | null
      webhook_secret: string | null
      last_commit_sha: string | null
      last_sync_at: string | null
      created_at: string
      updated_at: string
    }

    export interface GitRepoCreate {
      name: string
      url: string
      branch?: string
      auth_type: "ssh_key" | "https_token"
      ssh_key_id?: number | null
      https_token?: string | null
      webhook_secret?: string | null
    }

    export interface GitOpsStatus {
      gitops_enabled: boolean
      git_repository_id: number | null
      gitops_file_path: string | null
      gitops_status: string
      gitops_error_message: string | null
      gitops_last_import_at: string | null
    }
    ```
  - Update `frontend/components/sidebar.tsx`: add "Git Repos" nav item with a git-branch icon, linking to `/git-repos`. Place it after "SSH Keys" in the nav order.

  **Recommended Agent Profile**:
  - **Category**: `quick`
  - **Skills**: []

  **Parallelization**: Wave 1 (parallel with T2) | Blocks: T2, T3, T4

  **References**:
  - `frontend/lib/types.ts` — existing interfaces to extend
  - `frontend/components/sidebar.tsx` — navigation component
  - `backend/app/schemas/git_repos.py` — response shapes to match

  **Acceptance Criteria**:
  - [ ] `GitRepository` and `GitRepoCreate` interfaces in types.ts
  - [ ] Sidebar shows "Git Repos" nav item
  - [ ] `npm run build` passes

  **Commit**: YES — `feat(ui): add GitRepository types and sidebar navigation`

- [ ] 2. Git Repositories Management Page

  **What to do**:
  - Create `frontend/app/(dashboard)/git-repos/page.tsx`:
    - **Header**: "Git Repositories" title + "Add Repository" button
    - **Table**: columns — Name, URL, Branch, Auth Type, Last Sync, Actions (edit/delete)
    - **Create/Edit Dialog**: form with fields:
      - Name (text input)
      - URL (text input, placeholder: `git@github.com:org/repo.git`)
      - Branch (text input, default: "main")
      - Auth Type (select: SSH Key / HTTPS Token)
      - If SSH Key: dropdown of available SSH keys (GET /api/ssh-keys)
      - If HTTPS Token: password input for token (only shown on create/edit, never displayed)
      - Webhook Secret (text input, optional — shown with copy button)
    - **Webhook URL display**: After creating a repo, show the webhook URLs:
      - GitHub: `https://{server}/webhooks/github`
      - GitLab: `https://{server}/webhooks/gitlab`
      - Gitea: `https://{server}/webhooks/gitea`
      - With copy-to-clipboard buttons
    - **Delete confirmation**: dialog asking "Are you sure? This will disconnect all linked groups."
    - **Linked groups count**: show how many groups use each repo
    - API calls: GET /api/git-repos, POST /api/git-repos, PUT /api/git-repos/{id}, DELETE /api/git-repos/{id}

  **Must NOT do**:
  - Do NOT show encrypted HTTPS tokens (backend never returns them)
  - Do NOT add test-connection button (placeholder in backend)

  **Recommended Agent Profile**:
  - **Category**: `visual-engineering`
  - **Skills**: [`frontend-ui-ux`]

  **Parallelization**: Wave 1 (parallel with T1) | Blocks: T5

  **References**:
  - `frontend/app/(dashboard)/ssh-keys/page.tsx` — very similar CRUD page pattern to follow
  - `frontend/app/(dashboard)/hosts/page.tsx` — table + action buttons pattern
  - `frontend/components/ui/dialog.tsx` — dialog component for create/edit
  - `frontend/lib/api.ts` — apiFetch pattern
  - `backend/app/schemas/git_repos.py` — exact request/response shapes

  **Acceptance Criteria**:
  - [ ] /git-repos page renders with table of repositories
  - [ ] Create dialog collects all fields including auth type toggle
  - [ ] Edit updates existing repository
  - [ ] Delete shows confirmation and removes
  - [ ] Webhook URLs displayed with copy buttons
  - [ ] SSH key dropdown populated from API
  - [ ] `npm run build` passes

  **QA Scenarios**:
  ```
  Scenario: Create and list a git repository
    Tool: Playwright
    Steps:
      1. Navigate to /git-repos
      2. Click "Add Repository"
      3. Fill: name="test-repo", url="git@github.com:test/rules.git", auth_type=SSH Key
      4. Submit
      5. Assert: table shows "test-repo" row
    Evidence: .sisyphus/evidence/task-2-git-repos.png
  ```

  **Commit**: YES — `feat(ui): add Git Repositories management page`

- [ ] 3. GitOps Settings on Group Detail Page

  **What to do**:
  - Modify `frontend/app/(dashboard)/groups/[id]/page.tsx`:
    - Add a **"GitOps" section/card** below the existing group info:
    - **When GitOps is disabled**:
      - Show "Enable GitOps" button
      - Clicking opens a dialog with:
        - Git Repository dropdown (GET /api/git-repos)
        - File path input (placeholder: "groups/my-group.yaml")
        - "Enable" button → POST /api/groups/{id}/gitops/enable
    - **When GitOps is enabled**:
      - Show status card with:
        - Status badge (synced/error/importing/disconnected) using existing GitOpsStatusBadge component
        - Repository name (linked to /git-repos)
        - File path
        - Last import timestamp (relative: "2 minutes ago")
        - Error message (red text, shown when status is "error")
        - Webhook info: "Configure your Git hosting webhook to POST to `/webhooks/{provider}`"
      - "Disable GitOps" button (with confirmation dialog: "Rules will remain but will no longer sync from Git")
    - Fetch GitOps status: GET /api/groups/{id}/gitops/status

  **Must NOT do**:
  - Do NOT add YAML editing capability
  - Do NOT add Git history viewing
  - Do NOT add manual import trigger (webhook handles this)

  **Recommended Agent Profile**:
  - **Category**: `visual-engineering`
  - **Skills**: [`frontend-ui-ux`]

  **Parallelization**: Wave 2 (parallel with T4) | Blocks: T5

  **References**:
  - `frontend/app/(dashboard)/groups/[id]/page.tsx` — page to modify (already has GitOps badge)
  - `frontend/components/status-badge.tsx` — GitOpsStatusBadge component
  - `backend/app/schemas/git_repos.py` — GitOpsEnableRequest, GitOpsStatusResponse shapes

  **Acceptance Criteria**:
  - [ ] Group detail page shows GitOps section
  - [ ] "Enable GitOps" opens dialog with repo + file path selection
  - [ ] Enabled state shows status, repo name, file path, last sync
  - [ ] Error state shows red error message
  - [ ] "Disable GitOps" works with confirmation
  - [ ] `npm run build` passes

  **QA Scenarios**:
  ```
  Scenario: Enable GitOps on a group
    Tool: Playwright
    Steps:
      1. Navigate to /groups/{id}
      2. Assert: "Enable GitOps" button visible
      3. Click "Enable GitOps"
      4. Select repository from dropdown
      5. Enter file path
      6. Submit
      7. Assert: GitOps status card appears with "disconnected" badge
    Evidence: .sisyphus/evidence/task-3-gitops-enable.png
  ```

  **Commit**: YES — `feat(ui): add GitOps settings section on group detail page`

- [ ] 4. GitOps Status Column on Groups List Page

  **What to do**:
  - Modify `frontend/app/(dashboard)/groups/page.tsx`:
    - Add a "GitOps" column to the groups table
    - Show status badge for GitOps-enabled groups (synced/error/importing/disconnected)
    - Show "—" for groups without GitOps
    - The HostGroup data already includes `gitops_enabled` and `gitops_status` fields

  **Recommended Agent Profile**:
  - **Category**: `quick`
  - **Skills**: []

  **Parallelization**: Wave 2 (parallel with T3) | Blocks: T5

  **References**:
  - `frontend/app/(dashboard)/groups/page.tsx` — groups list to modify
  - `frontend/components/status-badge.tsx` — GitOpsStatusBadge component

  **Acceptance Criteria**:
  - [ ] Groups table has "GitOps" column
  - [ ] GitOps-enabled groups show status badge
  - [ ] Non-GitOps groups show "—"
  - [ ] `npm run build` passes

  **Commit**: YES — `feat(ui): add GitOps status column to groups list`

- [ ] 5. Build Verification

  **What to do**:
  - Run `cd frontend && npm run build` — must pass with 0 errors
  - Verify all new pages appear in build output
  - Run ruff on any backend changes (shouldn't be any)

  **Recommended Agent Profile**:
  - **Category**: `quick`
  - **Skills**: [`git-master`]

  **Acceptance Criteria**:
  - [ ] `npm run build` exits 0
  - [ ] `/git-repos` appears in build output
  - [ ] All commits clean

  **Commit**: YES (if any fixes needed) — grouped commit for fixes

---

## Final Verification Wave

- [ ] F1. **Plan Compliance Audit** — `unspecified-high`
  Verify all Definition of Done items. Check all pages render. Check sidebar has nav item. Check groups list has GitOps column.

- [ ] F2. **Code Quality Review** — `unspecified-high`
  Run `npm run build`. Check TypeScript types match backend schemas. No `any` types. No hardcoded URLs.

---

## Commit Strategy

| Task | Commit Message | Key Files |
|------|---------------|-----------|
| T1 | `feat(ui): add GitRepository types and sidebar navigation` | `lib/types.ts`, `components/sidebar.tsx` |
| T2 | `feat(ui): add Git Repositories management page` | `app/(dashboard)/git-repos/page.tsx` |
| T3 | `feat(ui): add GitOps settings section on group detail page` | `app/(dashboard)/groups/[id]/page.tsx` |
| T4 | `feat(ui): add GitOps status column to groups list` | `app/(dashboard)/groups/page.tsx` |

---

## Success Criteria

### Verification Commands
```bash
cd frontend && npm run build  # Expected: exit 0, all pages in output
```
