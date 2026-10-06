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

ID counter as of last housekeeping pass: `BUG-112`, `SEC-38`,
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

- [ ] **BUG-97** `frontend/components/shell/zones.ts:161-176` — the
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
and is confirmed against lin-manager's database. BUG-110 was
reproduced locally.

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

### Tests — Low

- [ ] **BUG-110** `backend/tests/integration/` — five of the seven
      integration tests fail, and CI never runs them

      Symptom: `pytest tests/integration` on `dev` fails 5 of 7. Both CI
      pytest runs pass `--ignore=tests/integration`
      (`.github/workflows/ci.yml:266`, `:819`), so nothing noticed.

      Root cause: the tests were not updated when the code moved.
      - `test_gitops_e2e.py::TestWebhookReceiver` (3 tests) post to
        `/webhooks/github`. BUG-56 (`a03d438a`) moved the route to
        `/api/webhooks/github`, and the CSRF middleware answers the old
        path with 403.
      - `test_gitops_e2e.py::TestMultiModuleGroupYAML::test_full_module_sweep`
        expects a `workflow` module, which went with the legacy workflow
        subsystem (`0b3f21c6`).
      - `test_full_workflow.py` registers and logs in at `/auth/...`.
        Those routes are under `/api/auth/` now, so the CSRF middleware
        refuses the first POST. It also shells out to `alembic`, which
        is found only when the venv's `bin` is on `PATH`.

      Production is unaffected: the main suite covers the moved routes
      (`tests/test_webhook_csrf.py`).

      Fix direction: update the paths, the module set and the CSRF
      handling, then either run `tests/integration` in CI or delete what
      the main suite already covers. A test that nothing runs will rot
      again.

      Severity Low: test-only.

---

## Open — 2026-10-04 discovery

Filed 2026-10-04 while investigating a host that "did not show up" after
discovery. The host (`tester`, 10.10.10.164) had been auto-added by a scan
schedule a minute earlier; confirmed against lin-manager's database and
logs.

### Usability — Low

- [ ] **BUG-111** `frontend/app/(dashboard)/hosts/discover/client-page.tsx:147`,
      `backend/app/api/discovery.py:46-53`, `backend/app/tasks/discovery.py:20-22`
      — a Discover scan that skips already-known hosts reports "No new SSH
      hosts found" without saying any were skipped

      Symptom: on lin-manager, scan schedule "Server" (`10.10.10.0/24`,
      `auto_add`) added `tester` at 10:52:16. A Discover scan of the same
      range at 10:53:19 returned `hosts_found: []`, `total_scanned: 242`,
      and the page said "No new SSH hosts found on this network." The
      operator had just run discovery and watched the log find 10.10.10.164,
      and read the empty result as a miss. Nothing on the page says that 12
      of the range's 254 addresses were already hosts in LabDog, or that
      `tester` was one of them.

      Root cause: `start_scan` passes every known `Host.ip_address` to the
      task as `exclude_ips`, and the task removes them before scanning, so
      they are never probed and never reported. The only trace is a total
      that is lower than the range (242, shown as "scanning N / 242
      hosts"). The banner's "new" is the sole hint, and it does not say how
      many were left out. The same exclusion makes a host that was added a
      moment ago by a scan schedule invisible to Discover, which is the
      case that confused the operator.

      Fix direction: have the task return the excluded IPs that fall in the
      range (it already has `all_hosts` and `exclude_set`) and carry them
      through `ScanStatus` as `skipped_known`. Render "No new SSH hosts
      found. N already in LabDog: tester (10.10.10.164), ..." (resolve IPs
      to hostnames from the `hosts-summary` query), and show the skipped
      count beside the results table when some hosts were found too. Add a
      test that a scan whose range contains known hosts reports them.

      Severity Low: nothing is lost or mis-added; the result is easy to
      misread.

