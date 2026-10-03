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

ID counter as of last housekeeping pass: `BUG-108`, `SEC-38`,
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

_No bugs are currently open._

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

_No bugs are currently open._

---

## Open — 2026-10-02 found adding the SMTP password to key rotation

Filed 2026-10-02 while adding `smtp_settings.encrypted_password` to the
rotation script, and confirmed against source.

### Correctness — Medium

_No bugs are currently open._
