# C-003: Bring the docs site back

| | |
|---|---|
| Edition | community |
| Source | roadmap, board item "Bring the docs site back" |
| Tier / score | P0 / 10.0 |
| Stage | 3 |
| Effort | S |
| Autonomy | A |
| Depends on | C-002 |

## Goal

The documentation site answers at the address the README links to, is titled unbork, and
shows the 2.0.0 documentation. Every documentation link in the README resolves.

## Scope

In scope:
- Making the site build correct before the release merges: title, links, anchors.
- A link check of the README against the built site.
- Verifying the deployment after Dennis merges the release PR, and reporting what is
  wrong if it is not live.

Out of scope:
- Changing repository settings. GitHub Pages is enabled by Dennis in the repository
  settings if it is not already.
- A custom domain, versioned docs, search, or a redesign.
- Deploying the site from `dev`. Pull request #295 proposes a manual deploy from `dev`;
  this item decides what to do with it (step 2) and does not need it.

## Context

- `website/docusaurus.config.ts`: `url: 'https://open-unbork.github.io'`,
  `baseUrl: '/unbork/'`, `organizationName: 'open-unbork'`, `projectName: 'unbork'`. The
  docs plugin reads the repository's `docs/` directory (`path: '../docs'`) and serves it
  at the site root. `EDIT_BRANCH` is `main`.
- On the branch this plan was written on, the config still says `LabDog` at lines 11
  (site title), 72 (navbar title), 74 (logo alt) and 109 (footer copyright). Pull request
  #297 renamed prose on `dev`, including this file, and C-051 sweeps what is left. Measure
  again on `dev` before changing anything.
- `.github/workflows/ci.yml`: `pages-build` (line 537) and `pages-deploy` (line 564) run
  on a push to `main` or a manual run on `main`. `pages-deploy` needs Pages enabled with
  "Source: GitHub Actions", as its comment says, and uses the `github-pages`
  environment. `docs-build-check` (line 376) builds the site on every pull request and
  on pushes to `dev`, and the build fails on broken links and anchors.
- `README.md` links to the site in the docs badge (line 10, with the badge text
  `docs-labdog`), the installation guide (`/#installation`, lines 27 and 31), the
  assistant guide, the GitOps examples, and the list at lines 120 to 124
  (`/#how-configuration-is-applied`, `/examples/precedence/`, `/#local-development`,
  `/#api-endpoints`, `/ui/actions/`, `/examples/action-packs/`,
  `/ui/scheduled-actions/`, `/ui/assistant/`, `/ui/alerts/`, `/ui/host-metrics/`,
  `/metrics-export/`).
- The site could not be reached from the planning session (the sandbox proxy refused the
  request), so "does not exist" is the board's statement and was not re-checked here. The
  last deployment would have been for 1.1.0 on 2026-11-06 if Pages is enabled.

## Dependencies

- C-002: the release PR, and then Dennis's merge, which is what triggers the deployment.
- Dennis, only if Pages is not enabled or the `github-pages` environment restricts
  deployments from `main`.

## Implementation steps

Before the release PR merges (do these as part of the stage, on `dev`):

1. On `dev`, run `grep -n -i "labdog" website/docusaurus.config.ts website/sidebars.ts
   website/src -r`. Fix whatever product-name uses remain (title, navbar, logo alt,
   footer). Leave identifiers that C-048 to C-050 kept on purpose.
2. Read pull request #295 with `gh api repos/open-unbork/unbork/pulls/295`. If it is
   open and only adds a manual deploy from `dev`, leave a comment on it saying this item
   deploys from `main` through the release and recommend closing it, and tell Dennis. Do
   not merge or close it.
3. Build the site: `cd website && npm ci && npm run build`.
4. Check every site link in the README against the build. For each
   `https://open-unbork.github.io/unbork/<path>#<anchor>` in `README.md`, confirm that
   `website/build/<path>/index.html` exists and, where there is an anchor, that the file
   contains `id="<anchor>"`. Write this as a small script,
   `website/scripts/check-readme-links.mjs`, and add it to `docs-build-check` after the
   build step, so the README cannot point at a missing page again.
