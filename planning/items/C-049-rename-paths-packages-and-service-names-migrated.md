# C-049: Rename: paths, packages and service names, migrated on upgrade

| | |
|---|---|
| Edition | community |
| Source | proposed (PR-02), decision D-02 |
| Tier / score | P0 / 7.0 |
| Stage | 2 |
| Effort | M |
| Autonomy | B |
| Depends on | C-048 |

## Goal

A new install has an `unbork` package, an `unbork.service` unit and its files under `/etc/unbork`, `/usr/lib/unbork`, `/var/lib/unbork` and `/var/log/unbork`. Upgrading a 1.1.0 package or container carries config, data and the running service across without the operator editing anything first.

## Scope

In scope:
- `.deb`, `.rpm` and tarball: package name, unit, tmpfiles, system user and group, directories, maintainer scripts.
- The Docker image: user name, directories, static frontend path, build metadata variables.
- Code defaults that name those paths.
- Internal names that need a migration or a matching pattern: the `hosts.labdog_source_ip` column, the seeded pack's display name, temp directory prefixes.

Out of scope:
- Env and config names (C-048). Names on managed hosts and in Proxmox (C-050). Docs prose (C-051).
- The automated 1.1.0 upgrade test in packaging-smoke (C-052) and the written upgrade guide (C-001).
- The Docker Hub repository name: it already comes from the `DOCKER_HUB_REPO` secret.

## Context

- `packaging/nfpm.yaml`: `name: labdog`; contents under `/usr/lib/labdog`, `/etc/labdog/labdog.toml` (`config|noreplace`, `root:labdog` 0640), `/var/lib/labdog`, `/var/log/labdog`; no `replaces`, `conflicts` or `provides`.
- `packaging/systemd/labdog.service`: `User=labdog`, `Environment=LABDOG_CONFIG=/etc/labdog/labdog.toml`, `ReadWritePaths=/var/lib/labdog /var/log/labdog /run/labdog /dev/shm`. `packaging/tmpfiles.d/labdog.conf` creates `/run/labdog`.
- `packaging/scripts/preinst.sh` creates user `labdog`; `postinst.sh` creates directories and restarts on upgrade; `postrm.sh` on `purge` runs `rm -rf /var/lib/labdog /var/log/labdog /etc/labdog` and deletes the user. `packaging/install.sh` and `uninstall.sh` do the same for the tarball. `packaging/Makefile:11` sets `PREFIX ?= /usr/lib/labdog`; artefact names are `labdog_*`/`labdog-*` (`Makefile:156-167`, `build.sh:151`, `ci.yml:880`).
- `packaging/tests/smoke.sh` asserts the old paths, modes and owner `root:labdog`. `ci.yml:664-670` scores `packaging/systemd/labdog.service` with `systemd-analyze`.
- `Dockerfile:72-74` creates user `labdog` (uid 1000) and `/var/lib/labdog/{packs,claude-cli}`; line 126 copies the frontend to `/usr/lib/labdog/frontend/out`; lines 132-133 set `LABDOG_COMMIT_SHA` and `LABDOG_BUILD_DATE`.
- Code defaults: `AnsibleConfig.packs_root_dir` (`backend/app/config.py:242`), `DEFAULT_CONFIG_DIR` (`backend/app/ai/providers/claude_cli.py:92`), `prod_path` (`backend/app/main.py:1309`).
- `docs/production-deploy.md:97,103` documents volumes at `/var/lib/labdog/packs` and `/var/lib/labdog/claude-cli`. Existing compose files mount there.
- Internal names: `Host.labdog_source_ip` (`backend/app/models/host.py:73`, also `backend/app/schemas/hosts.py:126` and three frontend uses); seeded pack row `labdog-playbooks` (`backend/alembic/versions/0001_initial_schema.py:987`, URL already moved by `0057`); temp prefixes `labdog-sync-` with the redaction regex `_TMPFS_PATH_RE` (`backend/app/tasks/host_sync_orchestrator.py:164,179`), `labdog-action-`, `labdog-action-group-`, `labdog-ca-`, `labdog-update-`, `labdog-git-`, `labdog-pack-ssh-`, `labdog-fw-`.

