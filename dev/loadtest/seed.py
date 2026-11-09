#!/usr/bin/env python3
"""Seed a fresh test instance through the public REST API only.

usage: seed.py N   (expects `docker compose up --scale fakehost=N` to be up)
Writes .state/seed.json with the ids scenario.py needs.
"""
import json
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from lt_api import Api

HERE = Path(__file__).resolve().parent
ADMIN = {"email": "admin@loadtest.example.com", "password": "loadtest-Passw0rd-only"}
SSH_USER = "labdog"
DRIFT_PATHS = ["hosts-mgmt", "resolver", "linux-users", "cron", "packages"]
MODULES = ["packages", "hosts-file", "cron", "linux-users", "resolver"]


def wait_syncs(api, stall_s=1800):
    """Wait for all syncs; give up when nothing finishes for ``stall_s``."""
    last, since = None, time.time()
    while True:
        time.sleep(10)
        jobs = api.get("/api/sync/jobs?limit=1000")
        done = sum(j["status"] not in ("pending", "running") for j in jobs)
        if done != last:
            last, since = done, time.time()
        elif time.time() - since > stall_s:
            left = sum(j["status"] in ("pending", "running") for j in jobs)
            sys.exit(f"sync stalled: {left} pending/running, none finished in {stall_s}s")
        if not any(j["status"] in ("pending", "running") for j in jobs):
            failed = [j for j in jobs if j["status"] == "failed"]
            if failed:
                sys.exit(f"{len(failed)} sync jobs failed: {failed[0]}")
            return


def assert_applied():
    """A sync whose playbook fails to parse still reports success, so look."""
    one = subprocess.check_output(
        ["docker", "ps", "-q", "--filter", "label=com.docker.compose.service=fakehost"],
        text=True).split()[0]
    probe = "grep -q svc0.all /etc/hosts && grep -q 10.255.0.1 /etc/resolv.conf && id ltuser"
    subprocess.run(["docker", "exec", one, "sh", "-c", probe], check=True)


def fake_host_ips():
    ids = subprocess.check_output(
        ["docker", "ps", "-q", "--filter", "label=com.docker.compose.service=fakehost",
         "--filter", "label=com.docker.compose.project=labdog-loadtest"], text=True).split()
    out = subprocess.check_output(
        ["docker", "inspect", "--format",
         "{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}", *ids], text=True)
    return sorted(out.split(), key=lambda ip: tuple(int(p) for p in ip.split(".")))


def main():
    n = int(sys.argv[1])
    api = Api()
    api.wait_ready()
    api.post("/api/auth/register", ADMIN)
    api.login(ADMIN["email"], ADMIN["password"])

    key = (HERE / ".state/id_ed25519").read_text()
    key_id = api.post("/api/ssh-keys", {"name": "loadtest", "private_key": key,
                                        "ssh_user": SSH_USER, "is_default": True})["id"]

    ips = fake_host_ips()
    if len(ips) != n:
        sys.exit(f"expected {n} fake hosts, found {len(ips)}")

    def add_host(i_ip):
        i, ip = i_ip
        return api.post("/api/hosts", {"hostname": f"fake{i:03d}", "ip_address": ip,
                                       "ssh_user": SSH_USER, "ssh_key_id": key_id})["id"]

    t0 = time.time()
    with ThreadPoolExecutor(6) as ex:
        host_ids = list(ex.map(add_host, enumerate(ips)))
    print(f"{n} hosts in {time.time() - t0:.0f}s", flush=True)

    # Index ranges overlap on purpose, to exercise the per-host lock.
    ranges = {"all": (0, 1.0), "a": (0, .4), "b": (.3, .7), "c": (.6, 1.0), "d": (.2, .5)}
    groups = {}
    for prio, (name, (lo, hi)) in enumerate(ranges.items(), start=1):
        gid = api.post("/api/groups", {"name": f"lt-{name}", "priority": prio * 10})["id"]
        api.post(f"/api/groups/{gid}/hosts",
                 {"host_ids": host_ids[int(lo * n):int(hi * n)]}, ok=(200,))
        groups[name] = gid

    # One schedule per target + action, so S3's 8 actions need 8 targets.
    # No config of their own; neighbours overlap.
    act = []
    for i in range(8):
        lo = i * .1
        gid = api.post("/api/groups", {"name": f"lt-act{i}", "priority": 100 + i})["id"]
        api.post(f"/api/groups/{gid}/hosts",
                 {"host_ids": host_ids[int(lo * n):int((lo + .3) * n)]}, ok=(200,))
        act.append(gid)

    # Real config in the container-safe modules, so syncs have real work.
    g = groups["all"]
    api.put(f"/api/groups/{g}/resolver", {"nameservers": ["10.255.0.1", "10.255.0.2"],
                                          "search_domains": ["loadtest.invalid"]})
    api.post(f"/api/groups/{g}/packages", {"package_name": "cron", "state": "present"})
    api.post(f"/api/groups/{g}/linux-users", {"username": "ltuser", "shell": "/bin/bash"})
    for name, gid in groups.items():
        for i in range(3):
            api.post(f"/api/groups/{gid}/hosts-entries",
                     {"ip_address": f"10.254.{gid % 250}.{i + 1}",
                      "hostname": f"svc{i}.{name}.loadtest.invalid"})
        api.post(f"/api/groups/{gid}/cron-jobs",
                 {"name": f"lt-{name}", "schedule": "17 3 * * *", "command": "/bin/true"})

    def enable_drift(host_id):
        for path in DRIFT_PATHS:
            api.put(f"/api/{path}/hosts/{host_id}/drift-settings", {"drift_check_enabled": True})

    with ThreadPoolExecutor(6) as ex:
        list(ex.map(enable_drift, host_ids))

    # First contact records each host key (TOFU); playbooks refuse unknown keys.
    with ThreadPoolExecutor(6) as ex:
        list(ex.map(lambda h: api.post(f"/api/cron/hosts/{h}/drift-check"), host_ids))

    pack = api.post("/api/action-packs", {"name": "loadtest", "source_type": "local",
                                          "local_path": "/srv/loadtest-pack"})
    api.post("/api/actions/refresh", {}, ok=(200, 201, 202))
    keys = [a["key"] for a in api.get("/api/actions/")]
    if "loadtest-sleep" not in keys:
        sys.exit(f"loadtest-sleep not registered: pack={pack} keys={keys}")

    # Apply the config once so later drift checks compare a converged fleet.
    t0 = time.time()
    api.post(f"/api/sync/groups/{groups['all']}/bulk", {"module_filter": MODULES})
    wait_syncs(api)
    print(f"initial sync of {n} hosts in {time.time() - t0:.0f}s", flush=True)
    assert_applied()

    (HERE / ".state/seed.json").write_text(json.dumps(
        {"n": n, "groups": groups, "action_groups": act, "host_ids": host_ids, "admin": ADMIN}))
    print("seeded", flush=True)


if __name__ == "__main__":
    main()
