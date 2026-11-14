# Stage 0: Foundations: safety nets and contributor terms

Goal: every later stage has a safety net to compare against (coverage, a frontend unit harness, a load-test number), CI stops getting in the way, and outside contributions are covered by a CLA.

## Entry criteria

- None. This is the first stage.

## Items, in execution order

| # | ID | Item | Class | Effort | Depends on |
|---|---|---|---|---|---|
| 1 | [C-064](../items/C-064-make-planning-a-named-exception-to-the.md) | Make planning/ a named exception to the plans/ rule | A | S | none |
| 2 | [S-012](../items/S-012-copyright-holder-line-in-license.md) | Copyright holder line in LICENSE | A | S | none |
| 3 | [S-011](../items/S-011-contributor-licence-agreement-and-a-cla-check.md) | Contributor licence agreement and a CLA check on pull requests | B | S | none |
| 4 | [C-024](../items/C-024-ci-stop-every-pr-moving-the-floating.md) | CI: stop every PR moving the floating :test image tag | A | S | none |
| 5 | [C-025](../items/C-025-ci-ubuntu-26-04-runners-and-the.md) | CI: Ubuntu 26.04 runners and the React Compiler lint rules | A | M | none |
| 6 | [C-062](../items/C-062-measure-backend-coverage-in-ci.md) | Measure backend coverage in CI | A | S | none |
| 7 | [C-061](../items/C-061-a-unit-test-harness-for-the-frontend.md) | A unit-test harness for the frontend | A | M | none |
| 8 | [C-063](../items/C-063-record-a-load-test-baseline.md) | Record a load-test baseline | A | M | none |
| 9 | | Review and hardening: follow `.claude/skills/review-stage/SKILL.md` for stage 0 | | | all of the above |

Enterprise items in this stage, in the Enterprise repository (listed for transparency, with no dates or commitments): E-001 (internal Enterprise item); E-028 (internal Enterprise item); E-022 (internal Enterprise item); E-021 (internal Enterprise item).

## Exit criteria

- [ ] Backend: `pytest tests/ -q` green; `ruff check .` and `ruff format --check .` clean; `bandit -q -r app -b bandit-baseline.json` shows nothing new.
- [ ] Frontend: `npx tsc --noEmit` clean; `npm run lint` with no errors and no more warnings than the previous stage; unit tests green once C-061 has landed.
- [ ] `docs/` and `CHANGELOG.md` (`[Unreleased]`) updated for every user-visible change; `BUGS.md` entries fixed here are deleted.
- [ ] `planning/roadmap.yaml` and `planning/PROGRESS.md` are up to date.
- [ ] The behaviours listed below are verified.
- [ ] `/review-stage 0` is done, `planning/reviews/stage-0.md` exists, and no critical or high finding is open.

Behaviours to verify:

- [ ] A PR from a fork fails the CLA check until the author signs, and passes after.
- [ ] `LICENSE` names Dennis Tyresson as copyright holder.
- [ ] A PR without the `no-test-image` label no longer moves the floating `:test` tag (or the tag is gone and deploy docs name `:test-<sha>`).
- [ ] CI prints a backend coverage figure on every PR, and `planning/PLAN.md` records it.
- [ ] `npm test` in `frontend/` runs at least one real unit test, and CI runs it.
- [ ] `planning/reviews/baseline-loadtest.md` holds one `S2 300 default` run from lin-manager-2.
- [ ] `CONTRIBUTING.md`, `CLAUDE.md`, the release template and `.dockerignore` all treat `planning/` as intended and `plans/` as scratch.

## Where to test

- Automated tests run in the session's own environment.
- Anything that needs a running stack goes on local Docker on lin-manager-2.
- Anything that needs a real managed host uses hosts named "test", which are free to break. No other host may break.
- The live unbork on lin-manager (`ssh lin-manager`) is semi-production: use it only for a final check of something already proven elsewhere, and never for a destructive run against a host that is not named "test".
