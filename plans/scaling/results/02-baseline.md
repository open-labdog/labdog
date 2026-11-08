# 02 — Baseline on unchanged code

Status: **complete** (2026-11-08). All 14 runs finished; raw data stays on
the test box (`results/<SID>-n<N>-<cfg>/`).

- Image `openlabdog/labdog:dev-5cb85d7` (PR base, v1.1.0), harness in
  `dev/loadtest/` (see its README for scenarios and deviations).
- Test box: Debian 12 VM, 4 vCPU, 16 GB, Docker 29.9. LabDog, Postgres,
  Redis and all 300 fake hosts share it.
- N = 300 hosts, 20 ms netem, 10 min warm-up, 60 min window, up to
  15 min drain. Drift interval left at the default 30 min.
- Initial bulk sync of 300 hosts at 4 slots: 2011–2052 s (~27 s per host
  per slot).

## Results, default config (celery 4 / orchestrator 4), N = 300

| | S1 drift | S2 + bulk 15 min | S3 + actions | S4 + par-0 run | S5 + same-minute |
|---|---|---|---|---|---|
| Bulk syncs succeeded¹ | – | 372 | 298 | 230 | 321 |
| Bulk run time avg | – | 26 s | 26 s | 25 s | 26 s |
| Bulk wait p50 / p95 | – | 1716 / 2161 s | 1909 / 2306 s | 1845 / 2283 s | 1855 / 2272 s |
| Syncs pending after drain | 0 | 92 + 1 running | 144 + 4 | 210 | 127 + 4 |
| Action host runs succeeded | – | – | 30 of 270 | 92 of 480 | 39 of 360 |
| Action runs finished | – | – | 0 of 7 | 0 of 8 | 0 of 8 |
| Gap between drift sweeps² | 30 min | ~48 min | ~49 min | ~50 min | ~48 min |
| Slowest sweep (package) | 397 s | 416 s | 424 s | 419 s | 435 s |
| `long_running` queue max | 1 | 323 | 373 | 662 | 363 |
| LabDog RSS max | ≥1 GiB³ | ≥1 GiB³ | 1513 MiB | 1518 MiB | 1507 MiB |
| Postgres RSS max | 140 MiB | 207 MiB | 232 MiB | 254 MiB | 230 MiB |
| Postgres connections max | 30 | 41 | 57 | 71 | 54 |
| Host CPU avg / max | 14 / 47 % | 76 / 88 % | 71 / 89 % | 66 / 86 % | 75 / 92 % |
| Min available memory | 10.5 GB | 9.8 GB | 9.7 GB | 9.7 GB | 9.6 GB |
| Failures | 0 | 0 | 0 | 0 | 1⁴ |

¹ Jobs created in the window that succeeded by the end of the drain.
Hosts that already have a pending job are skipped on the next bulk
request, so the backlog stays capped at 300.
² Time between the end of one package sweep and the next; the
configured interval is 30 min.
³ Sampler bug (locale made `awk` truncate `1.2GiB` to 1), fixed from S3.
Read as "at least 1 GiB".
⁴ A fake host's `apt-mark` hit the 128 MiB container limit: harness
artefact, not LabDog.

Drift check time per host (S1, stable across scenarios): package
~1.3 s, linux_user ~0.66 s, cron / resolver / hosts_file ~0.32 s, so
about 2.9 s per host over five modules. A full set of serial sweeps
uses ~1020 slot-seconds per 30 min (~14 % of 4 slots).

## Results, raised config (celery 16 / orchestrator 12), N = 300

Postgres `max_connections` raised from 100 to 400 for these runs; the
first attempt at 100 broke (see bugs).

| | S1 drift | S2 + bulk 15 min | S3 + actions | S4 + par-0 run | S5 + same-minute |
|---|---|---|---|---|---|
| Bulk syncs succeeded¹ | – | 510 | 432 | 317 | 471 |
| Bulk run time avg | – | 91 s | 97 s | 93 s | 93 s |
| Bulk wait p50 / p95 | – | 1278 / 1698 s | 1482 / 2066 s | 1407 / 2234 s | 1376 / 1850 s |
| Syncs pending after drain | 0 | 46 + 15 running | 103 + 12 | 221 + 16 | 79 + 15 |
| Action host runs succeeded | – | – | 71 of 810 | 370 of 1020 | 61 of 720 |
| Action runs finished | – | – | 0 of 9 | 1 of 9 (par-0, 2842 s) | 0 of 8 |
| Gap between drift sweeps² | 30 min | 32–43 min | ~44 min | ~44 min | ~44 min |
| Slowest sweep (package) | 424 s | 649 s | 649 s | 647 s | 646 s |
| `long_running` queue max | 0 | 275 | 367 | 658 | 359 |
| LabDog RSS max | 3.1 GiB | 4.3 GiB | 4.3 GiB | 4.3 GiB | 4.2 GiB |
| Postgres RSS max | 464 MiB | 559 MiB | 525 MiB | 593 MiB | 566 MiB |
| Postgres connections max | 116 | 131 | 123 | 183 | 132 |
| Host CPU avg / max | 15 / 54 % | 100 / 100 % | 99 / 100 % | 97 / 100 % | 99 / 100 % |
| Min available memory | 8.1 GB | 6.7 GB | 6.6 GB | 6.7 GB | 6.7 GB |
| Failures | 0 | 0 | 0 | 0 | 0 |

