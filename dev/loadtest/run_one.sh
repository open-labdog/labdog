#!/bin/bash
# One scenario x config: fresh stack, seed, load, collect. Tears down after.
# usage: run_one.sh <S1..S5> <N> <default|raised> [warmup_min window_min]
set -u
sid=$1; n=$2; cfg=$3; warm=${4:-10}; win=${5:-60}
cd "$(dirname "$0")"
out=results/$sid-n$n-$cfg; rm -rf "$out"; mkdir -p "$out"
if [ "$cfg" = raised ]; then export CELERY_CONCURRENCY=16 ORCHESTRATOR_CONCURRENCY=12
else export CELERY_CONCURRENCY=4 ORCHESTRATOR_CONCURRENCY=4; fi
docker compose down -v -t 3 >/dev/null 2>&1
docker compose up -d --scale fakehost="$n" >"$out/up.log" 2>&1 || { echo "up failed"; exit 1; }
sleep 5
ts=$(date -u +%FT%TZ)
if ! python3 seed.py "$n" >"$out/seed.log" 2>&1; then
    echo "$out: seed failed: $(tail -1 "$out/seed.log" | cut -c1-300)"
    docker logs labdog-loadtest >"$out/labdog.log" 2>&1
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
docker logs labdog-loadtest 2>&1 | grep -E 'succeeded in|ERROR|CRITICAL|Traceback|Timeout|WorkerLost' | gzip >"$out/labdog-log.gz"
echo "$out: done rc=$rc disk_free=$(df -m --output=avail / | tail -1)MiB ooms=$(docker ps -aq --filter label=com.docker.compose.service=fakehost | xargs docker inspect --format '{{.State.OOMKilled}}' | grep -c true)"
docker compose down -v -t 3 >/dev/null 2>&1
