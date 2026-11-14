# C-048: Rename: configuration and environment, with the old names as fallbacks

| | |
|---|---|
| Edition | community |
| Source | proposed (PR-02), decision D-02 |
| Tier / score | P0 / 7.0 |
| Stage | 2 |
| Effort | M |
| Autonomy | B |
| Depends on | none |

## Goal

An operator configures unbork with `UNBORK_*` variables and `/etc/unbork/unbork.toml`. An install that still uses `LABDOG_*` variables, `labdog.toml` or the old key names starts unchanged and logs one deprecation warning that names what to change.

## Scope

In scope:
- The environment prefix, `*_CONFIG`, `*_ROLE`, `*_DEV_MODE`, `*_VERSION`, `*_COMMIT_SHA`, `*_BUILD_DATE`, `*_PLAYBOOKS_LOCAL`.
- Config file discovery and the TOML key `security.labdog_server_ip`.
- Stripping both prefixes from the environment handed to Ansible.
- The Python distribution names `labdog-backend` and `labdog-lint`.
- The session cookies `labdog_auth` and `labdog_csrf`, and the browser storage keys.
- `dev/`, `.env.example`, CI variables and the test suite's own environment.

Out of scope:
- Install paths, the OS package, the service unit, the system user, the Docker image layout (C-049).
- Anything written to a managed host, alert labels, metrics, pack variables (C-050).
- Prose and docs (C-051).
- The Postgres database and role names. They stay `labdog` ([D-02](../DECISIONS.md)); only new example defaults may change.

## Context

- `backend/app/config.py`: `Settings._apply_env_overrides` (line 273) hard-codes `prefix = "LABDOG_"` at line 286. `_find_config_path` (line 378) reads `LABDOG_CONFIG`, then `<repo>/labdog.toml`, then `/etc/labdog/labdog.toml`. `SecurityConfig.labdog_server_ip` is at line 83 and is read in `backend/app/rules/merge.py:48` and `backend/app/ai/labdog_host.py:102`. The startup errors at lines 430 to 494 name the old variables.
- Other direct reads of the environment: `backend/app/__main__.py:140`, `backend/app/healthcheck.py:68`, `backend/app/tasks/__init__.py:174` and `backend/app/celery_manager.py:240` (`LABDOG_ROLE`); `backend/app/main.py:1294` (`LABDOG_DEV_MODE`); `backend/app/api/version.py:31,47,87` (`LABDOG_COMMIT_SHA`, `LABDOG_BUILD_DATE`, `LABDOG_VERSION`).
- `backend/app/ansible_runtime/runner.py:226` drops every `LABDOG_*` variable from the playbook child environment (SEC-46). If only the prefix moved, `UNBORK_SECURITY__SECRET_KEY` would reach every playbook. `backend/tests/test_runner_env.py` covers this.
- `backend/pyproject.toml:6` names the distribution `labdog-backend`; `backend/app/api/version.py:83` looks it up with `importlib.metadata`; `backend/app/ai/agent_sdk/__init__.py:47` prints `pip install 'labdog-backend[agent]'`. `labdog-lint/pyproject.toml` ships module `labdog_lint`.
- Cookies: `backend/app/auth/users.py:24,30`, `backend/app/auth/ws_auth.py:37`, `backend/app/middleware/csrf.py:43`, `frontend/lib/api.ts:54`. Browser storage keys `labdog:theme`, `labdog:settings-collapse`, `labdog:pending-dismissed`, `labdog.rail`, `labdog.run-hosts` are under `frontend/`.
- `dev/dev.sh`, `dev/labdog.toml`, `dev/docker-compose.yml`, `dev/docker-compose.split.yml`, `.env.example`, `.github/workflows/ci.yml` and `backend/tests/conftest.py:83-132` all set `LABDOG_*`.
- `docs/upgrade.md:44-60` promises under semver that the `labdog.toml` keys and `LABDOG_*` variables stay stable within 1.x. The first unbork release is 2.0.0 ([D-03](../DECISIONS.md)), and the fallbacks make the change non-breaking anyway.

## Dependencies

- [D-02](../DECISIONS.md) and the map at `/mnt/project-files/rename-depth/rename-map.md`.
- The Enterprise package depends on `labdog-backend` by name and its tests set `LABDOG_*` and read `labdog_csrf`. Its change must merge in the same release (noted there as a follow-up; nothing in Community waits for it).

## Implementation steps

