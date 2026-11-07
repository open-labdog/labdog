-- Read-only. plans/scaling/01-measure.md, plus :from/:to bounds for the harness.
\pset footer off
\echo == drift per module
SELECT module_type, count(*) checks, round(avg(duration_ms)) avg_ms,
       percentile_cont(0.5) WITHIN GROUP (ORDER BY duration_ms) p50_ms,
       percentile_cont(0.95) WITHIN GROUP (ORDER BY duration_ms) p95_ms, max(duration_ms) max_ms
FROM drift_samples WHERE checked_at BETWEEN :'from' AND :'to' AND duration_ms IS NOT NULL
GROUP BY 1 ORDER BY 3 DESC;
\echo == drift status
SELECT module_type, status, count(*) FROM drift_samples
WHERE checked_at BETWEEN :'from' AND :'to' GROUP BY 1,2 ORDER BY 1,2;
\echo == sync
SELECT coalesce(module_type,'bulk') kind, status, count(*) jobs,
       round(avg(extract(epoch FROM completed_at-started_at))) avg_run_s,
       round(percentile_cont(0.95) WITHIN GROUP (ORDER BY extract(epoch FROM completed_at-started_at))) p95_run_s,
       round(percentile_cont(0.5) WITHIN GROUP (ORDER BY extract(epoch FROM started_at-created_at))) p50_wait_s,
       round(percentile_cont(0.95) WITHIN GROUP (ORDER BY extract(epoch FROM started_at-created_at))) p95_wait_s,
       round(max(extract(epoch FROM started_at-created_at))) max_wait_s
FROM sync_jobs WHERE created_at BETWEEN :'from' AND :'to' AND started_at IS NOT NULL GROUP BY 1,2;
\echo == action host runs
SELECT ahr.status, count(*) host_runs,
       round(avg(extract(epoch FROM ahr.finished_at-ahr.started_at))) avg_s,
       round(percentile_cont(0.95) WITHIN GROUP (ORDER BY extract(epoch FROM ahr.finished_at-ahr.started_at))) p95_s
FROM action_host_runs ahr JOIN action_runs ar ON ar.id=ahr.action_run_id
WHERE ar.created_at BETWEEN :'from' AND :'to' GROUP BY 1;
\echo == action runs
SELECT id, parallelism, status, round(extract(epoch FROM started_at-created_at)) wait_s,
       round(extract(epoch FROM finished_at-started_at)) run_s
FROM action_runs WHERE created_at BETWEEN :'from' AND :'to' ORDER BY id;
\echo == error samples
SELECT left(error_message,160) err, count(*) FROM action_host_runs
WHERE error_message IS NOT NULL GROUP BY 1 ORDER BY 2 DESC LIMIT 10;
SELECT left(error_message,160) err, count(*) FROM sync_jobs
WHERE error_message IS NOT NULL GROUP BY 1 ORDER BY 2 DESC LIMIT 10;
\echo == stuck rows at end
SELECT 'sync' k, status::text, count(*) FROM sync_jobs WHERE status IN ('pending','running') GROUP BY 2
UNION ALL SELECT 'run', status::text, count(*) FROM action_runs WHERE status IN ('pending','queued','running') GROUP BY 2
UNION ALL SELECT 'hostrun', status::text, count(*) FROM action_host_runs WHERE status IN ('pending','running') GROUP BY 2;
