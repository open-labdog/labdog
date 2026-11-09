#!/bin/bash
# Write one CSV row every INTERVAL seconds until killed.
# usage: sample.sh out.csv [interval_s]
export LC_ALL=C  # mawk honours a comma-decimal locale and would truncate 1.2GiB to 1
out=$1; interval=${2:-10}
pg() { docker exec labdog-loadtest-postgres-1 psql -U labdog -d labdog -At -F, -c "$1" 2>/dev/null; }
rd() { docker exec labdog-loadtest-redis-1 redis-cli LLEN "$1" 2>/dev/null; }
# LabDog may be one container ("all") or several role containers ("split"):
# sum memory and CPU over every stack container that isn't postgres/redis/fakehost.
mib() { docker stats --no-stream --format '{{.Name}},{{.MemUsage}},{{.CPUPerc}}' \
    $(docker ps --filter label=com.docker.compose.project=labdog-loadtest --format '{{.Names}}' | grep -v fakehost) \
  | awk -F'[,/]' 'function m(s){v=s+0; if(s~/GiB/)v*=1024; if(s~/KiB/)v/=1024; return v}
      {gsub(/%/,"",$4)
       if ($1 ~ /postgres/) {pg=m($2); pc=$4} else if ($1 ~ /redis/) {rd=m($2)} else {lm+=m($2); lc+=$4}}
      END{printf "%.0f,%.0f,%.0f,%.2f,%s", lm,pg,rd,lc,pc}'; }
cpu() { read -r _ u n s i w _ < /proc/stat; echo "$((u+n+s+i+w)) $((i+w))"; }
# Two-host mode: the fake-host box's CPU, load and memory too.
fh_ssh=${FAKEHOST_DOCKER#ssh://}
fhstat() {
    [ -n "${FAKEHOST_DOCKER:-}" ] || { echo "0 0 0 0"; return; }
    ssh -o BatchMode=yes -o ControlMaster=auto -o ControlPath=/tmp/lt-sample-%C -o ControlPersist=5m "$fh_ssh" \
      'read -r _ u n s i w _ < /proc/stat; echo $((u+n+s+i+w)) $((i+w)) $(cut -d" " -f1 /proc/loadavg) $(awk "/MemAvailable/{print int(\$2/1024)}" /proc/meminfo)' 2>/dev/null || echo "0 0 0 0"
}
echo "ts,labdog_mib,pg_mib,redis_mib,labdog_cpu_pct,pg_cpu_pct,q_default,q_long_running,q_orchestrator,pg_conns,sync_pending,sync_running,run_pending,run_running,hostrun_pending,hostrun_running,host_cpu_busy_pct,load1,mem_avail_mib,disk_free_mib,fh_cpu_busy_pct,fh_load1,fh_mem_avail_mib,q_drift" > "$out"
set -- $(cpu); pt=$1; pi=$2
set -- $(fhstat); fpt=$1; fpi=$2
while :; do
    sleep "$interval"
    set -- $(cpu); dt=$(($1-pt)); di=$(($2-pi)); pt=$1; pi=$2
    busy=$(( dt>0 ? 100*(dt-di)/dt : 0 ))
    set -- $(fhstat); fdt=$(($1-fpt)); fdi=$(($2-fpi)); fpt=$1; fpi=$2; fload=$3; fmem=$4
    fbusy=$(( fdt>0 ? 100*(fdt-fdi)/fdt : 0 ))
    q=$(pg "select (select count(*) from pg_stat_activity where datname='labdog'),
        (select count(*) from sync_jobs where status='pending'),(select count(*) from sync_jobs where status='running'),
        (select count(*) from action_runs where status='pending'),(select count(*) from action_runs where status='running'),
        (select count(*) from action_host_runs where status='pending'),(select count(*) from action_host_runs where status='running')")
    echo "$(date -u +%FT%TZ),$(mib),$(rd default),$(rd long_running),$(rd orchestrator),$q,$busy,$(cut -d' ' -f1 /proc/loadavg),$(awk '/MemAvailable/{print int($2/1024)}' /proc/meminfo),$(df -m --output=avail / | tail -1 | tr -d ' '),$fbusy,$fload,$fmem,$(rd drift)" >> "$out"
done
