# C-064: Make planning/ a named exception to the plans/ rule

| | |
|---|---|
| Edition | community |
| Source | decision, D-17 |
| Tier / score | P0 / 6.0 |
| Stage | 0 |
| Effort | S |
| Autonomy | A |
| Depends on | none |

## Goal
A contributor or a session reading the project rules sees two different things: `plans/` is branch-scoped scratch that never reaches `dev`, and `planning/` is the long-lived staged roadmap that stays on `dev`. No checklist or build step treats `planning/` as a mistake.

## Scope
In scope:
- Wording in `CONTRIBUTING.md`, `CLAUDE.md` and `.github/PULL_REQUEST_TEMPLATE/release.md`.
- One line in `.dockerignore`, and the stale comment in `.gitignore`.

Out of scope:
- Changing the `plans/` rule itself. It stays exactly as it is.
- Whether `planning/` should be kept off `main`. D-17 says "keep `planning/` on dev" and does not settle `main`; see Assumptions.
- Any CI path filter for planning-only changes.

## Context
- `CONTRIBUTING.md` lines 107 to 124, "Working with `plans/` — branch-scoped scratchpad". Step 4 (line 121) says "Delete `plans/` before opening the PR. `dev` and `main` must never carry it."
- `CONTRIBUTING.md` lines 126 to 131, the "Where work is tracked" table. It has rows for the roadmap board, `BUGS.md` and `plans/`, and no row for `planning/`.
- `CONTRIBUTING.md` line 224, release checklist: "`plans/` is absent (never ships on `dev` or `main`)."
- `.github/PULL_REQUEST_TEMPLATE/release.md` line 22: "**`plans/` is absent** — it must never ship on `dev` or `main`".
- `CLAUDE.md` has "Working with `plans/`" (from line 416) and, already added, "Staged roadmap (`planning/`)" (from line 436), which says `planning/` is not the scratchpad and is meant to stay on `dev`. The `plans/` section does not point at it, and the "Where work is tracked" sentence (lines 430 to 434) does not list `planning/`.
- `.dockerignore` ends with "# Plans, local-only state" and `plans/`. `planning/` is not listed. The `Dockerfile` copies named paths only (`frontend/`, `backend/app/`, `backend/alembic/`, `VERSION`), so `planning/` is not in the image today; the ignore line keeps it out of the build context and guards against a later `COPY . .`.
- `.gitignore` lines 74 to 79 carry a comment that says "`plans/` is local-only" above a commented-out `#/plans/`. That contradicts CONTRIBUTING, which says plan files are committed on the work branch.
- `packaging/Makefile` copies `backend/app`, `backend/alembic`, `alembic.ini` and `frontend/out` only (lines 125, 146), so packages do not contain `planning/`. Nothing to change there.

## Dependencies
- Decision [D-17](../DECISIONS.md).
- None on other items. This is the first item of stage 0 so that the stage's own PRs do not trip the release checklist.

