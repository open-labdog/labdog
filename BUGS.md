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

ID counter as of last housekeeping pass: `BUG-92`, `SEC-35`,
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

- [ ] **BUG-88** `backend/app/auth/ws_auth.py:32-42`,
      `backend/app/api/ssh_terminal.py:35-37` — the web terminal refuses
      the UI's own origin unless `security.allowed_origins` lists it, and
      says so nowhere

      Symptom: every terminal session, on every host, ends before it
      starts with "Connection failed: Closed (1006)". Reproduced on
      lin-manager 2026-09-27, UI at `https://labdog.lan.tyresson.se`
      behind nginx: a terminal handshake through the proxy is answered
      403, and `check_ws_origin` evaluated with the live settings returns
      False for `https://labdog.lan.tyresson.se` and True only for
      `http://localhost:3000`.

      Root cause: SEC-34 (`99860707`, 2026-09-06) made the terminal check
      the handshake's `Origin` against `security.allowed_origins` before
      accepting. That setting defaults to `["http://localhost:3000"]`
      (`app/config.py:84`; `packaging/etc/labdog.toml:72` ships the same),
      and until SEC-34 a deployment whose UI and API share an origin never
      needed it, because CORS only applies cross-origin. So an install
      that never set it, like lin-manager, whose compose file passes no
      `LABDOG_SECURITY__ALLOWED_ORIGINS`, lost the terminal on upgrade.
      The 0.10.0 notes in `docs/upgrade.md` do not mention it.

      Why it was hard to see: the refusal happens before `accept()`, which
      the ASGI server answers with an HTTP 403, so the browser gets no
      close frame and reports 1006 with no reason. The handler's
      `4403 "Origin not allowed"` never reaches the page, which the SEC-34
      comment accepts as the price of authenticating before the
      handshake. Nothing is logged either: the handler returns silently,
      and no access line reaches the container log, so neither the UI nor
      the logs say what happened.

      Fix direction: treat a same-origin handshake as allowed, meaning the
      `Origin` host matches the request's own `Host`, and keep
      `allowed_origins` for genuinely cross-origin frontends such as the
      dev server. A cross-site page still fails the check, because the
      browser sends that page's origin. Log every refusal with the origin
      it named. Until then the workaround is to set
      `LABDOG_SECURITY__ALLOWED_ORIGINS='["https://labdog.lan.tyresson.se"]'`
      in the deployment.

      Severity High: the only interactive shell LabDog offers is down on
      every deployment that relies on the default.

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

- [ ] **BUG-90** `backend/app/models/action_run.py:171` —
      `action_host_runs.output` defaults to the two-character string `''`,
      which the host page shows as a warning after every "collect all"

      Symptom: "collect all" on any host ends with a warning toast that
      reads `''`. Reported on lin-manager 2026-09-27 against `nextcloud`,
      where it was taken for an SSH failure, but the collection had
      succeeded: all seven modules came back `in_sync`. The instance holds
      67 host runs whose output is `''`: 61 `_builtin.collect_state` runs,
      and 6 `linux-upgrade` runs that were cancelled or timed out before
      writing any, whose run view shows the same two characters.

      Root cause: the model declares `output` with `server_default="''"`.
      SQLAlchemy quotes a string server default itself, so the column's
      default became the literal two characters `''` rather than an empty
      string. `0001_initial_schema.py:124` carries the same
      `DEFAULT ::text`, and it is what the live database has. A
      collection with nothing to report finishes with `output=None`
      (`app/tasks/builtin_dispatchers.py:279`), `_finish_host_run` then
      leaves the column alone, and it keeps the default. The host page
      reads that output back as the collection's notices (`fetchNotices`
      in `frontend/lib/collect-state.ts`) and shows each non-empty line
      as a warning.

      Proposed fix:
      - Model: `server_default=""`, which renders as `DEFAULT ''`.
      - Migration: `ALTER TABLE action_host_runs ALTER COLUMN output SET
        DEFAULT ''`, then rewrite the rows already written: `UPDATE
        action_host_runs SET output = '' WHERE output = repeat(chr(39), 2)`.
        A real transcript is never exactly two quote characters, so the
        rewrite cannot touch genuine output. Downgrade leaves the rows
        alone, since putting the quotes back would only restore the bug.
      - Tests: the column's server default renders as an empty string, a
        host run finished without output reads back empty, and the
        migration's rewrite empties a two-quote row while leaving other
        output untouched.
      - The frontend needs no change: once the output is empty,
        `fetchNotices` finds no lines and shows nothing.

      Severity Low: nothing breaks, but a warning on every collection is
      noise that reads as a failure, as it did here.
