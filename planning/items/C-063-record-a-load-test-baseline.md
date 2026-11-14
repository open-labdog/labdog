# C-063: Record a load-test baseline

| | |
|---|---|
| Edition | community |
| Source | proposed, PR-07 "Measure coverage in CI and record a load-test baseline" (load-test half) |
| Tier / score | P1 / 4.5 |
| Stage | 0 |
| Effort | M |
| Autonomy | A |
| Depends on | none |

## Goal
`planning/reviews/baseline-loadtest.md` holds the numbers from one `S2 300 default` run of the existing harness on lin-manager-2, so later stage reviews can rerun the same command and say whether sync waits, drift checks or resource use got worse.

## Scope
In scope:
- One run of `dev/loadtest/run_one.sh S2 300 default` against an image built from the stage's base commit.
- Writing the result file and pointing `planning/PLAN.md` at it.
- Fixing the harness only if it cannot run at all, in the smallest way, as a separate commit.

Out of scope:
- The other scenarios and configurations (`run_matrix.sh`, `raised`, `split`, two-host mode).
- Running the harness in CI or on a schedule.
- Tuning anything in the application because of what the run shows. Findings become proposals.
- Any run on lin-manager, or against real managed hosts. The harness uses fake hosts in containers only.

## Context
- `dev/loadtest/README.md` describes the harness. `run_one.sh <SID> <N> <cfg> [warmup_min window_min]` brings up a fresh stack (`docker compose down -v` before and after), seeds it, drives the load, waits for in-flight work, measures and tears down.
- Timing from the README and the script: 10 minutes warm-up, 60 minutes window, then up to 15 minutes drain. Allow about 100 minutes including seeding.
- Size: "300 hosts used about 6 GB RAM in total". The `default` configuration is one `all` container with `CELERY_CONCURRENCY=4`, `ORCHESTRATOR_CONCURRENCY=4`, `PG_MAX_CONNECTIONS=100` (`run_one.sh`, the `case "$cfg"` block).
- Scenario S2 is "drift sweeps (30 minute interval) plus a bulk sync of all hosts every 15 minutes".
- Needs: Docker with Compose v2, `python3`, `openssl`, `ssh-keygen`. `prepare.sh` creates `.state/id_ed25519`, a `.env` with generated secrets (mode 600), installs `ansible.posix` into `.state/collections` using the image, and builds the `fakehost` image.
- The image under test comes from `LABDOG_IMAGE`; `docker-compose.yml` line 38 defaults to `openlabdog/labdog:dev-5cb85d7`, an old published image. The stack is started with `--no-build`, so a locally built image works.
- Networks and ports: the compose project uses 172.30.0.0/16 and publishes the API on 127.0.0.1:8000 only. Container names are fixed (`labdog-loadtest-postgres-1` is used by `run_one.sh` for `psql`).
- Output lands in `dev/loadtest/results/S2-n300-default/`: `measure.txt` (the `measure_live.sql` queries: drift per module with avg, p50, p95, max; drift status; sync jobs with run and wait percentiles; action runs; error samples; stuck rows at end), `samples.csv` (every 10 seconds: memory, CPU, queue depths, Postgres connections, pending and running rows, host load), `drift_samples.csv`, `marks.json`, `seed.log`, `labdog-log.gz`. `dev/loadtest/.gitignore` ignores `.state/`, `.env`, `results/` and `matrix.log`.
- The script's last line prints `done rc=<n> disk_free=<MiB> ooms=<count>`.
- `planning/PLAN.md`, "Quality baseline for stage reviews", already says stage 0 should record this run and that the last published numbers are in PR #270. `planning/reviews/` exists and is empty.
- lin-manager-2 also hosts other test services in Docker. They must keep running.

## Dependencies
- SSH access from the session to lin-manager-2, with Docker rights there. If the session cannot reach it, the item is blocked; say so and stop.
- A checkout of this repository on lin-manager-2 at the stage's base commit on `dev`.
- At least 8 GB of free RAM and 15 GB of free disk on lin-manager-2 for the duration.

