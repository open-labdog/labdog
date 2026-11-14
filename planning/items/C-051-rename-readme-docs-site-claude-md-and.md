# C-051: Rename: README, docs site, CLAUDE.md and changelog wording

| | |
|---|---|
| Edition | community |
| Source | proposed (PR-02 and PR-05), decision D-02 |
| Tier / score | P0 / 7.0 |
| Stage | 2 |
| Effort | S |
| Autonomy | A |
| Depends on | C-048 |

## Goal

A new user reading the README or the docs site, and a new session reading `CLAUDE.md`, see only the name unbork and the names that C-048 to C-050 introduced. The old name appears only where it is deliberately kept as a fallback or as history, and a check keeps it that way.

## Scope

In scope:
- `CLAUDE.md` brought up to date (name, commands, test count, new variable and path names).
- `CHANGELOG.md`: the `[Unreleased]` entry for the rename and the compare links at the foot.
- A final sweep of `README.md`, `docs/`, `website/`, `CONTRIBUTING.md`, `SECURITY.md`, `BUGS.md`, `.env.example` comments and `packaging/` comments for names changed by C-048, C-049 and C-050.
- Ansible play names shown in run output, with the old names kept as lookup keys.
- Code comments, docstrings and internal identifiers, as a separate mechanical PR.
- A guard that fails when the old name appears outside an allowlist.

Out of scope:
- Any behaviour change. Fallback logic belongs to C-048, C-049 and C-050.
- Rewriting released changelog entries. History stays as written.
- The Postgres database and role names ([D-02](../DECISIONS.md)).

## Context

- Pull request #297 ("Call the product unbork in the docs, docs site, UI and messages") merged into `dev` on 2026-11-14 and already renamed prose in `README.md`, `CONTRIBUTING.md`, `SECURITY.md`, `docs/`, `website/` (including `docusaurus.config.ts`), frontend copy and backend message strings, 218 files in all. The branch this plan was written on predates it: here `README.md` is still headed "LabDog" and `website/docusaurus.config.ts:11,72,74,109` still say `LabDog`. Rebase onto `dev` before starting and measure again.
- #297 did not touch `CLAUDE.md`, `CHANGELOG.md`, `BUGS.md`, `Dockerfile` comments or `packaging/`. `CLAUDE.md` is headed "LabDog Development Guide", says "Backend (326 tests)" at line 35 (the suite has about 3,400 test functions before parametrisation), and uses the old names in about 19 places.
- #297 deliberately left identifiers alone: the play names in `PLAY_NAME_TO_MODULE` (`backend/app/ansible_runtime/composer.py:81-90`, for example `"LabDog Package Management"`, set in `backend/app/packages/generator.py`, `resolver/generator.py:33`, `hosts_mgmt/generator.py:36`, `cron/generator.py:85`, `user_mgmt/generator.py:218`, `ca_certs/generator.py:170`), `backend/app/ai/labdog_host.py` (`labdog_runs_on`), `LabDogGroupYAML` and `LabDogGlobalYAML` (`backend/app/gitops/schema.py`), run-log kinds `labdog` and `labdog-failed` (`backend/app/ai/run_log.py:43-46`), Ansible register names such as `labdog_overrides`.
- `CHANGELOG.md:3498-3501` links compare URLs under `open-labdog/labdog`.
- The rename map counted 401 files and 2,668 occurrences after #297, 661 of them in tests.

## Dependencies

- C-048 must be merged. This item should run after C-049 and C-050 as well (it is fourth in the stage order), because the sweep documents their final names. If it runs earlier, repeat step 3 afterwards.
- [D-17](../DECISIONS.md) also edits `CLAUDE.md` and `CONTRIBUTING.md`; take care not to undo that wording.

## Implementation steps

