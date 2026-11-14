# C-045: kennel: PBS restore points, restored beside the original

| | |
|---|---|
| Edition | community |
| Source | user (Dennis: "restore points beyond the run"); decisions D-19, D-22, D-23 |
| Tier / score | P2 / 4.0 |
| Stage | 11 |
| Effort | L |
| Autonomy | B |
| Depends on | C-028, C-054 |

## Goal

kennel can list a VM's backups as restore points, take a new one on request before a risky change, and restore any of them as a new VM beside the original, in kennel's pool and off the production network, so an operator can inspect it or copy data back.

## Scope

In scope:
- Listing backups of a VM on Proxmox Backup Server storage and on vzdump file storage.
- Taking a backup on request, with a second, opt-in set of privileges.
- Restoring a restore point as a new VM in kennel's pool under a lease, with its network cards moved off production before first start.
- Keeping a restore point beyond the run that asked for it, with an expiry.

Out of scope:
- In-place restore over the original VM. [D-23](../DECISIONS.md): later, opt-in, with a second token, and only inside an unbork run.
- Memory. A backup never holds RAM ([D-19](../DECISIONS.md)); a restored VM boots cold.
- Backup schedules and retention policies for ordinary backups. Proxmox and PBS own those.
- Restoring across clusters. A backup is restored on the cluster it came from.
- Containers (LXC) in the first version.

## Context

