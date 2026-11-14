# C-002: Release dev to main as unbork 2.0.0

| | |
|---|---|
| Edition | community |
| Source | roadmap, board item "Release dev to main"; decision D-03 |
| Tier / score | P0 / 17.0 |
| Stage | 3 |
| Effort | S |
| Autonomy | B |
| Depends on | C-001, S-007, S-008, S-009, S-014, C-051 |

## Goal

A `dev` to `main` pull request titled "Release v2.0.0" is open, complete and green, so
that Dennis can release unbork 2.0.0 by merging it. The session opens the PR and stops
there.

## Scope

In scope:
- `VERSION`, the `CHANGELOG.md` release section, the release checklist, and the PR.
- The three additions D-03 names: the "Upgrading to 2.0.0" section, the reworded
  "Versioning" section, and the fixed image tag in `docs/production-deploy.md`. C-001
  writes them; this item confirms they are on `dev`.
- A post-merge checklist for Dennis.

Out of scope:
- Merging the PR, pushing to `main`, creating a tag or a GitHub Release by hand, or
  pushing an image. CI does the last three after Dennis merges.
- Any force push or history rewrite, on any branch.
- Deleting branches, including the old `release/1.0.0`.
- Upgrading the live instance on lin-manager.

## Context

- `VERSION` holds `1.1.0`. It is the only version source: `release-artifacts` syncs
  `backend/pyproject.toml` and `frontend/package.json` from it after the merge, and
  `CONTRIBUTING.md` says not to bump those by hand.
- `.github/workflows/ci.yml`: `version-check` (line 55) runs on pull requests into `main`
  and requires `VERSION` to be valid semver, different from `main`'s, and without an
  existing `v<VERSION>` tag. After the merge, `packaging-smoke` gates
  `release-artifacts`, which builds the packages, tags the merge commit and creates the
  GitHub Release; `build-image` pushes the image; `tag-release-image` adds `:2.0.0`,
  `:2.0` and `:2`, and copies to the old Docker Hub repository while
  `LEGACY_DOCKER_HUB_REPO` is set; `pages-build` and `pages-deploy` publish the docs.
- `CONTRIBUTING.md`, "Release process" (line 153): sync `main` into `dev` first, because
  `main` requires the PR branch to be up to date; then `VERSION`, then `CHANGELOG.md`,
  then the PR with the `release.md` template.
- `.github/PULL_REQUEST_TEMPLATE/release.md` is the checklist. One of its lines says
  "`plans/` is absent". D-17 keeps `planning/` on `dev`; C-064 amends the template and
  `.dockerignore`.
- `CHANGELOG.md`: `[Unreleased]` starts at line 8 and the `[1.1.0]` section (dated 2026-11-06) at line 162.
  The 1.0.0 entry links to `docs/upgrade.md#upgrading-to-100`. Comparison link
  references are at the bottom of the file.
- `/mnt/project-files/release-version/first-unbork-version.md` explains why 2.0.0: it is
  honest under the versioning promise, followers of the old `:1` image tag are not moved
  onto a renamed product without choosing to, and the rename lands in a major release.
- `dev` is more than 630 commits ahead of `main`.

## Dependencies

- C-001 (upgrade verified and documented), S-007 (README editions), S-008, S-009, S-014
  and C-051 merged to `dev`, and the stage 2 review closed with no critical or high
  finding open.
- C-064 merged, or its template change included here.
- Dennis: merges the PR, and before that confirms the repository variables and secrets
  the release jobs use are present (Docker Hub repository and token, the legacy
  repository variable if copies are still wanted). The session does not read or change
  secrets.

## Implementation steps

1. Confirm every dependency is `done` in `planning/roadmap.yaml`, and that `dev` is green
   in CI at the commit to be released. Stop and report if not.
2. On a work branch from `dev`:
   1. If `origin/main` has commits `dev` lacks, merge `origin/main` into the branch (a
      normal merge commit, never a rebase of shared history).
   2. Set `VERSION` to `2.0.0`.
   3. In `CHANGELOG.md`, rename `[Unreleased]` to `[2.0.0]` with the date of the PR, in the same heading form as the `[1.1.0]` section, add a
      fresh empty `[Unreleased]` above it, and update the comparison links
      (`v1.1.0...v2.0.0` and `v2.0.0...HEAD`).
   4. Put a short introduction at the top of the `[2.0.0]` section: LabDog is now unbork;
      why this is a major version; a link to
      `docs/upgrade.md#upgrading-to-200`; the items an operator must act on. Check that
      the anchor matches the heading C-001 wrote.
   5. Read the whole `[2.0.0]` section once for leftovers: entries that still say LabDog
      for the product (C-051 decides the wording rule), duplicated entries, and entries
      for things that were later reverted.
