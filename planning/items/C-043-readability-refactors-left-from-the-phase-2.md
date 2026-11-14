# C-043: Readability refactors left from the Phase 2 pass

| | |
|---|---|
| Edition | community |
| Source | roadmap, board item "Readability refactors left from the Phase 2 pass" |
| Tier / score | P3 / 2.0 |
| Stage | parked |
| Effort | L |
| Autonomy | B |
| Depends on | none |

## Goal
The remaining long frontend pages, repeated form blocks, duplicated schema validators and backend functions over 60 lines are split, with no change in behaviour.

## Why it is parked
The frontend has no unit tests (`frontend/package.json` has only `lint` and the Playwright `test:e2e` scripts), so each frontend refactor needs its own behaviour harness first. For the three pages already split that was a throwaway Playwright harness comparing the old and new builds step by step. The value is low and the risk of a silent regression is not.

## What would un-park it
- PR-06 (a frontend unit-test harness, see `../PROPOSALS.md`) is accepted and built. That removes the per-refactor harness for the frontend parts.
- The backend parts need no harness and can be picked up one function at a time, when that function is next changed. The board says the same.

## Context
The rule from the board for all of these: move code, do not rewrite it. Same statements, order, SQL, SSH and Celery calls, log text, commit points and lock scope. BUG and SEC comments move with their line. No existing test changes.
- **Host page:** `frontend/app/(dashboard)/hosts/[id]/client-page.tsx` (390 lines now). Planned: `useHostTabs`, `useSyncPreview`, `<TrustHostKeyDialog>`.
- **Host config tabs:** `frontend/app/(dashboard)/hosts/[id]/_tabs/config/` has eight tabs of 201 to 434 lines and a `shared.tsx` (408 lines). Planned: `HostConfigTabProps` and `useOverrideDialog(defaults)`. Check what `shared.tsx` already shares first.
- **Pending queue:** `usePendingQueue` in `frontend/lib/pending.ts` (line 76; file is 351 lines). Planned: one builder per kind of pending item.
- **Shared form blocks:** the secret-field block in the Grafana, Proxmox, SSH key and action pack settings, the login and register forms, the git repo auth step.
- **Create/Update validators:** written twice in `backend/app/hosts_mgmt/schemas.py`, `cron/schemas.py`, `schemas/hosts.py`, `packs/schemas.py`, `proxmox/schemas.py`, `grafana/schemas.py`. The pattern to copy is a module-level `_check_<field>` registered on both models, as in `schemas/rules.py`.
- **Functions over 60 lines:** the board lists 73, led by `run_ca_cert_action` (`tasks/ca_cert_action.py`), `build_host_sync_plan` (`sync/orchestrator.py`), `find_orphaned_snapshots` (`workflows/snapshot_cleanup.py`), `check_hosts_busy` and `check_host_busy` (`tasks/host_lock.py`). The playbook generators stay as they are.

## Assumptions
- The board's line counts were measured on PR #276 and have moved (the host page was 308 lines for the component then). Re-measure before starting any part.
- Assumption (not yet confirmed): split into four items when promoted (frontend pages, form blocks, validators, long functions). Question for Dennis: is the scope he approved on 2026-11-08 still wanted in full?
- Staged items that rewrite a listed function (for example S-003 around snapshot steps) take precedence; do not refactor a function a staged item is about to change.