- [D-19](../DECISIONS.md): "RAM in unbork, PBS in kennel." [D-23](../DECISIONS.md): "PBS restore goes beside the original first."
- Dennis's original wording (`planning/PROPOSALS.md`, "Other work Dennis asked to add"): "take a backup before a destructive run and restore from it on failure, for hosts or storage where a snapshot is not possible, and keep restore points beyond the run." This item delivers the backup, the kept restore point and the restore beside. Automatic restore on failure needs in-place restore and is deferred with it.
- Facts from the write-up "How far kennel can go" ("What is verified", read in Proxmox's qemu-server source):
  - A backup holds disk volumes and configuration only. A hibernated VM's backup carries a `vmstate:` line without its volume and its restore is reported to fail, so kennel refuses to back up or restore a hibernated VM.
  - Restore is `POST /nodes/{node}/qemu` with `archive=<backup volume id>`, a new `vmid`, `unique=1` (new MAC addresses and identifiers), `pool=` and `storage=`. `live-restore=1` starts the VM before the restore finishes, works for PBS only, and is marked experimental upstream: use it only for throwaway copies.
  - PBS and vzdump file storage go through the same restore call; only listing and free-space checks differ.
- Privileges: the pool token from C-028 can create VMs in the pool but cannot read or make backups of production VMs. Proxmox checks access to a backup volume by `Datastore.Allocate` on the storage, or `Datastore.AllocateSpace` together with `VM.Backup` on the VM the backup belongs to. Taking a backup needs `VM.Backup` on the source VM. Confirm both on the test cluster and write down what was observed.
- From earlier items: leases, tag stamp and sweeper (C-054); the readiness probe `newest_restore_point` and the backup listing call (C-058); `move_to_sandbox` (C-057). If C-057 is not done when this starts, restored VMs get every network card disconnected (`link_down=1`) instead, which needs no sandbox.

## Dependencies

- C-028, C-054. C-058 for the listing code if it landed first; otherwise this item adds it. C-057 for a networked sandbox copy; not needed for a disconnected one.
- Proxmox side, by Dennis, opt-in per cluster: a second API token (the "backup token") with `VM.Backup` and `VM.Audit` on the VMs or pools kennel may back up, and `Datastore.AllocateSpace` and `Datastore.Audit` on the backup storage. kennel stores it separately from the pool token.
- A Proxmox Backup Server datastore attached to the test cluster as a storage, with at least one backup of a VM named "test".
- Free space on the pool's disk storage for one restored copy.

## Implementation steps

1. **Model.** kennel migration `backend/alembic/versions/000N_restore_points.py`: add `backup_token_id` and `encrypted_backup_token_secret` (nullable) and `backup_storage` to `clusters`; table `restore_points` (id UUID, cluster_id, source_vmid, volid, storage, kind `pbs|vzdump`, taken_at, size_bytes, origin `existing|requested`, requested_by_token_id, owner_ref, keep_until nullable, protected bool, state `creating|available|failed|expired|deleted`, notes); table `restores` (id UUID, restore_point_id, lease_id, mode `beside`, state, started_at, finished_at, error).
2. **Client calls** added to kennel's Proxmox client: list backups (`GET /nodes/{node}/storage/{storage}/content?content=backup&vmid=`), backup (`POST /nodes/{node}/vzdump` with `vmid`, `storage`, `mode=snapshot`, `notes-template`, `protected`), restore (`POST /nodes/{node}/qemu` as above), set notes and protection on a backup volume (`PUT /nodes/{node}/storage/{storage}/content/{volume}`), delete a backup volume.
3. **Routes** in `kennel/api/restore_points.py`:
   - `GET /api/restore-points?cluster=&vmid=` (`require("read")`): existing backups read live from Proxmox merged with kennel's own rows.
   - `POST /api/restore-points` (`require("restore")`; body: cluster, vmid, owner_ref, keep_seconds, idempotency key): takes a backup with the backup token. 409 with a clear message when the cluster has no backup token. Returns 202 with a job.
   - `POST /api/restore-points/{id}/restore` (`require("restore")`; body: `mode`, `network` (`disconnected` or a sandbox network name), ttl_seconds, callback_url, idempotency key). `mode` accepts only `beside`; `in_place` returns 400 with the text "in-place restore is not available yet". Returns 202 with a lease and a job.
   - `DELETE /api/restore-points/{id}` (`require("restore")`): only for points with origin `requested`.
4. **Backup job** (`kind="restore_point_create"`): refuse a hibernated VM and a VM that Proxmox reports as locked (skip, do not wait, per the write-up's rule for kennel's own schedule); check free space on the backup storage; run vzdump in snapshot mode with notes `kennel restore point <uuid> owner <owner_ref>` and `protected=1` so pruning jobs leave it alone; record the resulting volume id; send `restore_point.created`.
5. **Restore job** (`kind="restore_beside"`), in this order:
   a. Create the lease row first (purpose `restore`, owner, expiry), then take a new vmid.
   b. Check free space on the target storage against the backup's size and refuse when a restore of the same source VM is already running.
   c. Restore with `unique=1`, `pool=<kennel pool>`, `start=0`. Never pass `force` and never pass the source's vmid: assert `new_vmid != source_vmid` and that the new vmid does not exist, in code, before the call.
   d. Stamp the lease tags (C-054's `stamp`).
   e. Network: `move_to_sandbox` when a sandbox network was named and its isolation test is current; otherwise set `link_down=1` on every `netN`. Re-read the config and fail the job if any card is still connected to a non-sandbox bridge.
   f. Remove settings that tie the copy to the original's hardware or boot behaviour and could hurt: set `onboot=0`, drop `hookscript`, and drop USB and PCI passthrough lines. Log each change.
   g. Start the VM only when the caller asked (`start: true`, default false). Send `restore.finished` with the lease, vmid and, when started and the guest agent answers, the IP address.
   h. On any failure after step c, destroy the new VM and mark the lease failed. The sweeper covers a crash.
6. **Expiry.** A sweeper step (same job as C-054's sweep) deletes restore points with origin `requested` whose `keep_until` has passed: clear `protected`, delete the volume, mark `deleted`. It never deletes a backup kennel did not take.
7. **Audit.** Every use of the backup token writes an `audit_log` row with the caller's token and `owner_ref`.
8. Docs: kennel `docs/restore-points.md` and `docs/proxmox-token.md` (the backup token, exactly which rights and why it is separate).

## Acceptance criteria

- `GET /api/restore-points?cluster=…&vmid=…` lists the existing PBS backups of a "test" VM with time and size.
- `POST /api/restore-points` for a "test" VM produces a protected backup on PBS carrying kennel's note, and is refused with 409 on a cluster without a backup token.
- Restoring a point yields a new VM in kennel's pool with a different vmid and different MAC addresses, lease tags, `onboot=0`, and no network card on a production bridge. The original VM's config and state are unchanged (compare its config digest before and after).
- A restore asked for with `mode: in_place` is refused.
- The restored VM disappears when its lease expires; a requested restore point disappears after `keep_until`; a pre-existing backup is never deleted by kennel.
- A hibernated source VM is refused with a reason.
- Killing the worker during a restore leaves no VM in the pool after the next sweep.

## Test plan

- kennel: `cd backend && source .venv/bin/activate && pytest tests/test_restore_points_api.py tests/test_restore_beside.py tests/test_restore_point_expiry.py -q` (new; Proxmox mocked, including an assertion that no request ever targets the source vmid with a write method), then `pytest tests/ -q`, ruff and bandit.
- Test cluster, from kennel on lin-manager-2: backups and restores of VMs named "test" only. The backup token is scoped by Dennis to "test" VMs for this stage, so a mistake cannot back up or read another VM.
- Nothing in this item runs against the live unbork instance.

## Docs and changelog

- kennel `docs/restore-points.md`, `docs/proxmox-token.md`, `docs/api.md`, `CHANGELOG.md` `[Unreleased]`: "Restore points: list backups, take a protected backup on request, and restore beside the original into the pool."
- unbork: none in this item.

## Risks and rollback

- Overwriting the original VM is the worst outcome. The guards: the restore always targets a fresh vmid in kennel's pool, `force` is never sent, the pool token cannot write outside the pool, and a test scans every mocked request.
- A restored copy on a production network would answer for the original. Step 5e fails closed.
- Protected backups that are never released fill the datastore. Requested points need an expiry (a maximum is configured), and `/health` reports their count and total size.
- Backups in snapshot mode without the guest agent are crash-consistent only. Say so in the docs and in the job result.
- Rollback: remove the backup token from the cluster row (backup requests then return 409); restores of existing backups can be disabled with a setting.

## Assumptions

- [D-19](../DECISIONS.md), [D-22](../DECISIONS.md), [D-23](../DECISIONS.md) as quoted.
- Assumption (not yet confirmed): kennel talks to backups through the Proxmox VE storage API only and needs no direct PBS API access in this item. Question for Dennis: is a direct PBS connection (port 8007) wanted for datastore free space and verification state?
- Assumption (not yet confirmed): taking a backup on request is in scope now, behind the opt-in backup token, because "restore points beyond the run" needs it. Question for Dennis: confirm, or limit this item to backups that already exist.
- Assumption (not yet confirmed): a requested restore point has a maximum lifetime (default 30 days).
- Assumption (not yet confirmed): the exact privileges for reading another VM's backups are as described in Context; to be confirmed on the test cluster.

## Definition of done

- [ ] Migration, client calls, routes and both jobs merged in kennel; tests and lint green.
- [ ] List, backup, restore beside and expiry proven on the test cluster with "test" VMs.
- [ ] kennel docs and changelog updated.
- [ ] `planning/roadmap.yaml` status updated; one line in `planning/PROGRESS.md`.
