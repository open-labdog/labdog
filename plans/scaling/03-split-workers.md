# Step 3 — Split workers by role

Purpose: run API, scheduler, orchestrator and the worker kinds as
separate containers from the **same image**, so worker capacity scales
with `docker compose up --scale` and drift can't starve syncs and
actions. The single-container install must keep working unchanged as
the default.

Do not start until step 2 has a baseline.

## 3.1 Role selection on the entrypoint

`app/__main__.py` today always runs migrations, spawns `CeleryManager`
(both workers + beat) and runs uvicorn; only `--no-celery` exists.

Add `--role` (comma list), also settable as `LABDOG_ROLE`:

| Role | Runs | Notes |
| --- | --- | --- |
| `all` (default) | Today's behaviour | Existing Docker, .deb, .rpm installs unchanged |
| `api` | uvicorn only | Runs migrations (only this role may) |
| `beat` | `celery beat` only | Exactly one per deployment |
| `orchestrator` | orchestrator worker only | `-Q orchestrator` |
| `worker` | worker on `default,long_running` | Scales |
| `worker-drift` | worker on `drift` (see 3.3) | Scales |

Keep `CeleryManager` as the supervisor for whichever Celery roles the
process runs, so each container still has one PID 1 (tini) and one
`poll()` loop. Node names stay unique per container: `-n <role>@%h`
already does that, since `%h` is the container hostname. Note that
`_is_orchestrator_worker` keys off the `orchestrator@` node-name prefix;
keep that prefix.

Migrations: only `api` (and `all`) run `alembic upgrade head`. Other
roles wait until `alembic_version` matches the image's head before
starting Celery, so a worker never runs against an old schema.

## 3.2 Health per role

`/health/ready` (`app/main.py`) checks DB, Redis and the local
`CeleryManager.is_alive()`. In a split deployment:

- `api` container: DB + Redis; Celery reported as "not supervised here"
  (the existing code path for a forked uvicorn worker).
- Worker / beat / orchestrator containers: a Docker `HEALTHCHECK`
  command (e.g. `celery inspect ping -d <role>@$HOSTNAME`; for beat,
  check the RedBeat lock key is held) is enough.
- Optional: an API endpoint listing live nodes per role, shown in
  Settings › System.

## 3.3 Drift on its own queue, and parallel sweeps

1. Route `app.tasks.*_drift.*` and `app.tasks.drift.*` to a new `drift`
   queue in `task_routes` (`app/tasks/__init__.py`). Add `drift` to
   `WORKER_QUEUES` for the `all` role so single-container installs keep
   consuming it. Update `tests/test_task_routing.py` and
   `tests/test_orchestrator_queue.py`.
2. Make `sweep_module` (`app/tasks/drift_sweep.py`) fan out instead of
   looping: the beat-fired task picks candidates and enqueues one
   per-host check task (or bounded batches) on `drift`. The per-host
   lock / check / re-check / commit logic (BUG-67) moves unchanged into
   the per-host task. The sweep must not wait on its children (the same
   trap the orchestrator queue split avoided); aggregate counters in
   logs.
3. Skip enqueueing a host whose check for that module is still in
   flight (Redis `SET NX` with a TTL near the interval), so a slow tick
   can't pile up duplicates.

## 3.4 Fairness for actions

Pick one and measure it in S4:

- **Priority queue:** route host runs of user-triggered runs to
  `actions_interactive`, listed first in the worker's `-Q`.
- **Cap "all at once":** treat `parallelism <= 0` as "up to N"
  (setting, default e.g. 20) so one run can't flood the queue.

Check `worker_prefetch_multiplier` and `task_acks_late` for the
long-running queues; with several containers, prefetch should be 1 so a
busy worker doesn't hoard host runs another container could start.

## 3.5 Things that assume one machine today

Each needs handling or a deliberate "fine as is" with a reason:

- **Pack checkouts.** `ansible.packs_root_dir` is a local path. The
  orchestrator worker reloads the registry but **skips** pack sync,
  relying on its sibling's checkout on the same disk
  (`_sync_packs_on_worker_start` in `app/tasks/__init__.py`). In its own
  container it must sync too, or share a volume. Recommendation: every
  container syncs its own checkout (the checkout lock is a Postgres
  advisory lock in `app/packs/locks.py`, so this is safe, just
  serialised). Check what `run_action` reads from disk vs the registry.
- **Startup orphan sweep.** Every non-orchestrator worker enqueues
  `sweep_stale_syncs` and `sweep_stale_action_runs` on boot
  (`_sweep_orphans_on_worker_start`). With N worker containers that's
  N copies racing over the same rows. They're deadline-based, so likely
  harmless; guard with a Redis `SET NX` or prove the race benign.
- **SSH terminal registry** (`app/ssh_terminal/session_registry.py`) is
  in-process. Fine with one `api` container; several need sticky
  sessions or Redis-backed counts. Not needed for this goal.
- **SSH keys in `/dev/shm`.** Per-container tmpfs; Docker's default
  64 MB is plenty for keys.
- **Encryption key.** Every worker container needs
  `LABDOG_SECURITY__ENCRYPTION_KEY`, same as today.
- **Postgres connections.** ~2 per in-flight task (`task_session`, pool
  2) + API pool 15. At 16 + 16 + 4 slots + API that's ~90, near the
  default `max_connections = 100`. Either raise it or add PgBouncer.
  PgBouncer in transaction mode is compatible with
  `pg_advisory_xact_lock` (transaction-scoped), but verify nothing uses
  session-level advisory locks or prepared statements that break it
  (asyncpg needs `statement_cache_size=0` behind transaction pooling).

## 3.6 Deployment artefacts

- `dev/docker-compose.split.yml`: `api`, `beat`, `orchestrator`,
  `worker` (scale 2), `worker-drift`, Postgres, Redis, optional
  PgBouncer. Same image, different `command`.
- Docs: a "Scaling out" page under `docs/` with the topology table from
  the README and the per-role settings.
- `packaging/systemd/labdog.service` stays the `all` role. Optionally a
  `labdog@.service` template per role for .deb / .rpm users.

## 3.7 Verify

Rerun step 2 scenarios S1–S5 with the split topology at the same total
slot count as the "raised" baseline. Expected wins: sync wait in S2/S3
(drift no longer competes), S4 (fairness), S1 at 300 hosts (parallel
sweeps). Record in `plans/scaling/results/03-split.md`.

Tests to add or update: task routing (new queue), role parsing,
`sweep_module` fan-out keeping BUG-67 behaviour (skip a busy host,
discard a verdict claimed mid-check), health per role.

Before the PR leaves draft: delete `plans/` and carry the numbers into
the PR body and commit messages.
