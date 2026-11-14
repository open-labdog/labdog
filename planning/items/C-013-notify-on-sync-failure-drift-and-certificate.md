# C-013: Notify on sync failure, drift and certificate expiry

| | |
|---|---|
| Edition | community |
| Source | roadmap, board item "Notification events: action-run failures, sync failures, drift, certificate expiry" (second part) |
| Tier / score | P1 / 5.5 |
| Stage | 6 |
| Effort | M |
| Autonomy | A |
| Depends on | C-012 |

## Goal
A user who subscribes gets an email when a sync fails, when a host drifts from its desired state, and when a managed CA certificate is about to expire or has expired.

## Scope
In scope:
- Three event types: `sync_failed`, `drift_detected`, `ca_cert_expiring`.
- A `notify()` call where each happens, with dedupe keys so a flapping host does not flood the outbox.
- A periodic scan for certificate expiry.

Out of scope:
- Action-run failures (C-012).
- New channels (webhook, Slack, ntfy) and HTML mail: a separate board item.
- Per-host or per-group subscriptions. Subscriptions stay per user and per event.
- Certificates other than the CA certificates unbork manages (`ca_cert_rules`). Unbork does not track host TLS certificates.

## Context
- `backend/app/notifications/events.py`: `EVENT_TYPES` is the one table of events; `EVENT_KEYS` is derived from it. The API (`backend/app/api/notifications.py`, line 198) and the email digest labels (`backend/app/notifications/delivery.py`, `_LABEL`) read it, so a new entry shows up in the UI without frontend work.
- `backend/app/notifications/service.py`: `notify(db, event_type, *, subject, body, link_path, dedupe_key)` (line 102) queues one row per subscriber inside a savepoint, does nothing while SMTP is off, never raises at run time, and raises `ValueError` for an unknown event. The per-event helpers (`notify_alert_fired`, `notify_alert_action`, and so on) build the subject and body with `clean`, `one_line` and `_hostname`. Dedupe is the unique constraint `uq_notifications_user_dedupe`.
- Sync: `backend/app/tasks/host_sync_orchestrator.py`, `_finalise_run` (line 490) sets `job.status = "failed"` and writes the `sync_failed` audit row (line 596) before its commit. Other paths that fail a `SyncJob`: `_compensate_failed_prepare` (line 755) and `backend/app/tasks/sync_sweeper.py`.
- Drift: `backend/app/tasks/drift_sweep.py`, `check_host` (line 206) loads the `HostModuleStatus` row, calls the module's `check_one`, re-checks the host lock, and commits. `write_drift_verdict` (line 403) sets `hms.sync_status` to `out_of_sync` or `in_sync`. A verdict gathered while another op claimed the host is rolled back, so anything queued in that transaction is discarded with it, which is what is wanted.
- Certificates: `backend/app/ca_certs/models.py`, `CACertRule.not_after` (line 50), with `state` (`CertState.present` or absent). `backend/app/metrics/aggregates.py`, `get_ca_cert_expiries` (line 742) already feeds `GET /api/dashboard/ca-cert-expiries`.
- The drain task: `backend/app/tasks/notifications.py`, `_deliver` runs every 60 seconds and already calls one scan (`scan_expiring_approvals`) before draining. `prune_old_notifications` deletes sent rows after `logging.run_retention_days`, so a dedupe key only protects for that long.
- Tests for the existing events are in `backend/tests/ai/test_notifications.py`.

## Dependencies
- C-012 adds the `action_run_failed` event and sets the pattern (helper name, link path, test layout). Follow whatever it established; where this spec and C-012's code differ in style, C-012's code wins.
- SMTP must be configured on the test stack to see mail. Use the stack's existing mail catcher on lin-manager-2 if one is set up; otherwise assert on the `notifications` table.

## Implementation steps
1. `events.py`: add three `EventType` rows after the C-012 entry:
   - `sync_failed`, "Sync failed", "A sync to a host finished with one or more modules in error."
   - `drift_detected`, "Drift detected", "A periodic drift check found a host out of sync with its desired state. Sent once per host and module per day."
   - `ca_cert_expiring`, "Certificate expiry", "A managed CA certificate expires in 30, 7 or 1 days, or has expired."
