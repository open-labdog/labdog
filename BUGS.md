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

ID counter as of last housekeeping pass: `BUG-114`, `SEC-38`,
`TYPE-03`, `DEAD-01`. Pick the next number in the relevant series
when filing a new entry.

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

---

## Open — 2026-09-28 lin-manager upgrade

Filed 2026-09-28 after upgrading lin-manager (container recreated
15:34 UTC, migration 0040 applied). Confirmed against the running
container, its database and its logs.

### Usability — Low

- [ ] **BUG-98** `frontend/app/(dashboard)/overview/client-page.tsx:119`,
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
      (`action_host.py:613` only snapshots when
      `spec.destructive and spec.snapshot_enabled`), but the Overview row
      (`SchedRow`) renders the tag from `snapshot_enabled` alone. The
      Schedules list is not affected: it shows its snap/verify/rollback
      tags only when `r.destructive`.

      Fix direction: render "snap" on the Overview only when
      `s.destructive && s.snapshot_enabled`. The schedule response already
      carries `destructive` (`app/api/scheduled_actions.py:164`, typed in
      `lib/types.ts`), so this needs no API change. Also stop storing true
      for non-destructive actions: send `false` from the dialog, or have
      the API normalise the three flags when the action is not
      destructive. Optionally a migration clearing them on existing
      schedules of non-destructive actions. Ad-hoc runs need nothing: the
      run dialog sends no flags (the column defaults to true, which the
      runner ignores for a non-destructive action), and the run view shows
      no "snap" tag.

      Severity Low: nothing runs differently, but the UI claims a
      rollback point for an unattended action that has none.

---

## Open — 2026-10-04 housekeeping pass

Filed 2026-10-04 while checking this file and TODO.md against `dev`
at `f1583444`. BUG-109 was first noticed investigating AI session 18
and is confirmed against lin-manager's database.

### Correctness — Low

- [ ] **BUG-109** `backend/app/ai/agent_sdk/runner.py:456-469` — a
      command refused on the Claude Agent SDK path is recorded without
      its host

      Symptom: on lin-manager, none of the 6 `run_ssh_command` calls with
      status `blocked` has a `target_host_id`, while all 18 executed
      calls and both errored ones do. The instance's only provider is
      `claude_agent`. The audit trail shows that a command was refused,
      but not which host it was for.

      Root cause: this path refuses in the SDK permission callback
      (`_can_use_tool`), before the tool runs, and its `record_refusal`
      writes the `AIToolCall` without `target_host_id`. The API path
      refuses inside `tools/ssh.py`, whose `ToolResult` carries
      `target_host_id=host_id`, and `loop.py` copies it onto the record.

      Fix direction: set `target_host_id` in `record_refusal` from the
      call's `host_id`, but only when it is an int in the session's
      `target_host_ids`. The column is a foreign key to `hosts.id`, so a
      made-up id would fail the insert and lose the record. Add a test
      that a refused SDK call records its host.

      Severity Low: nothing runs that should not; the record is
      incomplete.
