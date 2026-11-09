# 02 — Baseline on unchanged code

Status: **complete** (2026-11-09). All 14 single-box runs and 5 two-host runs finished; raw data stays on
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

## Results, raised config on two hosts, N = 300

Run 2026-11-08/09 to separate CPU contention from queueing. LabDog,
Postgres and Redis stay on the 4 vCPU / 16 GB box; the 300 fake hosts
move to a second VM (4 vCPU, ~6 GB), reached over a static route with
the same 20 ms netem. Same image, scenarios and settings as the raised
runs above (16 / 12, `max_connections` 400).

| | S1 drift | S2 + bulk 15 min | S3 + actions | S4 + par-0 run | S5 + same-minute |
|---|---|---|---|---|---|
| Bulk syncs succeeded¹ | – | 936 (510) | 813 (432) | 625 (317) | 820 (471) |
| Bulk run time avg | – | 55 s (91) | 59 s (97) | 59 s (93) | 56 s (93) |
| Bulk wait p50 / p95 | – | 627 / 1015 s | 886 / 1301 s | 1064 / 2098 s | 849 / 1228 s |
| Syncs pending after drain | 0 | 0 | 0 | 81 + 15 running | 0 |
| Action host runs succeeded | – | – | 175 of 720 (71) | 464 of 1380 (370) | 241 of 720 (61) |
| Action runs finished | – | – | 0 of 8 | 1 of 14 (par-0, 2199 s) | 0 of 8 |
| Drift sweep interval⁵ | 30 min | 28–31 min | 30–36 min | ~51 min | 27–35 min |
| Slowest sweep (package) | 407 s | 792 s | 798 s | 795 s | 754 s |
| `long_running` queue max | 0 | 251 | 350 | 725 | 365 |
| LabDog RSS max | 3.0 GiB | 4.3 GiB | 4.4 GiB | 4.5 GiB | 4.4 GiB |
| Postgres RSS max | 463 MiB | 571 MiB | 552 MiB | 648 MiB | 564 MiB |
| Postgres connections max | 119 | 133 | 136 | 213 | 151 |
| LabDog box CPU avg / max | 5 / 16 % | 88 / 99 % | 86 / 99 % | 86 / 99 % | 85 / 99 % |
| Fake-host box CPU avg / max | 6 / 33 % | 86 / 100 % | 76 / 100 % | 68 / 100 % | 75 / 100 % |
| Min available memory (LabDog / fake hosts) | 11.4 / 3.9 GB | 10.1 / 2.8 GB | 10.0 / 2.8 GB | 9.9 / 2.8 GB | 10.0 / 2.8 GB |
| Failures | 0 | 0 | 0 | 0 | 0 |

Figures in brackets are the single-box raised runs.
⁵ Start of one package sweep to the start of the next; configured
30 min. The single-box raised runs were ~44 min in S2–S5.

What changes with the second host:

- **Sync throughput nearly doubles** (+73–97 %); each sync takes ~57 s
  instead of ~93 s. The bulk backlog now drains within the run in S2, S3
  and S5, but a full pass (300 × ~57 s / 16 ≈ 18 min) still exceeds the
  15 min period: 46–130 hosts are skipped at each bulk request because
  their previous sync is still pending.
- **Both boxes are busy again.** The LabDog box averages ~86 % CPU
  (ansible-runner forks are LabDog's), the fake-host box peaks at 100 %.
  16 sync slots is about what 4 vCPU on the LabDog side can drive.
- **Actions still starve behind syncs.** Host runs succeed 2.5–4× more
  often, but no par-10 action run finishes in an hour in S3 or S5, and
  hundreds of host runs are still queued at the end. S4's par-0 run
  finishes in 37 min instead of 47.
- **Drift is on time only while the queue is short.** Sweeps hold the
  30 min interval in S2, slip to ~36 min in S3 and S5, and to ~51 min in
  S4, where the `long_running` queue peaks at 725. Each sweep takes
  ~13 min under load instead of ~7.
- Memory is not the limit on either box; Postgres peaked at 213
  connections in S4.

So taking the CPU contention away buys throughput, not fairness: drift
and action host runs still wait in the same FIFO queue as bulk syncs,
and how late they are depends on how long that queue is. That is the
part step 3 addresses. Step 3 will not raise total throughput: the
LabDog box is already near its CPU limit, so dedicated drift and action
slots come out of the sync budget.

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

Filed in `BUGS.md` as BUG-128 to BUG-132.

- **BUG-128: `ansible.posix` is missing from the image** (1.1.0 here; a 1.0.0
  install has the same gap). Any sync that includes a linux user fails at
  playbook parse (`ansible.posix.authorized_key`).
- **BUG-129: A playbook parse failure is reported as success.** ansible-runner
  emits no host events, every module is classed `no_tasks`, the job
  ends `success` and the host is marked `in_sync`, though nothing was
  applied.
- **BUG-130: Cron drift is always `out_of_sync` when the SSH user isn't root.**
  `backend/app/cron/collector.py:58` runs `crontab -l -u root` without
  sudo; Debian answers "must be privileged to use -u", the non-zero exit
  is read as an empty crontab, and every managed entry looks missing.
  Confirmed on a converged fake host: as root the `#Ansible:` entries
  are there, as `labdog` the command fails, with `sudo -n` it works.
  300 of 300 cron checks were `out_of_sync` in every scenario.
- **BUG-132: At 16/12 LabDog needs more than Postgres's default 100
  connections.** The first raised run (on stock `max_connections` 100)
  logged 499 `TooManyConnectionsError` within 4 minutes, 96 of them in
  `run_host_sync`, plus beat tasks (sweepers, notifications, alert
  handling). The deployment docs should say how many connections a given
  concurrency needs (measured: ~90–130 idle, 183 peak at 16/12).
- **BUG-131: A sync job whose task dies before claiming the host stays `pending`
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
