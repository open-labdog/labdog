# Bug Registry

Open bugs in LabDog. New entries are added as bugs are surfaced.

## Convention: open-only

**Only open bugs belong in this file.** When a bug is fixed:

1. Land the fix and write a descriptive commit message that references
   the bug ID (e.g. `fix(sync): BUG-37 — dispatch Celery tasks after
   commit`). That commit message is the canonical record (symptom,
   root cause, fix).
2. Delete the entry from this file in the same commit. Do **not** mark
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

ID counter as of last housekeeping pass: `BUG-108`, `SEC-37`,
`TYPE-03`, `DEAD-01`. Pick the next number in the relevant series
when filing a new entry.

---

## Open

### Security findings — High

Filed 2026-05-21 from a `security-auditor` whitebox source-level
review (see the `code-audit` branch history for the baseline). Each entry was
spot-checked against current HEAD before filing.

_No bugs are currently open._

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

### Security — Medium

### Security — Low

- [ ] **SEC-35** Grafana, Loki, Mimir and AI-provider base URLs are
      scheme-checked but may point at loopback, RFC1918 or 169.254.169.254
      (`app/grafana/schemas.py:16-25`, `app/ai/schemas.py:25-33`). Close to
      blind — responses must parse as the expected shape and httpx does not
      follow redirects by default — and a documented homelab decision. Git
      repo URLs *do* block these (`app/schemas/git_repos.py:18-25`). Filed
      so it is not re-discovered as new, not because it needs changing.

### Correctness — High

_No bugs are currently open._

### Correctness — Medium

_No bugs are currently open._

---

## Open — 2026-09-27 production investigation

Filed 2026-09-27 from the lin-manager instance (`openlabdog/labdog:test`,
image built from `8516b144`). Confirmed against the running container,
not just read from source.

### Correctness — High

_No bugs are currently open._

### Correctness — Low

_No bugs are currently open._

---

## Open — 2026-09-28 lin-manager upgrade

Filed 2026-09-28 after upgrading lin-manager (container recreated
15:34 UTC, migration 0040 applied). Confirmed against the running
container, its database and its logs.

### Correctness — High

_No bugs are currently open._

### Usability — Low

- [ ] **BUG-97** `frontend/components/shell/zones.ts:160-175` — the
      Overview pane keeps "Summary" highlighted whichever view is open

      Symptom: in the Overview pane, clicking Pending, Fleet state,
      Activity or Upcoming changes the page, but the highlight stays on
      Summary. Reproduced against `next dev`: at
      `/overview/?view=state` the only link with `aria-current="page"`
      is Summary.

      Root cause: `next.config.ts` sets `trailingSlash: true`, so the
      pathname is `/overview/`, while the pane items' hrefs are
      `/overview` and `/overview?view=...`. In `itemIsActive`,
      `pathname !== itemPath` is always true. That branch treats the page
      as one beneath the item, which only matches items without a query:
      Summary is always active and the query items never are. Items
      without a query elsewhere (`/hosts`, `/runs`, ...) still work by
      the same accident, since `/hosts/` starts with `/hosts/`. Overview
      is the only zone whose items differ by query, so it is the only one
      visibly broken.

      Fix direction: normalise a trailing slash off `pathname` (and
      `itemPath`) before comparing in `itemIsActive`. Add a unit test for
      `itemIsActive` with a trailing-slash pathname, or extend an e2e
      spec to assert `aria-current` after clicking an Overview item.

      Severity Low: navigation works; only the highlight is wrong.

