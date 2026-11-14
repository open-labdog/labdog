# C-029: Run workers on more than one box

| | |
|---|---|
| Edition | community |
| Source | roadmap, board item "Run workers on more than one box" |
| Tier / score | P2 / 4.7 |
| Stage | 10 |
| Effort | L |
| Autonomy | B |
| Depends on | none |

## Goal

An operator can run `worker`, `worker-drift` and `worker-actions` containers on a second machine against the same Postgres and Redis, by following one deployment document, and a built-in check tells them whether the second box is fit to take work.

## Scope

In scope:
- Pack checkouts that are correct on a box with its own disk.
- More than one unbork source address in the SSH lockout rule.
- A PgBouncer-compatible database setting.
- A functional check command for a worker box, and a list of live workers for the API box.
- A two-box compose example and the deployment document.

Out of scope:
- More than one `api` or `beat` container (covered by the Enterprise "High availability reference deployment").
- Running Postgres or Redis in a cluster.
- Kubernetes manifests.
- Any change to queue names, roles or routing (`ROLE_PROCESSES` stays as it is).

## Context

The role split already exists. `backend/app/celery_manager.py` defines `ROLE_PROCESSES` (line 91) and node names `<worker>@%h`; `backend/app/__main__.py::_wait_for_schema` (line 57) makes non-API roles wait for the schema; `backend/app/healthcheck.py` checks each role. `docs/scaling-out.md` and `dev/docker-compose.split.yml` describe the split on one Docker host. Four things only work because every container shares that host:

1. **Pack checkouts.** `docs/scaling-out.md` ("What stays single-instance") requires one shared volume at `/var/lib/labdog/packs` (`ansible.packs_root_dir`, `backend/app/config.py:242`). A pack synced in the UI is synced by the `api` container alone. Workers call `ensure_registry_current` (`backend/app/actions/registry.py:188`), which compares `registry_inputs` (a digest that includes `ActionPack.current_sha`) and rebuilds the registry from the local disk, but never updates the checkout on that disk. Workers sync only at boot (`_sync_packs_on_worker_start`, `backend/app/tasks/__init__.py:178`), and `sync_pack` (`backend/app/packs/service.py:115`) always pulls the branch tip, not the recorded commit. On a second box with its own disk, a worker would run an old playbook after every pack sync.
2. **Source address.** The SSH lockout rule allows one address: `Host.labdog_source_ip` if detected, else `security.labdog_server_ip` (`backend/app/rules/merge.py:44-48`). `Host.labdog_source_ip` is whatever address the first probe connected from (`backend/app/tasks/drift.py:112`, `resolver_drift.py:111`). With workers on two boxes, a firewall sync run from box A writes a rule that does not allow box B.
3. **PgBouncer.** `backend/app/db.py` builds asyncpg engines (`engine` at line 16, `with_task_engine` at line 110, `task_session` at line 139) with the default prepared-statement cache, which fails behind PgBouncer in transaction mode. Advisory locks are all transaction-level (`pg_advisory_xact_lock` in `app/tasks/host_lock.py`, `app/packs/locks.py`, `app/gitops/importer.py`, `app/auth/users.py:124`), so transaction pooling is otherwise safe. Verify that claim with `grep -rn "pg_advisory_lock\|pg_try_advisory_lock" backend/app` before relying on it.
4. **No functional check.** Nothing tells an operator that a remote worker has the same image version, the same encryption key, a current pack checkout and SSH reach.

Local-directory packs (`PackSourceType.LOCAL`, `_verify_local_pack`) are read from a path on the box that runs the task, so that path must exist on every box. That is a documentation point, not a code change.

## Dependencies

- None on other items.
- External: a second Docker host for the proof. Use lin-manager-2 for the main stack and a second machine or VM named "test" for the worker box. Dennis must say which machine is the second box if no "test" VM can run Docker.

## Implementation steps

