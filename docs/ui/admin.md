# Admin

SSH keys and users live under **Settings › Access**; the audit trail is in
the Operations zone.

---

## SSH Keys

**Path:** `/ssh-keys` (Settings › Access)

The credentials LabDog connects to managed hosts with. Each key carries the
user it logs in as. Private keys are encrypted at rest with AES-256-GCM
using `LABDOG_SECURITY__ENCRYPTION_KEY`, and never shown again after upload.

### Adding a key

**Upload key…** asks for a name, the SSH user (default `root`), and the
private key in PEM or OpenSSH format. Passphrase-protected keys are not
supported — LabDog uses keys from background workers with nobody there to
type a passphrase. Tick **set as the default key** to make it the one new
hosts get.

### The key list

| Column | Description |
|--------|-------------|
| name | The label you gave it, a **default** tag on the default key, and the start of the public key |
| user | The SSH user the key logs in as |
| hosts | How many hosts use this key |
| added | When it was uploaded |

Each row has **edit** (name, user, default) and **delete**; ticking rows
offers **Delete selected**. A host with no key of its own uses the default.

> Deleting a key that a host still uses is refused. Reassign those hosts
> first — the host page's **edit**.

Assign a key to a host on the host's **edit** dialog, or pick one when
adding hosts from [Discovery](hosts.md#discovery).

---

## Git Repos

**Path:** `/git-repos` (Settings › Integrations)

Git repository connections for GitOps-driven configuration and git-backed
action packs. See [GitOps UI](gitops-ui.md) for the full workflow.

---

## Audit Log

The audit log is in the Operations zone — see
[Operations › Audit](operations.md#audit).

---

## Users

**Path:** `/users` (Settings › Access) — admins only

Every account has one of three roles. The role decides what the account can
do everywhere: on every page, through the API, and in the SSH terminal.

| Role | Can |
|------|-----|
| **viewer** | See hosts, groups, configuration, drift, runs, dashboards and the audit log. Change nothing. |
| **operator** | Everything a viewer can, plus: edit host and group configuration, plan and run syncs, run discovery and scans, run actions, manage scheduled actions, open the SSH terminal, use the AI assistant and approve its changes, and set up alert-triggered actions. |
| **admin** | Everything an operator can, plus: integrations that hold credentials (git repositories, action packs and pack winners, Proxmox nodes, Grafana, notification email, AI providers, SSH keys), application settings, and user accounts. |

SSH session transcripts in the audit log need the operator role: a
transcript is everything typed and printed in a root shell.

A role change, disabling an account, and a password reset all end that
account's sessions at once; the user signs in again and gets the new rights.

Accounts that existed before roles were added kept what they could do:
superusers became admins and everyone else became an operator.

### Rules that always hold

- At least one active admin remains. Demoting, disabling or deleting the
  last one is refused.
- Nobody changes their own role, disables themselves, or deletes their own
  account.

### First user

The first account to register becomes an admin. Registration is open only
until that first account exists; after that, admins create accounts here.

### New accounts and password resets

An admin who creates an account or resets a password chooses that password,
so by default the user must replace it at their next sign-in. Until they do,
everything except changing their password is refused. The user is not
notified; tell them out of band. New accounts are viewers unless a role is
chosen.

### Changing your own password

Use **Change password…** in the account menu behind the avatar at the foot
of the rail. Every role has it. It asks for your current password, and it
signs you out everywhere, including the browser you are using.

### Recovering access from the server

When no admin can sign in, use the command line on the server (inside the
container for Docker installs):

```bash
python -m app.auth.users_cli list
python -m app.auth.users_cli set-role someone@example.com admin
python -m app.auth.users_cli reset-password someone@example.com
python -m app.auth.users_cli create new@example.com --role admin
```

Passwords are read from a prompt, or from standard input with
`--password-stdin`, never from the command line. Every command writes an
audit row. `set-role` also re-enables the account and still refuses to leave
no active admin.
