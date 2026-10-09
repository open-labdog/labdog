# GitOps UI

GitOps lets a Git repository drive a group's configuration. When a group is
bound to a file in a repository, every push to that repository (delivered by
webhook) imports the file as the group's desired state and syncs the group's
hosts.

For the YAML schema and file format, see the
[GitOps guide](../examples/gitops/README.md).

---

## Git repositories

**Path:** `/git-repos` (Settings › Integrations › Git remotes)

Every connected repository — used for GitOps and for git-backed
[action packs](actions.md#action-packs).

| Column | Description |
|--------|-------------|
| name | Label for this repository |
| url | Clone URL (SSH or HTTPS) |
| branch | The branch LabDog tracks |
| auth | `ssh`, `https` (token) or `public` |
| groups | How many groups are bound to it |
| last sync | *synced*, *stale* (over a day ago) or *never synced*, with how long ago. It counts the last GitOps import and the last successful sync of any action pack on the repository, whichever is newer |

Each row has **webhooks**, **edit** and **delete**; the row itself opens the
repository's page. **webhooks** shows the three push URLs —
`/api/webhooks/github`, `/gitlab` and `/gitea` on your LabDog address — each
with a copy button.

### Connecting a repository

**Add Repository** opens a three-step wizard at `/git-repos/new`:

1. **Connect** — a name, the URL (SSH or HTTPS; the auth fields follow the
   scheme), the branch, and credentials: the SSH key LabDog uses as a
   deploy key for an SSH URL, or a personal access token for HTTPS (blank
   for a public repository). An optional **webhook secret** lets a push be
   verified. Connecting clones the repository.
2. **Scan** — LabDog looks through the clone for action packs and GitOps
   files. **Cancel & remove repo** backs out.
3. **Review & activate** — two lists of checkbox rows:
   - **gitops files** — each file that declares a group (`group: web`), to
     bind to that group. A group already bound to another repository is
     marked **already bound** and cannot be ticked until GitOps is disabled
     on it.
   - **action packs** — each pack found, marked **contested** when one of
     its action keys already exists in another pack, and **conflict** when
     two packs in this repository contribute the same key. **resolve
     action-key conflicts** lets you pick the winner for each contested key.

   **Activate** binds the ticked groups and registers the ticked packs.

### A repository's page

**Path:** `/git-repos/{id}`

- **connection** — branch, auth, the last commit LabDog fetched and when
  (by a GitOps import or a pack sync, whichever is newer), and whether a
  webhook secret is set.
- **gitops-bound groups** — each group, the file it imports from, and its
  import status.
- **action packs** — each pack from this repository, its path and state.

**Re-scan** clones the repository again and looks for new packs and GitOps
files.

### Webhook setup

| Platform | Where to add |
|----------|-------------|
| GitHub | Settings → Webhooks → Add webhook |
| GitLab | Settings → Webhooks |
| Gitea | Settings → Webhooks |

Set the payload URL to the matching URL from **webhooks**, the content type
to `application/json`, and the secret to the repository's webhook secret if
you set one. Only `push` events are needed.

> **A webhook is required.** Imports run only when a push arrives — LabDog
> does not poll the repository, and there is no import button. A group
> bound to a repository without a webhook keeps the state it had.

---

## Enabling GitOps on a Group

On the group's page (`/groups/{id}`), the **gitops** panel on the Overview
tab has **Enable…**. The dialog asks for:

1. the **repository** (it must already be connected), and
2. the **file path** of the group's YAML within it (e.g.
   `groups/web-servers.yaml`).

Enabling binds the group; the file is imported on the next push. From then
on:

- The group's head carries a **gitops** tag, coloured by import status, and
  the panel shows the status, repository, file and last import.
- Every module editor on the Config tab is **read-only**, with a banner
  saying the repository is the source of truth.

### Disabling GitOps (break-glass)

To edit the group directly again, **Disable GitOps** in the same panel. The
editors unlock immediately.

> Nothing is deleted — not the repository, and not the group's items, which
> stay as they were last imported. You can enable GitOps again at any time.

---

## Import Flow

When a push arrives:

```
Git push → Webhook → Clone at the pushed commit
         → For each group bound to this repository:
             read its file → parse & validate the YAML
             → fan out to per-module handlers (firewall, services, packages, …)
             → audit entry
         → Sync every host of each imported group
```

If a group's file is missing or invalid, or a module fails to import, the
group's GitOps status shows **error** with the message. Each bound group is
handled on its own, so one bad file does not stop the others.

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
