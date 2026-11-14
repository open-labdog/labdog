# C-005: Check that a snapshot is possible before a destructive run

| | |
|---|---|
| Edition | community |
| Source | roadmap, "Basic rollback safety" (section "Check that a snapshot is possible before running") |
| Tier / score | P1 / 7.0 |
| Stage | 4 |
| Effort | M |
| Autonomy | B |
| Depends on | C-004, S-003 |

## Goal

A destructive run that asked for a snapshot is refused for a host that cannot be snapshotted (no VM mapping, storage that cannot snapshot, not enough free space, hypervisor unreachable), before the playbook starts and with the reason in the run.

## Scope

In scope:

- The Proxmox implementation of `Capabilities` checks from S-003, made complete.
- Calling the check in both executors before Phase A and refusing on a failed check.
- One setting that keeps today's "warn and continue" behaviour available.
- A read-only route so the UI (C-007) can show the answer before a run.

Out of scope:

- Refusing in the AI path. `backend/app/ai/snapshots.py` deliberately lets an unmapped host proceed without a snapshot, and that stays.
- Guest-agent or consistency checks (BUG-179).
- Checking at schedule time. A schedule is checked when it runs.

## Context

- `backend/app/tasks/action_host.py::_phase_a_snapshot` (line 758): when `env.can_snapshot` is false and the action is destructive, it logs a `[snapshot] skipped` line ("host has no Proxmox VM mapping (no rollback available on failure)") and returns True, so the playbook runs with no rollback point. `can_snapshot` (`_Envelope`, line 251) only tests that a client, a node name and a vmid exist. It says nothing about storage or space.
- `backend/app/tasks/action_group.py::_snapshot_all` (line 1007) does the same per member, and also skips a member whose mapping points at a missing node. A member whose snapshot attempt fails is dropped from the inventory (`_SNAPSHOT_FAILED_MARKER`, `_was_snapshot_failed`, line 1003); that is the mechanism to reuse.
- A snapshot that fails today fails only when attempted, after the lock wait (`workflow.snapshot_lock_wait_minutes`).
- `backend/app/ai/remediation.py::_disk_storages` and `_newest_only` already read `get_vm_config` and `list_node_storage`; S-003 moves that knowledge into `backend/app/snapshots/proxmox.py`.
- `backend/app/workflows/steps/preflight.py::run_preflight` has a 2 GB guest-disk floor (`_MIN_DISK_KB`). It checks the guest's root filesystem, not the hypervisor storage, and is not called by the action executors.
- `docs/ui/actions.md` (about line 612) documents the "skipped" line as expected behaviour.

## Dependencies

- S-003: `SnapshotProvider.capabilities(vm)` and `provider_for`.
- C-004: both items edit the same phases, and the refusal text names the host's rollback mode. Land C-004 first.
- [D-19](../DECISIONS.md): the capability check is the provider's, and C-005 calls it.

## Implementation steps

1. `backend/app/snapshots/proxmox.py::capabilities`: return `can_snapshot=False` with a reason for each of these.
   - The node or token does not answer (`get_vm_config` raises): "Proxmox node <name> did not answer: <error>".
   - The guest has no disks, or a disk is on a storage whose type cannot snapshot that guest type. Keep the table in one constant, `SNAPSHOT_CAPABLE = {"qemu": {...}, "lxc": {...}}`, and check each entry against the Proxmox storage documentation while writing it. A `dir`, `nfs` or `cifs` storage counts for a VM only when the volume is `qcow2`.
   - The storage type is unknown to the table: do not refuse; put a note in `details` and let the snapshot attempt decide.
   - A disk's storage reports less free space than the floor (`avail` from `list_node_storage`): "storage <id> has <n> GB free, under the <floor> GB floor".
   Set `free_bytes` to the smallest `avail` among the guest's storages.
