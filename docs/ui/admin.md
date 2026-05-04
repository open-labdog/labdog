# Admin

---

## SSH Keys

**Path:** `/ssh-keys`

Stores the SSH private keys LabDog uses to connect to managed hosts. Keys are encrypted at rest using AES-256-GCM with the `LABDOG_SECURITY__ENCRYPTION_KEY` from your environment.

### Adding a Key

Click **Add SSH Key**. Paste the private key in PEM format. Passphrase-protected keys are not supported — LabDog requires unencrypted private keys so it can use them from Celery workers without user interaction.

After adding a key you can assign it to hosts on the host edit form, or select it as the default key during host discovery.

### Key Details

Each key row shows:
- **Name** — label you assigned
- **Fingerprint** — the public key fingerprint (computed on upload, never exposes the private key)
- **Hosts** — count of hosts using this key

> Deleting a key that is still referenced by a host is blocked. Remove or reassign those hosts first.

---

## Git Repos

**Path:** `/git-repos`

Manages Git repository connections used for GitOps-driven configuration. See [GitOps UI](gitops-ui.md) for the full workflow.

---

## Audit Log

**Path:** `/audit`

An append-only log of every change made through LabDog. Events come
from API writes (group/host/module mutations, GitOps repo CRUD,
action-pack mutations, settings changes), sync and workflow runs,
discovery runs, and terminal session lifecycle events.

Sync events come in pairs: `sync_triggered` (at API entry — records
the operator's intent and the requested `module_filter`) and
`sync_completed` or `sync_failed` (at orchestrator finish — carries
a composite `{module: outcome}` payload covering every module that
ran). One pair per `SyncJob`, regardless of whether the sync was
multi-module bulk or single-module per-tab.

### Columns

| Column | Description |
|--------|-------------|
| Timestamp | When the action occurred |
| User | The acting user's email (joined from `users` for display); `system` for non-user events like scheduled drift checks |
| Action | What happened (e.g. `rule.create`, `sync_triggered`, `sync_completed`, `sync_failed`, `pack.sync`, `gitops.repo.create`, `terminal.open`) |
| Entity | What was acted on (e.g. `group:4`, `host:tester3`, `pack:gh-internal`) |
| Before | State before the change (JSON, secrets scrubbed) |
| After | State after the change (JSON, secrets scrubbed) |

The integer `user_id` is preserved internally (used for filtering and
linking back to the user detail page) but the table shows `user_email`
for legibility.

### Filtering

Use the filter bar to narrow by user, action type, or date range. The
table paginates with cursor-based pagination — scroll to load more.

### Retention

Audit entries older than the configured retention period are pruned
automatically. The retention period is set in
[Settings](settings.md) (`logging.audit_retention_days`, default 90
days).

---

## Users

**Path:** `/users` — superuser only

Manage LabDog user accounts. This page is only visible to superusers.

### User Table

| Column | Description |
|--------|-------------|
| Email | Login credential |
| Superuser | Whether this user has admin access |
| Active | Whether the account can log in |
| Actions | Edit, reset password, delete |

### First User

The first user to register is automatically promoted to superuser. Registration is open only until the first account exists — after that, new accounts must be created by an existing superuser from this page.

### Resetting a Password

Click **Reset Password** on any user row. You'll be prompted to enter a new password. The user does not receive any notification — inform them out of band.

### Deleting a User

Deleting your own account or the last superuser account is blocked. At least one superuser must always exist.

### Changing Your Own Password

Use **Change Password** in the sidebar footer (visible to all users). This does not require superuser access.
