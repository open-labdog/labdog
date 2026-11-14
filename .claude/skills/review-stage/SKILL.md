---
name: review-stage
description: Review everything a roadmap stage changed, fix what is safe to fix, defer the rest, and write the stage review report. Run as /review-stage <N>, or from /run-stage.
disable-model-invocation: true
argument-hint: <stage number>
---

# Review stage $ARGUMENTS

Review the whole diff of stage `$ARGUMENTS`, not the last commit. The
hard rules and the "Where to test" section of
`.claude/skills/run-stage/SKILL.md` apply here too: read them first.

## 1. Scope

1. Read `base_commit` for stage `$ARGUMENTS` from `planning/roadmap.yaml`.
   If it is null, stop: the stage was never started.
2. The diff under review is `git diff <base_commit>...HEAD`, with
   `git log --oneline <base_commit>..HEAD` for the commits. Leave
   `planning/` out of the code review.
3. Read the stage file and the specs of the items that are `done` or
   `review`, so the review judges the change against what it was meant to
   do.

## 2. Three reviews in parallel

Start three subagents in one message, so they run at the same time. Each
is read-only: it may read and search, and run tests or linters, but must
not edit, commit or push. Give each the base commit, the list of changed
files, the stage file path and the item spec paths. Do not give them your
own opinion of the change. Each returns findings as a list with:
severity (critical, high, medium, low), file and line, what is wrong, a
concrete failing case, and a suggested fix.

1. **Correctness and security.** Logic errors, unhandled failure paths,
   races around the per-host lock and the Celery queues, migrations that
   lose data or cannot be re-run, missing or wrong `require()`
   permission and scope on a route, missing audit rows for a change that
   touches a host, injection through operator input into a playbook or a
   command, secrets in logs or responses, anything that breaks the plugin
   contract or makes Community depend on an extension. Also: does each
   item's acceptance criteria actually hold, and do the tests test it?
2. **Performance.** Queries inside loops, missing indexes for new
   filters, unbounded lists and payloads, work done in the request that
   belongs in a task, tasks routed to the wrong queue, blocking calls in
   async code, frontend re-fetching and over-rendering. Where a number
   would settle it and `dev/loadtest/` covers the path, run
   `./run_one.sh S2 300 default` on local Docker on `lin-manager-2` and
   compare with `planning/reviews/baseline-loadtest.md`.
3. **Readability and maintainability.** Duplication of something that
   already exists in the codebase, names that mislead, dead code and
   leftover debugging, comments that contradict the code, tests that
   assert nothing, docs and changelog that do not match the behaviour,
   deviation from the patterns in `CLAUDE.md` and
   `frontend/FRONTEND.md`.

## 3. Triage

Merge the three lists and remove duplicates. Check each finding against
the code yourself before acting on it: a reviewer's finding is a claim,
not a fact. Then sort every finding into one of three:

- **Fix now:** every critical and high finding, and any medium or low one
  that is small, local and clearly right.
- **Defer:** a real finding whose fix is large, changes behaviour beyond
  the stage, or needs a decision. It goes into `planning/PROPOSALS.md`
  with status open, the finding, and the stage it came from.
- **Reject:** not a real problem. Keep one line saying why.

A critical or high finding may be deferred only when its fix needs a
decision from Dennis. Then it also goes into the Open section of
`planning/DECISIONS.md`, and the stage cannot be reported as passing.

## 4. Fix

Fix the "fix now" findings, one commit per finding or per tight group,
each message prefixed `review(stage-$ARGUMENTS):`, for example
`review(stage-1): Check the scope on the audit export route`. A fix that
changes behaviour gets a test that fails without it. The three-attempts
rule from run-stage applies: a fix that will not go green is reverted and
deferred.

## 5. Verify

Run the full checks once after the last fix:

```bash
cd backend && source .venv/bin/activate
pytest tests/ -q --cov=app
ruff check . && ruff format --check .
bandit -q -r app -b bandit-baseline.json
cd ../frontend && npx tsc --noEmit && npm run lint
```

Add `npm test` once the unit harness exists (C-061), and
`npx playwright test` against a stack on local Docker on `lin-manager-2`
when the stage changed a page. Compare with the stage's baseline numbers:
no new failing test, no new lint warning, coverage not lower than before
the stage unless the report says why.

## 6. Report

Write `planning/reviews/stage-$ARGUMENTS.md`:

- Base commit, head commit, number of commits and changed files.
- A table of findings: id, severity, area (correctness/security,
  performance, readability), file, one-line summary, outcome (fixed in
  `<commit>`, deferred as `PR-xx`, rejected with the reason).
- Counts per severity and outcome.
- Test, coverage and lint numbers before and after.
- Anything not verified, and why.
- Verdict: **pass** (no critical or high finding open), or **does not
  pass** with what is open.

Append one line to `planning/PROGRESS.md`. Commit the report as
`review(stage-$ARGUMENTS): Stage review report`. When this skill was
started from `/run-stage`, go back to its step 4. When it was started on
its own, push the branch and tell Dennis the verdict and the deferred
findings.
