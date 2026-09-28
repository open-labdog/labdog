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

ID counter as of last housekeeping pass: `BUG-94`, `SEC-35`,
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

- [ ] **BUG-89** `frontend/app/(dashboard)/settings/settings-editor.tsx:100-120`
      — `ssh.command_timeout` and `logging.drift_retention_days` render in
      "uncategorised" fallback cards instead of their SSH and Logging
      categories

      Symptom: Settings › Fleet shows two extra cards, "Ssh" and "Logging",
      each tagged `uncategorised` and holding one setting, beside the
      curated SSH and Logging cards. The settings still work and can be
      edited; they are just filed under the wrong heading.

      Root cause: both keys exist in `SETTING_DEFINITIONS`
      (`backend/app/settings_service.py:45`, `:103`) but were never added
      to the frontend's hand-kept `CATEGORIES` map. Of the 31 backend
      keys, these are the only two missing. This is the third time
      settings have drifted out of the map this way (the comment at
      `settings-editor.tsx:29-47` records the earlier two), and nothing
      checks for it.

      Fix direction: add `ssh.command_timeout` to `ssh.keys` and
      `logging.drift_retention_days` to `logging.keys`. To stop it
      recurring, either add a backend test that asserts every
      `SETTING_DEFINITIONS` key appears in `settings-editor.tsx`, or move
      the category into `SETTING_DEFINITIONS` and have the frontend group
      by the API's value.

      Severity Low: cosmetic, and the fallback keeps both settings
      reachable.

---

## Open — 2026-09-28 usability

Filed 2026-09-28 while scheduling the `nextcloud-vm-update` action.

### Usability — Low

- [ ] **BUG-93** `frontend/components/scheduled-actions/schedule-action-dialog.tsx:355-366`
      — "Schedule an action" lists hosts and groups in a plain dropdown,
      with no way to search them by typing

      Symptom: to pick the target host or group, the user has to open the
      list and scroll through the whole inventory. Typing does nothing
      useful. A native `<select>` offers only the browser's type-ahead,
      which jumps to the first option that starts with the typed prefix.
      It can't match text in the middle of a name, can't match the IP
      address after the `·`, and doesn't narrow the list. This gets worse
      as the fleet grows.

      Root cause: the host and group pickers are native `<select>`
      elements (`:356` and `:363`) that render every host
      (`hostname · ip_address`) and every group as an `<option>`.

      Fix direction: replace both with a searchable combobox that filters
      as the user types, on hostname, IP and group name. `cmdk` is already
      a dependency and powers the command palette
      (`frontend/components/shell/palette.tsx`), so a small shared picker
      built on it adds no new dependency. The same picker would fit the
      action picker in this dialog (`:332`) and the other host dropdowns
      (`frontend/app/(dashboard)/hosts/[id]/_tabs/config/firewall.tsx:215`,
      `:225`, and `hosts-file.tsx:198`).

      Severity Low: every target can still be picked, only slowly.
