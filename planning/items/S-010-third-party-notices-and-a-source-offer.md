# S-010: Third-party notices and a source offer in the Docker image

| | |
|---|---|
| Edition | shared |
| Source | roadmap (a licensing item on the other board) |
| Tier / score | P1 / 5.0 |
| Stage | 2 |
| Effort | M |
| Autonomy | B |
| Depends on | none |

## Goal

Anyone who pulls the image can find, inside it, the licence of unbork, a notices file listing every third-party component with its licence text, and a statement of where to get the source of unbork itself and of the copyleft components the image contains.

## Scope

In scope:
- A generated `THIRD-PARTY-NOTICES` file covering Python packages, frontend packages bundled into the static export, and Debian packages in the final image.
- A hand-written `SOURCE-OFFER.md`.
- `LICENSE` copied into the image; OCI labels for licence and source.
- A CI check that the files exist and that no component has an unknown or disallowed licence.
- The same notices in the `.deb`, `.rpm` and tarball, since they ship the same Python and frontend code.

Out of scope:
- The copyright line in `LICENSE` (S-012), the contributor agreement (S-011), any change of licence.
- Notices for the Claude Code program: after S-009 the image does not contain it.
- Legal review. This item produces the files; whether their wording is sufficient is for Dennis to confirm.

## Context

- The repository has `LICENSE` (AGPL-3.0-or-later; `backend/pyproject.toml:10`, `frontend/package.json:5`, `packaging/nfpm.yaml:15`) and no notices file. `Dockerfile` copies neither `LICENSE` nor any notice into the image, and sets no `LABEL`.
- Image contents (`Dockerfile`): base `python:3.12-slim` pinned by digest; apt packages `openssh-client`, `git`, `tini` (line 68); Python packages from `uv export --frozen --no-emit-project --extra agent` (line 54), which include `ansible-core` (GPL-3.0-or-later, `backend/pyproject.toml:27`) and `ansible-runner`; the Next.js static export built in the `frontend-builder` stage from `frontend/package-lock.json`.
- Debian keeps each package's licence at `/usr/share/doc/<pkg>/copyright` in slim images; confirm during implementation that the base image has not stripped them.
- `GET /api/version` (`backend/app/api/version.py:20,154`) already reports `license` and `repo_url`; Settings › System shows them (`frontend/app/(dashboard)/settings/client-page.tsx:70`).
- CI: `build-test-image` and `build-image` pass `GIT_SHA` as a build argument (`.github/workflows/ci.yml:440,491,531`); `pip-audit` and `npm audit` already run (line 1084 on). `packaging/Makefile` stages the venv and frontend for the packages.
- The packages never install the `agent` extra, so their Python list is shorter than the image's.

## Dependencies

- None to start. Coordinate with S-009: generate the notices after its Dockerfile change, so the list matches what ships. If C-026's `WITH_ASSISTANT=0` build exists, the generator must run inside each build so each image lists its own contents.
- Dennis: confirmation of the source-offer wording and of a contact address for source requests.

## Implementation steps

