# C-052: Test the upgrade from 1.1.0 in packaging-smoke

| | |
|---|---|
| Edition | community |
| Source | proposed, accepted proposal PR-03 (`planning/PROPOSALS.md`) |
| Tier / score | P0 / 6.5 |
| Stage | 3 |
| Effort | M |
| Autonomy | A |
| Depends on | C-049 |

## Goal

CI installs the released LabDog 1.1.0 package on Ubuntu and on Rocky, gives it a database
with data, upgrades to the candidate package, starts the service, and fails the build if
the data, the config or the service did not survive.

## Scope

In scope:
- A new upgrade scenario in `packaging/tests/`, for the `.deb` (Ubuntu 24.04) and the
  `.rpm` (Rocky Linux 9).
- Starting the service in the test container against a real PostgreSQL and Redis.
- Wiring it into the `packaging-smoke` job.

Out of scope:
- The tarball upgrade (`install.sh` over an old tree). C-001 checks it once by hand.
- The Docker image upgrade, which C-001 checks on lin-manager-2.
- A sync against a managed host. No host is reachable from CI.
- Upgrades from releases older than 1.1.0.

## Context

- `packaging/tests/run-smoke.sh` runs one clean container per artifact
  (`smoke_deb`, `smoke_rpm`, `smoke_tarball`), installs it, and runs
  `packaging/tests/smoke.sh`, which checks files, modes, the service account, imports and
  the reported version. `packaging/tests/README.md` says it "deliberately does not start
  the service".
- The `packaging-smoke` job is in `.github/workflows/ci.yml` (line 593). It builds the
  artifacts with `packaging/build.sh`, runs `run-smoke.sh`, and scores the unit with
  `systemd-analyze`. On pull requests it runs only when the diff touches `packaging/`,
  `backend/pyproject.toml`, `backend/uv.lock`, `VERSION` or `ci.yml`. `release-artifacts`
  needs it, so on `main` it gates the release.
- The 1.1.0 release assets are named `labdog_1.1.0-1_amd64.deb` and
  `labdog-1.1.0-1.x86_64.rpm` (the pattern in `run-smoke.sh`, lines 38 and 39), attached
  to the GitHub release `v1.1.0` of `open-unbork/unbork` with a `SHA256SUMS` file.
- 1.1.0 installs under `/usr/lib/labdog`, `/etc/labdog/labdog.toml`, `/var/lib/labdog`,
  with user `labdog` and `labdog.service` (`ExecStart=/usr/lib/labdog/venv/bin/python -m
  app`, `Environment=LABDOG_CONFIG=/etc/labdog/labdog.toml`).
- After C-049 the candidate is the `unbork` package with `replaces`, `conflicts` and
  `provides` for `labdog`, moves config and data to `/etc/unbork`, `/var/lib/unbork` and
  `/var/log/unbork`, renames the user keeping its uid, and aliases `labdog.service`. Read
  `planning/items/C-049-rename-paths-packages-and-service-names-migrated.md` for the exact
  promises; this test asserts them.
- `python -m app` in the default `all` role runs `alembic upgrade head` and then the API
  and workers (`backend/app/__main__.py`). `/health` and `GET /api/version` need no
  sign-in. The migration head on `dev` is `0060`; 1.1.0 is several revisions behind, so
  the upgrade exercises real migrations.
- The test containers have no systemd, so maintainer scripts that call `systemctl` must
  already tolerate its absence (the current smoke test proves the install path does).

## Dependencies

- C-049 merged, so the candidate package has the new name and the migration scripts.
- Network access from CI to the GitHub release assets of `v1.1.0`.
- Docker on the runner (already used by `run-smoke.sh`).

## Implementation steps

1. Add `packaging/tests/upgrade.sh`, run inside the target container, taking the old and
   new versions. It:
   1. Installs and starts PostgreSQL and Redis from the distribution's own packages, and
      creates a database and role.
   2. Installs the 1.1.0 package from `/old`, writes the database URL, Redis URL,
      `secret_key` and `encryption_key` into `/etc/labdog/labdog.toml`, and appends a
      marker comment line to that file.
   3. Starts 1.1.0 by hand as the service user with the unit's own command and
      environment (read `ExecStart`, `User` and `Environment` from the installed unit so
      the test follows the unit), waits for `/health`, and checks `/api/version` reports
      1.1.0.
   4. Seeds data through the API: register the first admin, sign in, create one group and
      one SSH key (an encrypted secret, so key handling is covered). Record the uid of
      `labdog`, the alembic revision, and write a marker file into `/var/lib/labdog`.
   5. Stops the old process, then installs the candidate from `/dist` with the same
      command an operator would use (`apt-get install ./unbork_*.deb`, `dnf install
      ./unbork-*.rpm`).
   6. Asserts the C-049 promises: the old package is gone, the new config file equals the
      edited old one including the marker line, the service user has the recorded uid,
      the marker file is reachable under the new data path, and the old paths were not
      deleted.
   7. Starts the candidate the same way from the new unit, waits for `/health`, and
      asserts: `/api/version` reports the candidate version; the alembic revision is the
      candidate's head; the admin can sign in with the old password; the group exists;
      the SSH key's public half is returned (the encryption key still decrypts).
   8. Runs `packaging/tests/smoke.sh` for the candidate, so the fresh-install assertions
      also hold after an upgrade.
