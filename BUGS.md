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

ID counter as of last housekeeping pass: `BUG-118`, `SEC-38`,
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

## Open — 2026-10-29 valheim

Filed 2026-10-29 while tracing why a hosts sync rewrote `/etc/hosts` on
the valheim game-server VM (BUG-115, since fixed), against lin-manager's
`sync_jobs` and audit rows.

### Correctness — High

- [ ] **BUG-116** `backend/app/sync/post_run.py:74-77`,
      `backend/app/tasks/host_sync_orchestrator.py:213-219` — a post-run
      sync that has to wait runs every module instead of the one it was
      queued for

      Symptom: alloy-install on valheim (action run 326, 2026-10-24 18:59
      UTC) queued its `post_run_register` follow-up syncs for `packages`
      and `services` (sync jobs 36 and 37). Both waited (BUG-117) and were
      dispatched at 2026-10-25 03:08 UTC, when linux-upgrade released the
      host. Their `sync_completed` audit rows show `module_filter: null`
      and an outcome for all seven modules: two full syncs, firewall and
      `/etc/hosts` included, that nobody asked for. They are the rewrite
      that BUG-115 traced.

      Root cause: `dispatch_post_run_sync` stores the canonical module name
      (`packages`) in `SyncJob.module_type` and leaves `module_filter`
      empty. The first dispatch passes `module_filter=[module]`, so a sync
      that runs at once is fine. A deferred one is re-dispatched from its
      row by `module_filter_for`, which falls back to
      `_filter_from_module_type`. That knows only the short database names
      (`package`, `service`, `hosts_file`, `linux_user`), logs "Unknown
      SyncJob.module_type", and returns `None`, which means every module.
      Only `firewall`, `cron` and `resolver`, whose two names agree, come
      through intact. The mismatch also stops `uq_sync_job_active` from
      deduplicating: a post-run `packages` row and an operator's `package`
      row for the same host do not collide.

      Fix direction: store `module_filter` on the row, write the short name
      to `module_type` as every other path does, and fail a job with an
      unknown `module_type` instead of widening it to every module.

      Severity High: an action run silently pushes every module's desired
      state to its host, firewall included, hours after it finished.

### Correctness — Medium

- [ ] **BUG-117** `backend/app/tasks/action_host.py:1181-1237`,
      `backend/app/tasks/action_host.py:1342-1357` — a post-run sync queued
      by an action waits for the next operation on the host, which can be
      hours away

      Symptom: sync jobs 36 and 37 (BUG-116) were created at 18:59 UTC on
      2026-10-24 and stayed `pending` on an otherwise idle valheim for eight
      hours, until linux-upgrade's release dispatched them at 03:08.

      Root cause (probable, not reproduced): `_phase_g_persist` dispatches
      the post-run syncs while the parent `ActionRun` is still `running`;
      it is closed later, by `finalise_run_if_complete` at the end of
      `_release_host`. `run_host_sync` claims without an
      `exclude_action_run_id`, so `check_host_busy` finds the parent run
      and defers. `_release_host` calls `release_host_queue` before
      `finalise_run_if_complete`, so the re-dispatched sync can find the
      parent still running and defer again. Nothing wakes it after that:
      `finalise_run_if_complete` does not release the host, and
      `sync_sweeper` reaps only `running` rows, never a `pending` one on a
      free host.

      Severity Medium: the follow-up lands at an arbitrary later time, for
      instance in the middle of the next night's maintenance run; with
      BUG-116 it lands as a full sync.

- [ ] **BUG-118** `backend/app/hosts_mgmt/merge.py:95`,
      `backend/app/hosts_mgmt/diff.py:34-35` — hosts entries that share an
      address collapse into one line, and the lower-priority names are
      dropped without a word

      Symptom: lin-manager's `add-hosts` group maps both
      `loki.lan.tyresson.se` (priority 100) and `mimir.lan.tyresson.se`
      (priority 101) to 10.10.200.3. nextcloud's effective view and its
      file preview carry only the mimir line, and nothing in the UI says
      why. Its `/etc/hosts` still has both: the file was last written
      2026-08-21, five minutes after LabDog's sync, apparently by hand. The
      next hosts sync there drops loki.

      Root cause: the merge is keyed on the resolved address (`first_wins`
      with the IP as key), so per address only the highest-priority entry
      survives and its names replace the others'. An `/etc/hosts` line may
      carry any number of names, and two lines for one address are valid
      too. The diff and the collector use the same key, so a host file with
      two lines for one address is also read as one.

      Workaround: put every name for an address in one entry, as hostname
      plus aliases.

      Severity Medium: a configured name silently does not resolve.
