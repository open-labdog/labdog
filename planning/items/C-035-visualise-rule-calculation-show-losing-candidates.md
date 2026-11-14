# C-035: Visualise rule calculation: show losing candidates

| | |
|---|---|
| Edition | community |
| Source | roadmap, board item "Visualise rule calculation: show losing candidates" |
| Tier / score | P3 / 2.5 |
| Stage | parked |
| Effort | M |
| Autonomy | A |
| Depends on | none |

## Goal
For any effective entry on a host, an operator can see which other candidates lost the merge, which group or override each came from, and what a host override replaced.

## Why it is parked
It is a board P3 idea. The effective entries and their sources already ship, so an operator can see what won; seeing what lost is a convenience for debugging group priorities, not a gap in safety or function.

## What would un-park it
- A support case or bug where an operator could not work out why an entry was dropped.
- Free capacity in a stage that already touches the plan pages or the merge.

## Context
- `backend/app/merge_utils.py`: `load_owned_rows` (line 80) returns every candidate row as an `OwnedRow` (line 52) in precedence order, from `ordered_groups_for_host` (line 34). `first_wins` (line 126) keeps the first row per merge key and drops the rest at its `continue`. The losers are known at that point and thrown away.
- Each module calls it from its own merge: `backend/app/rules/merge.py`, `packages/merge.py`, `services/merge.py`, `cron/merge.py`, `user_mgmt/merge.py`, `hosts_mgmt/merge.py`, `resolver/merge.py`, `ca_certs/merge.py`. Firewall rules are ordered lists, not keyed sets, so "losing" needs its own definition there.
- Effective views are served by the per-module API files (for example `backend/app/api/rules.py`, `cron_sync.py`, `linux_users.py`, `service_drift.py`).
- Frontend: `frontend/app/(dashboard)/plans/` (`client-page.tsx`, `_steps/`, `_hooks/`) and `frontend/components/module-diff-view.tsx`.

## Assumptions
- Assumption (not yet confirmed): the change is read-only. A variant of `first_wins` returns the discarded rows alongside the kept ones, and the merge result used by sync is unchanged.
- Assumption (not yet confirmed): losers are shown on the host's effective view, on demand, not on the plan diff. Question for Dennis: which page should show them?
- No new route is assumed; if one is added it declares `hosts.view` with the host scope.
