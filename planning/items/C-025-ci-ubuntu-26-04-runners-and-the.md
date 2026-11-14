# C-025: CI: Ubuntu 26.04 runners and the React Compiler lint rules

| | |
|---|---|
| Edition | community |
| Source | roadmap, "CI and toolchain maintenance: floating :test tag, Ubuntu 26.04, React Compiler lint rules" (second and third parts) |
| Tier / score | P2 / 3.5 |
| Stage | 0 |
| Effort | M |
| Autonomy | A |
| Depends on | none |

## Goal
CI runs on Ubuntu 26.04 by choice, with every job green, and the two React Compiler readiness lint rules are errors again, so a new violation fails the lint job instead of adding a warning.

## Scope
In scope:
- Part A: every `runs-on: ubuntu-24.04` in `.github/workflows/ci.yml` moves to `ubuntu-26.04`.
- Part B: fix the one remaining `react-hooks/set-state-in-effect` site and set that rule and `react-hooks/purity` to `error` in `frontend/eslint.config.mjs`.

Out of scope:
- The nine `react-hooks/incompatible-library` warnings. They come from react-hook-form's `watch()` and stay until that library is replaced or the rule learns it (board note).
- The base images in `Dockerfile` and the distribution containers `packaging-smoke` builds in (Ubuntu 24.04 and Rocky 9 are what packages are tested on, not the runner).
- Upgrading Python, Node, Playwright or ESLint, unless a job cannot pass on 26.04 without it. If one is needed, stop and record it in the PR.

## Context
- `.github/workflows/ci.yml` has 21 jobs, each with `runs-on: ubuntu-24.04` (`grep -c "runs-on: ubuntu-24.04"` prints 21). The board note says they were pinned on purpose so 1.0.0 was built on the image it was tested on, and asks for the move "in its own PR", checking that `actions/setup-python` has Python 3.12 for 26.04 and that `packaging-smoke` passes.
- Jobs most likely to notice the runner change: `frontend-e2e` (line 262; runs `npx playwright install --with-deps chromium`, which installs distribution packages), `packaging-smoke` (line 593; nfpm, containers), `release-guard` (line 699) and `release-artifacts` (line 784), `gitleaks` (line 1041; a downloaded static binary), `backend-test` (line 163; `services:` containers for `postgres:18-alpine` and `redis:8-alpine`).
- `frontend/eslint.config.mjs` lines 41 to 49 set `"react-hooks/set-state-in-effect": "warn"` and `"react-hooks/purity": "warn"`, with a comment that calls them advisory "until someone takes the pass (roadmap board)".
- `npm run lint` on the planning commit reports 0 errors and 11 warnings: nine `incompatible-library`, one `@typescript-eslint/no-unused-vars` (`frontend/e2e/ux-toast.spec.ts` line 23, `group`), and one `set-state-in-effect` at `frontend/components/scheduled-actions/cron-input.tsx` line 46. The board note also names `action-run-dialog.tsx`; that site is no longer flagged. `purity` has no hits.
- `cron-input.tsx`, component `CronInput` (line 37): a `useEffect` on `[value]` calls `setValidation(null)` synchronously when `value` is empty and `setValidating(true)` synchronously before starting a 250 ms debounce timer that posts to `/api/scheduled-actions/validate-cron`. Those two synchronous calls are what the rule flags.
- `frontend/e2e/scheduled-actions.spec.ts` covers the schedule dialog that contains `CronInput`.
- `frontend/AGENTS.md` says the Next.js version in use has breaking changes and to read `node_modules/next/dist/docs/` before writing frontend code.

## Dependencies
- None on other items. If C-061 has landed first, add a unit test for the derived state in step B2; if not, the Playwright spec is the check.
- GitHub must offer an `ubuntu-26.04` runner label to the repository (the board note says `ubuntu-latest` moved to 26.04 on 2026-10-19).

## Implementation steps
Part A, its own commit and preferably its own PR:
1. Replace every `runs-on: ubuntu-24.04` with `runs-on: ubuntu-26.04` in `.github/workflows/ci.yml`.
2. Push and read each job. Expected trouble spots and what to do:
   - `setup-python` cannot find 3.12: do not change the Python version; stop and report.
   - `playwright install --with-deps` fails on unknown packages: check whether the installed Playwright version lists Ubuntu 26.04 as supported. If it does not, the smallest fix is to run `frontend-e2e` in the Playwright container image for the installed version; record the choice in the PR. Do not leave that one job on 24.04 silently.
   - `packaging-smoke` fails: read the log; the build containers are independent of the runner, so a failure is most likely a runner tool (Docker, tar). Fix the invocation, not the package.