2. `service.py`: add `notify_sync_failed(db, job, *, hostname, module_outcomes)`. Subject `Sync failed on <host>`. Body: job id, the failed modules with their outcomes, `job.error_message` through `clean(..., 1000)`. `link_path=f"/hosts/{job.host_id}"` (check the route the sync history lives on and link there). `dedupe_key=f"sync_failed:{job.id}"`.
3. Call it in `_finalise_run` when `job.status == "failed"`, after the `log_action` call and before `await db.commit()`. Call it also where `_compensate_failed_prepare` and the sync sweeper mark a job failed. The dedupe key makes a double call harmless.
4. `service.py`: add `notify_drift_detected(db, host, module_type)`. Subject `Drift on <host>: <module>`. Body: host, module, when checked. `link_path="/drift"` (or the host's drift tab if one exists). `dedupe_key=f"drift:{host.id}:{module_type}:{today_utc}"`.
5. `drift_sweep.py`, `check_host`: read `before = hms.sync_status if hms is not None else None` before `check_one`. After the second `_blocker_reason` check and before `await db.commit()`, re-read the row (a module may create it) and call `notify_drift_detected` when the status is now `out_of_sync` and `before != "out_of_sync"`. Only the transition notifies; a host that stays drifted does not send mail every sweep.
6. `service.py`: add `scan_expiring_ca_certs(db, now=None) -> int`. For each `CACertRule` with `state == present` and a `not_after`, and each threshold in `(30, 7, 1, 0)` days: when `not_after - threshold <= now < not_after - threshold + 24h`, call `notify("ca_cert_expiring", …)` with `dedupe_key=f"ca_cert:{rule.id}:{threshold}"`, subject `<name> expires in N days` (or `has expired`), body with subject, issuer, fingerprint, `not_after`, and the group or host it is assigned to. `link_path` to the CA certificates page. The 24-hour window is what stops a pruned dedupe row from causing a repeat.
7. `backend/app/tasks/notifications.py`: add a task `app.tasks.notifications.scan_certificates` that runs `scan_expiring_ca_certs` and commits, registered hourly in this module's beat registration next to `deliver` and `prune_old_notifications`. Do not put it in the once-a-minute `_deliver`.
8. `tests/test_task_routing.py` must still pass: the new task has to land on a queue a worker consumes.

## Acceptance criteria
- `GET /api/notifications/events` (the route that serves `EVENT_TYPES`) lists the three new events, and the Notifications page shows three new checkboxes with no frontend change.
- With SMTP usable and one subscriber: a failed sync queues exactly one `sync_failed` row for that user, even when two failure paths both call the helper.
- A drift sweep that moves a module from `in_sync` to `out_of_sync` queues one `drift_detected` row. The next sweep, still drifted, queues none. A verdict discarded because the host was claimed mid-check queues none.
- A certificate whose `not_after` is 6 days 23 hours away queues one `ca_cert_expiring` row for threshold 7; running the scan again queues none; an `absent` rule queues none.
- With SMTP off, none of the three paths writes a row or raises.
- A sync, a drift check and the scan all complete normally when `notify` fails internally (simulate by making the insert raise).

## Test plan
- New `backend/tests/test_notification_events.py` (or extend the file C-012 created, if it made a non-AI one): the six criteria above, driving `_finalise_run`, `check_host` with a fake `check_one`, and `scan_expiring_ca_certs` with a fixed `now`.
- `cd backend && source .venv/bin/activate && pytest tests/test_notification_events.py tests/ai/test_notifications.py tests/test_drift_sweep.py tests/test_task_routing.py -q`
- Full backend suite once before the stage review. No frontend change, so `npx tsc --noEmit && npm run lint` only as the stage gate.
- Running stack on lin-manager-2: subscribe a test user, break a sync on a host named "test" (for example an invalid package name in a host override), change a managed file by hand to cause drift, and add a CA certificate rule with a short-lived test certificate. Check the delivery log on the Notifications page.

## Docs and changelog
- `docs/ui/notifications.md`: add the three events to the event table, with when each fires and its dedupe rule.
- `docs/ui/drift-detection.md`: one sentence that drift can notify.
- `CHANGELOG.md` `[Unreleased]`, Added: "**Notifications for sync failures, drift and certificate expiry.** Three new events on the Notifications page. Drift notifies once per host and module per day; certificates at 30, 7 and 1 days and on expiry."

## Risks and rollback
- Volume: a fleet-wide change that drifts every host sends one message per host and module. The outbox coalesces everything queued for a user within a minute into one mail, and the per-day dedupe caps repeats, but the first digest can be long. Nobody is subscribed until they opt in.
- `notify` runs in the caller's transaction inside a savepoint. Keep the calls before the commit, never after, so a rolled-back verdict sends nothing.
- Rollback: revert the commit. Rows already queued for the removed events would fail the `EVENT_KEYS` label lookup in the digest; check `delivery.py` falls back to the raw key, and if it does not, drain the outbox before reverting.

## Assumptions
- Assumption (not yet confirmed): "certificate expiry" means the CA certificates unbork distributes (`ca_cert_rules`), because that is the only certificate data unbork holds. Question for Dennis: is anything else meant?
- Assumption (not yet confirmed): thresholds of 30, 7 and 1 days and on expiry, fixed in code. The dashboard already marks certificates amber inside 30 days.
- Assumption (not yet confirmed): drift notifies only from the periodic sweeps and the `_builtin.drift_check` action if it goes through `check_host`; an operator pressing "Check drift" in the UI is looking at the result and gets no mail.
- Assumption (not yet confirmed): a resolved drift ("back in sync") sends nothing.

## Definition of done
- [ ] Three events, helpers, call sites and the hourly scan merged; tests pass; full backend suite green.
- [ ] `docs/ui/notifications.md`, `docs/ui/drift-detection.md` and `CHANGELOG.md` updated.
- [ ] Stage 6 behaviour 3 verified on lin-manager-2.
- [ ] `planning/roadmap.yaml` status set, and a line added to `planning/PROGRESS.md`.
