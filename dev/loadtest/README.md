# Load test harness

Runs a LabDog image against N fake SSH hosts and measures queues,
waits, drift sweeps and resources. Used to size the worker roles in
[`docs/scaling-out.md`](../../docs/scaling-out.md); the results are in
PR #270.

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

Configs (the third argument of `run_one.sh`; anything after the prefix
is a free label for the results directory):

| Config | Topology | Slots |
|---|---|---|
| `default` | one `all` container | celery 4 / orchestrator 4 |
| `raised*` | one `all` container | 16 / 12 |
| `split*` | role containers (`docker-compose.split.yml`): `api`, `beat`, `orchestrator` 12, `worker` 2 × 6, `worker-drift` 4 | 28, as `raised` |

## Two hosts

With `FAKEHOST_DOCKER=ssh://user@host` the fake hosts run on a second
Docker host, so their CPU does not compete with LabDog's. The fake-host
network there is a NAT bridge on 172.30.0.0/16 (`gateway_mode_ipv4:
nat-unprotected`), and the LabDog host needs a route to it:
`ip route replace 172.30.0.0/16 via <second host>`. The LabDog stack
uses 172.29.0.0/16. Raise `MaxStartups` in the second host's sshd
config: Compose opens many SSH sessions at once.

`MATRIX="S1:300:split2h S2:300:split2h" ./run_matrix.sh` runs just those
runs (`SID:N:config`).

## Notes

- `service` drift is skipped (needs systemd as PID 1).
- Images built before the BUG-128 fix lack `ansible.posix`, which
  linux-user sync needed; `prepare.sh` installs it into
  `.state/collections` and the compose mounts it. Newer images do not
  need it, and the mount is harmless.
- Postgres `max_connections` is 400 for the raised config (`PG_MAX_CONNECTIONS`);
  16/12 needs more than the default 100.
- 10 min warm-up, 60 min window, then up to 15 min drain before measuring.
- On one host, LabDog and the fake hosts share the CPU, so contention
  from the fake hosts is part of the numbers; use two hosts to avoid it.