5. Fix what the check finds, in the README or in `docs/`. Change the docs badge text from
   `docs-labdog` to `docs-unbork` if C-051 has not.
6. Make sure these changes are on `dev` before C-002 opens the release PR, so the first
   deployment is already correct.

After Dennis merges the release PR:

7. Watch `pages-build` and `pages-deploy` on the merge commit with
   `gh run list --branch main --workflow ci.yml` and `gh run view`.
8. Fetch the site: `curl -sSf https://open-unbork.github.io/unbork/ | grep -i "<title>"`.
   Expect "unbork" in the title. Then run the README link check against the live site
   (the same script with a `--live` option that fetches each URL and expects 200).
9. If the deployment did not run or failed:
   - `pages-deploy` failing with a Pages or environment error means a repository setting.
     Report to Dennis exactly which one (Settings, Pages, Source: GitHub Actions; or the
     `github-pages` environment's branch rule). Do not try to change settings.
   - A build failure is fixed by a normal PR into `dev`; the fix reaches the site with
     the next release, or Dennis can start `ci.yml` by hand on `main`
     (`workflow_dispatch` is allowed there for the pages jobs).
10. Record the result and the URL in `planning/PROGRESS.md`.

## Acceptance criteria

- `cd website && npm run build` passes on `dev`, and the built `index.html` has "unbork"
  in its `<title>` and no "LabDog" in the navbar or footer.
- `node website/scripts/check-readme-links.mjs` exits 0 on `dev`, and exits non-zero when
  a README link is changed to a page that does not exist.
- `docs-build-check` runs the link check.
- After the merge: `curl -sSf https://open-unbork.github.io/unbork/` returns 200 with
  "unbork" in the title, the page "Upgrading to 2.0.0" is reachable under
  `/upgrade/`, and the live link check passes.
- If the site is not live after the merge, Dennis has a message naming the failing job
  and the setting or fix needed, and the item stays open.

## Test plan

- `cd website && npm ci && npm run build`
- `node website/scripts/check-readme-links.mjs`
- After the merge, from the session's own environment or from lin-manager-2 if the
  session cannot reach github.io: the `curl` command above and the script with `--live`.
- No backend, frontend or host test is involved.

## Docs and changelog

- `README.md` and `docs/` only where a link was wrong.
- `CONTRIBUTING.md`, in the part about docs: one sentence that the docs build also checks
  the README's links to the site.
- `CHANGELOG.md`, `[Unreleased]`, under "Fixed": "The documentation site is published
  again and every README link to it is checked in CI."

## Risks and rollback

- The site only changes when `main` changes, so a mistake found after the release stays
  visible until the next release or a manual run on `main` by Dennis. Steps 1 to 6 exist
  to catch mistakes before the merge.
- The session may not be able to reach `github.io` through its proxy. Then the live
  check runs on lin-manager-2, which only reads a public site.
- Rollback: the previous deployment can be re-run from the Actions page by Dennis. The
  CI link check can be removed by reverting its commit.

## Assumptions

- Assumption (not yet confirmed): GitHub Pages is, or will be, enabled for the repository
  with "GitHub Actions" as the source. Question for Dennis: is it enabled today?
- Assumption (not yet confirmed): the site keeps the address
  `https://open-unbork.github.io/unbork/`; no custom domain is planned for 2.0.0.
- Assumption (not yet confirmed): pull request #295 is not needed once releases deploy
  the site. Its state was not readable from the planning session.

## Definition of done

- [ ] Site config and README links correct on `dev` before the release PR.
- [ ] `check-readme-links.mjs` in the repository and in `docs-build-check`.
- [ ] After the merge: site live, titled unbork, live link check green; or Dennis told
      what blocks it.
- [ ] `CHANGELOG.md` updated.
- [ ] `planning/roadmap.yaml`: C-003 set to done with its branch.
- [ ] One line appended to `planning/PROGRESS.md`.