- [ ] **BUG-98** `frontend/app/(dashboard)/overview/client-page.tsx:119`,
      `frontend/components/scheduled-actions/scheduled-actions-list.tsx:84`,
      `frontend/components/scheduled-actions/schedule-action-dialog.tsx:121`
      — a schedule of a non-destructive action shows "snap" although no
      snapshot is ever taken

      Symptom: Overview › Scheduled shows a green "snap" tag on "Update
      Docker Compose images" (`docker-compose-update`, group `docker`).
      That action's manifest says `destructive: false`, with the comment
      "No Proxmox snapshot: the playbook waits for health itself and
      rolls back at the image level". No run of it has snapshotted:
      every `action_host_runs.snapshot_name` for runs 166-213 is empty.
      The tag reads as a promise of a rollback point that does not exist,
      and the panel's footer adds "Snapshot-backed ones are reversible."

      Root cause: the schedule dialog's initial state sets
      `snapshotEnabled: true` (also `verifyEnabled` and `autoRollback`),
      and shows the toggles only when `action.destructive`. For a
      non-destructive action the operator never sees them, and the
      defaults are saved anyway: schedule 2 on lin-manager has all three
      true, as do its runs. The runner is right to ignore them
      (`action_host.py:594` only snapshots when
      `spec.destructive and spec.snapshot_enabled`), but both the
      Overview row and the schedules list render the tag from
      `snapshot_enabled` alone.

      Fix direction: render "snap" only when the action is destructive
      *and* `snapshot_enabled`. The schedule response needs the action's
      `destructive` flag next to `action_name`
      (`app/api/scheduled_actions.py:160`). Also stop storing true for
      non-destructive actions: send `false` from the dialog, or have the
      API normalise the three flags when the action is not destructive.
      Optionally a migration clearing them on existing schedules of
      non-destructive actions. The run-action dialog probably defaults the
      same way (runs 166-213 all have `snapshot_enabled = t`); check it
      and the run view for the same tag.

      Severity Low: nothing runs differently, but the UI claims a
      rollback point for an unattended action that has none.

---

## Open — 2026-10-01 scheduled runs on lin-manager

Filed 2026-10-01 from lin-manager's action runs 235-241 (schedules 1
`linux-upgrade`, group "Default allow", 17 hosts, batch size 1; and 2
`docker-compose-update`, group "docker", 6 hosts, all also in "Default
allow"). Confirmed against the database, the container log and the
host's apt history.

### Correctness — High

_No bugs are currently open._

### Correctness — Medium

_No bugs are currently open._

---

## Open — 2026-10-01 found while fixing BUG-101

Filed 2026-10-01. BUG-102 and BUG-103 were found reading the host queue
for BUG-101 and reproduced in tests against Postgres; neither has shown
up on lin-manager yet, where no host-targeted run and no built-in group
run has deferred. BUG-104 is from lin-manager's runs 237 and 240.

### Correctness — Medium

- [ ] **BUG-102** `backend/app/tasks/host_lock.py:726-732`,
      `backend/app/tasks/action_orchestrator.py:336-346` — a host-targeted
      action that deferred behind a busy host fails with a duplicate-key
      error when the host frees up, instead of running

      Symptom: run an action on a host while a sync is running on it.
      The per-host task defers, and its row and the run both go
      `pending`. When the sync finishes, `dispatch_next_pending_for_host`
      re-sends `run_action` for the run, the orchestrator inserts the
      host's row a second time, and the insert fails on
      `uq_action_host_run`. The run ends `failed` with the
      IntegrityError text, and its row stays `pending` for good: the
      queue only re-fires rows whose run is still open.

      Root cause: the pick has two candidates for the same work, the run
      and its row, and both carry the run's `created_at`. The run is
      appended to the candidate list first, so it wins the tie. Re-sending
      the run is right for a group dispatch (`supports_host: false`),
      which defers before it has any rows; a host-targeted run defers
      per row and has to be resumed through that row.

      Fix direction: the run branch of the pick only takes pending runs
      with no `ActionHostRun` rows (`_has_no_host_runs()`, as in
      `check_host_busy`), so a host-targeted run comes back through its
      row. When that row claims the host, put a single-host parent that
      is `pending` back to `running` and clear its `pending_reason`, in
      both `action_host._claim_or_defer` and
      `builtin_dispatchers._begin_host_run`.

      Severity Medium: any host action that collides with another
      operation on its host fails instead of waiting its turn.

- [ ] **BUG-103** `backend/app/tasks/host_lock.py:751-753` — a deferred
      member of a group run of a built-in is re-dispatched to the
      pack-playbook runner, which cannot run it

      Symptom: `_builtin.drift_check` (or `_builtin.collect_state`,
      `_builtin.ai_task`) on a group where one member is busy: that
      member's row defers, and when the host frees up the queue sends
      `run_action_host(run, row)`. That is the Ansible runner for pack
      actions; the built-in has no playbook (`playbook_path` is `None`),
      so the member fails. A deferred `_builtin.sync` row is picked up by
      the same branch, although the SyncJob it queued is what is meant to
      close it (BUG-81).

      Root cause: the `action_host_run` branch hardcodes
      `run_action_host`. The orchestrator picks the per-host task from
      `PER_HOST_TASK_FOR_BUILTIN`.

      Fix direction: dispatch through the same lookup the orchestrator
      uses. Leave a `_builtin.sync` row alone when a pending SyncJob
      carries it as `origin_action_host_run_id`, because that job closes
      the row when it runs.

      Severity Medium: a built-in run against a group reports a member as
      failed whenever that member was busy when its turn came.