With load on, the 4 vCPUs are pinned at 100 % (LabDog and 300 fake hosts
share them), so each sync takes 3.5× longer and 4× the slots buys only
~35 % more syncs. Read the raised numbers as "CPU-bound on this box",
not as what 16 slots can do with spare cores.

## Drift sweeps vs host count (S1)

| N | config | package sweep | user sweep | cron sweep | PG conns max | LabDog RSS max |
|---|---|---|---|---|---|---|
| 50 | default | 67 s | 34 s | 17 s | 52 | 1.2 GiB |
| 150 | default | 205 s | 105 s | 52 s | 34 | 1.1 GiB |
| 300 | default | 397 s | 209 s | 104 s | 30 | ≥1 GiB³ |
| 50 | raised | 72 s | 36 s | 18 s | 91 | 2.9 GiB |
| 150 | raised | 202 s | 106 s | 53 s | 128 | 2.9 GiB |
| 300 | raised | 404–418 s | 212 s | 107 s | 116 | 3.1 GiB |

Sweep time is linear in N (~1.33 s per host for package) and does not
depend on concurrency, since each sweep is one serial pass. The package
sweep would reach the 30 min interval at roughly 1300 hosts. Memory and
connections follow worker count, not host count: the raised config idles
at ~2.9 GiB and 90–130 connections even with 50 hosts.

## What it shows

1. **Drift alone is fine at 300**, at either config: every sweep ends
   in under 7.5 min against a 30 min interval. Sweeps are serial, so
   time grows linearly with hosts (limit near ~1300 at 30 min).
2. **A 15-minute all-host bulk sync does not fit**, at either config.
   At 4 slots a pass needs 300 × 26 s / 4 ≈ 33 min; at 16 slots on 4
   vCPUs each sync slows to ~93 s and a pass still takes ~29 min. The
   backlog never drains in any S2–S5 run.
3. **Everything else queues behind that backlog in `long_running`.**
   Drift sweeps (`app.tasks.*_drift`) and action host runs
   (`app.tasks.action_host`) share the FIFO queue with bulk syncs
   (`backend/app/tasks/__init__.py`).
   - Default: sweeps start 15–20 min late (effective interval ~48–50
     min); 30–92 action host runs succeed per hour; no action run
     finishes.
   - Raised: sweeps are less late (~44 min) but take ~50 % longer under
     CPU load; action runs all start (12 orchestrator slots) but their
     host runs still wait behind syncs: 61–71 succeeded per hour in S3
     and S5. Only S4's parallelism-0 run finished (300 hosts in 47 min).
4. **Raising concurrency is not a fix on its own.** It needs more than
   Postgres's default 100 connections (peak 183), triples LabDog's memory
   (~1.5 → ~4.3 GiB), and with 4 vCPUs mostly turns into CPU contention.
5. **Memory and Postgres size are not the limit** at default: LabDog
   ~1.5 GiB, Postgres ≤254 MiB / 71 connections.

This supports step 3: give sync, drift and actions their own queues
and workers so a sync backlog can't starve the others. Size the
connection pool and Postgres `max_connections` with it.

## Bugs found

- **`ansible.posix` is missing from the image** (1.1.0 here; a 1.0.0
  install has the same gap). Any sync that includes a linux user fails at
  playbook parse (`ansible.posix.authorized_key`).
- **A playbook parse failure is reported as success.** ansible-runner
  emits no host events, every module is classed `no_tasks`, the job
  ends `success` and the host is marked `in_sync`, though nothing was
  applied.
- **Cron drift is always `out_of_sync` when the SSH user isn't root.**
  `backend/app/cron/collector.py:58` runs `crontab -l -u root` without
  sudo; Debian answers "must be privileged to use -u", the non-zero exit
  is read as an empty crontab, and every managed entry looks missing.
  Confirmed on a converged fake host: as root the `#Ansible:` entries
  are there, as `labdog` the command fails, with `sudo -n` it works.
  300 of 300 cron checks were `out_of_sync` in every scenario.
- **At 16/12 LabDog needs more than Postgres's default 100
  connections.** The first raised run (on stock `max_connections` 100)
  logged 499 `TooManyConnectionsError` within 4 minutes, 96 of them in
  `run_host_sync`, plus beat tasks (sweepers, notifications, alert
  handling). The deployment docs should say how many connections a given
  concurrency needs (measured: ~90–130 idle, 183 peak at 16/12).
- **A sync job whose task dies before claiming the host stays `pending`
  forever.** After the connection errors, 82 of 300 initial sync jobs sat
  `pending` with an empty queue and idle workers for 2.5 h until the run
  was stopped. `sync_sweeper` only recovers rows stuck in `running`, and
  the next-pending dispatch only fires when an in-flight job on that
  host finishes, so nothing ever picks these up again.

## Harness notes

- The fake hosts have a 128 MiB memory limit; one `apt-mark` hit it in
  S5 default (the one failed sync there). Raise `FAKEHOST_MEM_LIMIT` if
  that matters.
- `seed.py` gives up when no sync finishes for 30 min, and
  `run_matrix.sh` skips runs that already have results, so a stalled run
  no longer blocks the matrix.
