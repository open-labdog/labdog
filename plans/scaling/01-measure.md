# Step 1 — Measure the live install (read-only)

Purpose: replace the "~2 s per host per module" and "~150 MB per Ansible
run" guesses with numbers from Dennis's live install (~20 hosts, Docker).
Nothing here writes to the database or changes config.

Deliverable: `plans/scaling/results/01-live.md` with the query output
(hostnames redacted) and the derived per-host numbers, committed to
this branch.

## How to run

The agent cannot reach the live install. Write the queries below into
`dev/loadtest/measure_live.sql` and a wrapper
`dev/loadtest/measure_live.sh` that runs them with
`docker exec <postgres-container> psql -U labdog -d labdog -f -`
and prints plain text. Dennis runs it and pastes the output back.

Default the window to the last 14 days (`:days` psql variable).

## Queries

All tables and columns verified against `dev` (v1.1.0).

### Drift check time per host per module

`drift_samples.duration_ms` is the wall-clock time of one host's
collect-and-diff.

```sql
SELECT module_type,
       count(*)                                         AS checks,
       round(avg(duration_ms))                          AS avg_ms,
       percentile_cont(0.5)  WITHIN GROUP (ORDER BY duration_ms) AS p50_ms,
       percentile_cont(0.95) WITHIN GROUP (ORDER BY duration_ms) AS p95_ms,
       max(duration_ms)                                 AS max_ms
FROM drift_samples
WHERE checked_at > now() - make_interval(days => :days)
  AND duration_ms IS NOT NULL
GROUP BY module_type
ORDER BY avg_ms DESC;
```

Sum of `avg_ms` across modules × host count = slot-seconds per drift
interval. This is the single most important number.

### Sync duration and queue wait

```sql
SELECT coalesce(module_type, 'bulk')                     AS kind,
       count(*)                                          AS jobs,
       round(avg(extract(epoch FROM completed_at - started_at)))  AS avg_run_s,
       round(percentile_cont(0.95) WITHIN GROUP (ORDER BY extract(epoch FROM completed_at - started_at))) AS p95_run_s,
       round(avg(extract(epoch FROM started_at - created_at)))    AS avg_wait_s,
       max(extract(epoch FROM started_at - created_at))           AS max_wait_s
FROM sync_jobs
WHERE created_at > now() - make_interval(days => :days)
  AND started_at IS NOT NULL AND completed_at IS NOT NULL
GROUP BY 1
ORDER BY jobs DESC;
```

### Action time per host, by action

```sql
SELECT ar.action_key,
       count(*)                                          AS host_runs,
       round(avg(extract(epoch FROM ahr.finished_at - ahr.started_at)))  AS avg_s,
       round(percentile_cont(0.95) WITHIN GROUP (ORDER BY extract(epoch FROM ahr.finished_at - ahr.started_at))) AS p95_s,
       round(avg(extract(epoch FROM ahr.started_at - ar.created_at)))    AS avg_wait_from_run_created_s
FROM action_host_runs ahr
JOIN action_runs ar ON ar.id = ahr.action_run_id
WHERE ar.created_at > now() - make_interval(days => :days)
  AND ahr.started_at IS NOT NULL AND ahr.finished_at IS NOT NULL
GROUP BY ar.action_key
ORDER BY host_runs DESC;
```

### How often runs overlap (is the 4-orchestrator cap ever hit?)

```sql
WITH ev AS (
  SELECT started_at AS t, 1 AS d FROM action_runs
   WHERE started_at > now() - make_interval(days => :days)
  UNION ALL
  SELECT finished_at, -1 FROM action_runs
   WHERE finished_at > now() - make_interval(days => :days)
)
SELECT max(running) AS max_concurrent_runs
FROM (SELECT sum(d) OVER (ORDER BY t) AS running FROM ev) s;
```

Repeat the same shape for `sync_jobs` (`started_at` / `completed_at`)
to see peak concurrent syncs.

### Fleet size and drift load

```sql
SELECT module_type,
       count(*) FILTER (WHERE drift_check_enabled) AS drift_enabled_hosts,
       count(*)                                     AS hosts
FROM host_module_status
GROUP BY module_type;
```

## Memory under load

While the queries above are cheap, memory needs a live observation:

1. `docker stats --no-stream labdog` at idle, three times a minute
   apart. Record RSS.
2. Trigger a bulk sync of all hosts from the UI (it already runs on a
   schedule, so this is an ordinary operation, not a test load).
3. Sample `docker stats --no-stream labdog` every 2 s for the duration
   (`while :; do docker stats --no-stream --format '{{.MemUsage}}' labdog; sleep 2; done`).
4. Inside the container, `ps -eo rss,args --sort=-rss | head -30` at the
   peak, to split RSS between Python workers and `ansible-playbook`.

Derived numbers to write down:

- Idle RSS and its split (API, each worker parent + children, beat).
- Peak RSS with 4 syncs in flight → per-slot Ansible cost.
- Postgres connections at peak:
  `SELECT count(*) FROM pg_stat_activity WHERE datname = 'labdog';`

## Output

Fill in the "Working estimates" table in `README.md` with real values and
note which bottleneck from the README the numbers confirm.