## Dependencies

- C-048 (the unit sets `UNBORK_CONFIG`; the image sets `UNBORK_COMMIT_SHA`).
- [D-02](../DECISIONS.md). C-052 turns the manual upgrade check below into a CI gate.
- External: Docker on `lin-manager-2` for the image check; throwaway Debian 13, Ubuntu 24.04 and Rocky 9 containers for the packages (the targets `packaging/tests/run-smoke.sh` already uses).

## Implementation steps

1. Packaging files: rename to `packaging/systemd/unbork.service`, `packaging/tmpfiles.d/unbork.conf`, `packaging/etc/unbork.toml`. In the unit set `User=unbork`, `Group=unbork`, the new paths, `Environment=UNBORK_CONFIG=/etc/unbork/unbork.toml`, and `Alias=labdog.service` under `[Install]`.
2. `nfpm.yaml`: `name: unbork`; all `dst` paths to the new roots; add `replaces: [labdog]`, `conflicts: [labdog]`, `provides: [labdog]` (and the rpm `obsoletes` override). Artefacts become `unbork_VERSION_amd64.deb`, `unbork-VERSION-1.x86_64.rpm`, `unbork-VERSION-linux-amd64.tar.gz`; update `Makefile`, `build.sh` and the `release-artifacts` job in `ci.yml`.
3. `preinst.sh`: if user `labdog` exists and `unbork` does not, stop `labdog.service` if active, then `usermod -l unbork labdog` and `groupmod -n unbork labdog`. This keeps uid and gid, so no file changes owner. Otherwise create `unbork` as today.
4. `postinst.sh`, guarded by a marker file `/var/lib/unbork/.migrated-from-labdog`: for each of `/var/lib`, `/var/log`, if the `labdog` directory is a real directory and the `unbork` one is empty, move the contents and leave a symlink `labdog -> unbork`. If `/etc/labdog/labdog.toml` exists, save the packaged default as `/etc/unbork/unbork.toml.dist`, copy the operator's file to `/etc/unbork/unbork.toml` (0640 `root:unbork`), and leave `/etc/labdog` in place with a `README` saying it is no longer read. Then `daemon-reload`; if `labdog.service` was enabled, enable `unbork.service`; start it if the old one was running. Never delete an old directory.
5. `postrm.sh` purge: remove only the `unbork` paths and user. `install.sh` and `uninstall.sh`: same logic as steps 3 and 4.
6. `Dockerfile`: `useradd -m -u 1000 unbork`; create `/var/lib/unbork/{packs,claude-cli}`; copy the frontend to `/usr/lib/unbork/frontend/out`; set `UNBORK_COMMIT_SHA` and `UNBORK_BUILD_DATE`; add `ln -s /var/lib/unbork /var/lib/labdog` and `ln -s /usr/lib/unbork /usr/lib/labdog` so a volume still mounted at `/var/lib/labdog/packs` lands in the new directory. Verify on `lin-manager-2` that Docker resolves the symlinked mount target; if it does not, replace the symlink with a startup fallback in step 7.
7. Code defaults: `packs_root_dir` to `/var/lib/unbork/packs`, `DEFAULT_CONFIG_DIR` to `/var/lib/unbork/claude-cli`, `prod_path` to `/usr/lib/unbork/frontend/out` with the old path as second candidate. Add a small `backend/app/paths.py` helper `state_path(sub)` that returns the new path unless only the old one exists, and use it for the two state defaults.
8. Migration `backend/alembic/versions/00NN_rename_labdog_names.py` (next free number after the current head; `0060` at the time of writing): `ALTER TABLE hosts RENAME COLUMN labdog_source_ip TO unbork_source_ip`; rename the seeded pack and its git repository row from `labdog-playbooks` to `unbork-playbooks` only where the name is still the seeded one and no row already uses the new name. Downgrade reverses both. Rename the model attribute; keep `labdog_source_ip` in `HostResponse` as a deprecated alias next to `unbork_source_ip`.
9. Temp prefixes: change to `unbork-…` and widen `_TMPFS_PATH_RE` to `(?:unbork|labdog)-sync-`. Check `dev/dev.sh` (`LOCAL_PACK_NAME`, the `WHERE name = 'labdog-playbooks'` update) against step 8.
10. Update `packaging/tests/smoke.sh` to the new paths and owner `root:unbork`, and the `systemd-analyze` step in `ci.yml` to the new unit file.