## Implementation steps
1. `CONTRIBUTING.md`, the `plans/` section: add one short paragraph after step 5: "`planning/` is a different directory and is not covered by this rule. It holds the staged roadmap (`PLAN.md`, `roadmap.yaml`, `DECISIONS.md`, `PROPOSALS.md`, `PROGRESS.md`, `stages/`, `items/`, `reviews/`), is committed on `dev` and is updated by the PRs that carry out a stage." Do not change steps 1 to 5.
2. `CONTRIBUTING.md`, the "Where work is tracked" table: add a row `planning/` with the purpose "Staged execution plan: one spec per item, one file per stage, decisions and progress log. Long-lived, kept on `dev`." Keep the `plans/` row.
3. `CONTRIBUTING.md` line 224: change to "`plans/` is absent (never ships on `dev` or `main`). `planning/` is expected and stays."
4. `.github/PULL_REQUEST_TEMPLATE/release.md` line 22: change to "**`plans/` is absent** — it must never ship on `dev` or `main`. (`planning/` is the staged roadmap and stays.)"
5. `CLAUDE.md`, "Working with `plans/`": add one sentence after step 4: "This rule is about `plans/` only; `planning/` (next section) stays on `dev`." In the "Where work is tracked" sentence add "`planning/` (the staged roadmap, kept on `dev`)". Leave the existing "Staged roadmap (`planning/`)" section as it is.
6. `.dockerignore`: under the `plans/` line add `planning/`, and change the comment to "# Plans (branch scratch) and the staged roadmap: never part of an image".
7. `.gitignore` lines 74 to 79: reword the comment so it no longer says `plans/` is local-only. Suggested: "`plans/` is a branch-scoped scratchpad (see CONTRIBUTING.md). It is committed on work branches and deleted before the PR, so it is not ignored here." Remove the commented-out `#/plans/` line.
8. Search for any other statement of the rule and align it: `grep -rn "plans/" --include=*.md --include=*.yml --include=*.toml . | grep -v node_modules | grep -v "^./planning/"`. `.gitleaks.toml` line 47 to 55 mentions a historical `plans/` path and needs no change.

## Acceptance criteria
- `grep -n "planning/" CONTRIBUTING.md` shows the new paragraph, the table row and the release checklist line.
- `grep -n "planning/" .github/PULL_REQUEST_TEMPLATE/release.md .dockerignore` shows one hit in each file.
- `CLAUDE.md` "Working with `plans/`" points to the `planning/` section, and the tracking sentence lists `planning/`.
- The `plans/` workflow steps 1 to 5 in `CONTRIBUTING.md` are unchanged (`git diff` shows additions only around them).
- No file says or implies that `planning/` must be deleted before a PR or a release.
- `docker build` context no longer includes `planning/` (see Test plan).

## Test plan
- Docs only, so no backend or frontend test changes. Run the cheap checks the stage needs anyway:
  `cd backend && source .venv/bin/activate && ruff check . && ruff format --check .` (unchanged, expected clean).
- Build context check, on any machine with Docker (lin-manager-2 if the session has none):
  `printf 'FROM busybox\nCOPY . /ctx\n' | docker build -q -f - -t ctxcheck . && docker run --rm ctxcheck ls /ctx | grep -c '^planning$'` must print `0`. Remove the image afterwards with `docker rmi ctxcheck`.
- `cd website && npm run build` is not needed: nothing under `docs/` or `website/` changes.

## Docs and changelog
- `CONTRIBUTING.md`, `CLAUDE.md`, the release PR template as above.
- No `docs/` change and no `CHANGELOG.md` line: nothing user-visible changes.

## Risks and rollback
- Risk: the wording could be read as loosening the `plans/` rule. The steps add text and leave the rule's own sentences untouched to avoid that.
- Risk: a release PR (`dev` to `main`) now carries `planning/` to `main`. That is the reading of D-17 used here; see Assumptions.
- Rollback: revert the one commit. No data or behaviour is involved.

## Assumptions
- [D-17](../DECISIONS.md) names the four files to amend. The `.gitignore` comment fix is added because it states the opposite of CONTRIBUTING; it changes no ignore behaviour.
- Assumption (not yet confirmed): `planning/` is allowed to reach `main` through release PRs, because D-17 says "keep on dev" and names no step that strips it. Question for Dennis: should `main` carry `planning/`, or should the release checklist remove it? If it must be stripped, that is a new item, not part of this one.
- Assumption (not yet confirmed): the list of files inside `planning/` named in step 1 matches what exists when the item runs. Check with `ls planning/` and correct the list.

## Definition of done
- [ ] The five files are amended as in the steps.
- [ ] The acceptance greps pass and the build-context check prints `0`.
- [ ] `planning/roadmap.yaml`: C-064 `status: done`, `branch` filled in.
- [ ] One line appended to `planning/PROGRESS.md`.
- [ ] No `CHANGELOG.md` entry (not user-visible), stated in the PR description.
