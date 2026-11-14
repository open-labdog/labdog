# C-015: Quiesce playbooks for gitlab-upgrade and nextcloud-vm-update

| | |
|---|---|
| Edition | community |
| Source | roadmap, board item "Quiesce hooks for stateful hosts (rollback phase 2)" (pack half) |
| Tier / score | P2 / 4.0 |
| Stage | 6 |
| Effort | M |
| Autonomy | B |
| Depends on | C-014 |

## Goal
The two pack actions that upgrade a stateful application, `gitlab-upgrade` and `nextcloud-vm-update`, declare quiesce and unquiesce playbooks, so an automatic rollback of either loses no user data. Their end-to-end scripts prove the order.

## Scope
In scope:
- Quiesce and unquiesce playbooks, manifest fields and e2e coverage for the two actions.
- One documentation example in this repository that packs can copy.

Out of scope:
- Any change to this repository's executors (C-014).
- Other actions in either pack. `docker-compose-update` rolls back by image tag and needs no VM quiesce.
- Multi-node GitLab or Nextcloud. Both actions are single-server (`supports_group: false`).

## Context
- The two actions live in a separate, private pack repository (decision note A-02 in `../DECISIONS.md`: the item is planned here because the board names the actions). This repository gains only the documentation example. Everything else is under "Pack-side steps" below and is done in that repository, following its own contributor notes and tooling.
- Both actions are `destructive: true` with a `verify_playbook`, so unbork snapshots the VM, runs verify, and reverts on failure. Both check health over HTTP from the server itself, in the main playbook's preflight and again in verify.
- C-014 fixes the order: quiesce, snapshot, main playbook, verify, rollback if needed, unquiesce. The main and verify playbooks receive `unbork_quiesced: true`; the unquiesce playbook receives `unbork_run_outcome`. After a rollback the VM is back in its quiesced state, and unquiesce must cope with both the upgraded and the reverted system.
- Unbork runs a playbook without its sibling files: tasks must sit in the action's own role.

## Dependencies
- C-014 merged, and an unbork build that has it running on lin-manager-2.
- Write access to the pack repository, and its local tooling (Linux or WSL with Docker).
- A GitLab server and a Nextcloud VM named "test", each mapped to a Proxmox VM, for the real proof. Dennis must supply or name them. Without them the item can reach "e2e scripts pass" but not the real-host check.

## Implementation steps
1. Blocked until C-014 is merged. Read its final manifest field names and extra-var names from `docs/ui/actions.md`.
2. In this repository, add a short, generic quiesce example (maintenance mode on, checkpoint, maintenance mode off) to one starter pack under `docs/examples/action-packs/`, if C-014 did not already add one.
3. Do the pack-side steps below in the pack repository, on a branch, with its own review.
4. Sync the pack into the unbork on lin-manager-2 and run each action against its "test" VM: once as a normal run, once with a forced verify failure (for example a wrong health URL) to see rollback followed by unquiesce.
5. Record the result in `planning/PROGRESS.md`.

### Pack-side steps (private pack repository, not this repository)
These are the requirements; the pack session chooses the mechanism and proves it.

For both actions:
1. Add `quiesce.yml` and `unquiesce.yml` beside `playbook.yml`, each a `hosts: all` play that imports a task file from the action's existing role. Add the two manifest fields and a `quiesce_timeout_seconds`. Bump the action `version`.
2. The quiesce playbook first runs the action's existing health check and its read-only gates, so a server that is already broken, or not ready to upgrade, is refused before the snapshot exists. Today a refusal in the main playbook happens after the snapshot and reverts the VM.
3. The quiesce playbook then stops user writes while leaving the server able to answer its own health checks from loopback. This matters because verify runs before unquiesce. Closing the front door to everything except loopback fits; an application maintenance mode that also makes the health endpoints fail does not, unless the health role is taught to accept it when `unbork_quiesced` is true.
4. The quiesce playbook ends with a flush or checkpoint of the database so the snapshot is as clean as it can be.
5. The main playbook's preflight and the verify playbook accept the quiesced state when `unbork_quiesced` is true and behave exactly as today when it is absent (plain `ansible-playbook` runs, older unbork versions).
6. The unquiesce playbook is idempotent, reopens the front door, and works for each `unbork_run_outcome`. It ends with the health check, so a server that did not come back fails the run visibly.
7. No task targets localhost or uses a shell; drive CLIs with `ansible.builtin.command` and `argv`.

