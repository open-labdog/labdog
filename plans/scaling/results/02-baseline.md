# 02 — Baseline on unchanged code

Status: **default config done; raised config (16/12) still running.**
This file is updated when the matrix finishes.

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

## What it shows

1. **Drift alone is fine at 300.** Every sweep finishes in under 7.5 min
   against a 30 min interval; CPU averages 14 %.
2. **A 15-minute all-host bulk sync does not fit in 4 slots.** One pass
   needs 300 × 26 s / 4 ≈ 33 min. Waits sit at 30–38 min and the backlog
   never drains.
3. **Everything else queues behind that backlog in `long_running`.**
   Drift sweeps (`app.tasks.*_drift`) and action host runs
   (`app.tasks.action_host`) are routed to the same FIFO queue as bulk
   syncs (`backend/app/tasks/__init__.py`). With syncs pending:
   - drift sweeps start ~15–20 min late, so the effective interval is
     ~48–50 min instead of 30;
   - scheduled actions barely progress: 30–92 host runs succeeded in an
     hour and no action run finished. Each run holds one orchestrator
     slot while it waits, so with 4 slots at most 4 runs are active and
     the rest stay `queued`.
4. **Memory and Postgres are not the limit.** LabDog peaks around
   1.5 GiB, Postgres at 254 MiB and 71 connections, and over 9.5 GB stays
   free. The limit is worker slots and, on this box, CPU.

This supports step 3: give sync, drift and actions their own queues
and workers so a sync backlog can't starve the others.

## Bugs found while setting up

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

## Still to come

- Raised config (16/12) for S1–S5 at N = 300.
- S1 at N = 50 and 150 for both configs.
