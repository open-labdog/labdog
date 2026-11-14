# Stage 6: Day-two operations

Goal: the everyday follow-ups to a run are one click or zero: rerun what failed, turn a run into a schedule, hear about failures, and keep automatic rollback on stateful hosts through quiesce hooks.

## Entry criteria

- Stage 5 has passed its exit criteria, or Dennis has said to start this one anyway.

## Items, in execution order

| # | ID | Item | Class | Effort | Depends on |
|---|---|---|---|---|---|
| 1 | [C-017](../items/C-017-rerun-only-the-failed-hosts-of-a.md) | Rerun only the failed hosts of a run, linked to the original | B | M | none |
| 2 | [C-018](../items/C-018-save-an-action-run-as-a-schedule.md) | Save an action run as a schedule | A | S | none |
| 3 | [C-013](../items/C-013-notify-on-sync-failure-drift-and-certificate.md) | Notify on sync failure, drift and certificate expiry | A | M | C-012 |
| 4 | [S-013](../items/S-013-store-each-action-run-s-structured-per.md) | Store each action run's structured per-host results | B | M | S-002 |
| 5 | [C-014](../items/C-014-quiesce-hooks-in-both-action-executors-rollback.md) | Quiesce hooks in both action executors (rollback phase 2) | B | M | C-004 |
| 6 | [C-015](../items/C-015-quiesce-playbooks-for-gitlab-upgrade-and-nextcloud.md) | Quiesce playbooks for gitlab-upgrade and nextcloud-vm-update | B | M | C-014 |
| 7 | [C-021](../items/C-021-alert-remediation-takes-the-host-lock-instead.md) | Alert remediation takes the host lock instead of refusing | B | M | none |
| 8 | | Review and hardening: follow `.claude/skills/review-stage/SKILL.md` for stage 6 | | | all of the above |

## Exit criteria

- [ ] Backend: `pytest tests/ -q` green; `ruff check .` and `ruff format --check .` clean; `bandit -q -r app -b bandit-baseline.json` shows nothing new.
- [ ] Frontend: `npx tsc --noEmit` clean; `npm run lint` with no errors and no more warnings than the previous stage; unit tests green once C-061 has landed.
- [ ] `docs/` and `CHANGELOG.md` (`[Unreleased]`) updated for every user-visible change; `BUGS.md` entries fixed here are deleted.
- [ ] `planning/roadmap.yaml` and `planning/PROGRESS.md` are up to date.
- [ ] The behaviours listed below are verified.
- [ ] `/review-stage 6` is done, `planning/reviews/stage-6.md` exists, and no critical or high finding is open.

Behaviours to verify:

- [ ] "Rerun failed hosts" starts a run on exactly the failed hosts, linked to the original, and the server rejects parameters the current manifest no longer accepts.
- [ ] "Save as schedule" creates a schedule with the run's target and parameters.
- [ ] Sync failure, drift and certificate expiry each send a notification to subscribers.
- [ ] A pack's `set_stats` results are stored per host and returned by the run API.
- [ ] A manifest with quiesce and unquiesce playbooks runs them around the snapshot window, in both executors, including on failure.
- [ ] gitlab-upgrade and nextcloud-vm-update ship quiesce playbooks, and their e2e scripts pass.
- [ ] An AI remediation command claims the host through the host lock instead of refusing, and releases it.

## Where to test

- Automated tests run in the session's own environment.
- Anything that needs a running stack goes on local Docker on lin-manager-2.
- Anything that needs a real managed host uses hosts named "test", which are free to break. No other host may break.
- The live unbork on lin-manager (`ssh lin-manager`) is semi-production: use it only for a final check of something already proven elsewhere, and never for a destructive run against a host that is not named "test".
