# C-028: kennel: repository, service skeleton and Proxmox pool token

| | |
|---|---|
| Edition | community |
| Source | roadmap, board item "Companion VM layer: backup restore tests, leases, sweeper, and LabDog integration"; decisions D-19, D-22, D-23 |
| Tier / score | P2 / 4.7 |
| Stage | 11 |
| Effort | L |
| Autonomy | H |
| Depends on | S-003 |

## Goal

kennel (working name) exists as its own AGPL repository and service: it starts from its own image with its own database, answers a health check, authenticates callers with API tokens, and holds a connection to a Proxmox cluster through a token that can only touch one resource pool.

## Scope

In scope:
- The repository, licence, CI and contribution files.
- A service skeleton: API process, worker process, database, migrations, configuration, API tokens, audit log, health check, image and compose file.
- The Proxmox cluster connection model and a client, with the pool-scoped token and a self-test that proves the scope.
- A job table and worker loop that later items put their work on.

Out of scope:
- Leases, the sweeper, restore tests, restore points, the sandbox network, the readiness API (C-054, C-055, C-045, C-057, C-058).
- Any change to unbork (C-059).
- A web UI. See Assumptions.
- In-place restore over a production VM ([D-23](../DECISIONS.md): later, opt-in, second token).

## Context

kennel is new. Nothing of it exists. The sources for its design are:

