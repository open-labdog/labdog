#!/usr/bin/env python3
"""Apply one scenario's load for warm-up + window. Assumes seed.py has run.

usage: scenario.py S1..S5 [--warmup MIN] [--window MIN]
S2+ bulk-sync all hosts every 15 min; S3+ add 8 scheduled actions;
S4 adds one all-hosts run 20 min into the window; S5 puts 4 of them on
one cron minute. Prints JSON timing marks on stdout.
"""
import argparse
import json
import time
from pathlib import Path

from lt_api import Api
from seed import MODULES

HERE = Path(__file__).resolve().parent


def schedules(sid, act):
    """(group, cron, sleep seconds). Each fires twice an hour."""
    rows = [(act[i], f"{3 + i * 3},{33 + i * 3} * * * *", (20, 30, 40, 60)[i % 4])
            for i in range(8)]
    if sid == "S5":
        # Same minute, overlapping groups: per-host lock contention.
        rows[:4] = [(act[i], "5,35 * * * *", 30) for i in range(4)]
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("sid", choices=["S1", "S2", "S3", "S4", "S5"])
    ap.add_argument("--warmup", type=float, default=10)
    ap.add_argument("--window", type=float, default=60)
    a = ap.parse_args()

    seed = json.loads((HERE / ".state/seed.json").read_text())
    g = seed["groups"]
    api = Api()
    api.login(seed["admin"]["email"], seed["admin"]["password"])

    sync_on = a.sid != "S1"
    actions_on = a.sid in ("S3", "S4", "S5")
    sched_ids = []
    if actions_on:
        for gid, cron, dur in schedules(a.sid, seed["action_groups"]):
            sched_ids.append(api.post("/api/scheduled-actions", {
                "target_kind": "group", "target_id": gid, "action_key": "loadtest-sleep",
                "parameters": {"duration": dur}, "schedule_cron": cron, "enabled": True,
                "snapshot_enabled": False, "verify_enabled": False, "auto_rollback": False,
                "batch_size": 10})["id"])

    t0 = time.time()
    win_start, win_end = t0 + a.warmup * 60, t0 + (a.warmup + a.window) * 60
    big_at = win_start + 20 * 60 if a.sid == "S4" else None
    next_sync = t0
    log = []
    while time.time() < win_end:
        now = time.time()
        if sync_on and now >= next_sync:
            r = api.post(f"/api/sync/groups/{g['all']}/bulk", {"module_filter": MODULES})
            log.append({"t": now - t0, "sync_jobs": len(r["triggered_job_ids"]),
                        "skipped": len(r["skipped_host_ids"])})
            next_sync += 15 * 60
        if big_at and now >= big_at:
            r = api.post("/api/actions/runs", {
                "action_key": "loadtest-sleep", "group_id": g["all"],
                "parameters": {"duration": 30}, "parallelism": 0})
            log.append({"t": now - t0, "big_run": r["id"]})
            big_at = None
        time.sleep(5)

    for sid in sched_ids:
        api.put(f"/api/scheduled-actions/{sid}", {
            "target_kind": "group", "target_id": api.get(f"/api/scheduled-actions/{sid}")["target_id"],
            "action_key": "loadtest-sleep", "enabled": False, "schedule_cron": None,
            "snapshot_enabled": False, "verify_enabled": False, "auto_rollback": False})
    print(json.dumps({"scenario": a.sid, "t0": t0, "window_start": win_start,
                      "window_end": win_end, "log": log}))


if __name__ == "__main__":
    main()