3. Update the places that state the runner: `CONTRIBUTING.md` line 183 mentions the smoke containers (Ubuntu 24.04, Rocky 9), which is still true and stays. Add nothing unless a document names the runner image.
4. Before the next release PR, `release-guard` and `packaging-smoke` must have passed on 26.04 at least once on a PR to `dev`. Note this in `planning/PROGRESS.md`.

Part B, a second commit:
1. In `CronInput`, remove the synchronous state updates from the effect. Keep one piece of state that is only written from the asynchronous callback: `const [result, setResult] = useState<{ forValue: string; data: ValidateCronResponse } | null>(null)`. In the effect, return early when `value` is empty (no state call), otherwise start the timer and in its callback call `setResult({ forValue: value, data: resp })` (or the "Validation failed" object in `catch`), guarded by `cancelled` as today.
2. Derive during render: `const validation = value && result?.forValue === value ? result.data : null` and `const validating = Boolean(value) && result?.forValue !== value`. Remove the `validation` and `validating` state hooks. The rendered output and the request (path, method, body, 250 ms debounce) must stay the same.
3. In `frontend/eslint.config.mjs`, delete the two `"warn"` overrides for `react-hooks/set-state-in-effect` and `react-hooks/purity` so they take the `error` level from `reactHooks.configs.recommended.rules`. Replace the comment above them with one sentence: both rules are errors; new code must derive state during render or reset with a `key`.
4. Run `npm run lint`. Expected: 0 errors, 10 warnings (the nine `incompatible-library` and the unused variable). If `purity` or `set-state-in-effect` reports anything else, fix it the same way; do not add a disable comment.

## Acceptance criteria
- `grep -c "runs-on: ubuntu-24.04" .github/workflows/ci.yml` prints `0` and `grep -c "runs-on: ubuntu-26.04"` prints `21` (or the PR explains each exception).
- Every CI job that runs on a PR to `dev` is green on 26.04, including `frontend-e2e`, `packaging-smoke` and `trivy-scan`.
- `grep -n "set-state-in-effect\|react-hooks/purity" frontend/eslint.config.mjs` shows no `"warn"`.
- `cd frontend && npm run lint` exits 0 with no `set-state-in-effect` or `purity` line, and no more than 10 warnings.
- In the schedule dialog, typing a cron expression still shows "validating" and then the next run times; an invalid expression still shows the error; clearing the field clears the result.

## Test plan
- Frontend: `cd frontend && npx tsc --noEmit && npm run lint`.
- `cd frontend && npx playwright test scheduled-actions.spec.ts` against a running stack. The session's environment may have no stack; run it on local Docker on lin-manager-2, or rely on the `frontend-e2e` CI job, which runs the whole suite.
- If C-061 is in place: add `frontend/components/scheduled-actions/cron-input.test.tsx` covering empty value (no request, no result), a valid response, and a failed request; run `cd frontend && npm test`.
- Part A is tested by CI itself on the PR. Nothing runs on a managed host or on lin-manager.
- Backend is untouched; the full backend suite runs once before the stage review.

## Docs and changelog
- No `docs/` change. `frontend/FRONTEND.md`: add one line under its patterns section that the two rules are errors and how to satisfy them.
- `CHANGELOG.md` `[Unreleased]`: no line. Neither part is user-visible; say so in the PR.

## Risks and rollback
- A runner image change can alter tool versions under a release build. Mitigation: do Part A early in the stage, well before the release PR in stage 3, and require one green `packaging-smoke` and `release-guard`.
- The `CronInput` change could flash a stale result if the `forValue` comparison is wrong. The Playwright spec and the manual check in the criteria cover it.
- Rollback: the two parts are separate commits. Reverting Part A restores 24.04; reverting Part B restores the warnings.

## Assumptions
- Assumption (not yet confirmed): the label `ubuntu-26.04` exists and is generally available for this repository. If it is not, Part A is blocked; Part B still ships.
- Assumption (not yet confirmed): the item may be delivered as two PRs. The board asks for the runner move "in its own PR"; the plan lists one item.
- Assumption (not yet confirmed): the unused variable in `e2e/ux-toast.spec.ts` may be removed in Part B to bring the count to 9. It is a one-line change outside the item's title; leave it if in doubt.

## Definition of done
- [ ] All jobs on `ubuntu-26.04` and green, exceptions explained in the PR.
- [ ] `CronInput` no longer sets state synchronously in an effect; both rules are errors.
- [ ] `npx tsc --noEmit` clean, `npm run lint` 0 errors and at most 10 warnings; new baseline warning count written in `planning/PLAN.md` (the "Frontend lint" row).
- [ ] `frontend/FRONTEND.md` line added.
- [ ] `planning/roadmap.yaml`: C-025 `status: done`; one line in `planning/PROGRESS.md`.
