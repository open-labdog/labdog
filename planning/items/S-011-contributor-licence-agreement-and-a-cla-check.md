# S-011: Contributor licence agreement and a CLA check on pull requests

| | |
|---|---|
| Edition | shared |
| Source | roadmap, "Add a contributor licence agreement (CLA) before accepting outside code"; decision D-01 |
| Tier / score | P0 / 14.0 |
| Stage | 0 |
| Effort | S |
| Autonomy | B |
| Depends on | none |

## Goal
Every pull request from someone outside the project carries a signed contributor licence agreement before it can merge. Signing is one comment on the PR, done once, and the record stays in a private repository in the organisation.

## Scope
In scope:
- The individual and corporate agreement texts (ICLA, CCLA), adapted from the Apache texts as D-01 describes.
- A CLA Assistant Lite workflow in `open-unbork/unbork`, and the same workflow in `open-unbork/unbork-playbooks`.
- Rewriting the licensing section of `CONTRIBUTING.md`.

Out of scope:
- Creating the private signatures repository, its token, and marking the check required. Only Dennis can do those (see Dependencies).
- Legal review of the texts. The PR says plainly that they are not lawyer-reviewed.
- A DCO sign-off, copyright assignment, or any change to the project licence.
- Signing on behalf of anyone. Nobody has contributed from outside yet, so there is nothing to collect retroactively.

## Context
- `CONTRIBUTING.md` line 3 to 4: "contributions land under the same license (inbound = outbound)". Lines 133 to 138, "Licensing & copyright": "We don't use a CLA; GitHub's default "inbound = outbound" model applies." Both statements become wrong with this item.
- `.github/workflows/` holds one file, `ci.yml`. Every action in it is pinned to a commit SHA with the version in a comment (for example `actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1`). The new workflow follows the same rule.
- `.github/dependabot.yml` has a `github-actions` entry targeting `dev`, so a pinned action gets update PRs.
- `/home/user/unbork-playbooks` has `.github/workflows/ci.yml`, a `README.md` with a "License" section (line 259), and no `CONTRIBUTING.md`.
- The decision write-up (linked from D-01) records: CLA Assistant Lite is the GitHub Action `contributor-assistant/github-action`, last release v2.6.1 (September 2024). It runs on `pull_request_target`, which is why it must be pinned to a commit and must never check out PR code. A contributor signs by posting a fixed sentence as a PR comment; the action writes the GitHub login, id, time and PR number to a JSON file in a repository of your choice and sets a status.
- Commits in this repository often carry a `Co-Authored-By:` trailer for an AI assistant with a no-reply address. How the action treats a co-author with no GitHub account is not known and must be tested (step 7).

## Dependencies
- Decision [D-01](../DECISIONS.md).
- From Dennis, before the workflow can go green:
  1. A private repository in the organisation for signatures. Name assumed: `open-unbork/cla-signatures`, default branch `main`.
  2. A fine-grained personal access token with Contents read and write on that repository only, saved as the Actions secret `CLA_SIGNATURES_TOKEN` in both `unbork` and `unbork-playbooks`. The session never sees or handles the token.
  3. After the first green run: mark the `CLAAssistant` status required on `dev` and `main` in both repositories.
  4. The list of his own GitHub logins for the allowlist.
- A second GitHub account that is not on the allowlist, for the fork test (step 8).

