# C-032: Host tagging and filtering

| | |
|---|---|
| Edition | community |
| Source | roadmap, board item "Host tagging and filtering" |
| Tier / score | P3 / 4.0 |
| Stage | parked |
| Effort | M |
| Autonomy | B |
| Depends on | none |

## Goal
An operator can put free-form tags on a host (for example `region:eu`, `env:prod`) and filter host lists by them, without creating a group for every label.

## Why it is parked
It is a board P3 idea with a one-line note and no design. Groups already cover both jobs a tag could do (configuration merge and selecting targets), so the item needs a decision on what tags are for before it can be scoped.

## What would un-park it
- A stated need that groups do not meet, for example filtering a host list of several hundred hosts by more than one dimension.
- An answer to the scope question below.

## Context
- `backend/app/models/host.py`: `Host` (line 53) has no tag or label column. Group membership is the only way to classify a host.
- `backend/app/models/host_group.py`: `HostGroup` carries `name`, `description`, `category` and a priority that drives the merge (`backend/app/merge_utils.py`).
- Host list: `frontend/app/(dashboard)/hosts/client-page.tsx`, fed by `list_hosts_summary` in `backend/app/api/hosts.py`.
- Lists are narrowed with `visible_filter()` and `visible_host_ids()` (`backend/app/auth/permissions.py`). A tag filter must be applied on top of that, never instead of it.
- Latest Community migration is `backend/alembic/versions/0060_schedule_owner.py`; a tags table or column would take the next free number.
- GitOps: `backend/app/gitops/schema.py` describes groups, not hosts, so tags would not round-trip through GitOps without a schema change.

## Assumptions
- Assumption (not yet confirmed): tags are for filtering and display only. They do not take part in the configuration merge, do not target action runs or schedules, and are not a permission scope. Question for Dennis: should a run or a schedule be able to target a tag? That would make it a much larger item.
- Assumption (not yet confirmed): tags are `key:value` strings set by anyone with `config.edit` on the host.
