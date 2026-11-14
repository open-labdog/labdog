# Stage 8: A safer assistant

Goal: the assistant is safer and easier to steer: it judges cleaner evidence, admins can teach it new read-only commands, and it can ask for a vetted action instead of a shell command.

## Entry criteria

- Stage 7 has passed its exit criteria, or Dennis has said to start this one anyway.

## Items, in execution order

| # | ID | Item | Class | Effort | Depends on |
|---|---|---|---|---|---|
| 1 | [C-010](../items/C-010-ai-verify-narrow-the-evidence-window-to.md) | AI verify: narrow the evidence window to before the action | A | S | none |
| 2 | [C-020](../items/C-020-command-policy-count-diagnostics-as-reads.md) | Command policy: count diagnostics as reads | A | S | none |
| 3 | [C-037](../items/C-037-alert-remediation-visibility-on-the-overview-lift.md) | Alert remediation visibility on the Overview; lift the 24-hour lockout | A | S | none |
| 4 | [C-011](../items/C-011-ai-verify-second-evidence-producer-with-stored.md) | AI verify: second evidence producer with stored evidence packs | B | M | C-010 |
| 5 | [C-019](../items/C-019-command-policy-rules-that-operators-can-edit.md) | Command policy rules that operators can edit | B | L | none |
| 6 | [C-009](../items/C-009-propose-action-let-the-assistant-request-a.md) | propose_action: let the assistant request a vetted action | B | L | none |
| 7 | | Review and hardening: follow `.claude/skills/review-stage/SKILL.md` for stage 8 | | | all of the above |

## Exit criteria

- [ ] Backend: `pytest tests/ -q` green; `ruff check .` and `ruff format --check .` clean; `bandit -q -r app -b bandit-baseline.json` shows nothing new.
- [ ] Frontend: `npx tsc --noEmit` clean; `npm run lint` with no errors and no more warnings than the previous stage; unit tests green once C-061 has landed.
- [ ] `docs/` and `CHANGELOG.md` (`[Unreleased]`) updated for every user-visible change; `BUGS.md` entries fixed here are deleted.
- [ ] `planning/roadmap.yaml` and `planning/PROGRESS.md` are up to date.
- [ ] The behaviours listed below are verified.
- [ ] `/review-stage 8` is done, `planning/reviews/stage-8.md` exists, and no critical or high finding is open.

Behaviours to verify:

- [ ] AI verify judges evidence from before the action started.
- [ ] A second evidence producer exists and each verify step's evidence pack is stored and queryable.
- [ ] Diagnostic commands named in the item's spec run without approval.
- [ ] An admin can add a read-only command rule in the UI; a non-admin gets 403; a rule that is not a read is rejected.
- [ ] `propose_action` creates a run and ends the turn; the session resumes when the run finishes; approval uses the existing cards.
- [ ] The Overview shows fix outcomes, and an operator can lift the 24-hour lockout.

## Where to test

- Automated tests run in the session's own environment.
- Anything that needs a running stack goes on local Docker on lin-manager-2.
- Anything that needs a real managed host uses hosts named "test", which are free to break. No other host may break.
- The live unbork on lin-manager (`ssh lin-manager`) is semi-production: use it only for a final check of something already proven elsewhere, and never for a destructive run against a host that is not named "test".
