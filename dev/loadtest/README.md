# Load test harness (plans/scaling step 2)

Runs a published LabDog image against N fake SSH hosts on one Docker
host and measures queues, waits, drift sweeps and resources. LabDog's
own code is not changed.

Needs Docker with Compose v2, python3, and enough room for N small
containers (300 hosts used about 6 GB RAM in total).

```sh
./prepare.sh                     # SSH key, .env secrets, ansible.posix, fakehost image
LABDOG_IMAGE=openlabdog/labdog:dev-5cb85d7 ./run_one.sh S2 300 default
./run_matrix.sh                  # S1-S5 x default/raised at 300, then S1 at 50/150
```

Results land in `results/<SID>-n<N>-<cfg>/`: `measure.txt` (the
`measure_live.sql` queries over the window), `samples.csv` (every 10 s),
`drift_samples.csv`, `marks.json`, `seed.log`, `labdog-log.gz`.

## Pieces

| File | What it does |
|---|---|
| `docker-compose.yml` | postgres, redis, labdog, `fakehost` (scaled to N) on 172.30.0.0/16; API on 127.0.0.1:8000 only |
| `fakehost/` | Debian slim with sshd, sudo, cron, python3-apt; `netem` adds `NETEM_DELAY_MS` (20 ms); memory limit `FAKEHOST_MEM_LIMIT` (128m) |
| `pack/` | local pack with action `loadtest-sleep` (gather facts, sleep, write a marker) |
| `seed.py N` | admin, SSH key, hosts, overlapping groups with config for packages, hosts-file, cron, linux-users, resolver; drift on; initial bulk sync, then checks it applied |
| `scenario.py SID` | drives the load for the window (below) |
| `sample.sh` | CSV of memory, CPU, queue depths, pg connections, pending/running rows, host load |
| `measure_live.sql` | drift, sync, action and stuck-row queries bounded by `:from`/`:to` |
| `run_one.sh` / `run_matrix.sh` | one scenario on a fresh stack (`down -v` before and after) / the full matrix |

## Scenarios

| ID | Load |
|---|---|
| S1 | drift sweeps only (default 30 min interval) |
| S2 | S1 + bulk sync of all hosts every 15 min |
| S3 | S2 + 8 action schedules, batch 10, sleep 20-60 s, two firings each per hour |
| S4 | S3 + one all-hosts run with parallelism 0 at window minute 20 |
| S5 | S3 with 4 of the schedules on the same minute |

Configs: `default` = celery 4 / orchestrator 4; `raised` = 16 / 12
(`LABDOG_CELERY__CONCURRENCY`, `LABDOG_CELERY__ORCHESTRATOR_CONCURRENCY`).

## Deviations from the plan

- `service` drift is skipped (needs systemd as PID 1), as the plan allows.
- The image lacks the `ansible.posix` collection; `prepare.sh` installs it
  into `.state/collections` and the compose mounts it. Without it every
  sync with a linux user fails to parse (see results).
- Postgres `max_connections` is 400 for the raised config (`PG_MAX_CONNECTIONS`);
  16/12 needs more than the default 100.
- 10 min warm-up, 60 min window, then up to 15 min drain before measuring.
- LabDog and all fake hosts share the same machine, so CPU contention
  from the fake hosts is part of the numbers.