1. **Checkout follows the recorded commit.** In `backend/app/packs/service.py` add `async def ensure_checkouts_current(db) -> None`. For each enabled git pack it reads the local `HEAD` of `checkout_path_for(pack.id)` (a `git rev-parse HEAD` through the helpers in `backend/app/actions/git_sync.py`) and, when it differs from `ActionPack.current_sha` or the directory is missing, fetches and checks out exactly `current_sha` under `lock_pack_checkout`. Add a `sync_remote_pack_at(repo, sha, path, auth=...)` helper next to `sync_remote_pack` (`git_sync.py:77`) for that. It must not write `current_sha`, `last_synced_at` or `last_sync_status`: only the API and the boot sync move those.
2. Call `ensure_checkouts_current` from `ensure_registry_current` before `reload_registry_async`, on the branch where the digest changed. It never raises, like its caller. Because `registry_inputs` already includes `current_sha`, a pack sync on the API box makes every worker on every box update its own disk on its next task.
3. Make the checkout lock key per box, so two boxes with separate disks do not wait on each other: add a setting `ansible.packs_shared: bool = True` in `backend/app/config.py`. When false, `lock_pack_checkout` (`backend/app/packs/locks.py:47`) mixes a stable hash of `socket.gethostname()` into the lock key. Default true keeps today's behaviour.
4. **Source addresses.** Add `security.labdog_server_ips: list[str] = []` next to `labdog_server_ip` (`config.py:83`). In `backend/app/rules/merge.py` the lockout rule is built from one `server_ip`; change it to emit one lockout rule per address in the union of `labdog_server_ips`, `labdog_server_ip` and the host's `labdog_source_ip`. Check the paired renderers (`app/rules/renderers/*`) and parsers (`app/sync/parsers/*`) and the drift comparison still agree, since the lockout rule is now several rules. Update `backend/app/ai/labdog_host.py:102` to treat every listed address as unbork's own.
5. **PgBouncer.** Add `database.pgbouncer: bool = False`. When true, every `create_async_engine` call in `backend/app/db.py` passes `connect_args={"statement_cache_size": 0, "prepared_statement_name_func": <uuid-based name>}` and `poolclass=NullPool` for the task engines. Put the three call sites behind one private helper `_engine_kwargs()`.
6. **Functional check.** Add `backend/app/doctor.py`, run as `python -m app.doctor`, exit code 0 or 1, one line per check: database reachable and schema at head; Redis reachable; this image's version equals the API's (store the API's version and migration head in Redis under a key written at API start in `backend/app/main.py` lifespan); the encryption key decrypts a canary (store an encrypted canary in `app_settings` on first API start, model `backend/app/models/app_setting.py`); every enabled pack's checkout is at `current_sha`; optional `--host <id>` opens an SSH connection to that host through `backend/app/ssh_utils.py`.
7. **Worker list.** Add `GET /api/system/workers` in a new `backend/app/api/system_workers.py`, declared with `Depends(require("settings.manage"))`. It returns, per Celery node from `celery_app.control.inspect(timeout=2)`: node name, queues, concurrency and the version each worker reports. Register a custom Celery inspect command in `backend/app/tasks/__init__.py` that returns `importlib.metadata.version("labdog-backend")` and the box hostname. Show the list in Settings › System (`frontend/app/(dashboard)/settings/about/`), with a warning row when a queue has no consumer or versions differ.
8. **Example.** Add `dev/docker-compose.workers.yml`: only `worker`, `worker-drift` and `worker-actions` services, no Postgres or Redis, with `LABDOG_DATABASE__URL`, `LABDOG_REDIS__URL`, the two secrets and `LABDOG_ANSIBLE__PACKS_SHARED=false` taken from an env file. Add an optional `pgbouncer` service to `dev/docker-compose.split.yml` behind a compose profile.
9. Rewrite `docs/scaling-out.md` (see Docs).
10. Follow the document on lin-manager-2 plus the second box, and record the result in `planning/PROGRESS.md`.

## Acceptance criteria

- With `ansible.packs_shared=false` and two worker containers on separate volumes, syncing a pack to a new commit in the UI and then starting an action makes both workers run the playbook from the new commit, with no restart.
- A firewall plan for a host shows one SSH lockout rule per configured source address, and a drift check right after a sync reports no drift.
- With `database.pgbouncer=true` and PgBouncer in transaction mode in front of Postgres, a bulk sync, a drift sweep and an action run complete.
- `python -m app.doctor` exits 0 on a healthy worker box and exits 1, naming the check, when the encryption key differs, the image version differs or a checkout is stale.
- `GET /api/system/workers` lists the workers on both boxes and is refused for a role without `settings.manage`.
- `tests/test_worker_roles.py`, `tests/test_task_routing.py` and `tests/test_orchestrator_queue.py` pass unchanged.
- On the two-box setup, stopping every worker container on the first box leaves syncs and action runs working through the second box.

## Test plan

- `cd backend && source .venv/bin/activate && pytest tests/test_pack_checkout_follow.py tests/test_lockout_multi_source.py tests/test_db_pgbouncer.py tests/test_doctor.py tests/test_system_workers_api.py -q` (all new files), then `pytest tests/test_permissions.py tests/test_worker_roles.py tests/test_task_routing.py -q`, then the full suite once before the stage review.
- `cd frontend && npx tsc --noEmit && npm run lint`.
- Running stack: `dev/docker-compose.split.yml` on lin-manager-2 with the `pgbouncer` profile, and `dev/docker-compose.workers.yml` on the second box. Use the short split smoke run in `dev/loadtest` (`./run_one.sh S3 50 split`) for load.
- Real hosts: firewall sync and drift only against hosts named "test". Never point the two-box stack at the database of the live instance on lin-manager.

## Docs and changelog

- `docs/scaling-out.md`: new section "Workers on a second machine" covering the same image tag everywhere, the four shared settings, Redis and Postgres reachable over the network with a password and TLS (`rediss://`), `packs_shared`, local-directory packs, source addresses, PgBouncer, `python -m app.doctor`, and the worker list. Replace the "One pack checkout, shared" bullet.
- `docs/production-deploy.md` ("Sizing Postgres connections"): link to the PgBouncer setting.
- `docs/security-hardening.md`: Redis must not be exposed without a password, because it carries task arguments.
- `CHANGELOG.md` `[Unreleased]`, Added: "Workers can run on more than one machine: pack checkouts follow the synced commit on each box, the SSH lockout rule accepts several unbork addresses, a PgBouncer mode, and `python -m app.doctor`."

## Risks and rollback

- The lockout rule change touches every firewall sync. A wrong rule can lock unbork out of a host. Prove it on "test" hosts with both backends before anything else, and keep the single-address output byte-identical when `labdog_server_ips` is empty.
- Checking out a recorded commit fails if the remote has dropped it (a force push). Then the worker keeps its old checkout and logs it; the doctor reports it.
- PgBouncer in session mode or with server-side prepared statements off is fine; transaction mode with a session-level lock added later would break silently. Add a test that fails if `pg_advisory_lock(` appears in `backend/app`.
- Rollback: every change sits behind a setting that defaults to today's behaviour (`packs_shared=true`, empty `labdog_server_ips`, `pgbouncer=false`). Reverting the commits removes the doctor and the route; there is no migration.

## Assumptions

- [A-03](../DECISIONS.md): this board P3 item is planned because the Enterprise high availability item depends on it.
- [A-04](../DECISIONS.md): stack tests on lin-manager-2, real hosts named "test" only.
- Assumption (not yet confirmed): the env prefix stays `LABDOG_` or follows whatever the rename item C-001 has done by the time this starts. Use the names the code has at that point.
- Assumption (not yet confirmed): several source addresses are solved by listing them, not by requiring all boxes to share one egress address. Question for Dennis: is a list acceptable, or should worker boxes be required to NAT behind one address?
- Assumption (not yet confirmed): no migration is needed; the canary lives in the existing `app_settings` table.

## Definition of done

- [ ] Code for steps 1 to 8 merged to the stage branch.
- [ ] New tests pass; full backend suite, ruff and bandit clean.
- [ ] `docs/scaling-out.md` rewritten and followed once, end to end, on lin-manager-2 plus a second box.
- [ ] `CHANGELOG.md` line added.
- [ ] `planning/roadmap.yaml` status set to done; one line in `planning/PROGRESS.md` naming the two boxes used.
