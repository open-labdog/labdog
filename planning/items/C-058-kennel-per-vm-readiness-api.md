# C-058: kennel: per-VM readiness API

| | |
|---|---|
| Edition | community |
| Source | user (Dennis, kennel thread); decisions D-19 and D-22 |
| Tier / score | P2 / 6.0 |
| Stage | 11 |
| Effort | M |
| Autonomy | B |
| Depends on | C-028 |

## Goal

One call to kennel answers, for one VM: whether a snapshot is possible, whether a snapshot with RAM is possible, whether a restore point is available and how old it is, and how much free space there is. A caller can decide before a risky run whether the VM can be rolled back or recovered.

## Scope

In scope:
- `GET /api/vms/{cluster}/{vmid}/readiness` in kennel, and the probes behind it.
- The extra read-only Proxmox privileges it needs, documented.
- A short cache so a pre-run check on many hosts does not flood Proxmox.

Out of scope:
- unbork calling this route (C-059).
- Last restore test result. C-055 adds that field when restore tests exist.
- Taking a snapshot or a backup. This route only reads.

## Context

- [D-19](../DECISIONS.md) fixes the four answers: "a per-VM capability check (snapshot, RAM, restore point, free space), which C-005 calls". The stage file repeats them as the behaviour to prove.
- unbork already has logic for parts of this, to be copied into kennel and kept in step with the S-003 Proxmox provider: `backend/app/proxmox/client.py` `get_vm_config` (line 289), `list_node_storage` (line 297), `list_snapshots` (line 275), `get_vm_status` (line 369). The pre-run snapshot check that C-005 builds in unbork (`backend/app/tasks/action_host.py::_phase_a_snapshot`, line 758; `backend/app/workflows/steps/snapshot.py`) answers the first two questions against Proxmox directly. Read the S-003 and C-005 code as merged before writing this, and return the same field names where the meaning is the same, so C-059 can map one onto the other without translation.
- What decides each answer on Proxmox (from the write-up "How far kennel can go", section "What is verified"):
  - Snapshot possible: every disk in the VM config sits on a storage type that supports snapshots, and no disk is a passthrough or raw device. Since PVE 9.0 thick LVM on shared storage can snapshot through an opt-in storage setting.
  - RAM snapshot possible: a QEMU VM (not a container), running, with enough free space on its `vmstatestorage` (or the first disk's storage) for about the VM's memory size. A hibernated VM (a `vmstate:` line in the config) must be reported as not ready.
  - Restore point: the newest backup of that vmid on any backup storage the token can read (`GET /nodes/{node}/storage/{storage}/content?content=backup&vmid={vmid}`), with its time, size and whether it is on Proxmox Backup Server.
  - Free space: `avail` on the storages involved (`GET /nodes/{node}/storage/{storage}/status`).
- The pool-scoped token from C-028 cannot read a production VM's config. This route needs read access outside the pool.

## Dependencies

- C-028 (service skeleton, cluster connection, `require()` scopes, Proxmox client).
- Proxmox side, by Dennis: add read-only rights for the kennel token on the VMs and storages it should report on: `VM.Audit` on `/vms` (or on chosen VMs or pools), `Datastore.Audit` on the disk and backup storages. Confirm on the test cluster whether listing another VM's backups needs more than `Datastore.Audit` (Proxmox may require `VM.Backup` on that VM or `Datastore.Allocate` on the storage); record the answer in `docs/proxmox-token.md`.
- S-003 and C-005 merged in unbork, to align field names.

## Implementation steps

1. Add `backend/kennel/readiness/probes.py` with four pure functions over already-fetched API data, so they can be unit tested without Proxmox: `snapshot_possible(config, storages)`, `ram_snapshot_possible(config, status, storages)`, `newest_restore_point(backups)`, `free_space(config, storages)`. Each returns a value plus a machine-readable reason code when the answer is no (for example `disk_on_unsupported_storage`, `vm_hibernated`, `not_enough_space_for_ram`, `no_backup_found`, `vm_is_container`).
2. Add `backend/kennel/readiness/service.py::get_readiness(cluster, vmid)`. It finds the VM's node from `/cluster/resources`, fetches config, status, storage list and backup listings concurrently, and calls the probes. A part that cannot be read because of a missing privilege returns `unknown` with reason `permission_denied` instead of failing the whole call.
3. Add the route in `backend/kennel/api/vms.py`: `GET /api/vms/{cluster}/{vmid}/readiness`, declared with `Depends(require("read"))`. `{cluster}` is the cluster's name. Response:
   ```json
   {
     "cluster": "pve-test", "cluster_fingerprint": "sha256:…", "vmid": 104, "node": "pve1",
     "vm_type": "qemu", "status": "running",
     "snapshot":      {"possible": true,  "reason": null},
     "ram_snapshot":  {"possible": false, "reason": "not_enough_space_for_ram", "needed_bytes": 17179869184, "free_bytes": 9000000000},
     "restore_point": {"available": true, "newest_at": "2026-11-13T02:00:11Z", "age_seconds": 91234, "storage": "pbs-main", "kind": "pbs", "size_bytes": 12345678},
     "free_space":    [{"storage": "local-zfs", "free_bytes": 0, "total_bytes": 0}],
     "checked_at": "2026-11-14T03:20:45Z"
   }
   ```
   Return 404 when the cluster or the VM is unknown, and 502 with the Proxmox error when the cluster does not answer.
4. Cache the answer per `(cluster, vmid)` for a configurable time (default 60 seconds) in process memory; `?fresh=true` bypasses it. No database table is needed.
5. Add `GET /api/vms/{cluster}/readiness?vmids=101,104` (`require("read")`) that returns a list, with bounded concurrency, for a pre-run check over a group.
6. Extend the cluster self-test from C-028 with one line: whether the token can read VMs outside the pool, reported as information (readiness available or not), not as a failure.
7. Document the route in kennel's `docs/api.md` and the privileges in `docs/proxmox-token.md`.

## Acceptance criteria

- For a "test" VM on snapshot-capable storage the route reports `snapshot.possible: true`; after adding a disk on a storage type without snapshot support it reports `false` with a reason code.
- For a running "test" VM with less free space on its state storage than its memory size, `ram_snapshot.possible` is `false` with both byte counts; for a container it is `false` with `vm_is_container`.
- For a VM with a backup, `restore_point.available` is `true` with the newest backup's time; for one without, `false` with `no_backup_found`.
- `free_space` lists every storage the VM's disks use.
- This matches the stage behaviour: "The readiness API answers, per VM: snapshot possible, with RAM, restore point available, free space."
- A token without the `read` scope gets 403. A missing Proxmox privilege yields `unknown` for that part and 200 overall.
- A second call within the cache time makes no Proxmox request (asserted with the mock transport).

## Test plan

- In the kennel repository: `cd backend && source .venv/bin/activate && pytest tests/test_readiness_probes.py tests/test_readiness_api.py -q` (new), then `pytest tests/ -q`, `ruff check . && ruff format --check .`, `bandit -q -r kennel`.
- Against the test cluster from lin-manager-2: call the route for VMs named "test" in each state above. The route is read-only, so calling it for other VMs is safe, but changing a VM to create the states (adding a disk, filling storage) is done on "test" VMs only.

## Docs and changelog

- kennel `docs/api.md`, `docs/proxmox-token.md`, and `CHANGELOG.md` `[Unreleased]`: "Per-VM readiness API: snapshot, RAM snapshot, newest restore point and free space."
- unbork: none in this item.

## Risks and rollback

- Storage capability rules differ by type and Proxmox version. Encode them in one table in `probes.py` with the Proxmox version they were checked on, and answer `unknown` for a storage type the table does not list. A wrong `true` is worse than `unknown`.
- Listing backups on a large Proxmox Backup Server datastore can be slow. Filter by vmid in the API call and keep the cache.
- Rollback: remove the route; nothing else depends on it until C-059.

## Assumptions

- [D-19](../DECISIONS.md) defines the four answers.
- Assumption (not yet confirmed): the answer is computed on request and cached briefly, not stored. Question for Dennis: is history of readiness wanted?
- Assumption (not yet confirmed): the VM is addressed by cluster name and vmid, and the fingerprint is returned for the caller to compare, as the write-up proposes.
- Assumption (not yet confirmed): widening the token with read-only rights outside the pool is acceptable, since it cannot change anything there.

## Definition of done

- [ ] Probes, service and both routes merged in kennel; tests and lint green.
- [ ] Proven against the test cluster for the listed states.
- [ ] kennel docs and changelog updated.
- [ ] `planning/roadmap.yaml` status updated; one line in `planning/PROGRESS.md`.
