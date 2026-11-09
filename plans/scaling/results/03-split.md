# 03 — Split workers (step 3)

Status: **complete** (2026-11-09). All five runs finished, with no failures
and no OOM kills. Raw data stays on the test box
(`results/<SID>-n300-split2h/`).

- Image `labdog-loadtest:step3-f7e39c8d`: the PR base (`dev-5cb85d7`, the
  same code as the baseline) plus the step 3 commit f7e39c8d.
- Same two-host setup, harness and scenarios as the two-host raised
  baseline in `02-baseline.md`. LabDog runs on the 4 vCPU / 16 GB box and
  the 300 fake hosts on a second VM.
- Split topology (`dev/loadtest/docker-compose.split.yml`): `api`,
  `beat`, `orchestrator` (12), `worker` ×2 (6 each) and `worker-drift`
  (4). That is 28 slots, the same as raised (16 + 12). Postgres
  `max_connections` is 400. `celery.all_at_once_cap` is left at its
  default of 20.

## Results vs the two-host raised baseline, N = 300

Figures in brackets are the baseline (`raised2h`).

| | S1 drift | S2 + bulk 15 min | S3 + actions | S4 + par-0 run | S5 + same-minute |
|---|---|---|---|---|---|
| Bulk syncs succeeded | – | 773 (936) | 765 (813) | 715 (625) | 776 (820) |
| Bulk run time avg | – | 54 s (55) | 47 s (59) | 46 s (59) | 48 s (56) |
| Bulk wait p50 / p95 | – | 902 / 1300 s (627 / 1015) | 957 / 1353 s (886 / 1301) | 1029 / 1432 s (1064 / 2098) | 930 / 1311 s (849 / 1228) |
| Syncs pending after drain | 0 | 0 (0) | 0 (0) | 7 running (81 + 15) | 0 (0) |
| Action host runs succeeded | – | – | 185 of 720 (175) | 208 of 1020 (464 of 1380) | 147 of 720 (241) |
| Action runs finished | – | – | 0 of 8 (0) | 0 of 9 (1 of 14) | 0 of 8 (0) |
| Drift sweep interval¹ | 30 min (30) | 30 min (28–31) | 29–30 min (30–36) | 30 min (~51) | 29–30 min (27–35) |
| Package sweep, first to last check | 236–243 s (403–407) | 409–450 s (761–792) | 376–435 s (727–798) | 347–426 s (734–795) | 379–400 s (710–754) |
| Package checks per sweep | 300 | 281–285 (298–299) | 285–300 (298–300) | 284–295 (300) | 282–299 (299–300) |
| `long_running` queue max | 0 | 295 (251) | 383 (350) | 444 (725) | 400 (365) |
| `drift` queue max² | 1468 | 1490 | 1474 | 1492 | 1492 |
| LabDog memory max (all role containers) | 3.6 GiB (3.0) | 4.5 GiB (4.3) | 4.6 GiB (4.4) | 4.6 GiB (4.5) | 4.6 GiB (4.4) |
| Postgres RSS max | 334 MiB (463) | 438 MiB (571) | 470 MiB (552) | 490 MiB (648) | 477 MiB (564) |
| Postgres connections max | 83 (119) | 97 (133) | 113 (136) | 130 (213) | 117 (151) |
| LabDog box CPU avg / max | 15 / 51 % (5 / 16) | 90 / 99 % (88 / 99) | 87 / 99 % (86 / 99) | 87 / 99 % (86 / 99) | 88 / 99 % (85 / 99) |
| Fake-host box CPU avg | 7 % (6) | 79 % (86) | 71 % (76) | 70 % (68) | 73 % (75) |
| Min available memory (LabDog / fake hosts) | 11.0 / 3.7 GB | 10.0 / 3.1 GB | 9.9 / 3.0 GB | 9.8 / 3.0 GB | 9.9 / 3.0 GB |
| Failures | 0 | 0 | 0 | 0 | 0 |

¹ From the start of one package sweep to the start of the next. The
configured interval is 30 min.
² Each sweep fans out one task per host for each of the five modules,
so ~1500 tasks are queued at once. They drain in ~7 min.

## What it shows

1. **Drift is fixed.** Sweeps start every 30 min in every scenario,
   including S4, where the baseline slipped to ~51 min. Fanned out over
   4 drift slots, a sweep finishes in about half the time
   (~6.5–7.5 min under load instead of ~12–13). Under load 1–5 % of
   checks per sweep are skipped because the host is busy syncing (the
   BUG-67 skip). The serial sweep skipped fewer, because it reached each
   host at a different moment. Those checks are picked up by the next
   sweep.
2. **Actions are not fixed.** Action host runs (`app.tasks.action_host`)
   still share `long_running` with bulk syncs on the `worker`
   containers. No par-10 action run finishes within the hour in any
   scenario, and 150–210 host runs succeed per hour, roughly the
   baseline.
3. **The all-at-once cap makes S4 worse under a sync backlog.** The
   par-0 run on 300 hosts is now cut into batches of 20
   (`celery.all_at_once_cap`). Each batch queues behind the syncs, so
   the run is still going after the 15 min drain (baseline: done in
   37 min). Uncapped, its 300 host runs entered the queue together and
   took a large share of it. The cap does what it was meant to do: the
   sync backlog drained in S4 (0 pending, against 81 in the baseline).
   But it costs the run itself.
4. **Syncs lose a little, as expected.** Four of the 16 non-orchestrator
   slots moved to drift, so bulk syncs drop by 5–17 % in S2, S3 and S5.
   They gain 14 % in S4, where the capped action run takes less of the
   queue. Each sync is a little faster (46–54 s against 55–59 s).
5. **Resources are fine.** Postgres connections went down by 20–40 %
   (peak 130 against 213), since drift no longer holds a connection for
   a whole serial sweep. The role containers together use ~0.2 GiB more
   memory than the single container. CPU on the LabDog box is unchanged
   at ~87 %, still the limit.

## Next

Step 3 does what it promised for drift, and not for actions. To stop
action host runs starving behind bulk syncs, they need their own queue
and worker role (for example `actions`, with `app.tasks.action_host` and
`app.tasks.action_group`), sized separately from `worker`. In the
meantime, reconsider the default `all_at_once_cap`: 0 (no cap) restores
the old behaviour for "all at once" runs, and the cap could become an
opt-in. Both changes need another split run to confirm them.

The performance fixes in PRs #274–#278 (SSH pipelining, one remote
command per module, one SSH connection per host, one engine per task)
will shorten syncs and drift checks and lower CPU on the LabDog box.
After that stack merges, rebase step 3 onto it and rerun the baseline
and the split.
