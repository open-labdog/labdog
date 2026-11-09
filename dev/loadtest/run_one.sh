#!/bin/bash
# One scenario x config: fresh stack, seed, load, collect. Tears down after.
# usage: run_one.sh <S1..S5> <N> <default|raised> [warmup_min window_min]
set -u
sid=$1; n=$2; cfg=$3; warm=${4:-10}; win=${5:-60}
cd "$(dirname "$0")"
out=results/$sid-n$n-$cfg; rm -rf "$out"; mkdir -p "$out"
extra_scale=()
case "$cfg" in
  raised*) export CELERY_CONCURRENCY=16 ORCHESTRATOR_CONCURRENCY=12 PG_MAX_CONNECTIONS=${PG_MAX_CONNECTIONS:-400} ;;
  # Step 3: role containers, same 28 slots as raised
  # (2x4 work + 4 drift + 4 actions + 12 orch).
  split*) export CELERY_CONCURRENCY=4 DRIFT_CONCURRENCY=4 ACTIONS_CONCURRENCY=4 ORCHESTRATOR_CONCURRENCY=12 \
              PG_MAX_CONNECTIONS=${PG_MAX_CONNECTIONS:-400} COMPOSE_FILE=docker-compose.yml:docker-compose.split.yml
          extra_scale=(--scale worker=2) ;;
  *) export CELERY_CONCURRENCY=4 ORCHESTRATOR_CONCURRENCY=4 PG_MAX_CONNECTIONS=${PG_MAX_CONNECTIONS:-100} ;;
esac
# Two-host mode: FAKEHOST_DOCKER=ssh://user@host runs the fake hosts there.
if [ -n "${FAKEHOST_DOCKER:-}" ]; then
    fh() { COMPOSE_FILE=docker-compose.yml COMPOSE_PARALLEL_LIMIT=16 DOCKER_HOST=$FAKEHOST_DOCKER LT_SUBNET=172.30.0.0/16 LT_GW_MODE=nat-unprotected docker compose "$@"; }
    stack() { LT_SUBNET=172.29.0.0/16 docker compose "$@"; }
    fhd() { DOCKER_HOST=$FAKEHOST_DOCKER docker "$@"; }
    local_fh=0
else
    fh() { docker compose "$@"; }; stack() { docker compose "$@"; }; fhd() { docker "$@"; }
    local_fh=$n
fi
teardown() { stack down -v -t 3 >/dev/null 2>&1; [ -n "${FAKEHOST_DOCKER:-}" ] && fh down -v -t 3 >/dev/null 2>&1; true; }
teardown
{ fh up -d --scale fakehost="$n" fakehost && stack up -d --no-build --scale fakehost="$local_fh" "${extra_scale[@]}"; } >"$out/up.log" 2>&1 || { echo "$out: up failed"; teardown; exit 1; }
sleep 5
ts=$(date -u +%FT%TZ)
if ! env ${FAKEHOST_DOCKER:+DOCKER_HOST=$FAKEHOST_DOCKER} python3 seed.py "$n" >"$out/seed.log" 2>&1; then
    echo "$out: seed failed: $(tail -1 "$out/seed.log" | cut -c1-300)"
    stack logs --no-color >"$out/labdog.log" 2>&1
    exit 1
fi
./sample.sh "$out/samples.csv" 10 & spid=$!
python3 scenario.py "$sid" --warmup "$warm" --window "$win" >"$out/marks.json" 2>"$out/scenario.err"
rc=$?
if [ $rc -ne 0 ]; then kill "$spid"; echo "$out: scenario failed: $(tail -1 "$out/scenario.err" | cut -c1-300)"; exit 1; fi
# Let in-flight work finish so stuck rows really are stuck (max 15 min).
for _ in $(seq 90); do
    busy=$(docker exec labdog-loadtest-postgres-1 psql -U labdog -d labdog -At -c \
      "select (select count(*) from sync_jobs where status in ('pending','running'))+(select count(*) from action_runs where status in ('pending','running'))")
    [ "${busy:-1}" = 0 ] && break
    sleep 10
done
kill "$spid" 2>/dev/null
python3 - "$out" >"$out/window.env" <<'PY'
import json,sys,datetime as d
m=json.load(open(sys.argv[1]+"/marks.json"))
f=lambda t:d.datetime.fromtimestamp(t,d.timezone.utc).isoformat()
print(f"FROM={f(m['window_start'])}\nTO={f(m['window_end'])}")
PY
. "$out/window.env"
docker exec -i labdog-loadtest-postgres-1 psql -U labdog -d labdog -v from="$FROM" -v to="$TO" -f - <measure_live.sql >"$out/measure.txt" 2>&1
docker exec labdog-loadtest-postgres-1 psql -U labdog -d labdog -At -F, -c \
  "copy (select * from drift_samples where checked_at >= '$ts') to stdout with csv header" >"$out/drift_samples.csv"
stack logs --no-color 2>&1 | grep -E 'succeeded in|ERROR|CRITICAL|Traceback|Timeout|WorkerLost' | gzip >"$out/labdog-log.gz"
echo "$out: done rc=$rc disk_free=$(df -m --output=avail / | tail -1)MiB ooms=$(fhd inspect --format '{{.State.OOMKilled}}' $(fhd ps -aq --filter label=com.docker.compose.service=fakehost) | grep -c true)"
teardown