## Implementation steps
1. On lin-manager-2, before anything else, record the environment for the result file: `nproc`, `free -g`, `df -h /`, `uname -r`, `docker version --format '{{.Server.Version}}'`, `docker compose version --short`, and `docker ps --format '{{.Names}}'` (what else is running).
2. Check for clashes and stop if there is one: `docker network ls` and `docker network inspect` for an existing 172.30.0.0/16 subnet outside the `labdog-loadtest` project; `ss -ltn | grep ':8000 '` for the API port. Do not stop or change another project's containers to make room. If there is a clash, report it and ask Dennis.
3. Check out the commit: `git fetch && git checkout <stage base commit>`; note `git rev-parse --short HEAD`.
4. Build the image under test from that commit, from the repository root: `docker build -t unbork-loadtest:<short sha> --build-arg GIT_SHA=$(git rev-parse HEAD) --build-arg BUILD_DATE=$(date -u +%FT%TZ) .`
5. `cd dev/loadtest && LABDOG_IMAGE=unbork-loadtest:<short sha> ./prepare.sh`
6. Start the run detached so a dropped SSH session does not end it: `LABDOG_IMAGE=unbork-loadtest:<short sha> nohup ./run_one.sh S2 300 default > run.log 2>&1 &`. Poll `run.log` and `results/S2-n300-default/` about every ten minutes. Do not start a second run in parallel.
7. When `run.log` ends with `done rc=0 ... ooms=0`, the run is valid. If it ends with `up failed`, `seed failed` or `scenario failed`, read the named log, fix only what stops the harness from running (as its own commit, described in the result file), and run again. Two failed attempts for the same reason: stop and report.
8. Confirm the teardown left nothing: `docker ps -a --filter label=com.docker.compose.project=labdog-loadtest -q | wc -l` prints 0, and the other containers from step 1 are still up.
9. Write `planning/reviews/baseline-loadtest.md` with these sections:
   - Run: date (UTC), host (lin-manager-2, values from step 1), commit, image tag, the exact command, window start and end from `window.env`, the final `done` line.
   - Results: the full content of `measure.txt` in a fenced block.
   - Resources: from `samples.csv`, the peak and the median over the window of application memory, CPU, each queue depth, Postgres connections, pending rows and host load. Compute them with a short Python one-off; put the command in the file.
   - Headline numbers, as a small table: sync p50 and p95 wait, sync p95 run time, slowest drift module p95, stuck rows at end, OOM count.
   - How to rerun and compare: the commands from steps 4 to 6, and the rule that a comparison is only valid on the same host with the same scenario, N and configuration.
   - Notes: anything odd (errors in `labdog-log.gz`, failed drift checks, what else was running on the host).
10. Copy the raw `results/S2-n300-default/` directory to a place outside the repository on lin-manager-2 (for example `~/loadtest-baselines/<date>-<short sha>/`) and name that path in the file. Do not commit raw results.
11. `planning/PLAN.md`: in "Quality baseline for stage reviews", replace "Stage 0 should record one ... run" with a link to the file and the headline numbers.
12. Remove the local image if disk is tight: `docker rmi unbork-loadtest:<short sha>`. Leave `.state/` and `.env` in place for the next run; they are ignored by git.

## Acceptance criteria
- `planning/reviews/baseline-loadtest.md` exists and names the host lin-manager-2, the commit, the command `./run_one.sh S2 300 default`, and a `done rc=0` line with `ooms=0`.
- The file contains every section of `measure.txt` (the `==` headings: drift per module, drift status, sync, action host runs, action runs, error samples, stuck rows at end).
- "Stuck rows at end" is empty, or each stuck row is explained in Notes.
- `git status` in the repository shows no file from `dev/loadtest/results/`, `.state/` or `.env` staged.
- After the run, no `labdog-loadtest` container, network or volume remains on lin-manager-2, and the containers listed in step 1 are still running.
- `planning/PLAN.md` links to the file.

## Test plan
- The run itself is the test. It executes on local Docker on lin-manager-2 only, with fake hosts in containers; no managed host and not lin-manager.
- Sanity check before the long run (optional, about 10 minutes): `LABDOG_IMAGE=unbork-loadtest:<short sha> ./run_one.sh S1 20 default 1 3` and confirm it ends with `done rc=0`. Its output is overwritten by nothing else and can be deleted.
- No application code changes, so no backend or frontend suite is needed for this item. If the harness had to be fixed, run `bash -n dev/loadtest/*.sh` and `python3 -m py_compile dev/loadtest/*.py`.

## Docs and changelog
- `planning/reviews/baseline-loadtest.md` (new) and `planning/PLAN.md`.
- `dev/loadtest/README.md`: only if a harness fix changed how it is used.
- No `CHANGELOG.md` line.

## Risks and rollback
- The run takes about 6 GB of RAM and competes for CPU with whatever else runs on lin-manager-2. Record what else was running; do not stop it. If memory is short, do not reduce N silently: stop and report, because the stage asks for N=300.
- `run_one.sh` starts with `docker compose down -v` in its own project. It cannot remove another project's data, but check step 2 anyway.
- On one host the fake hosts share the CPU with the application (README, last note). The baseline is only comparable with later runs on the same host.
- The container names and the image variable still say "labdog". The rename in stage 2 may change them; later reviews must use the harness as it is at their commit and compare numbers, not file names.
- Rollback: delete the result file and the PLAN.md link. To clean the host: `cd dev/loadtest && docker compose down -v`.

## Assumptions
- [A-04](../DECISIONS.md): stage runs test on local Docker on lin-manager-2.
- Assumption (not yet confirmed): the image is built locally from the base commit and not pulled from the registry. This avoids depending on which registry name and tag exist for that commit.
- Assumption (not yet confirmed): one run is enough for a baseline. Run-to-run variance is unknown; if a later comparison is close, the reviewer reruns the baseline commit as well.
- Assumption (not yet confirmed): raw results are kept on lin-manager-2 outside the repository and are not uploaded anywhere.

## Definition of done
- [ ] One valid `S2 300 default` run on lin-manager-2, torn down cleanly.
- [ ] `planning/reviews/baseline-loadtest.md` written with all sections; `planning/PLAN.md` links to it.
- [ ] Any harness fix is its own commit and is described in the file.
- [ ] `planning/roadmap.yaml`: C-063 `status: done`; one line in `planning/PROGRESS.md` with the headline numbers.