2. `backend/app/settings_service.py`: add `workflow.snapshot_min_free_gb` (int, default 2, min 0; 0 turns the space check off) and `actions.unsnapshottable_host` (`refuse` or `warn`, default `refuse`), each with a description and help text in the style of `workflow.snapshot_lock_wait_minutes`.
3. New `backend/app/snapshots/precheck.py::check_snapshot_possible(db, host_id) -> PrecheckResult(ok, reasons, vm, provider)`. No provider resolving the host gives `ok=False` with "host has no VM mapping". Otherwise it wraps `capabilities`. An exception inside a provider becomes a reason, never a crash.
4. `action_host.py`: after `_preflight_ok` and before `_phase_a_snapshot`, when `spec.destructive and spec.snapshot_enabled` and the run is not a dry run, call the pre-check. On failure with the setting at `refuse`: log `[snapshot] not possible: <reasons>`, then `_finish_early(ctx, "failed", error="Refused: no snapshot is possible for <hostname> (<reasons>). Run with snapshots turned off to proceed without a rollback point.", include_output=True)`. With `warn`: log the reasons and continue as today. `snapshot_enabled=false` is unchanged: the operator opted out.
5. `action_group.py::_snapshot_all`: run the pre-check per member in the same parallel step. A member that fails it is marked the way a snapshot failure is (dropped from the inventory, host run `failed` with the refusal text), so the playbook never touches it. Members that pass continue. If every member is refused the run ends `failed` without starting the playbook.
6. Route for the UI: `GET /api/hosts/{host_id}/snapshot-check` in `backend/app/api/hosts.py`, `Depends(require("hosts.view"))`, returning `{ok, reasons, provider, free_bytes, can_include_ram}`. It makes live hypervisor calls, so bound it with a 10 second timeout and report a timeout as a reason.
7. Tests in `backend/tests/test_snapshot_precheck.py`: each reason above against a fake `ProxmoxClient`; the unknown-storage pass-through; the host executor refusing before `run_ansible` is called; the group executor dropping one member and running the other; `warn` keeping today's behaviour; a dry run and a non-destructive action making no capability call.
8. Update the existing tests that assert the old "skipped, host has no Proxmox VM mapping" line by setting `actions.unsnapshottable_host=warn` in those tests only if their intent is the old path; otherwise assert the refusal. S-003's "unchanged" rule applied to S-003 only.

## Acceptance criteria

- With the default settings, a destructive run with snapshots on against a host with no VM mapping ends `failed` with the refusal text, and no playbook task ran (no `run_ansible` call in the test; no `PLAY` line in the output).
- The same holds for a storage type that cannot snapshot, and for free space under the floor.
- With `actions.unsnapshottable_host=warn`, the run proceeds and logs the reasons.
- With `snapshot_enabled=false`, no capability call is made and the run proceeds.
- In a group run, a refused member is `failed` with the reason and the other members run.
- `GET /api/hosts/{id}/snapshot-check` answers for a viewer and is covered by `tests/test_permissions.py`.

## Test plan

- `cd backend && source .venv/bin/activate && pytest tests/test_snapshot_precheck.py tests/test_action_host.py tests/test_action_host_phases.py tests/test_action_group.py tests/test_action_group_paths.py tests/test_permissions.py -q`; full suite once before the stage review.
- Real hosts, on local Docker on `lin-manager-2`: run `example` against a host named "test" that has no VM mapping and expect the refusal; against a mapped VM named "test" and expect a normal run. The "no space" and "unsupported storage" cases stay in unit tests unless a "test" VM on such storage exists. Do not fill a real storage to prove it.

## Docs and changelog

- `docs/ui/actions.md`, "Destructive actions and snapshot safety": replace the paragraph about the "skipped" line with the refusal, the reasons, the two settings, and how to run without a snapshot on purpose. `docs/ui/settings.md`: the two settings. `docs/upgrade.md`: a note that destructive runs against unmapped hosts are now refused by default.
- `CHANGELOG.md` `[Unreleased]`, Changed: "A destructive action with snapshots on is refused for a host that cannot be snapshotted (no VM mapping, unsupported storage, too little free space). Set `actions.unsnapshottable_host` to `warn` for the old behaviour."

## Risks and rollback

- This changes a default. Existing schedules that run destructive actions on bare-metal or unmapped hosts will start failing until the operator turns snapshots off for them or sets `warn`. The upgrade note and the setting are the mitigation.
- A too-strict storage table would refuse VMs that can snapshot. Unknown types pass, and the table is one constant.
- A slow hypervisor adds latency before each destructive run; the check is two GET calls per host.
- Back out operationally with `actions.unsnapshottable_host=warn`, or revert the commits. No schema change.

## Assumptions

- Assumption (not yet confirmed): refusal is the default and `warn` is the opt-out. The board says "refuse (or warn)" and `stages/stage-4.md` says "refused". Question for Dennis: is breaking existing schedules on unmapped hosts acceptable for the first release that carries this, or should the default be `warn` for one release?
- Assumption (not yet confirmed): the free-space floor is a fixed number of GB per storage, default 2, mirroring `_MIN_DISK_KB`. Proxmox gives no reliable estimate of a snapshot's size on thin storage.
- Assumption (not yet confirmed): the stateful flag from C-004 does not change the rule. A stateful host with snapshots turned off still runs.

## Definition of done

- [ ] Capability checks, pre-check, both executors, settings and route merged.
- [ ] New tests green, adjusted tests reviewed, ruff clean.
- [ ] `docs/ui/actions.md`, `docs/ui/settings.md`, `docs/upgrade.md` and `CHANGELOG.md` updated.
- [ ] `planning/roadmap.yaml` status set, one line added to `planning/PROGRESS.md`.