1. Rebase on `dev`. Run `git grep -il labdog -- . ':!planning' | wc -l` and record the number in the PR.
2. `CLAUDE.md`: retitle "unbork Development Guide"; replace product-name prose; replace `LABDOG_*`, `labdog.toml`, `/etc/labdog`, chain and package names with the new ones and one sentence each on the fallback; replace "Backend (326 tests)" with a heading that carries no number and a line saying how to count (`pytest --collect-only -q | tail -1`); check every command and file path in it still exists. Do not change sections the project owner added about diagrams.
3. Docs sweep: for each of `README.md`, `docs/**`, `website/**`, `CONTRIBUTING.md`, `SECURITY.md`, `BUGS.md`, `.env.example`, `packaging/**` comments, `Dockerfile` comments, replace old names with the new ones. Keep old names only in `docs/upgrade.md` (the old-to-new table and upgrade notes) and in sections that describe the fallbacks.
4. `CHANGELOG.md`: add one `[Unreleased]` "Changed" entry that summarises the rename and links `docs/upgrade.md`. Point the `[Unreleased]` compare link at `open-unbork/unbork`. Leave released sections and their links unchanged (GitHub redirects them).
5. Play names: rename the six "LabDog …" plays to "unbork …" in the generators and add the new names to `PLAY_NAME_TO_MODULE`, keeping the old keys so events from a run started before the upgrade still resolve. Update `tests` that assert play names. This is the only functional edit in this item.
6. Separate PR, mechanical: rename `labdog_host.py` to `server_host.py` and `labdog_runs_on` to `server_runs_on`, `LabDog*YAML` classes to `Unbork*YAML`, register names, comments and docstrings. Keep run-log kinds `labdog` and `labdog-failed` if they are stored in the database or sent to the frontend; check `backend/app/ai/run_log.py` consumers first and leave them if so.
7. Add `scripts/check-old-name.sh` (or a test `backend/tests/test_old_name_allowlist.py`) that fails when `labdog` (case-insensitive) appears in a tracked file not listed in `planning/` or in an allowlist file `.old-name-allowlist`. Seed the allowlist with: `CHANGELOG.md`, `docs/upgrade.md`, `backend/alembic/versions/*`, `backend/app/env_compat.py`, `backend/app/markers.py`, `backend/app/ansible_runtime/composer.py`, packaging maintainer scripts, the dev and CI files that keep the `labdog` Postgres names, and the tests that exercise the fallbacks. Wire it into the lint job in `.github/workflows/ci.yml`.

## Acceptance criteria

- `CLAUDE.md` contains no "LabDog" outside sentences that describe a fallback, and no hard-coded test count.
- `cd website && npm run build` passes (it fails on broken links and anchors).
- The old-name check passes, and fails when a new file containing "labdog" is added outside the allowlist.
- A sync's run output shows play names beginning "unbork", and module outcomes are still attributed (`aggregate_module_outcomes` resolves both old and new play names).
- `git diff --stat` for the step 6 PR shows no change under `backend/alembic/versions/` and the full test suite passes without test logic changes other than renamed imports.

## Test plan

- `cd backend && source .venv/bin/activate && pytest tests/ -q -k "composer or outcomes or orchestrator"`, then `pytest tests/ -q` for the step 6 PR.
- `ruff check . && ruff format --check .`
- `cd frontend && npx tsc --noEmit && npm run lint`
- `cd website && npm ci && npm run build`
- `scripts/check-old-name.sh`
- Nothing here needs a running stack or a managed host.

## Docs and changelog

- This item is the docs change. `CHANGELOG.md` `[Unreleased]`, Changed: "The product is called unbork throughout the documentation, run output and developer guide. See the upgrade guide for the old and new names."

## Risks and rollback

- A sweep that renames an identifier a fallback depends on (a legacy constant, a migration, a database value). The allowlist in step 7 lists those files; never run a blind search-and-replace across them.
- The mechanical PR conflicts with every open branch. Merge it last in the stage and announce it in `planning/PROGRESS.md`.
- Rollback: revert either PR; neither changes stored data.

## Assumptions

- [D-02](../DECISIONS.md) and accepted proposals PR-02 and PR-05.
- Assumption (not yet confirmed): the mechanical identifier sweep belongs to this item. The rename map lists it as a separate last step and no item names it. If Dennis prefers, drop step 6 to a later stage; nothing a user sees depends on it.
- Assumption (not yet confirmed): released changelog sections keep the old name, as #297 did.

## Definition of done

- [ ] `CLAUDE.md`, docs sweep, changelog entry and play names merged to `dev`.
- [ ] Old-name check in CI.
- [ ] Mechanical PR merged or explicitly deferred with a note in `planning/PROGRESS.md`.
- [ ] `planning/roadmap.yaml` status set to done; `planning/PROGRESS.md` line added.
