# Bug Registry

Open bugs in LabDog. New entries are added as bugs are surfaced.

## Convention: open-only

**Only open bugs belong in this file.** When a bug is fixed:

1. Land the fix and write a descriptive commit message that references
   the bug ID (e.g. `fix(sync): BUG-37 — dispatch Celery tasks after
   commit`). That commit message is the canonical record (symptom,
   root cause, fix).
2. Delete the entry from this file in the same commit, and its heading
   and intro when it was the last entry under them. Do **not** mark
   bugs `[x]` and leave them here — fixed entries belong in git history,
   not in the registry.

To retrace a historical bug ID referenced elsewhere
(`BUG-NN`, `SEC-NN`, `TYPE-NN`, `DEAD-NN`), search the commit log:

```
git log --grep BUG-37
git log -- backend/app/api/sync.py
```

## How to file an entry

Format each entry as:

    - [ ] **BUG-NN** `path/to/file.ext:LINE` — one-line summary

      Symptom, root cause, severity tier (Critical / High / Medium /
      Low). If reproduced from a specific scenario, note it. Group
      related bugs under the same severity heading.

ID counter as of last housekeeping pass: `BUG-142`, `SEC-51`,
`TYPE-03`, `DEAD-01`. Pick the next number in the relevant series
when filing a new entry. (`BUG-127` to `BUG-133` are taken on the
PR #270 branch, not yet on `dev`.)

---

## Open — 2026-11-08 security and bug audit

Filed 2026-11-08 from a whitebox review of the backend at `9e0f8b0`
(`dev`), aimed at injection, authentication, data handling, logic bugs,
edge cases and races. BUGS.md on every open branch was read first, so
nothing here repeats an entry on `dev` or on the PR #270 branch
(`BUG-127` to `BUG-133`). Entries marked *Reproduced* were run against
the real code (ansible-core 2.21.1 from `uv.lock` where Ansible is
involved); the rest were traced by reading. Each carries a fix direction
for the fix thread; none is fixed here.

### Security — Low

- [ ] **SEC-48** `backend/app/api/webhooks.py:140,180`,
      `backend/app/tasks/gitops.py:54,86` — a webhook deploys whatever
      commit its payload names

      Symptom: the importer reads files at the payload's `after` /
      `checkout_sha` and only skips a SHA equal to the last one. A
      replayed genuine GitHub delivery (the signature covers the body, and
      there is no delivery-id or age check), or any request carrying the
      static GitLab token, rolls the groups back to an older commit on the
      branch, such as one before a firewall rule was tightened.

      Fix direction: treat the webhook as a trigger only: fetch the branch
      and import its tip, ignoring the payload SHA, or require the SHA to
      equal the fetched tip. Record `X-GitHub-Delivery` ids to refuse
      replays.

      Severity Low: needs the GitLab token or a captured delivery.

- [ ] **SEC-50** `backend/app/crypto/encryption.py:21,35` — stored secrets
      are encrypted without binding them to their row

      Symptom: `context_id` (the AES-GCM associated data) is never passed
      by any caller, so a ciphertext copied from one row to another
      decrypts fine; someone with database write access can swap which SSH
      key a host or repo uses without knowing the master key.

      Fix direction: pass `"<table>:<id>:<column>"` as `context_id` on
      encrypt and decrypt, with a migration that re-encrypts existing rows
      (try with context, fall back to none, rewrite).

      Severity Low: needs database write access.

### Correctness — Low

- [ ] **BUG-139** `backend/app/api/actions.py:236-252` — the duplicate-run
      guard only catches requests that arrive at the same instant

      Symptom (by reading): the `pg_try_advisory_xact_lock` is released
      at the request's commit, so a second request a moment later starts
      a second run of the same action on the same target while the first
      is queued or running. The key `host_id or group_id` also makes host
      5 and group 5 share a lock.

      Fix direction: check for an existing queued or running `ActionRun`
      for the action and target under the lock in every case, not only
      when the lock is busy, and put the target kind in the key
      (`actions.<key>.host.<id>` / `.group.<id>`).

      Severity Low: two runs of one action on one target, serialised by
      the host queue but not refused.

- [ ] **BUG-140** `backend/app/api/admin_users.py:115-123`,
      `backend/app/main.py:703-706` — the last-superuser guard can be
      sidestepped

      Symptom (by reading): `update_user` refuses demoting the last
      superuser but not deactivating them (`is_active: false`), and it
      counts inactive superusers as "others". The fastapi-users router
      mounted at `/api/users` also offers `PATCH` and `DELETE
      /api/users/{id}` to superusers, with neither the last-superuser nor
      the "cannot delete yourself" check.

      Fix direction: count only active superusers, apply the guard to
      `is_active=False` as well, and mount the users router without the
      `{id}` routes (or override them) so `admin_users` is the only path.

      Severity Low: a superuser can lock everyone out of user
      administration, recoverable from the database.

- [ ] **BUG-141** `backend/app/api/webhooks.py:56-67`
      (`_find_repo_by_url`) — two repositories with the same URL break
      every webhook for it

      Symptom (by reading): two `GitRepository` rows for one URL (two
      branches of one repo is a normal setup) make `scalar_one_or_none()`
      raise `MultipleResultsFound`, so every delivery answers 500.

      Fix direction: select all rows for the URL and keep those whose
      `branch` matches the push's ref, verifying the signature per row.

      Severity Low: webhooks stop for that repository; manual sync works.

---

## Open — 2026-09 whitebox audit

Filed 2026-09-03 from a full whitebox review (security, correctness,
performance) of the backend, frontend, packaging and CI. Every entry was
verified against source at `c784afb` before filing; the ones already
fixed are absent rather than ticked, per the open-only convention.

Findings from the same pass that have already landed are absent rather
than listed: the reflected XSS in the SPA dynamic-route rewrite, the AI
command-classifier bypasses, action-parameter template injection, pack
manifest path containment, and the quick-win batch (unauthenticated
resolver reads, registration privilege flags, `retention_days=0` wiping
the audit log, the scheduler tick poisoning itself, two temp-dir leaks,
the sync-tray request loop, the CSRF-less password change). Search the
log: `git log --grep "SEC"`.

**Note on the privilege model.** LabDog is deliberately flat — every
authenticated user has the same permissions and `is_superuser` gates only
user administration. That is a confirmed design decision, not a finding.
Its consequence shapes the severities below: the AI classifier, the
action-parameter validator and pack content policy are the *only* controls
protecting host root from an ordinary account. All three are now in
place: the classifier hardening, the extra-vars validator, and the pack
content policy.

### Security — Low

- [ ] **SEC-35** Grafana, Loki, Mimir and AI-provider base URLs are
      scheme-checked but may point at loopback, RFC1918 or 169.254.169.254
      (`app/grafana/schemas.py:16-25`, `app/ai/schemas.py:25-33`). Close to
      blind — responses must parse as the expected shape and httpx does not
      follow redirects by default — and a documented homelab decision. Git
      repo URLs *do* block these (`app/schemas/git_repos.py:18-25`). Filed
      so it is not re-discovered as new, not because it needs changing.