### Correctness — Low

_No bugs are currently open._

---

## Open — 2026-10-02 found while fixing BUG-96

Filed 2026-10-02 from lin-manager's schedules, its run history and its
container log, and confirmed against source.

### Correctness — High

_No bugs are currently open._

---

## Open — 2026-10-02 found investigating AI session 18

Filed 2026-10-02 from AI session 18 on lin-manager (a read-only chat
session on k8s-0) and the other sessions' refused calls, and confirmed
against `safety.py` and bash.

### Correctness — Medium

_No bugs are currently open._

### Correctness — Low

- [ ] **BUG-107** `backend/app/ai/safety.py:562-569` — `curl -s -o
      /dev/null -w '%{http_code}'`, the usual health check, is refused as
      a file write

      Symptom: session 12, an alert investigation on lin-manager, ran
      ``ss -tlnp 2>/dev/null | grep -E '8096|8920' ; curl -s -o /dev/null
      -w '%{http_code}\n' http://localhost:8096/health`` and was refused:
      "curl can write a file or upload local data with these options". The
      model was checking whether Jellyfin answered.

      Root cause: the curl gate in `_ARG_GATED_HEADS` matches any short
      flag cluster that contains `o`, `O` or `T`, and `--output`, because
      each of them names a file to write or upload. `-o /dev/null` names
      the one file that discards what it is given, and `-s -o /dev/null -w
      '%{http_code}'` is how a status code is read without the body.

      Fix direction: let `-o` and `--output` through when their operand is
      exactly `/dev/null`, and gate every other target as now. `-O` and
      `-T` stay gated; neither has a harmless form. Needs the operand, so
      this is a rule on the argument list, not on the flag cluster the
      regex reads today.

      Severity Low: it fails safe, and a model that is refused can use
      `curl -sI`, which is allowed, though that sends HEAD rather than GET.

---

## Open — 2026-10-02 found adding the SMTP password to key rotation

Filed 2026-10-02 while adding `smtp_settings.encrypted_password` to the
rotation script, and confirmed against source.

### Correctness — Medium

- [ ] **BUG-108** `backend/scripts/rotate_encryption_key.py:39-57` — key
      rotation skips `git_repositories.encrypted_webhook_secret`, so after
      a rotation every signed Git push webhook fails with a 500

      Symptom (from source, not yet seen live): after
      `python -m scripts.rotate_encryption_key`, a push webhook for any
      repository with a webhook secret reaches `get_webhook_secret`
      (`backend/app/gitops/webhook_secret.py:37-46`), which decrypts the
      unrotated ciphertext with the new key and raises `InvalidTag`. The
      three handlers in `backend/app/api/webhooks.py` (`:116`, `:162`,
      `:201`) do not catch it, so GitHub, GitLab and Gitea all get a 500,
      and GitOps sync on push stops for every such repository. The script
      reports success, and nothing in LabDog says why pushes stopped
      arriving.

      Root cause: `_build_column_registry` lists one column per secret, and
      SEC-28 (`40c75d6d`) moved the webhook secret into
      `encrypted_webhook_secret` without adding it. It lists
      `GitRepository.encrypted_https_token`, the other secret on the same
      row. `test_rotate_re_encrypts_all_columns` checks the registry's
      columns, not the model's, so it could not notice, and
      `docs/encryption-key-rotation.md` leaves it out of its list too.

      Fix direction: add `(GitRepository, "encrypted_webhook_secret",
      True)` to the registry, and the column to the doc's list. Then make
      the omission impossible to repeat: a test that collects every
      `encrypted_*` column on `Base.metadata` and fails if the registry
      does not name it. Until the fix, re-entering each repository's
      webhook secret after a rotation restores it.

      Severity Medium: rotation is rare and the repair is a re-entry per
      repository, but it fails silently and stops automation the operator
      relies on.
