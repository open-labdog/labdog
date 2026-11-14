# Stage 10: Scale and assurance

Goal: unbork itself can be scaled out, recovered and installed without internet access.

## Entry criteria

- Stage 9 has passed its exit criteria, or Dennis has said to start this one anyway.

## Items, in execution order

| # | ID | Item | Class | Effort | Depends on |
|---|---|---|---|---|---|
| 1 | [C-029](../items/C-029-run-workers-on-more-than-one-box.md) | Run workers on more than one box | B | L | none |
| 2 | | Review and hardening: follow `.claude/skills/review-stage/SKILL.md` for stage 10 | | | all of the above |

Enterprise items in this stage, in the Enterprise repository (listed for transparency, with no dates or commitments): E-018 Disaster recovery: verified backup and restore of unbork's own state; E-019 High availability reference deployment; E-017 Offline install for air-gapped networks; E-016 Hardening baselines.

## Exit criteria

- [ ] Backend: `pytest tests/ -q` green; `ruff check .` and `ruff format --check .` clean; `bandit -q -r app -b bandit-baseline.json` shows nothing new.
- [ ] Frontend: `npx tsc --noEmit` clean; `npm run lint` with no errors and no more warnings than the previous stage; unit tests green once C-061 has landed.
- [ ] `docs/` and `CHANGELOG.md` (`[Unreleased]`) updated for every user-visible change; `BUGS.md` entries fixed here are deleted.
- [ ] `planning/roadmap.yaml` and `planning/PROGRESS.md` are up to date.
- [ ] The behaviours listed below are verified.
- [ ] `/review-stage 10` is done, `planning/reviews/stage-10.md` exists, and no critical or high finding is open.

Behaviours to verify:

- [ ] Workers on a second box take work from the same Postgres and Redis; the deployment doc was followed on lin-manager-2 to prove it.

## Where to test

- Automated tests run in the session's own environment.
- Anything that needs a running stack goes on local Docker on lin-manager-2.
- Anything that needs a real managed host uses hosts named "test", which are free to break. No other host may break.
- The live unbork on lin-manager (`ssh lin-manager`) is semi-production: use it only for a final check of something already proven elsewhere, and never for a destructive run against a host that is not named "test".
