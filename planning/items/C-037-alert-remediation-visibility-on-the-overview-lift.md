# C-037: Alert remediation visibility on the Overview; lift the 24-hour lockout

| | |
|---|---|
| Edition | community |
| Source | roadmap, board item "Alert remediation visibility: fix outcomes on the Overview, lift the 24-hour lockout" |
| Tier / score | P2 / 8.0 |
| Stage | 8 |
| Effort | S |
| Autonomy | A |
| Depends on | none |

## Goal
An automatic fix that did not work, made things worse or was rolled back shows up on the Overview without opening the alerts page. When a "made worse" verdict has suspended full auto on a host, the host says so and until when, and an operator can lift the suspension with one audited click. The length of the suspension is a setting.

## Scope
In scope:
- Fix outcomes on the Overview's Pending view.
- Setting `ai.alert_worse_suspension_hours`.
- "Full auto suspended until …" on the host, and a lift button.

Out of scope:
- Changing how an outcome is judged (`app/ai/remediation.py`, `assess` and `judge`).
- The cooldown and the daily cap, which already have settings.
- A severity floor for full auto (recorded on the board as considered and not added).

## Context
- Outcomes: `AlertEvent.remediation_outcome`, `remediation_detail`, `remediation_checked_at` in `backend/app/ai/models.py` (lines 546 to 557). The alerts page renders them: `frontend/app/(dashboard)/alerts/client-page.tsx` (line 60) with labels from `REMEDIATION_OUTCOME` in `frontend/lib/status.ts` (line 155 has `made_worse`).
- Overview: `frontend/app/(dashboard)/overview/client-page.tsx` uses `usePendingQueue` from `frontend/lib/pending.ts`. Lanes are `approvals | failed | stuck | alerts | drift` (line 26). The alerts lane is built from `GET /api/alerts?status=firing&limit=100` (line 86) into `PendingItem`s at line 164, with no outcome. Resolved alerts go to `useSettled` (line 313).
- Lockout: `WORSE_SUSPENDS_FULL_AUTO = timedelta(hours=24)` in `backend/app/ai/remediation.py` (line 83); `recently_made_worse` (line 586) finds a `made_worse` alert on the host inside the window; `_full_auto_refusal` in `backend/app/ai/alert_autonomy.py` (lines 194 to 200) refuses full auto with "an automatic fix made this host worse N min ago, which keeps full auto off it for 24 hours". Nothing in the API, the UI or the settings shows or clears it. In the 2026-10-04 live test the only way past it was editing `remediation_checked_at` in the database.
- Settings registry: `backend/app/settings_service.py`; the neighbouring entry is `ai.alert_remediation_cooldown_minutes` (line 460), read with `get_setting_typed`.
- Host alerts API: `GET /api/alerts/hosts/{host_id}` (`backend/app/api/alerts.py`, line 298, `HostAlertsResponse`), permission `hosts.view`. `backend/app/api/dashboard.py` holds the Overview's aggregate endpoints.
- Audit: `log_action` as used across the API.

## Dependencies
- None. Latest Community migration today is `0060_schedule_owner.py`; use the next free number.

## Implementation steps
1. `settings_service.py`: add `ai.alert_worse_suspension_hours` (int, default 24, min 0, max 720; 0 means no suspension), with description and help in the style of the cooldown entry. Replace `WORSE_SUSPENDS_FULL_AUTO` with a helper `worse_suspension(db) -> timedelta` and use it in `recently_made_worse` and in the refusal text.
2. Migration `backend/alembic/versions/00NN_alert_suspension_lift.py`: add to `alert_events` nullable `suspension_lifted_at` (timestamp with time zone) and `suspension_lifted_by_user_id` (FK `users`, SET NULL). Add both to `AlertEvent`.
3. `recently_made_worse`: add `AlertEvent.suspension_lifted_at.is_(None)` to the filter, and return nothing when the setting is 0. Add `suspended_until(db, host_id, now) -> datetime | None` beside it.
4. `backend/app/api/alerts.py`:
   - Add `full_auto_suspended_until: datetime | None` to `HostAlertsResponse`, filled from `suspended_until`.
   - New route `POST /api/alerts/hosts/{host_id}/lift-suspension`, declaring `Depends(require("alerts.manage"))` (path scope gives the host). It sets `suspension_lifted_at` and the user on every `made_worse` event of the host inside the window, writes one audit row (`alert_full_auto_suspension_lifted`, `entity_type="host"`, `entity_id=host_id`, `after_state` with the alert ids and the time the suspension would have ended), commits, and returns the updated `HostAlertsResponse`. 409 when the host is not suspended.
