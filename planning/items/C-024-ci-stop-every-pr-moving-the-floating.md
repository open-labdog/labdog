# C-024: CI: stop every PR moving the floating :test image tag

| | |
|---|---|
| Edition | community |
| Source | roadmap, "CI and toolchain maintenance: floating :test tag, Ubuntu 26.04, React Compiler lint rules" (first part) |
| Tier / score | P1 / 7.0 |
| Stage | 0 |
| Effort | S |
| Autonomy | A |
| Depends on | none |

## Goal
A pull request can no longer change what an install following a floating image tag runs. Test images are published under an immutable `:test-<short sha>` tag only, and a PR from a fork no longer shows a red image job the contributor cannot fix.

## Scope
In scope:
- The `build-test-image` job in `.github/workflows/ci.yml` and the comments around it.
- The places that describe the tag: `CLAUDE.md`, `CONTRIBUTING.md`.

Out of scope:
- Deleting the existing `:test` tag from Docker Hub. It is left where it is; removing it is Dennis's call.
- Repointing any running install. No session changes the instance on lin-manager for this item.
- `build-image` (`:latest`, `:<sha>`), `build-dev-image` (`:dev-latest`, `:dev-<short sha>`) and `tag-release-image`.
- The trivy job's rules.

## Context
- `.github/workflows/ci.yml`, job `build-test-image` (starts at line 450). Its `if:` runs it on every pull request not labelled `no-test-image`, on every push to `dev`, and on `workflow_dispatch` off `main`. Its `docker/build-push-action` step pushes two tags (lines 488 to 490): `${{ vars.DOCKER_HUB_REPO }}:test` and `${{ vars.DOCKER_HUB_REPO }}:test-${{ steps.meta.outputs.short_sha }}`.
- The job's output `image_ref` (line 481) is the immutable `:test-<short sha>` tag. `trivy-scan` (line 1012) `needs: [build-test-image]`, runs only when that job succeeded, and scans `needs.build-test-image.outputs.image_ref`. The floating tag is used by nothing in the workflow.
- The comment above the job (lines 446 to 449) explains the `no-test-image` label by the floating tag: "`:test` floats, and an install that follows it would pick up a work-in-progress branch". The summary comment at line 405 says "→ :test + :test-<short_sha>".
- The job logs in to Docker Hub with `secrets.DOCKER_HUB_PAT`. On a PR from a fork that secret is empty, so the login step fails and the job goes red.
- The board note says an instance was following the floating tag and that `build-dev-image` already publishes `:dev-latest` from `dev` only, which is the right tag for an install that wants to follow development.
- `CLAUDE.md`, CI/CD section: "**build-test-image**: pushes a `:test-<sha>` Docker image and feeds trivy". Already correct for the target state.
- `CONTRIBUTING.md` lines 225 to 235 name `build-test-image` in the release checklist. No tag is named there.

## Dependencies
- None on other items.
- Dennis: if any install still follows `:test`, it stops receiving updates after this change. It keeps running the last image pushed. The PR description must say so and name `:dev-latest` as the replacement.

## Implementation steps
1. In `build-test-image`, remove the line `${{ vars.DOCKER_HUB_REPO }}:test` from `tags:` so the only tag is `:test-${{ steps.meta.outputs.short_sha }}`.
2. Gate the job on the PR coming from this repository. Change the pull request branch of the `if:` to:
   ```
   (github.event_name == 'pull_request'
    && github.event.pull_request.head.repo.full_name == github.repository
    && !contains(github.event.pull_request.labels.*.name, 'no-test-image')) ||
   ```
   Leave the `push` to `dev` and `workflow_dispatch` branches as they are.
