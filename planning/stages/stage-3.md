# Stage 3: Release unbork 2.0.0

Goal: unbork 2.0.0 is released from main: images, packages and the docs site, with a tested upgrade from LabDog 1.1.0. Dennis merges the release PR; nothing in this stage pushes to main.

## Entry criteria

- Stage 2 has passed its exit criteria, or Dennis has said to start this one anyway.

## Items, in execution order

| # | ID | Item | Class | Effort | Depends on |
|---|---|---|---|---|---|
| 1 | [S-007](../items/S-007-say-in-the-readme-which-parts-are.md) | Say in the README which parts are AGPL and which are commercial | B | S | none |
| 2 | [C-052](../items/C-052-test-the-upgrade-from-1-1-0.md) | Test the upgrade from 1.1.0 in packaging-smoke | A | M | C-049 |
| 3 | [C-001](../items/C-001-verify-and-document-the-upgrade-from-labdog.md) | Verify and document the upgrade from LabDog 1.1.0 to unbork | B | M | C-048, C-049, C-050, C-052 |
| 4 | [C-002](../items/C-002-release-dev-to-main-as-unbork-2.md) | Release dev to main as unbork 2.0.0 | B | S | C-001, S-007, S-008, S-009, S-014, C-051 |
| 5 | [C-003](../items/C-003-bring-the-docs-site-back.md) | Bring the docs site back | A | S | C-002 |
| 6 | | Review and hardening: follow `.claude/skills/review-stage/SKILL.md` for stage 3 | | | all of the above |

Enterprise items in this stage, in the Enterprise repository (listed for transparency, with no dates or commitments): E-020 (internal Enterprise item); E-026 (internal Enterprise item); E-027 (internal Enterprise item).

## Exit criteria

- [ ] Backend: `pytest tests/ -q` green; `ruff check .` and `ruff format --check .` clean; `bandit -q -r app -b bandit-baseline.json` shows nothing new.
- [ ] Frontend: `npx tsc --noEmit` clean; `npm run lint` with no errors and no more warnings than the previous stage; unit tests green once C-061 has landed.
- [ ] `docs/` and `CHANGELOG.md` (`[Unreleased]`) updated for every user-visible change; `BUGS.md` entries fixed here are deleted.
- [ ] `planning/roadmap.yaml` and `planning/PROGRESS.md` are up to date.
- [ ] The behaviours listed below are verified.
- [ ] `/review-stage 3` is done, `planning/reviews/stage-3.md` exists, and no critical or high finding is open.

Behaviours to verify:

- [ ] `packaging-smoke` installs the released 1.1.0 package, upgrades to the candidate, and the service starts with its data, on Ubuntu and Rocky.
- [ ] `docs/upgrade.md` has an "Upgrading to 2.0.0" section that a test install was upgraded with, step by step.
- [ ] The README says which parts are AGPL and which are commercial.
- [ ] A `dev` to `main` PR is open with `VERSION` 2.0.0, the changelog section and the release checklist filled in. It is not merged by the session.
- [ ] After Dennis merges: the docs site answers at the URL the README links to, and titles itself unbork.

## Where to test

- Automated tests run in the session's own environment.
- Anything that needs a running stack goes on local Docker on lin-manager-2.
- Anything that needs a real managed host uses hosts named "test", which are free to break. No other host may break.
- The live unbork on lin-manager (`ssh lin-manager`) is semi-production: use it only for a final check of something already proven elsewhere, and never for a destructive run against a host that is not named "test".
