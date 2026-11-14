# C-044: Keep the VM's memory in the rollback snapshot

| | |
|---|---|
| Edition | community |
| Source | user (Dennis, 2026-11-14), confirmed by decision D-19 |
| Tier / score | P1 / 6.5 |
| Stage | 4 |
| Effort | M |
| Autonomy | B |
| Depends on | S-003 |

## Goal

With one setting turned on, the snapshot taken before a destructive run includes the VM's memory, and a rollback brings the machine back running with its processes intact, not as if it had lost power.

## Scope

In scope:

- `include_ram` through the provider, the Proxmox client and both action executors.
- A rollback that copes with a VM Proxmox has already resumed.
- A longer task wait for memory snapshots.
- One instance setting, off by default.

Out of scope:

- Containers (LXC has no memory snapshot) and hosts without a VM.
- AI session snapshots (`labdog-ai-...`). They are kept for days, and a days-old memory image is a different decision.
- A per-run, per-schedule or per-pack switch (see Assumptions).
- Backups. [D-19](../DECISIONS.md) established that a Proxmox backup never holds memory.

## Context

- `backend/app/proxmox/client.py::create_snapshot` (line 229) posts `{"snapname", "description"}` to `/nodes/{node}/{qemu|lxc}/{vmid}/snapshot`. Proxmox accepts `vmstate` on the qemu form of that call.
- `backend/app/workflows/steps/snapshot.py::create_snapshot` (line 51) calls it and then `wait_for_task(pve_node, upid)`, whose default timeout is 120 seconds (`client.py:337`). Writing several GB of memory can take longer.
- `backend/app/workflows/steps/rollback.py::rollback_to_snapshot` always calls `start_vm` after the rollback task, with the comment "Proxmox rollback leaves the VM stopped". That is true for a disk-only snapshot. After rolling back to a snapshot that holds memory, Proxmox resumes the VM itself, and starting a running VM is an error.
- S-003 gives `SnapshotProvider.create(..., include_ram=False)`, `rollback(handle, start=True)`, `Capabilities.can_include_ram` and `SnapshotHandle.include_ram`.
- `backend/app/tasks/action_host.py::_phase_a_snapshot` (line 758) and `action_group.py::_snapshot_member` (line 1143) are the two callers to change.
- A VM with PCI passthrough (`hostpci*` in its config) cannot save its state. A stopped VM has no memory to save.

## Dependencies

- S-003 merged. C-005 is not required, but if it has landed its pre-check result already carries `can_include_ram`.
- A VM named "test" on the Proxmox host that `lin-manager-2` can reach, with enough free space on its state storage for its memory size.

## Implementation steps

1. `backend/app/proxmox/client.py::create_snapshot`: add keyword `vmstate: bool = False`. Send `"vmstate": 1` only when it is true and the guest is `qemu`. The default call must produce exactly today's request body (`tests/test_proxmox_client.py` asserts it; add a second case for `vmstate=True`).
2. `backend/app/workflows/steps/snapshot.py::create_snapshot`: add keyword `include_ram: bool = False` and `task_timeout: int | None = None`; pass them through. Append "with memory" to the snapshot description when set.
3. `backend/app/settings_service.py`: add `workflow.snapshot_include_memory` (bool, default false) and `workflow.snapshot_memory_timeout_minutes` (int, default 15, min 1, max 120), with help text that states the cost: the snapshot takes as long as writing the VM's memory to storage, needs that much space, and the VM is paused briefly.
4. `backend/app/snapshots/proxmox.py`:
   - `capabilities`: set `can_include_ram` true only for a `qemu` guest that is running, has no `hostpci*` entry, and whose state storage (`vmstate_storage` in the config, else the storage of the first disk) has more free space than the VM's `memory` plus the C-005 floor. Put the reason in `details["ram_reason"]` otherwise.
   - `create`: when `include_ram` is asked and `can_include_ram` is false, take a disk-only snapshot and report the reason through `on_wait` or a returned note. Do not fail the run for that. Record `include_ram` on the handle as what was actually taken.
   - `rollback(handle, start=True)`: after the rollback task, read `get_vm_status`. Start the VM only when it is not `running`. This applies to every rollback, which also makes the disk-only path tolerant of a VM that is already up.