## Implementation steps
1. Add `legal/cla/v1/ICLA.md`: the Apache Individual Contributor License Agreement v2.2 text with these changes only: "The Apache Software Foundation" / "the Foundation" becomes "Dennis Tyresson, and his successors and assigns ("the Maintainer")"; a clause stating that the Maintainer may transfer the agreement and the rights granted under it; a clause in which the Maintainer promises that each contribution stays available under AGPL-3.0-or-later or another OSI-approved licence; a first paragraph in plain words saying the contributor keeps their copyright. Keep the copyright and patent grants (including the right to sublicense) as in the Apache text.
2. Add `legal/cla/v1/CCLA.md`: the Apache Corporate CLA v2.0 adapted the same way, with a schedule listing the GitHub logins of covered employees.
3. Add `legal/cla/README.md`: what the two documents are, how to sign (step 4's sentence), that a company signs the CCLA by sending it to the maintainer, what is stored (GitHub login and id, time, PR number, comment id), where (a private repository in the organisation), and why (proof of the agreement). This is the short GDPR note D-01's write-up asks for.
4. Add `.github/workflows/cla.yml`:
   - `on: issue_comment: types: [created]` and `pull_request_target: types: [opened, closed, synchronize]`.
   - `permissions: actions: write, contents: read, pull-requests: write, statuses: write`.
   - One job, `if:` the comment is `recheck` or the signing sentence, or the event is `pull_request_target`.
   - One step: `uses: contributor-assistant/github-action@<commit SHA of v2.6.1> # v2.6.1`. Resolve the SHA at implementation time with `git ls-remote https://github.com/contributor-assistant/github-action refs/tags/v2.6.1` and write it in the file.
   - `env: GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}` and `PERSONAL_ACCESS_TOKEN: ${{ secrets.CLA_SIGNATURES_TOKEN }}`.
   - `with: path-to-signatures: signatures/v1/cla.json`, `path-to-document: https://github.com/open-unbork/unbork/blob/main/legal/cla/v1/ICLA.md`, `branch: main`, `remote-organization-name: open-unbork`, `remote-repository-name: cla-signatures`, `allowlist: dependabot[bot],github-actions[bot],<Dennis's logins>`, `custom-pr-sign-comment: I have read the CLA Document and I hereby sign the CLA`, `lock-pullrequest-aftermerge: false`.
   - No `actions/checkout` step. A comment at the top of the file says why: the workflow runs with a write token on untrusted PRs.
5. Rewrite `CONTRIBUTING.md`: line 3 to 4 becomes "LabDog is AGPL-3.0-or-later. Contributions are accepted under a contributor licence agreement (see Licensing & copyright)." The "Licensing & copyright" section says: you keep your copyright; you sign once by commenting on your first PR; the agreement is a licence, not an assignment; the maintainer promises your contribution stays available under the AGPL; the agreement also lets the maintainer license the project under other terms; link to `legal/cla/`. Keep the paragraph about per-file headers.
6. In `unbork-playbooks` (separate PR in that repository): add the same `cla.yml` (same `path-to-document` URL and signatures file, so one signature covers both repositories) and a short `CONTRIBUTING.md` that links to the agreement.
7. Open a test PR from a branch in the repository whose commit has a `Co-Authored-By:` trailer with a no-reply address. If the check lists that co-author as unsigned, add the co-author name to `allowlist` and note it in the workflow comment.
8. Fork test, with the second account: open a PR from a fork, see the check fail and the bot comment; post the signing sentence; see the check pass and a new entry in `signatures/v1/cla.json`. Close the test PR.
9. Tell Dennis the check name so he can mark it required (Dependencies, point 3).

## Acceptance criteria
- `legal/cla/v1/ICLA.md` and `CCLA.md` exist and name Dennis Tyresson "and his successors and assigns", contain a transfer clause and the AGPL promise.
- `grep -n "don't use a CLA\|inbound = outbound" CONTRIBUTING.md` prints nothing.
- `.github/workflows/cla.yml` pins the action to a 40-character SHA and has no checkout step.
- A PR from a fork by an account that has not signed shows a failing `CLAAssistant` status; after the signing comment it passes.
- A PR by an allowlisted account (Dennis, Dependabot) passes with no comment needed.
- The same holds in `unbork-playbooks`, and a contributor who signed in one repository is not asked again in the other.

## Test plan
- No backend or frontend code changes. Run `cd backend && source .venv/bin/activate && ruff check .` only as the stage's standing check.
- Workflow syntax: `python3 -c "import yaml,sys; yaml.safe_load(open('.github/workflows/cla.yml'))"`.
- Steps 7 and 8 are the real test and run on GitHub against throwaway PRs. They need no managed host and nothing on lin-manager or lin-manager-2.
- `gitleaks` in CI must stay green: the workflow references secrets by name only.

## Docs and changelog
- `CONTRIBUTING.md` as in step 5. `README.md`: add one sentence under "License" pointing to the agreement if a contributing section exists there.
- `CHANGELOG.md` `[Unreleased]`, under "Changed": "Contributions now require a signed contributor licence agreement, checked on every pull request. See CONTRIBUTING.md."

## Risks and rollback
- `pull_request_target` runs with repository secrets on PRs from forks. Mitigation: pinned SHA, no checkout, minimal `permissions`.
- If the token is missing or expired, every PR shows a failing check. Until Dennis marks the check required this blocks nothing.
- The action is quiet upstream (last release 2024). If it breaks, the fallback is to fork it into the organisation; raise that as a proposal, do not do it in this item.
- A wrong clause is expensive after people have signed. The texts live under `v1/` so a corrected text can be `v2/` with a new signatures file.
- Rollback: delete `cla.yml` and revert `CONTRIBUTING.md`. Signatures already collected stay in the private repository; never delete them.

## Assumptions
- [D-01](../DECISIONS.md) fixes the form, the grantee, the two added clauses, the tool and the two repositories.
- Assumption (not yet confirmed): the texts live in this repository under `legal/cla/v1/`. D-01's write-up suggests "a public repo, for example `open-unbork/.github`". Question for Dennis: here, or in `open-unbork/.github`?
- Assumption (not yet confirmed): repository name `cla-signatures` and secret name `CLA_SIGNATURES_TOKEN`.
- Assumption (not yet confirmed): a company covered by a CCLA is handled by adding its employees' logins to `allowlist`, with the signed document kept in the signatures repository. The action itself records individuals only.
- Assumption (not yet confirmed): no exemption for trivial changes. D-01's write-up leaves this open.
- Assumption (not yet confirmed): no governing-law clause is added beyond what the Apache text has. Question for Dennis, ideally with a lawyer.
- Assumption (not yet confirmed): the one sentence in `CONTRIBUTING.md` on why the agreement exists is worded by Dennis in review.

## Definition of done
- [ ] Texts, README and workflow merged in `unbork`; workflow and CONTRIBUTING merged in `unbork-playbooks`.
- [ ] `CONTRIBUTING.md` rewritten; changelog line added.
- [ ] Steps 7 and 8 done and their PR links noted in the PR description.
- [ ] Dennis has reviewed the texts before merge (class B) and marked the check required.
- [ ] `planning/roadmap.yaml`: S-011 `status: done`; one line in `planning/PROGRESS.md`.
