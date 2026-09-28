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

ID counter as of last housekeeping pass: `BUG-97`, `SEC-35`,
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

- [ ] **BUG-96** `backend/app/actions/registry.py:163-191`,
      `backend/app/main.py:482-509`,
      `backend/app/tasks/__init__.py:126-158` — after a restart the API
      can be left serving the bundled actions only, because its registry
      reload races the Celery worker's and loses

      Symptom: after the upgrade, Actions › Registry lists only the six
      bundled keys, all won by `bundled`. The two actions from
      `labdog-private` (`docker-compose-update`, `nextcloud-vm-update`)
      are gone from the UI, and so is every key `labdog-playbooks` should
      win. Both packs show `enabled` and `synced`. The Celery worker's
      registry is correct: it logged `loaded 13 action(s) from 3
      pack(s)`, and `action_registry_snapshot` holds all eight keys with
      the private pack's two. Only the API process is wrong, so anything
      that goes through the API, including starting a run by hand,
      cannot see the private actions.

      Root cause: two processes rebuild the registry at startup at the
      same moment. The API's lifespan runs `_sync_packs_then_reload`, and
      the `work` worker's `worker_ready` hook runs the same sync and
      reload. Both end in `_persist_merge_outcome_async`, which does
      `DELETE FROM action_registry_snapshot` and then inserts every key.
      Under READ COMMITTED the second transaction's DELETE cannot see the
      rows the first one just committed, so its INSERT collides: the API
      logged `UniqueViolationError ... pk_action_registry_snapshot, Key
      (action_key)=(alloy-install)` and "action-pack startup sync
      failed; on-disk packs only". `reload_registry_async` calls
      `_install(result)` only after the persist succeeds, so the
      correctly merged registry is thrown away and the API keeps what it
      loaded before the sync. Nothing reloads it again until someone
      syncs a pack or changes a resolution in the UI.

      Why it showed on this upgrade: lin-manager has no volume on
      `/var/lib/labdog/packs` (only `claude-cli` is mounted), although
      `docs/production-deploy.md` lists `labdog_packs` as needed for git
      packs. Recreating the container emptied the checkouts, so the API's
      pre-sync load found only the bundled pack. With the volume, the
      same failed reload would have left the API on the previous
      checkout and hidden the bug. Whether a restart hits it at all
      depends on timing.

      Related: the API and the `work` worker also both run
      `sync_enabled_packs` into the same checkout directories at boot.
      Here the API cloned `packs/1` at 15:34:34 and the worker pulled it
      at 15:34:40, so they did not overlap this time. The orchestrator
      already skips the sync for this reason (docstring on
      `_sync_packs_on_worker_start`); the API and `work` worker do not.

      Fix direction:
      - Install the merged registry in memory before persisting, or
        whether or not persisting succeeds. The snapshot is bookkeeping;
        losing that write must not cost the process its registry.
      - Make the snapshot write safe under concurrency: take a
        transaction-level advisory lock around the persist (and
        preferably the whole sync-and-reload), or replace DELETE and
        INSERT with an upsert plus deleting keys no longer present.
      - Only one process should sync the git checkouts at boot. Let the
        `work` worker own it (or take the same advisory lock), and have
        the API reload from disk once the sync is done, or retry its
        reload on failure.
      - Tests: two concurrent `reload_registry_async` calls both finish
        with the full registry installed and a consistent snapshot; a
        failing persist still installs the merged registry.

      Workaround: press **sync** on any pack in Actions › Pack sources.
      That reloads the API's registry and brings the actions back until
      the next restart. Adding the `labdog_packs` volume on lin-manager
      makes a recurrence much less visible, but does not fix the race.

      Severity High: an upgrade or restart can silently remove every
      git-pack action from the UI and API with both packs reporting
      healthy, and it stays that way until someone happens to sync a
      pack.

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