1. Add `backend/scripts/third_party_notices.py` (standard library only). Inputs: the Python environment it runs in (`importlib.metadata`: name, version, `License-Expression` or `License` and classifiers, and licence files from `.dist-info/licenses` or the dist-info root), a JSON file of frontend packages, and optionally a dpkg status file plus `/usr/share/doc/*/copyright`. Output: one text file with a summary table (component, version, licence, origin) followed by the full licence texts, grouped so identical texts appear once. Exit non-zero if a component has no detectable licence and is not listed in `packaging/licence-overrides.toml` (a small reviewed file mapping such packages to their licence and text).
2. Frontend list: in the `frontend-builder` stage run `npx --yes license-checker-rseidelsohn --production --json --out /tmp/frontend-licences.json` after `npm ci` (pin its version; it is a build-time tool and is not shipped). Copy the JSON to the stage that runs the generator.
3. `Dockerfile`, final stage, after all packages are in place: run the generator with the frontend JSON and the image's own dpkg data, writing `/usr/share/doc/unbork/THIRD-PARTY-NOTICES`. Copy `LICENSE` and `SOURCE-OFFER.md` to `/usr/share/doc/unbork/`. Add labels `org.opencontainers.image.licenses="AGPL-3.0-or-later"`, `org.opencontainers.image.source="https://github.com/open-unbork/unbork"`, `org.opencontainers.image.revision=$GIT_SHA`, `org.opencontainers.image.documentation`.
4. Write `SOURCE-OFFER.md` at the repository root. Content: unbork's own source is at the repository URL at the commit named by the image's revision label and by `GET /api/version`; modified versions offered over a network must offer their source (AGPL section 13); the image contains GPL and LGPL components listed in the notices file; their exact source is available from Debian's snapshot archive (for the Debian packages, by the versions listed) and from PyPI source distributions (for Python packages), with a short how-to for each; and a written offer, valid for three years from the date of the image, to supply that source on request to a stated contact address at no more than the cost of distribution.
5. Licence policy check: in the generator, classify each licence as permissive, weak copyleft, GPL-compatible copyleft or other. Fail on "other" unless overridden. This is the guard against a dependency that is not compatible with AGPL-3.0-or-later arriving unnoticed.
6. Packages: `packaging/Makefile` runs the generator against the staged venv and a frontend JSON, and `nfpm.yaml` and the tarball install `THIRD-PARTY-NOTICES`, `SOURCE-OFFER.md` and `LICENSE` under `/usr/share/doc/unbork/` (path follows C-049; use the current package name's doc directory if this lands first).
7. Surface: add one line to Settings › System and to `docs/` saying where the files are in the image and in a package install. No API change and no new route.
8. CI: after `build-test-image`, run the image with `test -s /usr/share/doc/unbork/THIRD-PARTY-NOTICES && test -s /usr/share/doc/unbork/SOURCE-OFFER.md && test -s /usr/share/doc/unbork/LICENSE`, and check the labels with `docker inspect`. Extend `packaging/tests/smoke.sh` with the same three file checks.
9. Tests: `backend/tests/test_third_party_notices.py` runs the generator against the test environment and asserts it exits 0, lists `ansible-core` with a GPL licence and its text, and fails on a fabricated package with no licence.

## Acceptance criteria

- `docker run --rm <image> cat /usr/share/doc/unbork/THIRD-PARTY-NOTICES` prints a table that includes `ansible-core`, `fastapi`, `git`, `openssh-client`, `tini`, `next` and `react`, each with a licence, followed by licence texts.
- `docker run --rm <image> cat /usr/share/doc/unbork/SOURCE-OFFER.md` names the repository, the commit, the two source locations and the contact address.
- `docker inspect` shows the four OCI labels with the build's commit.
- Adding a Python dependency with no licence metadata and no override makes the image build fail.
- The `.deb`, `.rpm` and tarball install the same three files, and `packaging/tests/run-smoke.sh` passes.
- The number of Python components in the notices equals the number of distributions `pip list` reports in the image.

## Test plan

- `cd backend && source .venv/bin/activate && pytest tests/test_third_party_notices.py -q`, then the full suite before the stage review.
- `docker build -t unbork:s010 .` and the three commands in the criteria, on local Docker on `lin-manager-2`.
- `packaging/build.sh && packaging/tests/run-smoke.sh`
- `cd frontend && npx tsc --noEmit && npm run lint` (only if the Settings line in step 7 is added).
- No managed host is needed.

## Docs and changelog

- `docs/production-deploy.md` and `docs/README.md`: where the notices and the source offer are. `README.md`: a "Licence and third-party software" paragraph linking `SOURCE-OFFER.md`. `CONTRIBUTING.md`: adding a dependency means the notices build must pass, and how to add an override.
- `CHANGELOG.md` `[Unreleased]`, Added: "The Docker image and the packages carry a third-party notices file, the licence and a source offer under `/usr/share/doc/unbork/`, and the image has OCI licence and source labels."

## Risks and rollback

- Licence metadata in Python packages is inconsistent, so the first run will fail on several packages. Expect to fill `packaging/licence-overrides.toml` by hand, checking each project's repository; do not guess.
- The base image may have removed copyright files. Then take them from the matching Debian source packages or list the packages with a link to their copyright on `sources.debian.org`, and say so in the notices.
- The build-time npm tool is a new development dependency. Check its licence is permissive before adding it; if in doubt, write the frontend list from `package-lock.json` and `node_modules/*/package.json` in the generator instead.
- The notices are per build, so they cannot drift, but build time grows by some seconds.
- Rollback: revert the PR. Nothing at runtime depends on the files.

## Assumptions

- Assumption (not yet confirmed): pointing to Debian's snapshot archive and PyPI source distributions, together with a three-year written offer, is the form of source offer Dennis wants. Question for Dennis: confirm the wording and supply the contact address for requests. This spec does not give legal advice.
- Assumption (not yet confirmed): the files live under `/usr/share/doc/unbork/` in both the image and the packages.
- Assumption (not yet confirmed): the packages are in scope although the title names only the image, because they ship the same third-party code. If not, drop step 6 and the smoke-test part of step 8.
- [D-04](../DECISIONS.md): the image no longer contains the Claude Code program, so it needs no entry.

## Definition of done

- [ ] Generator, `SOURCE-OFFER.md`, Dockerfile, packaging and CI checks merged to `dev`.
- [ ] Overrides file reviewed entry by entry.
- [ ] Docs and changelog updated.
- [ ] Wording confirmed by Dennis and noted in `planning/DECISIONS.md`.
- [ ] `planning/roadmap.yaml` status set to done; `planning/PROGRESS.md` line added.
