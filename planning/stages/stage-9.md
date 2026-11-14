# Stage 9: Rollback beyond Proxmox, tickets and teams

Goal: Enterprise customers get rollback on servers that are not Proxmox VMs, runs tied to change tickets, and teams that share one install cleanly.

This stage has no Community or shared items; its work is in the Enterprise repository.

## Entry criteria

- Stage 8 has passed its exit criteria, or Dennis has said to start this one anyway.

## Items, in execution order

| # | ID | Item | Class | Effort | Depends on |
|---|---|---|---|---|---|
| 1 | | Review and hardening: follow `.claude/skills/review-stage/SKILL.md` for stage 9 | | | all of the above |

Enterprise items in this stage, in the Enterprise repository (listed for transparency, with no dates or commitments): E-011 Bare-metal rollback: detect LVM, ZFS and Btrfs layouts in state collection; E-012 Bare-metal rollback: LVM snapshot provider with driven reboot; E-008 Change tickets through ServiceNow; E-014 Multi-tenancy: close the soft-tenancy gaps for teams.

## Exit criteria

- [ ] Backend: `pytest tests/ -q` green; `ruff check .` and `ruff format --check .` clean; `bandit -q -r app -b bandit-baseline.json` shows nothing new.
- [ ] Frontend: `npx tsc --noEmit` clean; `npm run lint` with no errors and no more warnings than the previous stage; unit tests green once C-061 has landed.
- [ ] `docs/` and `CHANGELOG.md` (`[Unreleased]`) updated for every user-visible change; `BUGS.md` entries fixed here are deleted.
- [ ] `planning/roadmap.yaml` and `planning/PROGRESS.md` are up to date.
- [ ] The behaviours listed below are verified.
- [ ] `/review-stage 9` is done, `planning/reviews/stage-9.md` exists, and no critical or high finding is open.

Behaviours to verify:


## Where to test

- Automated tests run in the session's own environment.
- Anything that needs a running stack goes on local Docker on lin-manager-2.
- Anything that needs a real managed host uses hosts named "test", which are free to break. No other host may break.
- The live unbork on lin-manager (`ssh lin-manager`) is semi-production: use it only for a final check of something already proven elsewhere, and never for a destructive run against a host that is not named "test".