For `gitlab-upgrade`:
8. Decide how user writes are stopped on a Community Edition server (web, API, Git over HTTP and over SSH, and Sidekiq jobs). Candidates to evaluate: restricting the bundled web server to loopback through `gitlab.rb` and a reconfigure; stopping Sidekiq after it drains. Check what the package's own post-install restart does to anything the quiesce stopped, because the package restarts services during the upgrade.
9. Move the background-migration wait into the quiesce playbook, where waiting costs nothing, and revisit the manifest default that is 0 "because the snapshot comes first".

For `nextcloud-vm-update`:
10. Decide how user writes are stopped. Nextcloud's maintenance mode makes `status.php` report maintenance and pages answer 503, which the health role treats as failure, and the VM's own update script manages maintenance mode itself. Evaluate restricting the web server to loopback instead. Do not touch the VM's own cron job.

Tests:
11. Extend each action's e2e script and fixture with a fake or real quiesce target, and add cases: quiesce refused on an unhealthy server; main playbook and verify pass while quiesced; unquiesce after success; unquiesce after a simulated rollback (state restored to the post-quiesce point). The e2e greps report lines verbatim, so change script and report together.

## Acceptance criteria
- Both manifests declare `quiesce_playbook` and `unquiesce_playbook` and pass the pack's validation script against an unbork checkout that has C-014.
- `ansible-lint` passes on the pack at its current profile with no new ignores.
- Both e2e scripts pass, including the new cases.
- On the "test" VMs: during a run, a write from another machine (a push, an upload) is refused; after a successful run the application is reachable again; after a forced verify failure the VM is reverted, then unquiesced, and reachable.
- Running either playbook with plain `ansible-playbook`, without `unbork_quiesced`, behaves as before this item.

## Test plan
- In the pack repository, the commands in its contributor notes: `LABDOG_SRC=<unbork checkout> python scripts/validate_pack.py`, `ansible-lint`, `tests/e2e-gitlab-upgrade.sh`, `tests/e2e-nextcloud-vm-update.sh`. They need Linux with Docker; the Nextcloud script takes about seven minutes.
- In this repository, only the docs build if step 2 added an example.
- Real hosts: lin-manager-2's unbork against VMs named "test" only. Never against the production GitLab or Nextcloud, and never from the live instance on lin-manager.

## Docs and changelog
- This repository: the example under `docs/examples/action-packs/` and, if added here, one `CHANGELOG.md` `[Unreleased]` line under Docs: "Example pack shows quiesce and unquiesce playbooks."
- Pack repository: each action's `README.md` gains a "Quiesce" section (what is closed, for how long, what a rollback restores), and the contributor notes gain the architecture lines for the new files.

## Risks and rollback
- The quiesce lasts the whole run: up to an hour or more of planned downtime for GitLab. The action README and the run dialog line from C-014 must say so.
- A quiesce that the package upgrade undoes halfway (services restarted by the installer) gives a false sense of safety. Step 8 must test this on a real upgrade, not only in the fixture, whose packages are fakes.
- A failed unquiesce leaves the application closed. The playbook must be safe to run again by hand, and the README must give the manual commands.
- Rollback: remove the two manifest fields; the playbooks become unused files and the actions behave as before.

## Assumptions
- Decision note A-02: planned although the code is in the private pack repository.
- Assumption (not yet confirmed): "front door closed to all but loopback" is the quiesce mechanism for both, because verify runs before unquiesce. Question for Dennis: is that acceptable, or should C-014 let a pack ask for unquiesce before verify?
- Assumption (not yet confirmed): Git over SSH on the GitLab server can be blocked without cutting unbork's own SSH access. If it cannot, the README states that SSH pushes during the run are lost on rollback.
- Assumption (not yet confirmed): "test" VMs for both applications exist or can be cloned. Question for Dennis: which hosts?

## Definition of done
- [ ] Pack branch reviewed and merged: two actions with hooks, e2e scripts green, lint and validation clean.
- [ ] Example and changelog line in this repository, if added.
- [ ] Stage 6 behaviour 6 verified; the real-host check done or explicitly recorded as waiting for test VMs.
- [ ] `planning/roadmap.yaml` status set, and a line added to `planning/PROGRESS.md`.
