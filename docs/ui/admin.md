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

**Path:** `/users` (Settings › Access) — superusers only

LabDog user accounts. Anyone else who opens the page is told it is for
administrators rather than shown an empty table.

| Column | Description |
|--------|-------------|
| user | The email address they sign in with; **you** marks your own row |
| status | `active` or `inactive` — an inactive account cannot sign in |
| role | **superuser** — manages accounts, integrations and settings |
| created | When the account was created |

Each row has **edit** (email, and whether the account is active), **reset
password** and **delete**. **New user…** creates an account from an email,
a password typed twice, and whether it is a superuser — the role is chosen
at creation; the screen has no promote or demote. The search box filters by
email; the **status** (active / inactive) and **role** (superuser / user)
chips narrow the list.

### First user

The first account to register is made a superuser automatically.
Registration is open only until that first account exists; after that, new
accounts are created here by a superuser.

### Resetting a password

**reset password** sets a new password for the user. They are not notified
— tell them out of band.

### Deleting

You cannot delete your own account, and the last superuser cannot be
deleted — there is always at least one.

### Changing your own password

Use **Change password…** in the account menu behind the avatar at the foot
of the rail. Every user has it; it does not need superuser access.
