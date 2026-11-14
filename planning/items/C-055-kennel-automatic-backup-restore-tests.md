# C-055: kennel: automatic backup restore tests

| | |
|---|---|
| Edition | community |
| Source | user (Dennis's first use case); decision D-22; board item "Companion VM layer" |
| Tier / score | P2 / 5.0 |
| Stage | 11 |
| Effort | L |
| Autonomy | B |
| Depends on | C-054, C-057, C-045 |

## Goal

kennel proves on a schedule that a VM's newest backup restores and boots: it restores the backup beside the original in the sandbox network, runs checks inside the copy, records pass or fail, and removes the copy. An operator can see, per VM, when its backup last passed.

## Scope

In scope:
- Restore-test policies: which VMs, how often, which checks.
- The test run: restore, isolate, start, check, record, destroy.
- Run history per VM, and the `restore_test.finished` webhook.
- A "run now" call, and the last result in the readiness answer.

Out of scope:
- Showing the result in unbork (a badge on the host page). The board notes describe it; it belongs with C-059 or a follow-up. See Assumptions.
- Making backups or managing backup schedules.
- Multi-VM drills (restoring several VMs and testing a service end to end). Listed as a future use case.
- Application-specific checks beyond running a command in the guest.

## Context

- The board item defines the feature: "Restore a VM's newest backup as a throwaway copy, boot it with networking cut off, check it, destroy it, and keep a pass/fail history per VM. A backup nobody has restored is a hope, not a backup." Its mechanics: restore with `archive=` into the pool with `unique=1`, rewrite every network card to the sandbox before first boot, check through the QEMU guest agent (`agent/exec`, which needs no network), destroy in a `finally`.
- The stage behaviour to prove: "A restore test restores a PBS backup beside the original in the sandbox network, runs its check, records the result and cleans up."
- What is not enough, from the board notes: a PBS verify job checks chunk checksums, not that the backup boots.
- Pieces this item reuses: the `restore_beside` job and backup listing from C-045; leases, tag stamp and the sweeper from C-054 (a test VM is a lease with purpose `restore_test`); `move_to_sandbox` and the isolation gate from C-057; `agent/exec` in kennel's Proxmox client from C-054; the job table, worker and webhook outbox from C-028; the readiness answer from C-058.
- `live-restore=1` (PBS only) starts the VM before the restore completes and is marked experimental upstream. The write-up recommends it for restore tests, because the copy is thrown away.
- The lock rule from the write-up: kennel's own schedule skips a VM that is busy on Proxmox instead of waiting, and never runs two copies of the same large VM at once.

## Dependencies

- C-054, C-057 and C-045. C-045 provides the restore job and the backup listing; the stage order runs it first.
- The guest agent installed in the VMs under test. A VM without it can only be checked for "the VM process started", which the result must state.
- Reading existing backups needs the read rights described in C-045 and C-058. Restore tests of existing backups do not need the backup token.
- Free space on the pool's storage for the largest VM under test.
- For failure alerts: kennel sends the webhook; who receives it is the caller's business. unbork's notification events for action failures already exist (`backend/app/notifications/events.py`).

## Implementation steps

1. **Migration** `backend/alembic/versions/000N_restore_tests.py` in kennel: `restore_test_policies` (id, name, cluster_id, selector JSON (explicit vmids, or a Proxmox tag, or a pool), schedule (cron text), sandbox_network_id, checks JSON, boot_timeout_seconds, max_backup_age_seconds nullable, live_restore bool, enabled, callback_url) and `restore_test_runs` (id UUID, policy_id nullable, cluster_id, source_vmid, backup_volid, backup_taken_at, lease_id, state `queued|restoring|booting|checking|passed|failed|skipped|error`, started_at, finished_at, restore_seconds, boot_seconds, checks_result JSON, failure_reason, skip_reason, cleaned_up bool).
2. **Checks.** A check is `{name, kind, ...}`. Kinds in the first version:
   - `agent_ping`: the guest agent answers within `boot_timeout_seconds`. Always first.
   - `exec`: run a command in the guest through `agent/exec` with an argv list, expect exit code 0 within a timeout (examples: `systemctl is-system-running --wait` accepting `running` or `degraded` by option, `test -s /etc/hostname`, `pg_isready`).
   - `fs_writable`: write and remove a file under a given path through `agent/exec`.
   Check output is stored truncated (first 4 KiB) and never logged at info level, since it can hold data from the VM.
3. **Routes** in `kennel/api/restore_tests.py`:
   - `GET/POST/PATCH/DELETE /api/restore-tests/policies` (`require("admin")`; `GET` with `read`).
   - `POST /api/restore-tests/runs` (`require("restore")`; body: cluster, vmid, optional volid, sandbox network, checks, idempotency key): run a test now. Returns 202 with the run.
   - `GET /api/restore-tests?cluster=&vmid=&limit=` and `GET /api/restore-tests/runs/{id}` (`require("read")`).
4. **Run job** (`kind="restore_test"`):
   a. Pick the newest backup of the VM (or the given one). No backup: `skipped` with `no_backup_found`. Newest backup older than `max_backup_age_seconds`: `failed` with `backup_too_old`, without restoring, because a stale backup is a finding.
   b. Skip with a reason when the source VM is locked on Proxmox, when another test of the same VM is running, when free space is short, or when the sandbox network's isolation test is not current.
   c. Call C-045's `restore_beside` with the sandbox network, a short lease TTL (restore estimate plus boot timeout plus a margin), `live_restore` per policy, and `start: false`.
   d. Before the first start, confirm in the VM's config that every network card is on the sandbox bridge. If not, destroy and end `error`. This repeats C-045's check on purpose.
   e. Start, run the checks in order, stop at the first failure, record timings.
   f. In a `finally`: release the lease (stop and destroy), set `cleaned_up`. If cleanup fails, the lease's expiry lets the sweeper finish it; the run records `cleaned_up=false` and the next sweep report names it.
   g. Write the result, send `restore_test.finished` (run id, cluster, vmid, backup time, state, failed check name), and write an audit row.
5. **Scheduler.** The worker loop evaluates policy cron expressions each minute and queues one run per selected VM, at most `restore_tests.max_parallel` (default 1) at a time per cluster, spreading the rest. Use a small pure function for "is this cron due", with tests; if a cron library is added it must have a licence compatible with the AGPL (`croniter` is MIT).
6. **Readiness.** Extend C-058's answer with `restore_test: {last_run_at, last_state, last_passed_at, backup_taken_at}`.
7. **Health.** `/health` stays about kennel itself. Add `GET /api/restore-tests/summary` (`require("read")`): VMs under policy, passed, failed, never tested, oldest pass.
8. Docs: kennel `docs/restore-tests.md` (what a pass proves and does not, writing checks, guest agent requirement, storage and time cost, why the sandbox is mandatory).

## Acceptance criteria

- A "run now" test of a "test" VM with a PBS backup ends `passed` with restore and boot timings recorded, and afterwards no VM from the run exists in the pool.
- During the run, the copy's network cards are all on the sandbox bridge, and the original VM is untouched (its config digest and uptime are unchanged).
- A check that fails on purpose (`exec` of `false`) ends the run `failed` with the check named, and the copy is still removed.
- Killing the worker during the checking phase leaves a VM that the sweeper removes after the lease expires, and the run is marked `error` by the stale-job handling from C-028.
- A policy with a cron schedule produces a run at the scheduled minute; with two VMs selected and `max_parallel=1`, the runs do not overlap.
- `restore_test.finished` arrives at the callback with a valid signature.
- `GET /api/restore-tests?cluster=…&vmid=…` returns the history, and the readiness answer shows the last result.
- A VM without the guest agent ends `failed` at `agent_ping` with a reason that says the agent did not answer.

## Test plan

- kennel: `cd backend && source .venv/bin/activate && pytest tests/test_restore_test_job.py tests/test_restore_test_policies.py tests/test_restore_test_scheduler.py -q` (new; Proxmox mocked, including the failure and crash paths and an assertion that cleanup is attempted on every path), then `pytest tests/ -q`, ruff and bandit.
- Test cluster, from kennel on lin-manager-2: source VMs named "test" only, with existing PBS backups, on Dennis's sandbox network after its isolation test passed. Run one pass, one deliberate failure and one worker kill.
- No policy is created for a VM that is not named "test" during this stage.

## Docs and changelog

- kennel `docs/restore-tests.md`, `docs/api.md`, `CHANGELOG.md` `[Unreleased]`: "Automatic backup restore tests: policies, guest checks, history and a webhook."
- unbork: none in this item.

## Risks and rollback

- A booted copy of a production VM runs its cron jobs and services. The sandbox gate is mandatory for restore tests: there is no "disconnected is enough" mode here, because a later change that reconnects a card would be silent. Outbound NAT still lets the copy reach the internet (mail relays, webhooks, licence servers). Document it and offer a per-policy option to set `link_down=1` on all cards, since the checks need no network.
- Storage and I/O load from restoring large VMs. One at a time per cluster by default, a free-space check, and a schedule the operator sets.
- A test that always passes proves little. The default check set includes `agent_ping` plus a system state check, and the docs push for one application check per VM.
- Live restore is experimental upstream. It is off by default per policy.
- Rollback: disable policies (`enabled=false`); the sweeper removes any copy left.

## Assumptions

- [D-22](../DECISIONS.md) puts restore tests in kennel's scope; [D-23](../DECISIONS.md) provides the sandbox.
- Assumption (not yet confirmed): the plan lists dependencies C-054 and C-057, but this item also needs C-045's restore job. It is written to run after C-045.
- Assumption (not yet confirmed): the "last restore test" badge on unbork's host page and a schedulable `_builtin.restore_test`, both described in the board notes, are not in any staged item. Question for Dennis: add them to C-059 or as a new item?
- Assumption (not yet confirmed): checks run only through the guest agent, never over SSH, so a restore test needs no credentials for the VM.
- Assumption (not yet confirmed): one concurrent test per cluster by default.

## Definition of done

- [ ] Migration, routes, run job and scheduler merged in kennel; tests and lint green.
- [ ] Pass, deliberate failure and worker kill proven on the test cluster with "test" VMs; results saved in the PR.
- [ ] kennel docs and changelog updated.
- [ ] `planning/roadmap.yaml` status updated; one line in `planning/PROGRESS.md`.