5. Overview data: extend `usePendingQueue` in `frontend/lib/pending.ts`. Fetch recent alerts whose fix was checked (`GET /api/alerts?limit=100`, filtered client side to `remediation_outcome` in `not_effective`, `made_worse`, or with a rollback, and `remediation_checked_at` inside the last 24 hours; add a server-side `remediated=true` filter to `list_alerts` only if the unfiltered list proves too large). Add each as a `PendingItem` in the `alerts` lane: title "<alertname> on <host>", detail from `REMEDIATION_OUTCOME` plus `remediation_detail`, severity `fail` for `made_worse` and `warn` otherwise, link to the investigation session. For a firing alert already in the lane, add the outcome to its detail instead of a second item.
6. A `fixed` outcome is not pending: show it in `useSettled` with outcome "fixed automatically".
7. Host page: where the host's alerts are shown, render a `Banner` "Full auto suspended until <time>: an automatic fix made this host worse." with a "Lift suspension" button for users with `alerts.manage` (`useCan`), behind the existing confirm dialog. Show the same tag on the alert's row on `/alerts`.
8. `frontend/lib/types.ts`: add the new response field.

## Acceptance criteria
- An alert whose fix was judged `not_effective`, `made_worse`, or was rolled back in the last 24 hours appears in the Overview's Pending view under Alerts with its outcome, and the lane count includes it.
- With the setting at 0, a `made_worse` verdict does not stop the next full-auto fix on that host. With it at 2, the host is suspended for two hours.
- `GET /api/alerts/hosts/{id}` returns `full_auto_suspended_until` while suspended and null otherwise.
- `POST /api/alerts/hosts/{id}/lift-suspension` by an operator clears the suspension, writes one audit row naming the user, and the next matching alert may act at full auto. A viewer gets 403. A host that is not suspended gets 409.
- A second `made_worse` verdict after a lift suspends the host again.
- `tests/test_permissions.py` passes with the new route.

## Test plan
- Extend `backend/tests/ai/test_alert_autonomy.py` and `tests/ai/test_remediation.py` (setting, lift, re-suspend) and `tests/ai/test_alerts.py` (route, 403, 409, audit row).
- `cd backend && source .venv/bin/activate && pytest tests/ai/test_alert_autonomy.py tests/ai/test_remediation.py tests/ai/test_alerts.py tests/test_permissions.py tests/test_permission_scopes.py -q`, then the full suite once before the stage review.
- Frontend: `cd frontend && npx tsc --noEmit && npm run lint && npx playwright test overview.spec.ts alert-actions.spec.ts` (extend `overview.spec.ts` with a mocked `made_worse` alert and the lift button).
- Running stack on lin-manager-2: there is no cheap way to produce a real `made_worse` verdict, so set one on an alert row of a host named "test" in the test database, then check the Overview, the banner, the lift, and the audit log page.

## Docs and changelog
- `docs/ui/alerts.md`: the suspension, the setting, the lift button. `docs/ui/dashboard.md`: fix outcomes in the Pending view. `docs/ui/settings.md` if it lists AI settings.
- `CHANGELOG.md` `[Unreleased]`, Added: "**Automatic fix outcomes on the Overview, and a way out of the lockout.** Fixes that did not work or were rolled back appear under Pending. A host suspended from full auto says until when, an operator can lift it, and `ai.alert_worse_suspension_hours` sets the length (0 turns it off)."

## Risks and rollback
- Lifting the suspension re-arms automatic changes on a host that a fix just made worse. The confirm dialog must say that, and the audit row records who did it.
- Setting 0 removes a safeguard. The help text must say what it protects against.
- Rollback: revert the commit and downgrade the migration. The constant returns and hosts fall back to a fixed 24 hours.

## Assumptions
- Decision note A-03 in `../DECISIONS.md`: this board P3 item was raised to P2.
- Assumption (not yet confirmed): lifting needs `alerts.manage` on the host (operators and admins), since no narrower permission exists for alert autonomy.
- Assumption (not yet confirmed): outcomes stay in the Pending view for 24 hours after the check and then drop out; they are not dismissible one by one beyond the Overview's existing "hide".
- Assumption (not yet confirmed): the lift applies to the whole host, as the suspension does, not per alert name.

## Definition of done
- [ ] Setting, migration, route, Overview items and host banner merged.
- [ ] Tests pass; full backend suite green; `tsc`, lint and the two Playwright specs clean.
- [ ] Docs and `CHANGELOG.md` updated.
- [ ] Stage 8 behaviour 6 verified on lin-manager-2.
- [ ] `planning/roadmap.yaml` status set, and a line added to `planning/PROGRESS.md`.
