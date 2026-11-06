# Scaling LabDog to 200–300+ hosts

Branch-scoped scratchpad (see "Working with `plans/`" in `CLAUDE.md`).
Delete `plans/` before the PR leaves draft.

## Goal

Run LabDog in production against 200–300 hosts (or more), with 10–20
hosts being worked on at once and a busy schedule of many parallel,
different actions. Today's reference install: one Docker container,
~20 hosts, ~1 GB RSS.

## Decision already made

- **No split by module or domain** (firewall service, packages service…).
  Sync composes all modules into one playbook per host, and every
  host-writing op shares one per-host lock in Postgres. Splitting those
  buys nothing and costs distributed locking.
- **Split by runtime role** is the direction: the same image runs as
  `api`, `worker` (several kinds), `orchestrator` or `beat`, and the
  worker roles scale out. Coordination stays exactly as it is (Postgres
  row status + advisory locks, Celery over Redis).

## Order of work

| Step | File | Changes code? | Touches production? |
| --- | --- | --- | --- |
| 1. Measure the live install | [01-measure.md](01-measure.md) | No | Read-only queries + one bulk sync |
| 2. Build a load-test harness, get a baseline | [02-simulate.md](02-simulate.md) | Adds `dev/loadtest/` only | No, separate instance |
| 3. Split workers by role, re-run the harness | [03-split-workers.md](03-split-workers.md) | Yes | No, until merged |

Step 1 replaces the guesses below with real numbers. Step 2 must produce
a baseline on **unchanged** code before step 3 starts, so the before and
after are comparable.

## Current shape (verified against `dev`, v1.1.0)

| Process | Queues | Default slots | Where |
| --- | --- | --- | --- |
| uvicorn API | – | 1 process, pool 5 + 10 | `app/__main__.py`, `app/db.py` |
| `work` worker | `default`, `long_running` | prefork 4 (`celery.concurrency`) | `app/celery_manager.py` |
| `orchestrator` worker | `orchestrator` | prefork 4 (`celery.orchestrator_concurrency`) | same |
| `beat` | – | exactly 1, RedBeat | `app/tasks/scheduler.py` |

`python -m app` always starts all four (only `--no-celery` exists).
Celery tasks open a throwaway engine per task (`task_session`, pool 2,
overflow 0), so Postgres connections ≈ 2 × in-flight tasks + API pool.

## Bottlenecks to confirm or refute

1. **Drift sweeps share the `long_running` slots with syncs and actions**
   (`app/tasks/__init__.py` routes `*_drift.*` there). Each of the seven
   sweeps is one task that walks every host **serially**
   (`sweep_module` in `app/tasks/drift_sweep.py`) every
   `drift.check_interval_minutes` (30). At ~2 s/host/module a sweep
   holds its slot for 2N seconds.
2. **4 concurrent action runs max.** `run_action` holds an orchestrator
   slot for the whole run while waiting on its batches.
3. **No fairness in `long_running`.** A run with `parallelism <= 0`
   enqueues every host at once ahead of everything scheduled later.
4. **Per-host lock.** Overlapping actions/syncs on one host serialise no
   matter how many workers exist. Group actions lock every member.
5. **Memory per slot.** ~80 MB per prefork child + ~100–200 MB per
   running `ansible-playbook`. Unmeasured; step 1 measures it.

## Working estimates (to be replaced by step 1 numbers)

| Setup | Hosts it handles comfortably |
| --- | --- |
| Today, defaults | ~200–300 (drift alone saturates 4 slots near 500) |
| Same container, tuned (parallel sweeps, ~32 slots, longer drift interval) | ~1,000–3,000 |
| Role split, runner workers scaled out | ~10,000+ (then Postgres/Redis/beat) |

Target topology for 200–300 hosts with a busy action schedule:

| Container | Slots | Peak RAM (estimate) |
| --- | --- | --- |
| api ×1–2 | – | ~300 MB each |
| beat ×1 (never scaled) | – | ~100 MB |
| orchestrator | 12–16 | ~1.3 GB |
| worker (actions + sync) ×2 | 8 each | ~2 GB each |
| worker-drift | 4 | ~0.5 GB |
