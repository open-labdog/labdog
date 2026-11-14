# Stage 4: Rollback you can trust

Goal: an operator can trust a rollback: unbork knows which hosts hold state, refuses a run it could not undo, restores the running machine with its memory, says what a rollback threw away, and never writes config to a host marked protected.

## Entry criteria

- Stage 3 has passed its exit criteria, or Dennis has said to start this one anyway.

## Items, in execution order

| # | ID | Item | Class | Effort | Depends on |
|---|---|---|---|---|---|
| 1 | [C-004](../items/C-004-rollback-policy-stateful-flag-and-rollback-mode.md) | Rollback policy: stateful flag and rollback_mode on hosts | B | M | none |
| 2 | [S-003](../items/S-003-snapshot-provider-interface-with-proxmox-as-the.md) | Snapshot provider interface, with Proxmox as the first provider | B | M | none |
| 3 | [C-005](../items/C-005-check-that-a-snapshot-is-possible-before.md) | Check that a snapshot is possible before a destructive run | B | M | C-004, S-003 |
| 4 | [C-044](../items/C-044-keep-the-vm-s-memory-in-the.md) | Keep the VM's memory in the rollback snapshot | B | M | S-003 |
| 5 | [C-006](../items/C-006-log-what-a-rollback-discarded.md) | Log what a rollback discarded | A | S | C-004 |
| 6 | [C-007](../items/C-007-rollback-safety-in-the-ui-and-docs.md) | Rollback safety in the UI and docs; record BUG-177 to BUG-179 | B | M | C-004, C-005, C-006 |
| 7 | [C-016](../items/C-016-protected-hosts-a-per-host-never-write.md) | Protected hosts: a per-host never-write-config flag | B | M | none |
| 8 | [C-012](../items/C-012-notify-on-action-run-failure.md) | Notify on action-run failure | A | S | none |
| 9 | [C-023](../items/C-023-password-reset-by-email.md) | Password reset by email | B | S | none |
| 10 | | Review and hardening: follow `.claude/skills/review-stage/SKILL.md` for stage 4 | | | all of the above |

## Exit criteria

- [ ] Backend: `pytest tests/ -q` green; `ruff check .` and `ruff format --check .` clean; `bandit -q -r app -b bandit-baseline.json` shows nothing new.
- [ ] Frontend: `npx tsc --noEmit` clean; `npm run lint` with no errors and no more warnings than the previous stage; unit tests green once C-061 has landed.
- [ ] `docs/` and `CHANGELOG.md` (`[Unreleased]`) updated for every user-visible change; `BUGS.md` entries fixed here are deleted.
- [ ] `planning/roadmap.yaml` and `planning/PROGRESS.md` are up to date.
- [ ] The behaviours listed below are verified.
- [ ] `/review-stage 4` is done, `planning/reviews/stage-4.md` exists, and no critical or high finding is open.

Behaviours to verify:

- [ ] A host marked stateful with `rollback_mode` set to manual is not rolled back automatically; the run stops and says why.
- [ ] A destructive run against a VM that cannot be snapshotted (no mapping, no space, unsupported storage) is refused before the playbook starts.
- [ ] With memory in the snapshot turned on, a rolled-back VM is running with its processes intact. Proven on a VM named "test".
- [ ] Every Proxmox call the executors make goes through the snapshot provider interface, and the existing snapshot tests pass unchanged.
- [ ] After a rollback the run and the audit log say what was discarded.
- [ ] A sync to a protected host is refused at the API, at the orchestrator and in a group sync, with tests for each.
- [ ] A failed scheduled action sends an email to subscribers.
- [ ] A password reset email arrives with a working, single-use, expiring link.

## Where to test

- Automated tests run in the session's own environment.
- Anything that needs a running stack goes on local Docker on lin-manager-2.
- Anything that needs a real managed host uses hosts named "test", which are free to break. No other host may break.
- The live unbork on lin-manager (`ssh lin-manager`) is semi-production: use it only for a final check of something already proven elsewhere, and never for a destructive run against a host that is not named "test".
