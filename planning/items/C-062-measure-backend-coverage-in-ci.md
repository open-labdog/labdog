# C-062: Measure backend coverage in CI

| | |
|---|---|
| Edition | community |
| Source | proposed, PR-07 "Measure coverage in CI and record a load-test baseline" (coverage half) |
| Tier / score | P1 / 8.0 |
| Stage | 0 |
| Effort | S |
| Autonomy | A |
| Depends on | none |

## Goal
Every pull request shows the backend's statement coverage as a number, and the plan records the number stage 0 started from, so a stage review can say whether coverage went up or down.

## Scope
In scope:
- Coverage on the `backend-test` job in `.github/workflows/ci.yml`.
- A coverage configuration in `backend/pyproject.toml`.
- Recording the first CI figure in `planning/PLAN.md`.

Out of scope:
- A minimum threshold that fails the build (`--cov-fail-under`). The number is reported, not enforced; see Assumptions.
- Third-party coverage services, badges, or a bot comment on the PR.
- Frontend coverage (C-061 adds the harness; coverage for it is not planned).
- Writing tests to raise the number.

## Context
- `.github/workflows/ci.yml`, job `backend-test` (line 163). Its last step (lines 223 to 225) is `python -m pytest tests/ -v` in `backend/`. Dependencies are installed from `uv.lock` with `--extra dev --extra agent`.
- `backend/pyproject.toml` line 76 already has `pytest-cov>=5` in the `dev` extra, so nothing new is installed. There is no `[tool.coverage.*]` section and no `.coveragerc`. `[tool.pytest.ini_options]` (line 97) sets `asyncio_mode = "auto"` and one marker.
- `planning/PLAN.md`, "Tests, lint and CI" table: "Backend coverage | `pytest tests/ -q --cov=app --cov-report=term` | 76% of statements (30,478 statements, 7,301 missed)", measured in the planning container with default settings. The same table notes the suite took 8 min 14 s with coverage on, and that two tests failed there for environment reasons.
- The backend is async SQLAlchemy, which runs database calls inside greenlets. Coverage's default tracer can miss lines executed in greenlets unless `concurrency` includes `greenlet`. The 76% figure was taken without that setting, so the configured figure may differ.
- The job `release-guard` (line 699) also runs `pytest tests/ -q` on release PRs. It is left without coverage.
- `backend/app/_build_info.py` is generated at package build time (see `CLAUDE.md`, "About / version surface") and may not exist in a checkout.

## Dependencies
- None. `pytest-cov` is already a dev dependency and is in `uv.lock` (`uv lock --check` runs in the job and must stay green; no dependency changes are made).

## Implementation steps
1. `backend/pyproject.toml`: add
   ```toml
   [tool.coverage.run]
   source = ["app"]
   concurrency = ["greenlet", "thread"]
   omit = ["app/_build_info.py"]

   [tool.coverage.report]
   skip_empty = true
   ```
   Do not add `fail_under`.
2. `ci.yml`, `backend-test`, the `pytest` step: change the command to
   `python -m pytest tests/ -v --cov --cov-report=term --cov-report=xml:coverage.xml`.
   `--cov` with no value takes `source` from the configuration.
3. Add a step after it, `if: always()`, named "Coverage summary", in `backend/`:
   ```sh
   total=$(python -m coverage report --format=total)
   echo "Backend coverage: ${total}% of statements" | tee -a "$GITHUB_STEP_SUMMARY"
   ```
   This prints the figure in the job log and on the run's summary page. It needs no extra permissions, so it also works for PRs from forks.
4. Add a step that uploads `backend/coverage.xml` with the already pinned `actions/upload-artifact@cf430e030ddbb5b0abf93d22962f4752f3646cd9 # v7.0.2` (the same pin `frontend-e2e` uses at line 369), artifact name `backend-coverage`, `if: always()`, `retention-days: 14`.
5. Run locally once with the new configuration and compare with the planning figure:
   `cd backend && source .venv/bin/activate && pytest tests/ -q --cov --cov-report=term | tail -5`.
   If the total moves by more than two points against 76%, note in the PR that the `greenlet` setting is the cause (check by running once with `concurrency` removed).
