# Scaling out

A single LabDog container runs everything: the API, the scheduler and two
Celery workers. That is the default and needs no configuration. For a few
hundred hosts, or a busy schedule, the same image can run as several
containers, one per **role**, so worker capacity scales on its own.

## Roles

Set `LABDOG_ROLE` (or pass `--role` to `python -m app`). A comma list runs
several roles in one container.

| Role | Runs | Notes |
| --- | --- | --- |
| `all` (default) | API, `beat`, `work` worker (`default`, `long_running`, `drift`), `orchestrator` worker | The single-container install. Cannot be combined with other roles. |
| `api` | uvicorn only | The only split role that runs database migrations. |
| `beat` | the RedBeat scheduler | Exactly **one** per deployment. |
| `orchestrator` | worker on `orchestrator` | Holds one slot per running action run. |
| `worker` | worker on `default`, `long_running` | Syncs, the per-host part of action runs, discovery. Scales. |
| `worker-drift` | worker on `drift` | Drift checks, one task per host and module. Scales. |

Roles other than `api` wait at startup until the database schema is at the
image's migration head, so start order does not matter. A Celery-only
container exits when its worker dies, so the container runtime restarts
it. The image's health check (`python -m app.healthcheck`) checks the
right thing for each role: `/health/ready` for `all` and `api`, a broker
ping for workers, and the scheduler lock for `beat`.

`dev/docker-compose.split.yml` is a working example:

```sh
docker compose -f dev/docker-compose.split.yml up -d --scale worker=2 --scale worker-drift=1
```

## Settings per role

| Setting | Used by | Default |
| --- | --- | --- |
| `celery.concurrency` (`LABDOG_CELERY__CONCURRENCY`) | `work` in `all`, `worker` | 4 |
| `celery.drift_concurrency` | `worker-drift` | 4 |
| `celery.orchestrator_concurrency` | `orchestrator` | 4 |

Every container needs the same `LABDOG_DATABASE__URL`, `LABDOG_REDIS__URL`,
`LABDOG_SECURITY__SECRET_KEY` and `LABDOG_SECURITY__ENCRYPTION_KEY`.

## Drift checks

Each module's periodic sweep enqueues one check per host on the `drift`
queue and returns. The checks run in parallel across every `drift` slot,
so a sweep's duration no longer grows with the host count at one host
at a time. A host whose previous check for that module is still queued or
running is not enqueued again in the same tick. The per-host lock rules
are unchanged: a host busy with a sync or action run is skipped for that
tick, and a verdict gathered while a sync claimed the host is discarded.

## Postgres connections

Budget about six Postgres connections per worker slot, counting every
role's slots (`concurrency` per `worker` container, `drift_concurrency`,
`orchestrator_concurrency`), plus the API's pool; see
[Sizing Postgres connections](production-deploy.md#sizing-postgres-connections).
In load tests at 300 hosts, the split topology with 28 slots peaked at
130 connections. The example compose file sets `max_connections` to 200,
plenty at the default slot counts. Put PgBouncer (transaction mode) in
front of Postgres once the total grows.

## What stays single-instance

* **One `api` container.** The SSH terminal session registry is kept in
  process. Several API containers would need sticky sessions.
* **One `beat`.** Two schedulers against one RedBeat keyspace would fire
  every periodic job twice.
* **Pack checkouts** live on each container's own disk. Every worker
  container, the orchestrator included when it runs alone, syncs its own
  checkout at boot; the checkout lock is a Postgres advisory lock, so this
  is safe, just serialised.
