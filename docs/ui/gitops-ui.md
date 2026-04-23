# GitOps UI

GitOps lets a Git repository drive all configuration for a group. When enabled, LabDog pulls YAML from the repo on every push (via webhook) and imports it as the group's desired state.

For the YAML schema and file format, see the [GitOps guide](../examples/gitops/README.md).

---

## Git Repos Page

**Path:** `/git-repos`

Lists all connected repositories. Each row shows:

| Column | Description |
|--------|-------------|
| Name | Label for this repo connection |
| URL | Clone URL (SSH or HTTPS) |
| Branch | Branch LabDog tracks |
| Groups | How many groups use this repo |
| Last Import | When the repo was last pulled and imported |
| Status | `ok`, `error`, or `never` |

### Adding a Repository

Click **Add Repository**. Fields:

| Field | Notes |
|-------|-------|
| Name | Display label |
| URL | `git@github.com:org/repo.git` or `https://github.com/org/repo` |
| Branch | Usually `main` or `master` |
| Auth method | **SSH key** (select from stored keys) or **HTTPS token** |
| Token / Deploy key | Credentials for private repos |

After saving, the **Webhook URL** is shown on the repo's detail row. Copy it into your GitHub / GitLab / Gitea project settings to enable push-triggered imports.

### Webhook Setup

| Platform | Where to add |
|----------|-------------|
| GitHub | Settings → Webhooks → Add webhook |
| GitLab | Settings → Webhooks |
| Gitea | Settings → Webhooks |

Set the payload URL to the webhook URL shown in LabDog, content type to `application/json`, and configure the secret if shown. Only `push` events are needed.

---

## Enabling GitOps on a Group

Open the group's detail page (`/groups/{id}`). In the **GitOps** card, click **Enable**. A dialog asks you to:

1. Select a repository (must already be added on the Git Repos page)
2. Enter the path to the YAML file within the repo (e.g. `groups/web-servers.yaml`)

After enabling:

- A **GitBranch banner** appears at the top of every module tab
- **Add**, **Edit**, and **Delete** controls are hidden — the repo is the source of truth
- An immediate import is triggered from the current HEAD of the branch

### Disabling GitOps (Break-glass)

If you need to make emergency edits directly in the UI:

1. Open the group detail page
2. In the GitOps card, click **Disable**
3. Confirm. The mutation lock is lifted and all controls reappear.

> The repo is **not** deleted — you can re-enable GitOps at any time. The database state is whatever was last imported from the repo (disabling does not revert anything).

---

## Import Flow

When a push arrives via webhook:

```
Git push → Webhook → Parse & validate YAML
         → Advisory lock on group
         → Fan out to per-module handlers (firewall, services, packages, …)
         → All modules updated in one transaction
         → Audit log entry written
         → Sync triggered (if auto-sync enabled)
```

If the YAML is invalid or a module handler fails, the entire import is rolled back and an error is recorded on the repo row.

### Missing Sections

What happens when a module section is absent from the YAML file:

| Module | Behaviour when section omitted |
|--------|-------------------------------|
| Firewall rules | All rules for this group are **wiped** |
| Services | All service rules **wiped** |
| Packages | All package rules **wiped** |
| Hosts entries | All entries **wiped** |
| Cron jobs | All cron jobs **wiped** |
| Linux users | All users **wiped** |
| DNS resolver | Configuration **left untouched** (singleton — omit = no change) |

See the [GitOps guide](../examples/gitops/README.md#missing-section-semantics) for details.