6. After the PR's CI run finishes, edit `planning/PLAN.md`: in the "Backend coverage" row, replace the baseline cell with the CI figure, the commit, and the run date, and keep the old planning-container figure in the notes below the table as "measured without the greenlet setting". Change the note "Coverage is not measured in CI" to say it is, and where to read it.
7. `CONTRIBUTING.md`: in the testing section, add the local command from step 5 and one sentence that CI prints the total on every PR.
8. `backend/.gitignore` or the root `.gitignore`: make sure `.coverage` and `coverage.xml` are ignored (`git check-ignore backend/.coverage backend/coverage.xml`); add them if not.

## Acceptance criteria
- On a PR, the `backend-test` job log contains a line `Backend coverage: NN% of statements`, and the same line is on the run's summary page.
- The run has an artifact named `backend-coverage` containing `coverage.xml`.
- `backend-test` still fails when a test fails (the coverage steps are `if: always()` and do not mask the pytest exit code).
- `grep -n "tool.coverage" backend/pyproject.toml` shows the two sections, and `grep -n "fail_under\|cov-fail-under" backend/pyproject.toml .github/workflows/ci.yml` prints nothing.
- `planning/PLAN.md` holds the CI figure with its commit and date.
- `uv lock --check` passes: `uv.lock` is unchanged.

## Test plan
- Local: `cd backend && source .venv/bin/activate && pytest tests/test_permissions.py -q --cov --cov-report=term` as a quick check that the configuration is read (the report lists `app/...` files only).
- Then the full suite once: `cd backend && source .venv/bin/activate && pytest tests/ -q --cov --cov-report=term`. It needs Postgres through testcontainers or `DATABASE_URL`, as in `planning/PLAN.md`. It takes about eight minutes; run it once, not per edit.
- Lint: `cd backend && ruff check . && ruff format --check .`.
- Workflow syntax: `python3 -c "import yaml; yaml.safe_load(open('.github/workflows/ci.yml'))"`.
- No test file is added: this item changes how tests are run, not what they test. Nothing needs lin-manager-2 or a managed host.

## Docs and changelog
- `CONTRIBUTING.md` (step 7) and `planning/PLAN.md` (step 6).
- `CLAUDE.md`, "Running Tests": add the coverage command on one line.
- No `docs/` change and no `CHANGELOG.md` line: not user-visible.

## Risks and rollback
- Coverage slows the suite. If `backend-test` becomes more than about 30% slower than the last runs on `dev`, say so in the PR; do not drop `-v` or split the suite in this item.
- `concurrency = ["greenlet", ...]` requires the `greenlet` package, which SQLAlchemy's async support already installs. If coverage errors on it, remove the setting, keep the rest, and record that the figure undercounts async database code.
- A figure that differs between the planning container and CI can look like a regression. Step 6 keeps both and says why.
- Rollback: revert the commit. No data is involved.

## Assumptions
- Assumption (not yet confirmed): no failing threshold. PR-07 asks for a number to compare; a gate is a separate decision. Question for Dennis: should a later stage add `fail_under` at the recorded figure?
- Assumption (not yet confirmed): statement coverage is enough; branch coverage (`branch = true`) is not turned on, so the figure stays comparable with the 76% baseline.
- Assumption (not yet confirmed): the step summary and the job log count as "CI prints a backend coverage figure on every PR". No PR comment is posted, which avoids a write token on fork PRs.

## Definition of done
- [ ] `pyproject.toml` and `ci.yml` changed; `uv.lock` untouched.
- [ ] A PR run shows the coverage line and the artifact.
- [ ] `planning/PLAN.md` updated with the CI figure; `CONTRIBUTING.md` and `CLAUDE.md` lines added.
- [ ] `planning/roadmap.yaml`: C-062 `status: done`; one line in `planning/PROGRESS.md` that includes the figure.