5. `rollback_to_snapshot`: replace the unconditional `start_vm` with the status check from step 4, keeping the signature and the SSH wait.
6. `action_host.py::_phase_a_snapshot`: read the setting once per run, pass `include_ram`. Log `[snapshot] created <name> on <node>/<vmid> (with memory)` or `[snapshot] memory not included: <reason>` followed by the normal line. Keep the plain line exactly as today when the setting is off.
7. `action_group.py::_snapshot_member`: the same, per member.
8. A handle rebuilt from a stored name (`handle_for`) does not know whether memory was saved. `rollback` must not depend on it: the status check in step 4 is the only logic. To show it later, `ProxmoxSnapshotProvider.list` returns Proxmox's `vmstate` field per snapshot.
9. Tests in `backend/tests/test_snapshot_memory.py`: request body with and without the flag; LXC never sends it; fallback to disk-only with the reason logged; rollback does not call `start_vm` when the status is `running` and does when it is `stopped`; the timeout setting reaches `wait_for_task`; setting off means no behaviour or log change.

## Acceptance criteria

- With the setting off, the request to Proxmox and the run log are identical to today (existing tests unchanged and green).
- With it on, a destructive run on a running qemu VM sends `vmstate=1`, and the run log says the snapshot holds memory.
- With it on, a container, a stopped VM, a VM with PCI passthrough or too little state storage gets a disk-only snapshot and a log line with the reason. The run continues.
- A rollback to a memory snapshot does not call `start_vm` and still waits for SSH.
- On a VM named "test": a process started before the run is alive with the same PID after the rollback, and `uptime -s` shows a boot time earlier than the run's start.

## Test plan

- `cd backend && source .venv/bin/activate && pytest tests/test_snapshot_memory.py tests/test_proxmox_client.py tests/test_snapshot_lock_wait.py tests/test_action_host_phases.py tests/test_action_group_paths.py -q`; full suite once before the stage review.
- Real host (required by the stage): unbork on local Docker on `lin-manager-2`, target a VM named "test". Start a marker (`nohup sleep 86400 &`, note the PID and `uptime -s`), turn the setting on, run the `example` action from `unbork-playbooks` so that verify fails, wait for the rollback, then check the PID and the boot time over SSH. Repeat with the setting off and confirm the VM cold-booted. Never run this against a host that is not named "test".

## Docs and changelog

- `docs/ui/actions.md`, "Destructive actions and snapshot safety": what the setting does, what it costs (time, space, a short pause), when it falls back to disk-only, and what a memory rollback means for the guest (the clock is behind until time sync corrects it; open network connections are gone). `docs/ui/settings.md`: the two settings.
- `CHANGELOG.md` `[Unreleased]`, Added: "Rollback snapshots can include the VM's memory (`workflow.snapshot_include_memory`), so a rollback returns a running machine with its processes intact."

## Risks and rollback

- A memory snapshot of a large VM is slow and large. The setting is off by default, the wait is configurable, and the capability check falls back when space is short.
- The guest is paused while its state is written, and services with short timeouts may notice. This is documented.
- After a memory rollback the guest clock is stale until chrony or the guest agent corrects it. Verify playbooks that compare times could misjudge. This is documented.
- The rollback status check changes the disk-only path slightly (start only if not running). The unit test covers both branches.
- Back out by turning the setting off. A code revert needs no data change.

## Assumptions

- [D-19](../DECISIONS.md): memory snapshots belong in unbork and use the provider's `include_ram`.
- Assumption (not yet confirmed): the switch is one instance setting, off by default. The item's pointers name `backend/app/actions/manifest.py`, which suggests a per-pack switch was considered. A manifest field changes the public pack contract, so it is left out. Question for Dennis: instance setting only, or also per schedule or per pack?
- Assumption (not yet confirmed): when memory cannot be included, the run continues with a disk-only snapshot and says so. It is not refused.
- Assumption (not yet confirmed): AI session snapshots stay disk-only.
- D-19's text places this item in "stage 2"; `roadmap.yaml` and `stages/stage-4.md` place it in stage 4. This spec follows the stage file.

## Definition of done

- [ ] Client, step, provider, both executors and settings merged.
- [ ] Unit tests green; the "test" VM check done and its result noted in `planning/PROGRESS.md`.
- [ ] Docs and `CHANGELOG.md` updated.
- [ ] `planning/roadmap.yaml` status set.