3. Check `trivy-scan` still behaves: its `if:` is `always() && needs.build-test-image.result == 'success'`, so on a fork PR, where the build job is now skipped, trivy is skipped too and nothing is red. No change needed; confirm it by reading the job.
4. Rewrite the comment above the job (lines 446 to 449): the label now only saves a build ("A PR labelled `no-test-image` builds no image. Add the label before pushing; the lint and test jobs still run."), test images are published as `:test-<short sha>` only, and fork PRs build no image because the registry secret is not available to them.
5. Update the summary comment at line 405 to "→ :test-<short_sha>" and add "(same-repository PRs only)".
6. `CONTRIBUTING.md`: in the pull request section, add one sentence: "CI publishes a test image for PRs from branches in this repository as `<repo>:test-<short sha>`. There is no floating test tag; use `:dev-latest` to follow `dev`." Use the real image name from `README.md` line 20 at the time of writing.
7. `grep -rn ":test\b" --include=*.md --include=*.yml --include=*.sh . | grep -v node_modules | grep -v "^./planning/"` and fix any remaining mention of a floating `:test` tag. At planning time the only hits were in `ci.yml`.

## Acceptance criteria
- `grep -n "DOCKER_HUB_REPO }}:test$" .github/workflows/ci.yml` prints nothing.
- `grep -n "head.repo.full_name == github.repository" .github/workflows/ci.yml` prints one line, inside `build-test-image`.
- On this item's own PR, the `build-test-image` log shows exactly one pushed tag, of the form `:test-<7 hex characters>`, and `trivy-scan` runs against it and passes.
- After that run, the digest of the floating `:test` tag on Docker Hub is unchanged from before the PR (compare with `docker buildx imagetools inspect <repo>:test` before and after, on lin-manager-2 or any machine with Docker).
- A PR opened from a fork shows `build-test-image` and `trivy-scan` as skipped, not failed. This can be checked together with the fork PR used for S-011.

## Test plan
- Workflow syntax: `python3 -c "import yaml; yaml.safe_load(open('.github/workflows/ci.yml'))"`.
- The behaviour is tested by CI on the item's own PR (same-repository case) and by one fork PR (skip case). No backend or frontend tests change; the stage's standing checks still run: `cd backend && source .venv/bin/activate && ruff check . && ruff format --check .`.
- The digest comparison needs Docker and network access to Docker Hub only. Run it on lin-manager-2. It reads the registry and changes nothing.
- Nothing in this item touches a managed host or the instance on lin-manager.

## Docs and changelog
- `CONTRIBUTING.md` (step 6). `CLAUDE.md` needs no change.
- No file under `docs/` names the floating tag.
- `CHANGELOG.md` `[Unreleased]`, under "Changed": "CI no longer publishes a floating `:test` image tag. Test images are `:test-<short sha>`; follow `:dev-latest` to track `dev`."

## Risks and rollback
- An install that follows `:test` silently stops updating. This is the safer failure (it keeps a known image), and the changelog line and PR description name the replacement.
- Fork PRs lose the trivy image scan. They still get gitleaks, pip-audit, npm audit, lint and tests, and the image is scanned when a maintainer's branch or `dev` builds it.
- Rollback: revert the commit. The floating tag then moves again on the next PR.

## Assumptions
- Assumption (not yet confirmed): the floating tag is dropped, not kept for `dev` pushes only. The stage accepts either outcome ("no longer moves the floating `:test` tag, or the tag is gone"); dropping is simpler and `:dev-latest` already covers "follow dev".
- Assumption (not yet confirmed): the `no-test-image` label is kept as a way to skip a build. Question for Dennis: keep it or remove it now that the floating tag is gone?
- Assumption (not yet confirmed): the stale `:test` tag stays on Docker Hub. Question for Dennis: delete it once no install uses it?

## Definition of done
- [ ] `ci.yml` changed as in steps 1 to 5; `CONTRIBUTING.md` sentence added.
- [ ] Own PR shows one `:test-<sha>` tag pushed and trivy green; floating tag digest unchanged.
- [ ] Fork PR shows the two jobs skipped.
- [ ] `CHANGELOG.md` `[Unreleased]` line added.
- [ ] `planning/roadmap.yaml`: C-024 `status: done`; one line in `planning/PROGRESS.md`.
