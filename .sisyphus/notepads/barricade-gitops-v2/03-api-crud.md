# Task 3: GitRepository CRUD API + GitOps Enable/Disable

## What was done
- Created `backend/app/schemas/git_repos.py` with GitRepoCreate, GitRepoUpdate, GitRepoResponse, GitOpsEnableRequest, GitOpsStatusResponse
- Created `backend/app/api/git_repos.py` with full CRUD:
  - GET /api/git-repos — list all repos (superuser)
  - POST /api/git-repos — create repo with encrypted HTTPS token (superuser)
  - GET /api/git-repos/{id} — get single repo (superuser)
  - PUT /api/git-repos/{id} — update repo (superuser)
  - DELETE /api/git-repos/{id} — delete repo, blocked if groups linked (superuser)
  - POST /api/git-repos/{id}/test-connection — placeholder endpoint (superuser)
- Added 3 GitOps endpoints to `backend/app/api/groups.py`:
  - POST /api/groups/{id}/gitops/enable — links group to git repo (superuser)
  - POST /api/groups/{id}/gitops/disable — unlinks group, preserves rules (superuser)
  - GET /api/groups/{id}/gitops/status — returns gitops config+status (active user)
- Registered git_repos_router in main.py

## Security
- `encrypted_https_token` is NEVER in GitRepoResponse
- HTTPS token encrypted via AES-256-GCM before storage (same pattern as SSH keys)
- All write endpoints require superuser

## Verification
```
git-repos routes: ['/git-repos', '/git-repos', '/git-repos/{repo_id}', '/git-repos/{repo_id}', '/git-repos/{repo_id}', '/git-repos/{repo_id}/test-connection']
app routes: ['/api/groups/{group_id}/gitops/enable', '/api/groups/{group_id}/gitops/disable', '/api/groups/{group_id}/gitops/status', '/api/git-repos', '/api/git-repos', '/api/git-repos/{repo_id}', '/api/git-repos/{repo_id}', '/api/git-repos/{repo_id}', '/api/git-repos/{repo_id}/test-connection']
91 passed in 10.15s
```