2. Add `upgrade_deb` and `upgrade_rpm` to `run-smoke.sh`, behind two new targets
   `upgrade-deb` and `upgrade-rpm`, which mount `OLD_DIST` at `/old`. Add `OLD_VERSION`
   (default `1.1.0`) and `OLD_DIST` to the env block in the header. Keep the default
   `TARGETS` as it is and let CI pass the two new targets explicitly, so a developer
   without the old packages can still run the smoke.
3. In `packaging-smoke`, add a step before the smoke run that downloads the two 1.1.0
   assets and `SHA256SUMS` with `gh release download v1.1.0` (the job's `GITHUB_TOKEN`
   is enough for a public repository) and verifies them with `sha256sum -c
   --ignore-missing`. Then run `TARGETS="deb rpm tarball upgrade-deb upgrade-rpm"`.
4. Keep the pull-request change guard as it is, and add `backend/alembic/` to its path
   list: a migration is the most likely thing to break an upgrade.
5. Update `packaging/tests/README.md`: the table of scenarios, what the upgrade test
   seeds and asserts, and how to run it locally.

## Acceptance criteria

- `OLD_DIST=<dir with the 1.1.0 packages> TARGETS="upgrade-deb upgrade-rpm"
  packaging/tests/run-smoke.sh` passes locally against a candidate built from `dev` after
  C-049.
- The same run fails when any one of these is broken on purpose: the config copy in the
  candidate's `postinst.sh`, the data move, a migration (add a failing revision), or the
  version reported by the candidate.
- The `packaging-smoke` job runs both upgrade targets on a pull request that touches
  `packaging/` or `backend/alembic/`, and on `main`.
- The checksum step fails the job when a downloaded 1.1.0 asset does not match
  `SHA256SUMS`.
- The job's added time is recorded in the PR description. If it adds more than ten
  minutes, say so; do not drop assertions to save time.

## Test plan

- Local, needs Docker: `./packaging/build.sh --version="$(cat VERSION)"`, download the
  two 1.1.0 assets into a directory, then the command in the first acceptance line.
- Break each of the four things named above in a scratch build and confirm the red run.
  Do not commit the breakage.
- CI: push the branch and read the `packaging-smoke` log for both upgrade targets.
- `shellcheck packaging/tests/*.sh` if shellcheck is available.
- Nothing here touches lin-manager-2, lin-manager or any managed host.

## Docs and changelog

- `packaging/tests/README.md` as in step 5.
- `CONTRIBUTING.md`, release process: one sentence that `packaging-smoke` now also
  upgrades from 1.1.0.
- No `CHANGELOG.md` line: nothing user-visible changes.

## Risks and rollback

- Starting the service without systemd skips `ReadWritePaths` and the other sandboxing in
  the unit, so a path the unit forgets to allow is not caught here. C-001's manual
  upgrade on a real systemd host covers that.
- PostgreSQL from Ubuntu 24.04 and Rocky 9 is older than the PostgreSQL 18 the project
  develops on. If the app refuses the distribution's version, install the PGDG packages
  in the container, and note the version in the README.
- The first-admin registration and SSH key routes of 1.1.0 are fixed forever; the
  candidate's are not. Seed with the old API and verify with read-only calls only.
- A GitHub outage fails the download. That blocks a release until it passes, which is
  the intended direction for a gate.
- Rollback: remove the two targets from the CI step. The fresh-install smoke is unchanged.

## Assumptions

- Assumption (not yet confirmed): Ubuntu 24.04 and Rocky 9 are the two systems, as the
  stage behaviour and the current smoke images say.
- Assumption (not yet confirmed): running the unit's command by hand is an acceptable
  stand-in for systemd in CI. A systemd-in-container image was not chosen because it
  needs privileged containers.
- 1.1.0 stays the only starting point until a later release adds its own.

## Definition of done

- [ ] `packaging/tests/upgrade.sh` and the two targets in `run-smoke.sh`.
- [ ] `packaging-smoke` downloads, verifies and runs them; the guard includes
      `backend/alembic/`.
- [ ] Each deliberate breakage was seen to fail.
- [ ] `packaging/tests/README.md` and `CONTRIBUTING.md` updated.
- [ ] `planning/roadmap.yaml`: C-052 set to done with its branch.
- [ ] One line appended to `planning/PROGRESS.md`.