1. Add `backend/app/env_compat.py` with `NEW_PREFIX = "UNBORK_"`, `OLD_PREFIX = "LABDOG_"`, `getenv(name, default=None)` (reads `UNBORK_<name>`, then `LABDOG_<name>`), `prefixed_items(environ)` (yields `(suffix, value, was_old)` with the new name winning when both are set), and `warn_deprecated()` which logs a single WARNING per process listing the old names in use. It logs names only, never values.
2. `config.py`: make `_apply_env_overrides` iterate `prefixed_items`; make `_find_config_path` use `getenv("CONFIG")` and the candidate order `<repo>/unbork.toml`, `<repo>/labdog.toml`, `/etc/unbork/unbork.toml`, `/etc/labdog/labdog.toml`. Record a use of an old file path for the same warning.
3. Rename `SecurityConfig.labdog_server_ip` to `server_ip`. Add a `model_validator(mode="before")` on `SecurityConfig` that copies `labdog_server_ip` to `server_ip` when only the old key is present, and records it. This covers both TOML and `LABDOG_SECURITY__LABDOG_SERVER_IP`. Update the two readers.
4. Replace the eight direct `os.environ.get("LABDOG_…")` reads listed above with `env_compat.getenv`. In `celery_manager.py:240` set `UNBORK_ROLE` for children.
5. `runner.py:226`: drop keys that start with either prefix. Extend `test_runner_env.py` with an `UNBORK_SECURITY__SECRET_KEY` case and keep the existing `LABDOG_` case.
6. Call `warn_deprecated()` once from `python -m app` after settings load, and once at Celery worker start. Rewrite the startup error texts in `config.py` to name the new variables.
7. Rename the distributions to `unbork-backend` and `unbork-lint` (module `unbork_lint`, console script `unbork-lint`). `_resolve_version` tries `unbork-backend` then `labdog-backend`. Regenerate `backend/uv.lock` with `uv lock` and confirm the diff touches only the project name.
8. Cookies: rename to `unbork_auth` and `unbork_csrf` in the four backend places and `frontend/lib/api.ts`. On login and logout, also expire the two old cookies. Browser storage: add a helper in `frontend/lib/` that reads the new key, falls back to the old key once, writes the new key and removes the old one.
9. Rename `dev/labdog.toml` to `dev/unbork.toml`; update `dev/dev.sh`, both dev compose files, `.env.example`, `ci.yml` and `backend/tests/conftest.py` to the new names. Keep Postgres user and database `labdog` in dev and CI.
10. Add `backend/tests/test_env_compat.py`: new name works; old name works and warns once; both set means the new one wins; old TOML path and old key are found; one process emits exactly one warning. Leave at least one existing test file on `LABDOG_*` deliberately as a regression guard and say so in a comment.

## Acceptance criteria

- With only `UNBORK_SECURITY__SECRET_KEY`, `UNBORK_SECURITY__ENCRYPTION_KEY` and `UNBORK_DATABASE__URL` set, `python -m app` starts and logs no deprecation warning.
- With only the `LABDOG_*` equivalents set, it starts and logs exactly one WARNING per process that lists each old name and its replacement.
- With both `UNBORK_SERVER__PORT=9001` and `LABDOG_SERVER__PORT=9000`, the server listens on 9001.
- A config at `/etc/labdog/labdog.toml` containing `labdog_server_ip` is loaded when no new file exists.
- No variable starting with `UNBORK_` or `LABDOG_` appears in the environment of an ansible-playbook child.
- `GET /api/version` reports the version on a source checkout and in the image.
- Signing in sets `unbork_auth` and `unbork_csrf` and clears the old cookies.

## Test plan

- `cd backend && source .venv/bin/activate && pytest tests/test_env_compat.py tests/test_config_env_parsing.py tests/test_config_injection.py tests/test_runner_env.py -q`
- `pytest tests/ -q` once before the stage review.
- `cd frontend && npx tsc --noEmit && npm run lint`, then `npx playwright test` for the login spec.
- On local Docker on `lin-manager-2`: start the built image twice, once with an unchanged 1.1.0 compose file (old names) and once with the new names; check the log lines above. No managed host is needed.

## Docs and changelog

- `docs/upgrade.md`: a table of old and new names, the fallback rule and the deprecation period. `docs/production-deploy.md`, `docs/security-hardening.md`, `docs/scaling-out.md`, `docs/encryption-key-rotation.md` examples move to the new names (full prose sweep is C-051).
- `CHANGELOG.md` `[Unreleased]`, Changed: "Configuration uses `UNBORK_*` variables and `unbork.toml`. The `LABDOG_*` names, `labdog.toml` and `security.labdog_server_ip` still work and log a deprecation warning. Signing in again is needed once, because the session cookies were renamed."

## Risks and rollback

- A missed direct read leaves one variable honoured under the old name only. Mitigation: a test that greps `backend/app` for `"LABDOG_` outside `env_compat.py`.
- A secret leaking to playbooks if step 5 is skipped. The test in step 5 guards it.
- Cookie rename signs everyone out once. Documented.
- Rollback: revert the PR. The old names never stopped working, so no operator action is needed.

## Assumptions

- [D-02](../DECISIONS.md): old names remain as fallbacks. Assumption (not yet confirmed): they stay for all of 2.x and are removed no earlier than 3.0. Question for Dennis: confirm the period.
- Assumption (not yet confirmed): the new TOML key is `security.server_ip`.
- Assumption (not yet confirmed): one forced sign-in is acceptable, as the rename map says. Reading both cookie names was rejected as extra auth surface for little gain.
- Assumption (not yet confirmed): cookies, browser storage and the Python distribution names belong to this item. The rename map lists them but no item names them.

## Definition of done

- [ ] Code and tests merged to `dev`; full backend suite green.
- [ ] `docs/upgrade.md` table and changelog line written.
- [ ] `planning/roadmap.yaml` status set to done; one line in `planning/PROGRESS.md`.
- [ ] Note left for the Enterprise repository about the dependency name, cookie names and test variables.
