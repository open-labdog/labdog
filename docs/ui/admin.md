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

An append-only log of every change made through LabDog — API writes, sync runs, and terminal session events.

### Columns

| Column | Description |
|--------|-------------|
| Timestamp | When the action occurred |
| User | Which LabDog user performed the action |
| Action | What happened (e.g. `rule.create`, `sync.apply`, `terminal.open`) |
| Entity | What was acted on (e.g. `group:4`, `host:tester3`) |
| Before | State before the change (JSON) |
| After | State after the change (JSON) |

### Filtering

Use the filter bar to narrow by user, action type, or date range. The table paginates with cursor-based pagination — scroll to load more.

### Retention

Audit entries older than the configured retention period are pruned automatically. The retention period is set in [Settings](settings.md) (`logging.audit_retention_days`, default 90 days).

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
