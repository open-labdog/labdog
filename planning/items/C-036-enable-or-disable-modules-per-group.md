# C-036: Enable or disable modules per group

| | |
|---|---|
| Edition | community |
| Source | roadmap, board item "Enable/disable modules per group" |
| Tier / score | P3 / 4.0 |
| Stage | parked |
| Effort | M |
| Autonomy | B |
| Depends on | none |

## Goal
An operator can state per group which configuration modules apply, so that hosts in that group are not synced or drift-checked for a module the group has switched off.

## Why it is parked
It is a board P3 idea, and it overlaps C-016 (protected hosts: a per-host "never write config here" flag), which is scheduled. Both answer "this host must not be written to" at different grains. Building this before C-016 lands would risk two mechanisms that disagree.

## What would un-park it
- C-016 is merged, and a need remains that it does not cover (for example: manage firewall and users on a group, but never packages).
- The merge question below is answered.

## Context
- `backend/app/models/host_group.py`: `HostGroup` (line 10) has no `enabled_modules` column. The board suggests a list on the group.
- Which modules run is decided in one place: `determine_modules_to_run` in `backend/app/ansible_runtime/outcomes.py` (line 25), called by `build_host_sync_plan` in `backend/app/sync/orchestrator.py` (line 195), followed by one `if "<module>" in modules_to_run` block per module (lines 250 to 306). The canonical module list is `CANONICAL_ORDER` in `backend/app/ansible_runtime/composer.py`.
- Drift must honour the same switch: `backend/app/tasks/drift_sweep.py` `sweep_module` and the per-module `check_one` bodies, plus `HostModuleStatus` rows (`backend/app/models/host_module_status.py`), which would otherwise show a disabled module as drifted.
- The built-in `_builtin.sync` and `_builtin.drift_check` actions (`backend/app/tasks/builtin_dispatchers.py`) reach the same orchestrator.

## Assumptions
- Assumption (not yet confirmed): a host in several groups needs a rule. Question for Dennis: does a module run if any group enables it, or does the highest-priority group that states a list win, like the rest of the merge?
- Assumption (not yet confirmed): a disabled module is skipped and reported as skipped; nothing already on the host is removed.
- Assumption (not yet confirmed): the default for existing groups is "all modules", so the migration changes no behaviour.
