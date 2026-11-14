# Stage 2: The rename, and a clean image

Goal: the product is called unbork everywhere a user or an operator can see or type it, an install made under the old names keeps working, and the image no longer ships a third-party binary.

## Entry criteria

- Stage 1 has passed its exit criteria, or Dennis has said to start this one anyway.

## Items, in execution order

| # | ID | Item | Class | Effort | Depends on |
|---|---|---|---|---|---|
| 1 | [C-048](../items/C-048-rename-configuration-and-environment-with-the-old.md) | Rename: configuration and environment, with the old names as fallbacks | B | M | none |
| 2 | [C-049](../items/C-049-rename-paths-packages-and-service-names-migrated.md) | Rename: paths, packages and service names, migrated on upgrade | B | M | C-048 |
| 3 | [C-050](../items/C-050-rename-on-host-markers-alert-labels-metrics.md) | Rename: on-host markers, alert labels, metrics and pack variables | B | L | C-048 |
| 4 | [C-051](../items/C-051-rename-readme-docs-site-claude-md-and.md) | Rename: README, docs site, CLAUDE.md and changelog wording | A | S | C-048 |
| 5 | [S-008](../items/S-008-rename-the-claude-code-provider-labels.md) | Rename the Claude Code provider labels | A | S | none |
| 6 | [S-014](../items/S-014-remove-the-subscription-token-field-from-ai.md) | Remove the subscription token field from AI providers | B | S | none |
| 7 | [S-009](../items/S-009-stop-bundling-the-claude-code-binary-in.md) | Stop bundling the Claude Code binary in the Docker image | B | M | none |
| 8 | [C-026](../items/C-026-make-the-ai-assistant-removable-from-a.md) | Make the AI assistant removable from a build or install | B | L | S-009 |
| 9 | [S-010](../items/S-010-third-party-notices-and-a-source-offer.md) | Third-party notices and a source offer in the Docker image | B | M | none |
| 10 | | Review and hardening: follow `.claude/skills/review-stage/SKILL.md` for stage 2 | | | all of the above |

## Exit criteria

- [ ] Backend: `pytest tests/ -q` green; `ruff check .` and `ruff format --check .` clean; `bandit -q -r app -b bandit-baseline.json` shows nothing new.
- [ ] Frontend: `npx tsc --noEmit` clean; `npm run lint` with no errors and no more warnings than the previous stage; unit tests green once C-061 has landed.
- [ ] `docs/` and `CHANGELOG.md` (`[Unreleased]`) updated for every user-visible change; `BUGS.md` entries fixed here are deleted.
- [ ] `planning/roadmap.yaml` and `planning/PROGRESS.md` are up to date.
- [ ] The behaviours listed below are verified.
- [ ] `/review-stage 2` is done, `planning/reviews/stage-2.md` exists, and no critical or high finding is open.

Behaviours to verify:

- [ ] `UNBORK_*` variables and `/etc/unbork/unbork.toml` work; the `LABDOG_*` names and old paths still work and log one deprecation warning.
- [ ] A host last synced by 1.1.0 is recognised as managed, and its first sync removes the old chains, markers and files without a gap in firewall coverage. Proven on a host named "test".
- [ ] Alert rules and dashboards using the old label and metric names keep working for the documented deprecation period.
- [ ] The Docker image contains no Claude Code binary; turning the provider on installs it, and a failed install reads as "provider unavailable".
- [ ] A build or install made without the assistant has no AI routes, pages or providers, and the test suite passes in that configuration.
- [ ] No provider label says "Claude Code", and there is no subscription token field.
- [ ] The image carries a third-party notices file and a source offer.

## Where to test

- Automated tests run in the session's own environment.
- Anything that needs a running stack goes on local Docker on lin-manager-2.
- Anything that needs a real managed host uses hosts named "test", which are free to break. No other host may break.
- The live unbork on lin-manager (`ssh lin-manager`) is semi-production: use it only for a final check of something already proven elsewhere, and never for a destructive run against a host that is not named "test".