## Acceptance criteria

- Fresh `.deb`, `.rpm` and tarball installs pass `packaging/tests/run-smoke.sh` with the new names and no `labdog` path on disk.
- Installing the new `.deb` over an installed and configured 1.1.0 `labdog` package: `dpkg -l labdog` shows it removed, `/etc/unbork/unbork.toml` equals the operator's old file, `id unbork` has the uid `labdog` had, data from `/var/lib/labdog` is reachable under `/var/lib/unbork`, and `systemctl status labdog` resolves to the running `unbork.service`. Same for the `.rpm`.
- The new image, started with an unchanged 1.1.0 compose file, keeps its pack checkouts and passes `/health/ready`.
- `alembic upgrade head` then `alembic downgrade -1` then `upgrade head` succeeds on a database with hosts and the seeded pack.
- `systemd-analyze security` score of `unbork.service` stays at or under the 3.0 ceiling in CI.

## Test plan

- `cd backend && source .venv/bin/activate && pytest tests/ -q -k "migration or version or packs or host"` then the full suite before the stage review.
- `packaging/build.sh` then `packaging/tests/run-smoke.sh` (local Docker on `lin-manager-2`).
- Manual upgrade check in a throwaway container per distro: install the released 1.1.0 package, write a marker into `/etc/labdog/labdog.toml` and `/var/lib/labdog/`, install the new package, assert the criteria above. C-052 automates this.
- Image: `docker build`, then run it on `lin-manager-2` against a copy of the 1.1.0 compose file. Not on `lin-manager`.
- `cd frontend && npx tsc --noEmit && npm run lint`.

## Docs and changelog

- `docs/production-deploy.md` (volume paths, unit link), `docs/backup-restore.md`, `docs/upgrade.md` (package install commands and what the upgrade moves).
- `CHANGELOG.md` `[Unreleased]`, Changed: "The package, service and system user are now `unbork`, with files under `/etc/unbork`, `/var/lib/unbork` and `/var/log/unbork`. Upgrading from the `labdog` package moves config and data and keeps `labdog.service` as an alias. Container volumes mounted under `/var/lib/labdog` keep working."

## Risks and rollback

- The user rename fails while a process still runs as `labdog`. Step 3 stops the service first; on failure the script exits non-zero before anything is moved.
- A database-stored absolute path under `/var/lib/labdog` (pack checkouts). The symlinks in steps 4 and 6 keep such paths valid; check `action_packs` and `git_repositories` rows during the manual test.
- Rollback for packages: reinstall 1.1.0; `/etc/labdog` was never deleted, data is behind a symlink, and the alembic downgrade restores the column. Rollback for Docker: pin the previous tag after running the downgrade.

## Assumptions

- [D-02](../DECISIONS.md): paths, package, service and user are renamed and migrated. Assumption (not yet confirmed): the system user is renamed in place with `usermod`. Question for Dennis: is keeping the `labdog` user for good preferable if the rename proves fragile on a distro?
- Assumption (not yet confirmed): old directories are left as symlinks and `/etc/labdog` is kept, because this plan never deletes operator data.
- Assumption (not yet confirmed): the column, seeded pack name and temp prefixes belong here; the rename map lists them and no item names them. The new column name is `unbork_source_ip`.
- Postgres database and role names stay ([D-02](../DECISIONS.md)).

## Definition of done

- [ ] Packaging, Dockerfile, migration and code defaults merged to `dev`.
- [ ] Smoke tests green; manual 1.1.0 upgrade check recorded in `planning/PROGRESS.md` for `.deb`, `.rpm` and Docker.
- [ ] Docs and changelog updated.
- [ ] `planning/roadmap.yaml` status set to done.