- [ ] **BUG-112** `backend/app/api/scans.py:200`, `:413` — `POST
      /api/scans/{id}/run` is defined twice; the second handler is dead,
      so "run now" is never a manual run

      Symptom: the UI's "run now" on a scan schedule enqueues
      `scans.run_config` without `is_manual=True`. The documented
      behaviour of a manual run does not happen: hosts dismissed from the
      review queue are still suppressed
      (`DismissedHost`, `app/models/scan_config.py:97`), although the
      dismiss endpoint's docstring (`scans.py:357`) says a manual run
      brings them back. Running a disabled schedule also answers 202
      "queued" and then does nothing, because the task returns `skipped`
      for a disabled config; the dead handler would have answered 409.

      Root cause: both handlers are decorated `@router.post("/{config_id}/run")`.
      Starlette dispatches to the first registered, `run_scan_config_now`
      (`scans.py:200`: `args=[config_id]`, no enabled check), so
      `run_scan_now` (`scans.py:413`: `is_manual=True`, 409 when disabled)
      is never reached. Confirmed on lin-manager: the scans router lists
      both routes, `run_scan_config_now` first. The second was added by
      `487ecd3b` ("remember dismissed hosts") without removing the first.
      The only test (`tests/test_scan_configs.py:557`) pins the dead end:
      it asserts `send_task` was called with exactly `args=[config_id]`,
      which only the shadowing handler does.

      Fix direction: delete `run_scan_config_now` and keep `run_scan_now`.
      Update that test to expect `kwargs={"is_manual": True}`, and add one
      that a disabled config returns 409. The frontend already shows
      "Run triggered" on any 2xx and would surface the 409 through
      `showError`.

      Severity Low: dismissed hosts staying hidden on a manual run is the
      safe direction, and the disabled case only wastes a click.

---

## Open — 2026-10-06 full-auto live test

Filed 2026-10-06 after two live runs of a full-auto alert fix on a test VM
(AI sessions 25 and 26 on lin-manager). Both were confirmed against the
delivered email and the database.

### Correctness — Low

- [ ] **BUG-113** `backend/app/notifications/service.py:403`,
      `backend/app/ai/tools/ssh.py:241` — the "A full-auto investigation
      changed …" email repeats each command in its status brackets and
      loses the exit status of a long one

      Symptom: each line of "What ran" is `  - <command>  [failed:
      <summary>]`, and the summary starts with the command. For a short
      one that is only redundant: `nginx -t  [failed: nginx -t (exit
      127)]`. For a command of about 110 characters or more the bracket is
      cut before the exit status, so the one fact the bracket exists for
      is gone: `sed -i 's/…/…/' /etc/nginx/conf.d/zz-upload-limits.conf &&
      cat /etc/nginx/conf.d/zz-upload-limits.conf  [failed: sed -i
      's/client_max_body_size 50mm;/client_max_body_size 50m;/' /etc/ngi]`.
      Seen in session 26's email; the exit status (4) is in the database.

      Root cause: `_run_ssh_command` stores
      `result_summary = f"{command[:120]} (exit {exit_status})"`, and the
      email prints `one_line(call.result_summary, 120)` after the command
      it has already printed. The summary is 120 characters of command
      plus the status, so the 120-character cut always lands inside the
      command when the command is long. Other tools' summaries
      (`timed out`, `host key mismatch`, a refusal reason) are not prefixed
      by a command and read fine.

      Fix direction: in the email, drop the summary's leading command
      before printing it, so the bracket holds `exit 127` or the reason.
      Keep the stored summary as it is: the transcript and the approvals
      page read it. Add a test with a 200-character command that the exit
      status survives.

      Severity Low: the report and the transcript have the detail; the
      email is the only place that loses it.

### Tests — Low

- [ ] **BUG-114** `backend/tests/ai/conftest.py:9-10` — a single AI test
      file cannot be run on its own: it fails at startup with
      "security.secret_key is not set"

      Symptom: `pytest tests/ai/test_alert_autonomy.py` from `backend/`
      with no `LABDOG_SECURITY__*` variables prints "FATAL: LabDog cannot
      start: security.secret_key is not set" and runs nothing. `pytest
      tests/` works, which is why CI never notices.

      Root cause: `tests/ai/conftest.py` imports `app.ai.loop` and
      `app.ai.models` at module level. When a path under `tests/ai/` is
      named on the command line, pytest loads that conftest as an initial
      conftest, before any `pytest_configure` hook runs, so the import
      reads the settings before `tests/conftest.py:76` has set the test
      keys. Run as `pytest tests/`, the subdirectory conftest is loaded
      during collection, after `pytest_configure`.

      Fix direction: move the two imports into the fixtures that use them,
      or set the defaults at the top of `tests/conftest.py`, outside
      `pytest_configure`. Check `pytest tests/ai/test_loop.py` with a clean
      environment.

      Severity Low: test-only, and the workaround is exporting the three
      variables.