- [D-22](../DECISIONS.md): build it as a standalone AGPL "VM operations service" owning restore tests, PBS restore points, leases, sandbox copies, a sweeper and a per-VM readiness API.
- [D-23](../DECISIONS.md): sandbox network from the start, restore beside the original first, name "kennel" for now.
- [D-19](../DECISIONS.md): RAM snapshots stay in unbork; PBS restore belongs to kennel.
- The write-up "How far kennel can go" (https://claude.ai/artifact/MJo5wKpzFdKspSeMNDrL3S) and the board item body (closed PR #281). Their boundary rule: unbork calls kennel over REST with an API token, kennel answers with signed webhooks, neither reads the other's database, and a VM is named `(cluster, vmid)` on both sides plus the cluster's certificate fingerprint.

Code in unbork worth reusing as a starting point, because kennel talks to the same API. Copy it into kennel (both are AGPL, same copyright holder); do not import unbork as a dependency:

- `backend/app/proxmox/client.py`: `ProxmoxClient` (token auth header, TLS with an optional CA in `_get_ssl_context` line 69, `_request` line 110, `get_task_status` line 323, `wait_for_task` line 337, `list_node_storage` line 297, `get_vm_config` line 289).
- `backend/app/proxmox/models.py`: `ProxmoxNode` (API URL, token id, encrypted token secret, `verify_ssl`, plaintext `ca_cert_pem`). The encryption helpers are `app.crypto.encryption.encrypt_ssh_key` and `decrypt_ssh_key` (AES-256-GCM).
- `backend/app/workflows/steps/cleanup.py`: retry with backoff on a locked VM.
- The permission pattern `Depends(require("..."))` in `backend/app/auth/permissions.py`, and `tests/test_permissions.py`, which fails on any route without one.
- Roles in one image: `backend/app/celery_manager.py::ROLE_PROCESSES` and `backend/app/__main__.py`.

## Dependencies

- **The repository does not exist. Dennis must create it** (name, visibility, CLA check as on unbork per D-01) and say so in `DECISIONS.md`.
- **Proxmox side, by Dennis, on the test cluster:** a resource pool (proposed name `kennel`), a user and API token with privilege separation, and a role on `/pool/kennel` only. Proposed starting privileges: `VM.Allocate`, `VM.Audit`, `VM.Config.*`, `VM.PowerMgmt`, `VM.Monitor`, `VM.Clone`, `Pool.Audit`, plus `Datastore.AllocateSpace` and `Datastore.Audit` on the one storage the pool's disks go to, and `SDN.Use` on the sandbox bridge once C-057 exists. Later items list what they add.
- S-003 (the snapshot provider interface in unbork) is a dependency in the plan because its shape is what kennel must fit behind later. This item uses none of its code.
- A test Proxmox cluster and its API address, reachable from lin-manager-2.

## Implementation steps

1. **Blocked until the repository exists** and Dennis has logged its location and the pool token in `planning/DECISIONS.md`. Do not create a repository, and do not start in a scratch repository under another name. Until then, skip this item and every item that depends on it.
2. Proposed repository layout (a proposal; adjust in the first PR and record changes in kennel's own `CLAUDE.md`):
   ```
   LICENSE                     AGPL-3.0, same header and copyright line as unbork
   README.md  CONTRIBUTING.md  SECURITY.md  CHANGELOG.md  CLAUDE.md  VERSION
   backend/pyproject.toml      Python 3.12, FastAPI, SQLAlchemy async, asyncpg, httpx, alembic
   backend/kennel/__main__.py  python -m kennel --role all|api|worker
   backend/kennel/main.py      create_app(), router registration
   backend/kennel/config.py    TOML plus KENNEL_ env overrides
   backend/kennel/db.py
   backend/kennel/auth/        API tokens, require()
   backend/kennel/api/         health.py, clusters.py, tokens.py, jobs.py, audit.py
   backend/kennel/models/      cluster.py, api_token.py, job.py, audit_log.py
   backend/kennel/proxmox/     client.py (from unbork), errors.py
   backend/kennel/jobs/        runner.py (worker loop), registry.py
   backend/kennel/webhooks.py  signed outgoing calls
   backend/alembic/versions/   0001_initial.py
   backend/tests/
   Dockerfile  dev/docker-compose.yml  dev/kennel.toml
   .github/workflows/ci.yml    ruff, bandit, pytest, gitleaks, pip-audit, image build
   ```
3. **Configuration and secrets.** `KENNEL_DATABASE__URL`, `KENNEL_SECURITY__ENCRYPTION_KEY` (AES-256-GCM key for stored Proxmox token secrets), `KENNEL_SECURITY__BOOTSTRAP_TOKEN` (optional; creates the first admin token at first start). Startup refuses placeholder values, as unbork's `config.py` does.
4. **Database.** Migration `0001_initial.py`: `clusters` (name, api_url, token_id, encrypted_token_secret, verify_ssl, ca_cert_pem, tls_fingerprint, pool, disk_storage), `api_tokens` (name, sha256 of the token, scopes, created_at, last_used_at, revoked_at), `jobs` (id, kind, payload JSON, status `queued|running|succeeded|failed|cancelled`, idempotency_key unique per kind, owner_token_id, attempts, locked_at, heartbeat_at, result JSON, error, timestamps), `audit_log` (actor token, action, target, detail, at).
5. **API tokens and permissions.** Tokens are shown once and stored hashed. Scopes: `read`, `leases`, `restore`, `admin`. Every route declares one with `Depends(require("<scope>"))`; add a test that walks `app.routes` and fails on a route without one, with an explicit public list holding only `GET /health` and `GET /api/version`.
6. **Routes in this item:** `GET /health` (public: database reachable, worker heartbeat fresh), `GET /api/version` (public), `GET/POST/PATCH/DELETE /api/clusters` (`admin`; `GET` allowed with `read`), `POST /api/clusters/{id}/test` (`admin`), `GET/POST/DELETE /api/tokens` (`admin`), `GET /api/jobs/{id}` (`read`), `GET /api/audit` (`admin`).
7. **Cluster self-test.** `POST /api/clusters/{id}/test` checks: the API answers; the TLS fingerprint matches the stored one; the pool exists; the token can list the pool; and the token cannot see a VM outside the pool (list `/cluster/resources` and fail the test if any VM outside the pool is visible with more than audit rights, using `/access/permissions`). The result is stored and shown, so a token that is too broad is caught at setup.
8. **Worker.** `python -m kennel --role worker` runs a loop that claims jobs with `SELECT ... FOR UPDATE SKIP LOCKED`, runs the handler registered for the job kind, writes a heartbeat, and marks jobs whose heartbeat is stale as failed at start. One job kind exists in this item: `cluster_test`. No Redis.
9. **Webhooks.** `webhooks.py` sends JSON with an HMAC-SHA256 signature header and a timestamp, with retries and backoff from an outbox table. No event is emitted yet; later items use it.
10. **Image and compose.** `Dockerfile` (digest-pinned base, non-root user, `HEALTHCHECK` calling `/health`), `dev/docker-compose.yml` with kennel `api`, kennel `worker` and its own Postgres. kennel may share a Postgres server with unbork but never a database.
11. **Docs.** `README.md` (what kennel is and is not), `docs/install.md`, `docs/proxmox-token.md` (the exact pool, role and token commands, and why the scope matters), `docs/api.md` (generated OpenAPI plus the idempotency rule).

## Acceptance criteria

- `docker compose -f dev/docker-compose.yml up -d` starts kennel from its own image with its own database, and `GET /health` returns 200. This is the first stage behaviour.
- Stopping the worker turns `/health` unhealthy within the documented time.
- A request without a token gets 401; a token with scope `read` gets 403 on `POST /api/clusters`.
- The cluster self-test passes with the pool token and fails, naming the reason, with a token that has rights outside the pool.
- Posting the same job twice with one idempotency key returns the same job.
- The route test fails when a route is added without `require()`.
- The Proxmox token secret is never returned by any route and never appears in a log line.

## Test plan

- In the kennel repository: `cd backend && source .venv/bin/activate && pytest tests/ -q` (Postgres through testcontainers; Proxmox mocked with `httpx.MockTransport`, as `backend/tests/test_proxmox_client.py` does in unbork), `ruff check . && ruff format --check .`, `bandit -q -r kennel`.
- Running stack on local Docker on lin-manager-2, connected to the test Proxmox cluster with the pool token. The self-test is the only call that reaches Proxmox in this item, and it is read-only.
- Nothing in this item touches unbork's backend or frontend, so its suites are not affected.

## Docs and changelog

- kennel's own `README.md`, `docs/`, and `CHANGELOG.md` `[Unreleased]`: "Initial service skeleton: API tokens, cluster connections with a pool-scoped token and self-test, job worker, health check."
- unbork: no doc change in this item. `planning/roadmap.yaml` and `PROGRESS.md` only.

## Risks and rollback

- A token broader than the pool removes kennel's main safety property. The self-test and `docs/proxmox-token.md` are the guard; later items must not ask for cluster-wide rights without saying so.
- A second product for one maintainer. Keep the skeleton small and copy patterns from unbork instead of inventing new ones.
- Rollback: kennel is a separate service; stopping it affects nothing in unbork until C-059.

## Assumptions

- [D-22](../DECISIONS.md), [D-23](../DECISIONS.md), [D-19](../DECISIONS.md) as quoted above.
- Assumption (not yet confirmed): Python with FastAPI, SQLAlchemy and Postgres, like unbork. Question for Dennis: confirm the stack.
- Assumption (not yet confirmed): a Postgres job table and one worker process instead of Celery and Redis, because kennel's jobs are few and long. Question for Dennis: is that acceptable, or should it match unbork's Celery setup?
- Assumption (not yet confirmed): no web UI in stage 11. The board notes ask for a UI of its own, but no staged item covers it. Question for Dennis: add a UI item, or is the API enough for the first version?
- Assumption (not yet confirmed): one resource pool per cluster connection, named by the operator.
- Assumption (not yet confirmed): the image is published under the same registry organisation as unbork once Dennis has named the repository.

## Definition of done

- [ ] Repository created by Dennis and logged in `DECISIONS.md`.
- [ ] Skeleton merged to kennel's default development branch through a PR; CI green.
- [ ] Health check and cluster self-test proven on lin-manager-2 against the test cluster.
- [ ] kennel docs and changelog written.
- [ ] `planning/roadmap.yaml` status updated; one line in `planning/PROGRESS.md` with the repository URL.
