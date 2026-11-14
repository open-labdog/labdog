# C-061: A unit-test harness for the frontend

| | |
|---|---|
| Edition | community |
| Source | proposed, PR-06 "A unit-test harness for the frontend" |
| Tier / score | P1 / 5.0 |
| Stage | 0 |
| Effort | M |
| Autonomy | A |
| Depends on | none |

## Goal
`npm test` in `frontend/` runs unit tests in seconds without a backend, CI runs them on every PR, and a frontend bug can be pinned down with a failing test before it is fixed.

## Scope
In scope:
- A test runner, its configuration and an `npm test` script.
- A first set of real tests: two pure modules and one component.
- A CI job, and a short section in the contributor docs.

Out of scope:
- Coverage measurement or a coverage threshold for the frontend.
- Rewriting or moving the Playwright specs in `frontend/e2e/`.
- Testing pages under `frontend/app/` (they need routing and auth context; later items add tests as they touch pages).
- Mocking the API layer in general. Tests that need `apiFetch` mock it per test.

## Context
- `frontend/package.json` scripts: `dev`, `build`, `start`, `lint`, `test:e2e`, `test:e2e:ui`. There is no `test` script and no unit-test dependency. React is 19.2.3, Next is ^16.4.3, TypeScript 5.9.3, ESLint ^10.10.0.
- `frontend/tsconfig.json`: `"jsx": "react-jsx"`, `"moduleResolution": "bundler"`, path alias `"@/*": ["./*"]`, and `include` covers `**/*.ts` and `**/*.tsx`, so test files are type-checked by `npx tsc --noEmit`.
- `frontend/playwright.config.ts`: `testDir: "./e2e"`; its files are `*.spec.ts`. Unit tests must use a different suffix so neither runner picks up the other's files.
- Good first targets, both pure and free of React:
  - `frontend/lib/cron.ts`, `cronToHuman(schedule: string): string` (line 25). Returns the input unchanged for shapes it does not know.
  - `frontend/lib/ansible-log.ts`, `parseAnsibleLog(text: string): LogLine[]` (line 60) and `FAILURE_KINDS` (line 44). `frontend/e2e/ansible-log-sample.txt` is an existing sample log.
- A first component target: `frontend/components/scheduled-actions/cron-input.tsx`, `CronInput`, which debounces a call to `apiFetch` (`frontend/lib/api.ts`) and uses `useScheduleTimezone` (`frontend/lib/schedule-timezone.ts`).
- `.github/workflows/ci.yml`: `frontend-lint` (line 138) and `frontend-build-check` (line 227) both set up Node 24 with the npm cache and run `npm ci --prefer-offline` in `frontend/`. `frontend-audit` runs `npm audit --audit-level=high --omit=dev`, so dev dependencies are not audited there.
- `frontend/AGENTS.md` asks anyone writing frontend code to read `node_modules/next/dist/docs/` first, because this Next.js version differs from older ones.
- The Enterprise build copies this `frontend/` directory and type-checks the merged tree, so test files and the new dev dependencies travel with it. They must type-check on their own.

## Dependencies
- None on other items. C-025 (Part B) can add a `CronInput` test once this harness exists.
- New dev dependencies, all MIT licensed: `vitest`, `jsdom`, `@testing-library/react`, `@testing-library/dom`, `@testing-library/user-event`. Check each licence in `node_modules/<name>/package.json` after install and stop if any is not MIT, ISC, BSD or Apache-2.0.

## Implementation steps
1. In `frontend/`: `npm install --save-dev vitest jsdom @testing-library/react @testing-library/dom @testing-library/user-event`. Commit `package.json` and `package-lock.json`. Do not touch the `overrides` block unless `npm audit` reports a high finding from the new packages.
2. Add `frontend/vitest.config.mts`:
   - `test.environment: "jsdom"`.
   - `test.include: ["lib/**/*.test.{ts,tsx}", "components/**/*.test.{ts,tsx}", "hooks/**/*.test.{ts,tsx}"]`.
   - `test.exclude: ["e2e/**", "node_modules/**", ".next/**", "out/**"]`.
   - `resolve.alias: { "@": <absolute path of frontend/> }` to match the tsconfig alias.
   - `test.globals: false`. Tests import `describe`, `it`, `expect`, `vi` from `"vitest"`, so no global type configuration is needed and `tsc` stays clean.
   - `test.setupFiles: ["./vitest.setup.ts"]`.