3. Check the D-03 additions are present: `grep -n "Upgrading to 2.0.0" docs/upgrade.md`,
   the "Versioning" section speaks of 2.x, and
   `grep -rn "unbork/unbork:1.0.0" docs/` prints nothing.
4. Go through `release.md` line by line and make each true: `BUGS.md` holds only open
   bugs; `docs/` covers every entry in the `[2.0.0]` section; `plans/` is absent;
   `planning/` is handled as C-064 decided.
5. Open a PR from the work branch into `dev`, let CI pass, and merge it there as any
   other change. Do not push to `dev` directly.
6. Open the release PR `dev` into `main` with the `release.md` template, title
   "Release v2.0.0". Fill in every checkbox that is true, write the Highlights, and add
   a "After merging" list for Dennis:
   - watch `packaging-smoke`, `release-artifacts`, `build-image`, `tag-release-image`,
     `pages-build` and `pages-deploy` on the merge commit;
   - check the GitHub Release `v2.0.0` has four files and that `SHA256SUMS` verifies;
   - `docker pull unbork/unbork:2.0.0` and check `GET /api/version`;
   - check the docs site (C-003 picks this up);
   - sync `main` back into `dev` (`CONTRIBUTING.md` gives the command);
   - set the shipped roadmap board items to Done.
7. Wait for CI on the release PR. Fix failures with normal PRs into `dev`. When
   everything is green, tell Dennis the PR is ready, with its link, and stop.

## Acceptance criteria

- A PR from `dev` into `main` titled "Release v2.0.0" is open and not merged.
- On it, `version-check` passes, and so do lint, `backend-test`,
  `frontend-build-check`, `docs-build-check`, `build-test-image`, trivy and
  `packaging-smoke` (with the C-052 upgrade targets).
- `VERSION` on `dev` is `2.0.0`; `backend/pyproject.toml` and `frontend/package.json`
  were not hand-bumped.
- `CHANGELOG.md` has `[2.0.0]` with a date, an empty `[Unreleased]` above it, and correct
  comparison links.
- Every checkbox in the PR body is ticked or has a written reason.
- `git ls-remote --tags origin refs/tags/v2.0.0` prints nothing: no tag was created by
  the session.
- The session's git history shows no push to `main`, no force push and no merge of the
  release PR.

## Test plan

- CI on the release PR is the test. Nothing new is run locally except:
  `cd backend && source .venv/bin/activate && pytest tests/ -q` once on the release
  commit if CI is unavailable, and `cd website && npm run build`.
- Final check before telling Dennis: deploy the `:test-<sha>` image of the release
  commit on local Docker on lin-manager-2, sign in, and run one sync against a host
  named "test". The live instance on lin-manager is not touched.

## Docs and changelog

- `CHANGELOG.md` as in step 2. `docs/` changes belong to C-001; this item only verifies
  them.
- `planning/PROGRESS.md`: a line with the PR link.

## Risks and rollback

- `version-check` fails if `dev` is behind `main` or the tag exists. Step 2.1 and the
  tag check cover both.
- The release jobs run only after the merge, so a failure there happens on `main`. The
  jobs are idempotent on the tag; a failed run is fixed by a follow-up PR through `dev`
  and a patch release, never by rewriting `main`.
- A bad release is pulled back by releasing 2.0.1, or by operators pinning 1.1.0 as
  `docs/upgrade.md` describes. Tags and releases are not deleted by the session.
- If Dennis asks for changes after the PR is open, they go through `dev`; the release PR
  updates itself.

## Assumptions

- [D-03](../DECISIONS.md): the version is 2.0.0, with the three documentation additions.
- [D-17](../DECISIONS.md): `planning/` stays on `dev`. Whether it reaches `main` with the
  release is settled in C-064's spec; this item follows it.
- Assumption (not yet confirmed): the release date in the changelog is the day the PR is
  opened; Dennis may ask to change it to the merge day.
- Assumption (not yet confirmed): no release candidate tag is wanted first. Question for
  Dennis: release 2.0.0 directly, or a `2.0.0-rc.1` pre-release first? `version-check`
  and `tag-release-image` both support a pre-release.

## Definition of done

- [ ] `VERSION` and `CHANGELOG.md` updated on `dev` through a normal PR.
- [ ] Release PR open, template filled in, CI green, not merged.
- [ ] Final check on lin-manager-2 done.
- [ ] Dennis told, with the link and the after-merge list.
- [ ] `planning/roadmap.yaml`: C-002 set to "review" until Dennis merges, then done.
- [ ] One line appended to `planning/PROGRESS.md`.
