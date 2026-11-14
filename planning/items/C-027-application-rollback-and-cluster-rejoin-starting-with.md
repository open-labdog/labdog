# C-027: Application rollback and cluster rejoin, starting with etcd

| | |
|---|---|
| Edition | community |
| Source | roadmap, board item "Application rollback and cluster rejoin (rollback phase 3), starting with etcd" |
| Tier / score | P3 / 3.3 |
| Stage | parked |
| Effort | L |
| Autonomy | B |
| Depends on | C-014 |

## Goal
A failed member of a replicated cluster is rejoined from its peers, or rolled back by the application's own playbook, and is never reverted to a VM disk snapshot. The first technology is stacked etcd under `k8s-upgrade`.

## Why it is parked
The work needs a real multi-node cluster to test against: the failure only shows when an upgraded control-plane node fails `verify.yml`, and a rejoin can only be proved with live peers and quorum. No such cluster exists among the hosts named "test". The board rates it P3, and the cheaper protections (stateful-host flag, rollback mode, the pre-check) come first in stages 2 and 4.

## What would un-park it
- C-014 (quiesce hooks) has landed, so the pack manifest already carries per-phase playbooks to extend.
- A disposable kubeadm cluster of at least three control-plane nodes with stacked etcd exists on hosts named "test", and Dennis confirms it may be broken.
- BUG-177 is recorded in `BUGS.md` (C-007 does this) so the fix has a registry entry to close.

## Context
- `backend/app/tasks/action_group.py`: `_rollback_all` (line 1468, called at line 681) reverts every failed member that has a snapshot. It has no notion of a member whose state other machines replicate. This is BUG-177.
- `backend/app/actions/manifest.py`: `ActionManifest` has `verify_playbook` (line 102) and no `rollback_playbook` or `rejoin_playbook`. Both would be new manifest fields, which is a public pack contract.
- `backend/app/workflows/steps/rollback.py` and `snapshot.py`: the VM revert path that option C replaces and option D forbids for consensus members.
- Public pack, `/home/user/unbork-playbooks/actions/k8s-upgrade/`: `verify.yml` (comment at lines 5 to 13) already passes nodes the run never reached. The second half of the board's stopgap is not done: `etcd_backup_dir` still defaults to `/var/lib/labdog/etcd-backups` on the node itself (`manifest.yml` line 68, `roles/kubernetes-upgrade/defaults/main.yml` line 9, task file `roles/kubernetes-upgrade/tasks/etcd-backup.yml`).
- Board design: option C is a manifest `rollback_playbook` used instead of the VM revert. Option D is declared or probed member roles, a `rejoin_playbook`, a quorum check before each step, and never more than (N-1)/2 members down. Options E and F are not planned. A Postgres primary and replica rule set follows etcd.

## Assumptions
- Assumption (not yet confirmed): the stopgap (a control-plane node with a static-pod etcd passes verify and reports the failure in the run; the backup default moves off the node) is small enough to ship separately in the pack before this item. Question for Dennis: do it now as a pack-only fix?
- Assumption (not yet confirmed): member roles are probed by the pack's playbook, as `k8s-upgrade` already does for control-plane and worker groups, and are not stored on the host row.
- BUG-177 is not in `BUGS.md` on this branch (the counter there stops at BUG-176); it comes from closed PR #282.