3. Add `frontend/vitest.setup.ts` that calls `cleanup()` from `@testing-library/react` in an `afterEach`.
4. `package.json` scripts: `"test": "vitest run"` and `"test:watch": "vitest"`.
5. Add `frontend/lib/cron.test.ts`: the fixed strings ("Every minute", "Every hour", "Every day at midnight"), `0 3 * * *`, `*/15 * * * *`, an hourly-at-minute case, a weekday list, and two inputs that must come back unchanged (six fields; a month other than `*`).
6. Add `frontend/lib/ansible-log.test.ts`: feed `parseAnsibleLog` a short inline log with an ok task, a failed task and an unreachable host; assert the `kind` of each line and that the failure lines are in `FAILURE_KINDS`. Read the function first and assert what it does today; do not change it.
7. Add `frontend/components/scheduled-actions/cron-input.test.tsx`: mock `@/lib/api` with `vi.mock` so `apiFetch` resolves a valid response; mock `@/lib/schedule-timezone` if it calls the API; use `vi.useFakeTimers()`; render `CronInput` with a value, advance 250 ms, and assert one call to `/api/scheduled-actions/validate-cron` with `{ cron: value }` and that the next-run text appears. Add a second case with an empty value: no call is made.
8. If `globalIgnores` in `frontend/eslint.config.mjs` needs nothing new, leave it. Test files are linted like other code.
9. CI: add a job `frontend-test` to `.github/workflows/ci.yml`, a copy of `frontend-lint` (same `if:`, runner, pinned `actions/checkout` and `actions/setup-node`, `npm ci --prefer-offline`) whose last step is `npm test` in `frontend/`.
10. Docs: `frontend/FRONTEND.md`, new section "Unit tests" (where files go, the `.test.ts(x)` suffix, import from `vitest`, mock `apiFetch` per test, keep tests out of `app/`). `CLAUDE.md`: add "Frontend unit" under "Running Tests" and Vitest to the Testing row of the tech stack table. `CONTRIBUTING.md`: add `npm test` next to the lint command and `frontend-test` to the CI job lists (lines 225 to 235).

## Acceptance criteria
- `cd frontend && npm test` exits 0 and reports at least three test files and more than ten passing tests, in under 30 seconds, with no backend running.
- Changing an expected string in `cron.test.ts` makes `npm test` exit non-zero (check once by hand, then undo).
- `cd frontend && npx tsc --noEmit` is clean and `npm run lint` reports no errors and no new warnings.
- `npx playwright test --list` shows the same number of spec files as before (no `.test.` file is picked up).
- `npm run build` still succeeds and `out/` contains no test file.
- The `frontend-test` job runs and passes on the PR.

## Test plan
- `cd frontend && npm test`
- `cd frontend && npx tsc --noEmit && npm run lint`
- `cd frontend && npm run build`
- `cd frontend && npx playwright test --list | tail -1` (needs no running stack).
- `cd frontend && npm audit --audit-level=high --omit=dev` (unchanged result expected).
- Everything runs in the session's own environment. Nothing needs lin-manager-2 or a managed host.

## Docs and changelog
- `frontend/FRONTEND.md`, `CLAUDE.md`, `CONTRIBUTING.md` as in step 10.
- No `docs/` change and no `CHANGELOG.md` line: not user-visible.

## Risks and rollback
- Vitest may not transform JSX the way the Next compiler does. `"jsx": "react-jsx"` in tsconfig is what it reads; if a component test fails to compile, add `esbuild: { jsx: "automatic" }` to the config before reaching for a React plugin.
- Fake timers with async `apiFetch` mocks can hang. Use `await vi.advanceTimersByTimeAsync(250)`.
- New dev dependencies widen the Dependabot surface. They are dev-only and are not in the production audit.
- Rollback: revert the commit; no runtime code changes.

## Assumptions
- Assumption (not yet confirmed): Vitest with jsdom and Testing Library is the runner. PR-06 names no tool. It was chosen because it reads TypeScript and the path alias without a Babel setup. Question for Dennis only if he prefers another runner.
- Assumption (not yet confirmed): tests sit next to the code they test, as `*.test.ts(x)`, and not in a separate tree.
- Assumption (not yet confirmed): a separate `frontend-test` job is preferred over a step inside `frontend-lint`, so a failing test and a lint error show up as different checks.

## Definition of done
- [ ] Config, setup file, scripts and the three test files added; lockfile committed.
- [ ] `npm test`, `tsc`, lint and build pass locally; `frontend-test` green in CI.
- [ ] `FRONTEND.md`, `CLAUDE.md`, `CONTRIBUTING.md` updated.
- [ ] `planning/PLAN.md`: the note "The frontend has no unit tests" updated with the command and test count.
- [ ] `planning/roadmap.yaml`: C-061 `status: done`; one line in `planning/PROGRESS.md`.
